"""pair_and_probe.py - full Gadgetbridge-protocol pairing, then read-only probes.

Implements the documented CmfCharacteristic framing + auth flow from
Gadgetbridge (see CmfCharacteristic.java/CmfWatchProSupport.java upstream).
Run:

    python tools/pair_and_probe.py --address XX:XX:XX:XX:XX:XX

Only sends FIRMWARE_VERSION_GET and SERIAL_NUMBER_GET after pairing; anything
else in this file is a no-op. Never touches NAND/memory/resource writes.
"""
import argparse
import asyncio
import zlib

from bleak import BleakClient
from Crypto.Cipher import AES

# GATT (from Gadgetbridge CmfWatchProSupport)
UUID_CMD_READ  = '0000fff1-0000-1000-8000-00805f9b34fb'   # command notifications
UUID_CMD_WRITE = '0000fff2-0000-1000-8000-00805f9b34fb'   # command writes
UUID_SHELL_READ  = '77d4ff02-2fe2-2334-0d35-9ccd078f529c'
UUID_SHELL_WRITE = '77d4ff01-2fe2-2334-0d35-9ccd078f529c'

AES_IV = bytes([0x50,0x51,0x52,0x53,0x54,0x55,0x56,0x57,0x60,0x61,0x62,0x63,0x64,0x65,0x66,0x5a])
A5 = b'\xa5'


def _pkt(cmd1, cmd2, payload, session_key=None, total=1, idx=1):
    """Frame one command packet per CmfCharacteristic.java."""
    if session_key is not None and payload:
        chunk = payload + zlib.crc32(payload).to_bytes(4, 'big')
        chunk = AES.new(session_key, AES.MODE_CBC, AES_IV).encrypt(
            chunk + bytes([16 - len(chunk) % 16]) * (16 - len(chunk) % 16))
    else:
        chunk = payload + zlib.crc32(payload).to_bytes(4, 'big') if payload else b''
    hdr = (bytes([0xf5]) + len(chunk).to_bytes(2, 'big') + cmd1.to_bytes(2, 'big')
           + total.to_bytes(2, 'big') + idx.to_bytes(2, 'big') + cmd2.to_bytes(2, 'big'))
    return hdr + chunk


class Probe:
    def __init__(self, address):
        self.address = address
        self.c = None
        self.buf = bytearray()
        self.ev = asyncio.Event()
        self.session = None  # current session key (K1 / nonce-derived)

    def _h(self, _, data):
        self.buf.extend(data)
        self.ev.set()

    async def cmd(self, cmd1, cmd2, payload=b'', label=''):
        self.buf.clear(); self.ev.clear()
        p = _pkt(cmd1, cmd2, payload, self.session)
        await self.c.write_gatt_char(UUID_CMD_WRITE, p)
        try:
            await asyncio.wait_for(self.ev.wait(), 3.0)
        except asyncio.TimeoutError:
            return None, label
        return bytes(self.buf), label

    async def shell(self, text):
        """shell-channel command (read-only channel; separate from command ch)."""
        buf_s = bytearray(); ev_s = asyncio.Event()
        def hs(_, d): buf_s.extend(d); ev_s.set()
        await self.c.start_notify(UUID_SHELL_READ, hs)
        await self.c.write_gatt_char(UUID_SHELL_WRITE, text.encode())
        try:
            await asyncio.wait_for(ev_s.wait(), 5.0)
        except asyncio.TimeoutError:
            return None
        await self.c.stop_notify(UUID_SHELL_READ)
        return bytes(buf_s)

    async def run(self):
        self.c = BleakClient(self.address)
        print('Connected: ', end='', flush=True)
        await self.c.connect()
        print('YES')
        await self.c.start_notify(UUID_CMD_READ, self._h)

        # ---- AT GETSECRET (plaintext shell) --------------------------------
        print('Shell AT GETSECRET: ', end='', flush=True)
        r = await self.shell('AT GETSECRET')
        if not r or b'GETSECRET:' not in r:
            print('FAIL (no secret)')
            return
        secret_hex = r.decode(errors='ignore').split('GETSECRET:')[1].strip()[:32]
        app_secret = bytes.fromhex(secret_hex)
        print('OK')

        # ---- AUTH_PAIR_REQUEST ---------------------------------------------
        print('AUTH_PAIR_REQUEST: ', end='', flush=True)
        import os, hashlib
        random1 = os.urandom(16)
        signed1 = hashlib.sha256(random1 + app_secret).digest()
        resp, _ = await self.cmd(0xffff, 0x8047, random1 + signed1, 'pair')
        if resp is None:
            print('FAIL (no response)')
            return
        # reply payload: random2(16) + signed2(32) (plaintext reply)
        pl = resp[11:-4]
        if len(pl) < 48:
            print('FAIL (short reply)')
            return
        random2, signed2 = pl[:16], pl[16:48]
        ok = hashlib.sha256(random2 + app_secret).digest() == signed2
        if not ok:
            print('FAIL (random2 signature mismatch)')
            return
        k1 = hashlib.sha256(random1 + random2 + app_secret).digest()[:16]
        self.session = k1
        print('SUCCESS')

        # ---- AUTH_PHONE_NAME (encrypted; phone model) -----------------------
        print('AUTH_PHONE_NAME: ', end='', flush=True)
        name = b'probe'
        resp, _ = await self.cmd(0xffff, 0x8049, A5 + name, 'phone-name')
        print('OK' if resp is not None else 'no reply (continuing)')

        # ---- AUTH_NONCE / confirm (session refresh) -------------------------
        print('AUTH_NONCE_REQUEST: ', end='', flush=True)
        resp, _ = await self.cmd(0xffff, 0x804b, A5, 'nonce')
        if resp is not None:
            pl = resp[11:-4]
            self.session = hashlib.sha256(pl + app_secret).digest()[:16]
            print('SUCCESS')
        else:
            print('skipped (nonce: no response)')

        print('AUTHENTICATED_CONFIRM: ', end='', flush=True)
        resp, _ = await self.cmd(0xffff, 0x804d, A5, 'confirm')
        print('SUCCESS' if resp is not None else '(no reply; continuing anyway)')

        # ---- read-only probes ------------------------------------------------
        for name, c1, c2 in [('FIRMWARE_VERSION_GET', 0xffff, 0x8006),
                             ('SERIAL_NUMBER_GET',   0x00de, 0x0002)]:
            resp, _ = await self.cmd(c1, c2, b'', name)
            if resp is None:
                print(f'{name}: FAIL (no response)')
                continue
            pl = resp[11:-4]
            if self.session:
                try:
                    dec = AES.new(self.session, AES.MODE_CBC, AES_IV).decrypt(pl)
                    pl = dec[:-4]  # strip crc tail
                except Exception:
                    pass
            txt = pl.decode('utf-8', 'replace').rstrip('\x00')
            print(f'{name}: {txt!r}')

        await self.c.disconnect()
        print('Disconnected: YES')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--address', required=True)
    args = ap.parse_args()
    p = Probe(args.address)
    asyncio.run(p.run())


if __name__ == '__main__':
    main()
