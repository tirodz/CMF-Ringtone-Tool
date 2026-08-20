#!/usr/bin/env python3
"""cmf_flash_plan.py - offline planner for BLE-only ring1.act replacement.

Computes, from original.bin + a custom ring1.act (same 15556-byte size):
  1. the 0x4000-byte staging window for `snandw` (ring2 tail + custom ring1 +
     poweroff head - all other bytes identical to stock),
  2. the sdfs table sector with the 3 patched checksum words (u32 word sums),
  3. the mww command list to fill the staging buffer,
  4. the exact snandw offsets (as functions of PBASE, discovered via recon).

Run: python3 cmf_flash_plan.py original.bin custom_ring1.act
"""
import os, struct, sys, binascii

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fwmod
import fw_registry

# All layout constants come from the firmware compatibility registry
# (fw_registry.py).  This planner refuses firmware versions it does not know.
LAYOUT = fw_registry.LAYOUT_1_0_0_73
RING1_OFF = LAYOUT.ring1_off
RING1_SIZE = LAYOUT.ring1_size
RING1_END = RING1_OFF + RING1_SIZE
WIN_START = RING1_OFF & ~(LAYOUT.sector - 1)
WIN_END = (RING1_END + LAYOUT.sector - 1) & ~(LAYOUT.sector - 1)
RING1_TBL_ENTRY = LAYOUT.sdfs_entry_index
TBL_F4_OFF = LAYOUT.tbl_f4_off
TBL_F5_OFF = LAYOUT.tbl_f5_off
ENTRY_F5_OFF = (RING1_TBL_ENTRY + 1) * 0x20 + 0x1c


def sum32(b):
    n = len(b) - (len(b) % 4)
    return sum(struct.unpack('<%dI' % (n // 4), b[:n])) & 0xffffffff


def main():
    outdir = os.environ.get('FLASH_PLAN_OUT', 'flash_plan')
    os.makedirs(outdir, exist_ok=True)
    orig_path, custom_path = sys.argv[1], sys.argv[2]
    aota = fwmod.Aota(open(orig_path, 'rb').read())
    part = fwmod.lzma_unpack(aota.get('sdfs_k.bin'))
    custom = open(custom_path, 'rb').read()
    assert len(custom) == RING1_SIZE, f"custom ring1 must be {RING1_SIZE} bytes"

    old_ring1 = part[RING1_OFF:RING1_END]
    delta = (sum32(custom) - sum32(old_ring1)) & 0xffffffff
    print(f"old ring1 sum32: {sum32(old_ring1):#010x}")
    print(f"new ring1 sum32: {sum32(custom):#010x}  delta: {delta:#010x}")

    # window for snandw: [WIN_START..WIN_END)
    win = bytearray(part[WIN_START:WIN_END])
    rel = RING1_OFF - WIN_START
    win[rel:rel+RING1_SIZE] = custom
    print(f"staging window: {len(win)} bytes ({len(win)//512} sectors)")

    # table sector patch
    tbl = bytearray(part[0:512])
    f5_entry = (struct.unpack('<I', tbl[ENTRY_F5_OFF:ENTRY_F5_OFF+4])[0] + delta) & 0xffffffff
    f4 = (struct.unpack('<I', tbl[TBL_F4_OFF:TBL_F4_OFF+4])[0] + delta) & 0xffffffff
    f5 = (struct.unpack('<I', tbl[TBL_F5_OFF:TBL_F5_OFF+4])[0] + delta) & 0xffffffff
    struct.pack_into('<I', tbl, ENTRY_F5_OFF, f5_entry)
    struct.pack_into('<I', tbl, TBL_F4_OFF, f4)
    struct.pack_into('<I', tbl, TBL_F5_OFF, f5)
    print(f"patched: entry.f5={f5_entry:#010x} entry0.f4={f4:#010x} entry0.f5={f5:#010x}")

    # self-check: rebuild table sums from scratch
    entry_bytes = part[0x20:0x240]
    new_entries = bytearray(entry_bytes)
    struct.pack_into('<I', new_entries, ENTRY_F5_OFF - 0x20, f5_entry)
    assert sum32(new_entries) == f4
    new_data = bytearray(part[0x240:])
    new_data[RING1_OFF - 0x240:RING1_END - 0x240] = custom
    assert sum32(new_data) == f5
    print("checksum self-check: OK")

    open(os.path.join(outdir, 'stage_window.bin'), 'wb').write(win)
    open(os.path.join(outdir, 'stage_table.bin'), 'wb').write(tbl)

    def stage_cmds(content, base=LAYOUT.stage_buffer):
        """mwb for bytes 0..2, mww for the rest (padded to a word boundary)."""
        cmds = []
        for i in range(3):
            cmds.append(f"mwb {base + i:#x} {content[i]:#x}")
        padded = bytearray(content[3:])
        while len(padded) % 4:
            padded.append(0)
        pos = base + 3
        for i in range(0, len(padded), 4):
            w, = struct.unpack('<I', padded[i:i+4])
            cmds.append(f"mww {pos:#x} {w:#x}")
            pos += 4
        return cmds

    cmds = stage_cmds(win)
    with open(os.path.join(outdir, 'mww_stage.txt'), 'w') as f:
        f.write('\n'.join(cmds))
    print(f"mww commands: {len(cmds)}  ({outdir}/mww_stage.txt)")

    tbl_cmds = stage_cmds(tbl)
    with open(os.path.join(outdir, 'mww_table.txt'), 'w') as f:
        f.write('\n'.join(tbl_cmds))
    print(f"table mww commands: {len(tbl_cmds)}  ({outdir}/mww_table.txt)")

    print()
    print("=" * 70)
    print("EXECUTION PLAN (PBASE = sdfs_k FTL offset from recon)")
    print("=" * 70)
    print(f"1. recon: mdw {LAYOUT.boot_info_addr:#x} 0x40 -> boot info -> part table")
    print("2. recon: mdw <part_table> 0x100 -> find sdfs_k entry -> PBASE")
    print(f"3. verify: snandr (PBASE+{RING1_OFF:#x}) 0x200 -> compare with stock ring1 bytes")
    print(f"4. backup: sdfs ring1.act {RING1_SIZE}")
    print(f"5. stage ringtone window: run {outdir}/mww_stage.txt")
    print(f"6. write:  snandw (PBASE+{WIN_START:#x}) {len(win):#x}")
    print(f"7. stage table sector: run {outdir}/mww_table.txt")
    print(f"8. write:  snandw PBASE 0x200")
    print(f"9. verify: sdfs ring1.act {RING1_SIZE} -> compare; make a test call")


if __name__ == '__main__':
    main()
