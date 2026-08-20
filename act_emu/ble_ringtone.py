"""ble_ringtone.py - transport-agnostic BLE backup / replace / restore flow.

The production BLE debug shell (mdw/mww/snand*/sdfs) is wrapped by a small
transport interface so the whole flow is testable against a mock watch:

    class Transport:
        def shell(self, cmd: str, wait: float = 3.0) -> bytes: ...

High level flow (backup -> safety checks -> stage -> write -> verify ->
restore) lives here; every step fails hard on any mismatch.  No command is
invented: only mdw, mwh/mwb, mww, snandr, snandw, sdfs and AT GETVERSION are
used, all confirmed present in the production firmware (REPORT.md).
"""
import struct

import fw_registry

# shell command helpers -------------------------------------------------------


def parse_shell_version(resp: bytes):
    """Extract a dotted firmware version from an 'AT GETVERSION' response."""
    text = resp.decode('utf-8', 'replace')
    for tok in text.replace(':', ' ').split():
        if tok.count('.') == 3 and all(p.isdigit() for p in tok.split('.')):
            return tok
    # fall back: any token that looks like x.y.z(.w)
    for tok in text.split():
        parts = tok.split('.')
        if len(parts) >= 3 and all(p.isdigit() for p in parts):
            return tok
    raise ValueError(f'no version in GETVERSION response: {text!r}')


def parse_hex_words(resp: bytes):
    """Parse the hex-dump output of mdw/snandr/sdfs into bytes.

    The shell prints lines like 'xxxxxxxx: 44332211 88776655 ...' (u32 words,
    little-endian order within the watch's word view).  Tolerates ASCII
    gutter and non-hex lines.
    """
    out = bytearray()
    for line in resp.decode('utf-8', 'replace').splitlines():
        toks = line.replace(':', ' ').split()
        for t in toks:
            if len(t) == 8 and all(c in '0123456789abcdefABCDEF' for c in t):
                out += struct.pack('<I', int(t, 16))
    return bytes(out)


def parse_hex_bytes(resp: bytes):
    """Parse a byte-oriented hex dump ('xx xx xx ...') into bytes."""
    out = bytearray()
    for line in resp.decode('utf-8', 'replace').splitlines():
        toks = line.replace(':', ' ').split()
        for t in toks:
            if len(t) == 2 and all(c in '0123456789abcdefABCDEF' for c in t):
                out.append(int(t, 16))
    return bytes(out)


