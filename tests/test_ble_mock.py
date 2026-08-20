"""Deterministic mocked-transport tests for the BLE backup/replace/restore flow.

MockWatch implements the production debug shell (AT GETVERSION, mdw, mww/mwb,
snandr, snandw, sdfs) against a virtual NAND + RAM staging buffer, using the
1.0.0.73 registry layout.  Tests verify the exact commands/data the manager
generates, not just success flags.
"""
import struct
import sys

import pytest

import helpers  # noqa: F401  (sys.path setup)
import act_decode
import fw_registry
import ble_ringtone
from ble_ringtone import RingtoneManager, WriteAborted

LAYOUT = fw_registry.LAYOUT_1_0_0_73
PBASE = 0x200000          # virtual sdfs_k partition base on the NAND
PART_SIZE = 0x20000       # virtual partition span


def make_stock_ring1():
    """Deterministic valid ACT content (XOR form) sized to the slot."""
    raw = bytearray(b'\xe1\xd3')
    # 30 silent-ish frames: encode silence without an oracle (fast)
    import act_encode
    enc = act_encode.Encoder()
    import math
    sig = [int(1500 * math.sin(2 * math.pi * 440 * i / 16000)) for i in range(4800)]
    for i in range(len(sig) // 160):
        fr, _ = enc.encode_frame(sig[i * 160:(i + 1) * 160])
        raw += fr
    raw = bytes(raw[:LAYOUT.ring1_size])
    raw += b'\x00' * (LAYOUT.ring1_size - len(raw))
    return act_decode.obfuscate(raw)


class MockWatch:
    """Virtual CMF Watch Pro 2 debug shell."""

    def __init__(self, version='1.0.0.73'):
        self.version = version
        self.part = bytearray(PART_SIZE)
        self.ram = bytearray(0x8000)     # staging buffer window
        self.commands = []               # every command the manager issued
        self.fail_on = None              # inject failure on matching prefix
        # build a synthetic sdfs partition with the registry layout
        self._build_partition()

    # -- virtual media ---------------------------------------------------------
    def _build_partition(self):
        p = self.part
        files = [('poweroff.act', b'\xb6\xf9' + b'\x11' * 2000),
                 ('welcome.act', b'\xb6\xf9' + b'\x55' * 900),
                 ('ring2.act', b'\xb6\xf9' + b'\x22' * 4000),
                 ('ring3.act', b'\xb6\xf9' + b'\x66' * 800),
                 ('ring4.act', b'\xb6\xf9' + b'\x77' * 800),
                 ('ring5.act', b'\xb6\xf9' + b'\x88' * 800),
                 ('alarm.act', b'\xb6\xf9' + b'\x99' * 700),
                 ('find.act', b'\xb6\xf9' + b'\xaa' * 700),
                 ('ring1.act', make_stock_ring1()),   # entry index 8
                 ('sdfs.txt', LAYOUT.test_file_stock)]
        table_len = (len(files) + 1) * 0x20
        pos = table_len
        entries = []
        for name, data in files:
            if name == 'ring1.act':
                pos = LAYOUT.ring1_off  # registry-anchored location
            entries.append((name, pos, data))
            p[pos:pos + len(data)] = data
            pos += len(data)
        hdr = bytearray(0x20)
        hdr[0:8] = b'sdfs.bin'
        struct.pack_into('<II', hdr, 12, len(files), PART_SIZE)
        p[0:0x20] = hdr
        ent = bytearray()
        for name, off, data in entries:
            e = bytearray(0x20)
            e[0:len(name)] = name.encode()
            struct.pack_into('<II', e, 12, off, len(data))
            struct.pack_into('<I', e, 0x1c, ble_ringtone.sum32_words(data))
            ent += e
        p[0x20:0x20 + len(ent)] = ent
        # entry0 checksum words: table sum / data sum
        struct.pack_into('<I', p, LAYOUT.tbl_f4_off, ble_ringtone.sum32_words(bytes(ent)))
        struct.pack_into('<I', p, LAYOUT.tbl_f5_off,
                         ble_ringtone.sum32_words(bytes(p[0x240:pos])))

    def nand(self, off, size):
        rel = off - PBASE
        return bytes(self.part[rel:rel + size])

    # -- shell protocol ----------------------------------------------------------
    def shell(self, cmd, wait=3.0):
        self.commands.append(cmd)
        if self.fail_on and cmd.startswith(self.fail_on):
            raise ConnectionError('mock: connection dropped')
        parts = cmd.split()
        op = parts[0]
        if cmd == 'AT GETVERSION':
            return f'VERSION:{self.version}'.encode()
        if op == 'mdw':
            addr, n = int(parts[1], 16), int(parts[2], 0)
            return self._mdw(addr, n)
        if op == 'mww':
            addr, v = int(parts[1], 16), int(parts[2], 16)
            self._poke(addr, struct.pack('<I', v))
            return b'OK'
        if op == 'mwb':
            addr, v = int(parts[1], 16), int(parts[2], 16)
            self._poke(addr, bytes([v & 0xff]))
            return b'OK'
        if op == 'snandr':
            off, size = int(parts[1], 16), int(parts[2], 16)
            return self._dump_words(self.nand(off, size))
        if op == 'snandw':
            off, size = int(parts[1], 16), int(parts[2], 16)
            staged = self.ram[:size]
            rel = off - PBASE
            self.part[rel:rel + size] = staged
            return b'OK'
        if op == 'sdfs':
            name, size = parts[1], int(parts[2], 0)
            data = self._sdfs_file(name)[:size]
            return self._dump_words(data)
        raise ValueError(f'mock: unknown command {cmd!r}')

    # -- helpers ---------------------------------------------------------------
    def _sdfs_file(self, name):
        for i in range(0x20, 0x400, 0x20):
            e = self.part[i:i + 0x20]
            if not e[0:1]:
                break
            nm = e[0:12].split(b'\x00')[0].decode()
            if nm == name:
                off, size = struct.unpack('<II', e[12:20])
                return bytes(self.part[off:off + size])
        raise ValueError(f'mock: sdfs file {name} not found')

    def _ram_addr(self, addr):
        base = LAYOUT.stage_buffer
        if base <= addr < base + len(self.ram):
            return addr - base
        return None

    def _poke(self, addr, data):
        i = self._ram_addr(addr)
        assert i is not None, f'mock: mww outside staging buffer {addr:#x}'
        self.ram[i:i + len(data)] = data

    def _mdw(self, addr, n):
        if addr == LAYOUT.boot_info_addr:
            buf = bytearray(0x40)
            struct.pack_into('<I', buf, 0x18, 0x20001000)  # partition table ptr
            return self._dump_words(bytes(buf))
        if addr == 0x20001000:
            buf = bytearray(0x100 * 4)
            buf[0:8] = b'sdfs_k\x00\x00'
            struct.pack_into('<I', buf, 12, PBASE)
            return self._dump_words(bytes(buf))
        raise ValueError(f'mock: mdw unknown address {addr:#x}')

    @staticmethod
    def _dump_words(data):
        n = len(data) - (len(data) % 4)
        words = struct.unpack('<%dI' % (n // 4), data[:n])
        return (' '.join('%08x' % w for w in words)).encode()


# ---- fixtures ----------------------------------------------------------------

@pytest.fixture
def watch():
    return MockWatch()


@pytest.fixture
def custom_act():
    import act_encode
    import math
    enc = act_encode.Encoder()
    sig = [int(2500 * math.sin(2 * math.pi * 880 * i / 16000)) for i in range(4800)]
    raw = bytearray(b'\xe1\xd3')
    for i in range(len(sig) // 160):
        fr, _ = enc.encode_frame(sig[i * 160:(i + 1) * 160])
        raw += fr
    raw = bytes(raw[:LAYOUT.ring1_size])
    raw += b'\x00' * (LAYOUT.ring1_size - len(raw))
    return act_decode.obfuscate(raw)


# ---- tests -------------------------------------------------------------------

def test_discovery_supported(watch):
    m = RingtoneManager(watch)
    layout = m.identify()
    assert layout.version == '1.0.0.73'
    assert watch.commands == ['AT GETVERSION']


def test_discovery_unsupported():
    watch = MockWatch(version='9.9.9.99')
    m = RingtoneManager(watch)
    with pytest.raises(fw_registry.UnsupportedFirmwareError) as ei:
        m.identify()
    assert str(ei.value) == fw_registry.REFUSAL_MESSAGE
    # not a single command beyond GETVERSION, and no writes
    assert watch.commands == ['AT GETVERSION']


def test_find_pbase(watch):
    m = RingtoneManager(watch)
    m.identify()
    assert m.find_pbase() == PBASE
    # recon used only read commands
    assert all(c.split()[0] in ('mdw', 'AT') or c.startswith('AT')
               for c in watch.commands)


def test_backup(watch):
    m = RingtoneManager(watch)
    m.identify()
    data = m.backup_ring1()
    assert len(data) == LAYOUT.ring1_size
    assert data == watch._sdfs_file('ring1.act')
    # backup is a decodable ACT stream in XOR form
    raw = act_decode.deobfuscate(data)
    res = act_decode.decode(raw)
    assert res is not None and res[1] > 0


def test_write_replaces_ring1_and_repairs_checksums(watch, custom_act):
    m = RingtoneManager(watch)
    m.identify()
    m.find_pbase()
    before_tbl_f4, = struct.unpack('<I', watch.nand(PBASE, 0x20)[LAYOUT.tbl_f4_off:
                                                                 LAYOUT.tbl_f4_off + 4])
    m.write_ring1(custom_act, confirm=True)
    # content replaced
    got = watch.nand(PBASE + LAYOUT.ring1_off, LAYOUT.ring1_size)
    assert got == custom_act
    # checksum words adjusted by the ring1 delta
    old = m.backup
    delta = (ble_ringtone.sum32_words(custom_act)
             - ble_ringtone.sum32_words(old)) & 0xffffffff
    tbl = watch.nand(PBASE, 0x200)
    f4, = struct.unpack('<I', tbl[LAYOUT.tbl_f4_off:LAYOUT.tbl_f4_off + 4])
    assert f4 == (before_tbl_f4 + delta) & 0xffffffff
    entry_off = (LAYOUT.sdfs_entry_index + 1) * 0x20 + 0x1c
    ef5, = struct.unpack('<I', tbl[entry_off:entry_off + 4])
    assert ef5 == ble_ringtone.sum32_words(custom_act)
    # untouched files preserved byte-for-byte
    assert watch._sdfs_file('ring2.act') == b'\xb6\xf9' + b'\x22' * 4000
    assert watch._sdfs_file('sdfs.txt') == LAYOUT.test_file_stock
    # written content decodes
    res = act_decode.decode(act_decode.deobfuscate(got))
    assert res is not None and res[1] > 0


def test_write_requires_confirmation(watch, custom_act):
    m = RingtoneManager(watch)
    m.identify()
    with pytest.raises(WriteAborted):
        m.write_ring1(custom_act)
    assert not any(c.startswith('snandw') for c in watch.commands)


def test_write_rejects_bad_size(watch, custom_act):
    m = RingtoneManager(watch)
    m.identify()
    m.find_pbase()
    with pytest.raises(WriteAborted):
        m.write_ring1(custom_act[:-16], confirm=True)
    assert not any(c.startswith('snandw') for c in watch.commands)


def test_write_rejects_raw_stream(watch, custom_act):
    m = RingtoneManager(watch)
    m.identify()
    m.find_pbase()
    raw = act_decode.deobfuscate(custom_act)
    with pytest.raises(WriteAborted):
        m.write_ring1(raw, confirm=True)


def test_write_aborts_on_flash_mismatch(watch, custom_act):
    m = RingtoneManager(watch)
    m.identify()
    m.find_pbase()
    m.backup_ring1()
    # corrupt the on-flash ring1 head after backup
    rel = LAYOUT.ring1_off
    watch.part[rel] ^= 0xff
    with pytest.raises(WriteAborted):
        m.write_ring1(custom_act, confirm=True)
    assert not any(c.startswith('snandw') for c in watch.commands)


def test_failure_mid_write_and_restore(watch, custom_act):
    """Connection drop during window write: restore must recover the backup."""
    m = RingtoneManager(watch)
    m.identify()
    m.find_pbase()
    m.backup_ring1()
    original = m.backup
    watch.fail_on = 'snandw'
    with pytest.raises(ConnectionError):
        m.write_ring1(custom_act, confirm=True)
    watch.fail_on = None
    # restore path succeeds once the link is back
    assert m.restore(confirm=True)
    got = watch.nand(PBASE + LAYOUT.ring1_off, LAYOUT.ring1_size)
    assert got == original


def test_restore_roundtrip(watch, custom_act):
    m = RingtoneManager(watch)
    m.identify()
    m.find_pbase()
    original = m.backup_ring1()
    m.write_ring1(custom_act, confirm=True)
    assert watch.nand(PBASE + LAYOUT.ring1_off, LAYOUT.ring1_size) == custom_act
    m.restore(confirm=True)
    got = watch.nand(PBASE + LAYOUT.ring1_off, LAYOUT.ring1_size)
    assert got == original


def test_no_write_commands_for_readonly_flow(watch):
    m = RingtoneManager(watch)
    m.identify()
    m.find_pbase()
    m.backup_ring1()
    writes = [c for c in watch.commands
              if c.split()[0] in ('mww', 'mwb', 'snandw')]
    assert writes == []
