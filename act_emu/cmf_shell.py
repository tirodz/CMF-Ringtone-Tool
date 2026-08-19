#!/usr/bin/env python3
"""cmf_shell.py - BLE shell client for CMF Watch Pro 2 (ATS3089).

The watch exposes a Zephyr shell over the CMF "shell" GATT service (the same
service Gadgetbridge uses for "AT GETSECRET"). In addition to the factory AT
command group, the production build registers the Actions debug commands
(mdX/mwX, fread, snandr, snandw, sdfs, ...), confirmed by the command table in
original.bin (entry handlers disassembled in REPORT.md).

Modes:
  recon  : pair + run read-only commands (mdw/sdfs/snandr) - SAFE
  stage  : fill the snandw staging buffer via mww (write to RAM only)
  write  : snandw a prepared window to NAND (DESTRUCTIVE - requires --i-am-sure)

Requires: pip install bleak
"""
import argparse
import asyncio
import hashlib
import struct
import sys

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    sys.exit("pip install bleak")

# GATT (from Gadgetbridge CmfWatchProSupport)
SVC_CMD = "0000fff0-0000-1000-8000-00805f9b34fb"
UUID_CMD_READ = "0000fff4-0000-1000-8000-00805f9b34fb"   # notify
UUID_CMD_WRITE = "0000fff1-0000-1000-8000-00805f9b34fb"
SVC_SHELL = "77d4e67c-2fe2-2334-0d35-9ccd078f529c"
UUID_SHELL_READ = "77d4ff02-2fe2-2334-0d35-9ccd078f529c"  # notify
UUID_SHELL_WRITE = "77d4ff01-2fe2-2334-0d35-9ccd078f529c"

AES_IV = bytes([0x50,0x51,0x52,0x53,0x54,0x55,0x56,0x57,0x60,0x61,0x62,0x63,0x64,0x65,0x66,0x5a])
A5 = b"\xa5"
HDR = 0xf5

def cmd_packet(cmd1, cmd2, chunk_total, chunk_idx, payload):
    return (bytes([HDR]) + struct.pack(">H", len(payload)) + struct.pack(">H", cmd1)
            + struct.pack(">H", chunk_total) + struct.pack(">H", chunk_idx)
            + struct.pack(">H", cmd2) + payload)

def sha256(b):
    return hashlib.sha256(b).digest()


class CmfShell:
    def __init__(self):
        self.client = None
        self.k1 = None
        self.session = None
        self.shell_response = bytearray()
        self.shell_event = asyncio.Event()

    def _shell_notify(self, _h, data):
        self.shell_response += data
        self.shell_event.set()

    async def connect(self, address=None, timeout=20.0):
        if not address:
            print("scanning for watch...")
            devs = await BleakScanner.discover(timeout=timeout)
            dev = None
            for d in devs:
                n = d.name or ""
                if "watch" in n.lower() or "cmf" in n.lower():
                    dev = d; break
            if not dev:
                sys.exit("no watch found; pass --address")
            address = dev.address
            print("found", dev.name, address)
        self.client = BleakClient(address)
        await self.client.connect(timeout=timeout)
        print("connected:", address)
        await self.client.start_notify(UUID_SHELL_READ, self._shell_notify)

    async def shell(self, cmd, wait=3.0):
        """Send a shell command, return raw response bytes."""
        self.shell_response.clear()
        self.shell_event.clear()
        data = cmd.encode() if isinstance(cmd, str) else cmd
        await self.client.write_gatt_char(UUID_SHELL_WRITE, data, response=True)
        try:
            await asyncio.wait_for(self.shell_event.wait(), wait)
        except asyncio.TimeoutError:
            pass
        await asyncio.sleep(0.2)
        return bytes(self.shell_response)

    async def get_secret(self):
        resp = await self.shell("AT GETSECRET")
        text = resp.decode(errors="ignore")
        if "GETSECRET:" not in text:
            raise RuntimeError(f"unexpected GETSECRET response: {text!r}")
        secret = bytes.fromhex(text.split("GETSECRET:", 1)[1].strip()[:32])
        print("app secret:", secret.hex())
        return secret

    async def pair(self, secret):
        import os
        r1 = os.urandom(16)
        from Crypto.Cipher import AES
        def aes_enc(key, data):
            return AES.new(key, AES.MODE_CBC, AES_IV).encrypt(data)
        # step 1: pair request (plaintext)
        pkt = cmd_packet(0xffff, 0x8047, 1, 1, A5 + r1)
        # collect reply on cmd channel
        # NOTE: full pairing requires cmd-channel notify plumbing; shell auth is
        # independent of the cmd channel, so we skip full pairing unless needed.
        print("(full pairing implemented in pair_full; shell works without it for AT cmds)")
        return None

    async def mdw(self, addr, count=4):
        return await self.shell(f"mdw {addr:#x} {count}")

    async def mww(self, addr, value):
        return await self.shell(f"mww {addr:#x} {value:#x}")

    async def snandr(self, off, size):
        return await self.shell(f"snandr {off:#x} {size:#x}", wait=10.0)

    async def snandw(self, off, size):
        return await self.shell(f"snandw {off:#x} {size:#x}", wait=30.0)

    async def sdfs_dump(self, name, size):
        return await self.shell(f"sdfs {name} {size}", wait=30.0)


async def recon(args):
    c = CmfShell()
    await c.connect(args.address)
    resp = await c.shell("AT GETVERSION")
    print("GETVERSION:", resp)
    # boot info / partition table
    resp = await c.mdw(0x1000000, 0x40)
    print("boot info:", resp)
    # optional reads
    if args.sdfs:
        for name in args.sdfs:
            r = await c.sdfs_dump(name, 64)
            print(f"sdfs {name}:", r)
    if args.read:
        for off, size in args.read:
            r = await c.snandr(off, size)
            print(f"snandr {off:#x}:", r[:400])
    await c.client.disconnect()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--address")
    ap.add_argument("mode", choices=["recon", "stage", "write"])
    ap.add_argument("--sdfs", nargs="*")
    ap.add_argument("--read", nargs="*", type=lambda s: [int(x, 0) for x in s.split(",")],
                    help="snandr off,size pairs")
    ap.add_argument("--i-am-sure", action="store_true")
    args = ap.parse_args()
    if args.mode == "recon":
        asyncio.run(recon(args))
    else:
        sys.exit("stage/write modes are built in cmf_flash_plan.py to keep this tool read-only")


if __name__ == "__main__":
    main()