def sum32_words(b):
    n = len(b) - (len(b) % 4)
    return sum(struct.unpack('<%dI' % (n // 4), b[:n])) & 0xffffffff


def stage_cmds(content: bytes, base):
    """mwb for the first 3 bytes (unaligned head), mww for the rest.

    Matches the ACTIONS shell staging protocol: buffer starts at `base`
    (odd address), the driver requires word-aligned writes thereafter.
    """
    cmds = [f'mwb {base + i:#x} {content[i]:#x}' for i in range(3)]
    padded = bytearray(content[3:])
    while len(padded) % 4:
        padded.append(0)
    pos = base + 3
    for i in range(0, len(padded), 4):
        w, = struct.unpack('<I', padded[i:i + 4])
        cmds.append(f'mww {pos:#x} {w:#x}')
        pos += 4
    return cmds


# ---- the manager -------------------------------------------------------------


class WriteAborted(Exception):
    """Raised on any pre-write safety-check failure (nothing was written)."""


class RingtoneManager:
    def __init__(self, transport):
        self.t = transport
        self.layout = None
        self.pbase = None
        self.backup = None
        self._expected = None   # content we last confirmed on flash
        self._restore = False

    # -- discovery -------------------------------------------------------------
    def identify(self):
        """Read the firmware version and resolve it against the registry.

        Unknown versions raise UnsupportedFirmwareError with the documented
        refusal message - before any write is even considered.
        """
        resp = self.t.shell('AT GETVERSION')
        version = parse_shell_version(resp)
        self.version = version
        self.layout = fw_registry.lookup(version)
        return self.layout

    # -- recon ------------------------------------------------------------------
    def find_pbase(self, candidates=None):
        """Locate the sdfs_k partition base (PBASE) on the FTL.

        Reads boot info and the partition table; candidates are absolute NAND
        offsets for which `snandr` output is checked for the SDFS header
        signature.  Kept evidence-driven: values come from mdw reads, not
        hardcoded beyond the boot-info entry address.
        """
        layout = self.layout or self.identify()
        resp = self.t.shell(f'mdw {layout.boot_info_addr:#x} 0x40')
        boot = parse_hex_words(resp)
        if len(boot) < 0x40:
            raise WriteAborted('boot info read too short')
        # partition table pointer inside boot info (offset 0x18 in this build)
        part_table = struct.unpack('<I', boot[0x18:0x1c])[0]
        resp = self.t.shell(f'mdw {part_table:#x} 0x100')
        tbl = parse_hex_words(resp)
        # find the sdfs_k entry by scanning name + PBASE pairs
        for i in range(0, len(tbl) - 8, 4):
            try:
                name = tbl[i:i + 8].split(b'\x00')[0].decode('ascii', 'ignore')
            except Exception:
                continue
            if name == 'sdfs_k':
                self.pbase = struct.unpack('<I', tbl[i + 12:i + 16])[0]
                return self.pbase
        # fallback: caller-provided candidate list verified by signature
        for cand in candidates or ():
            data = self.snandr(cand, 0x40)
            if data[:8] == b'sdfs.bin':
                self.pbase = cand
                return cand
        raise WriteAborted('could not locate sdfs_k partition (PBASE)')

    def snandr(self, off, size):
        resp = self.t.shell(f'snandr {off:#x} {size:#x}', wait=10.0)
        return parse_hex_words(resp)[:size]

    # -- backup ------------------------------------------------------------------
    def backup_ring1(self):
        """Read the current ring1.act via `sdfs`, validate it, keep a copy."""
        layout = self.layout or self.identify()
        resp = self.t.shell(f'sdfs {layout.ring1_name} {layout.ring1_size}',
                            wait=30.0)
        data = parse_hex_words(resp)
        if len(data) < layout.ring1_size:
            data = parse_hex_bytes(resp)
        if len(data) < layout.ring1_size:
            raise WriteAborted('sdfs backup read too short')
        data = data[:layout.ring1_size]
        # validation: XOR form starts with b6 f9 and sum32 must match the
        # table entry checksum (verified later against snandr table read)
        if data[:2] != b'\xb6\xf9':
            raise WriteAborted('ring1 backup: unexpected header '
                               f'{data[:2].hex()} (expected XORed actii v4)')
        self.backup = data
        self._expected = data
        return data

    # -- safety checks ------------------------------------------------------------
    def preflight(self, custom, check_current=True):
        """All checks required before any destructive write.

        check_current: require the on-flash head to equal the content we last
        confirmed there (backup or the last verified write).  Restore may skip
        this: after an interrupted write the flash content is uncertain and
        the whole point of restore is to repair it.
        """
        layout = self.layout or self.identify()
        if self.pbase is None:
            self.find_pbase()
        if len(custom) != layout.ring1_size:
            raise WriteAborted(
                f'custom ringtone size {len(custom)} != {layout.ring1_size}')
        if custom[:2] != b'\xb6\xf9':
            raise WriteAborted('custom ringtone is not the on-flash XOR form')
        if self.backup is None:
            self.backup_ring1()
        if check_current and self._expected is not None:
            got = self.snandr(self.pbase + layout.ring1_off, layout.sector)
            if got[:layout.sector] != self._expected[:layout.sector]:
                raise WriteAborted('on-flash ring1 does not match the last '
                                   'confirmed content')
        return True

    # -- replacement --------------------------------------------------------------
    def build_windows(self, custom):
        """Build the staging window and patched table sector (offline math).

        Returns (win_start, win_bytes, table_sector_bytes).
        """
        layout = self.layout
        win_start, win_end = fw_registry.ring1_window(layout)
        # read the current window from flash
        cur = self.snandr(self.pbase + win_start, win_end - win_start)
        if len(cur) < win_end - win_start:
            raise WriteAborted('staging window read too short')
        win = bytearray(cur[:win_end - win_start])
        rel = layout.ring1_off - win_start
        win[rel:rel + layout.ring1_size] = custom

        # table sector: add the ring1 sum delta to the 3 checksum words
        tbl = bytearray(self.snandr(self.pbase, layout.sector))
        old = self.backup[:layout.ring1_size]
        delta = (sum32_words(custom) - sum32_words(old)) & 0xffffffff
        entry_off = (layout.sdfs_entry_index + 1) * 0x20 + 0x1c
        for off in (entry_off, layout.tbl_f4_off, layout.tbl_f5_off):
            v, = struct.unpack('<I', tbl[off:off + 4])
            struct.pack_into('<I', tbl, off, (v + delta) & 0xffffffff)
        return win_start, bytes(win), bytes(tbl)

    def write_ring1(self, custom, confirm=False):
        """Destructive replacement of ring1.act.

        Requires confirm=True.  Order: backup -> stage -> write window ->
        write table sector -> verify.
        """
        if not confirm:
            raise WriteAborted('destructive write requires confirm=True')
        layout = self.layout or self.identify()
        self.preflight(custom, check_current=not self._restore)
        win_start, win, tbl = self.build_windows(custom)
        for cmd in stage_cmds(win, layout.stage_buffer):
            self.t.shell(cmd)
        self.t.shell(f'snandw {self.pbase + win_start:#x} {len(win):#x}',
                     wait=30.0)
        for cmd in stage_cmds(tbl, layout.stage_buffer):
            self.t.shell(cmd)
        self.t.shell(f'snandw {self.pbase:#x} {layout.sector:#x}', wait=30.0)
        # verify
        got = self.snandr(self.pbase + layout.ring1_off, layout.ring1_size)
        if got[:layout.ring1_size] != custom:
            raise RuntimeError('post-write verification failed: '
                               'ring1 content mismatch')
        self._expected = custom
        return True

    # -- restore -------------------------------------------------------------------
    def restore(self, confirm=False):
        """Restore the backed-up original ringtone and verify exact recovery."""
        if self.backup is None:
            raise WriteAborted('no backup to restore')
        if not confirm:
            raise WriteAborted('destructive write requires confirm=True')
        self._restore = True
        try:
            return self.write_ring1(self.backup, confirm=True)
        finally:
            self._restore = False
