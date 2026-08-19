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
import struct, sys, binascii

sys.path.insert(0, '/workspace/project/act_emu')
import fwmod

RING1_OFF = 0x5c80
RING1_SIZE = 0x3cc4  # 15556
RING1_END = RING1_OFF + RING1_SIZE   # 0x9944
WIN_START = 0x5a00                   # 512-aligned start covering ring1
WIN_END = (RING1_END + 511) & ~511   # 0x9a00
RING1_TBL_ENTRY = 8                  # ring1 is the 8th sdfs entry
TBL_F4_OFF = 0x18
TBL_F5_OFF = 0x1c
ENTRY_F5_OFF = (RING1_TBL_ENTRY + 1) * 0x20 + 0x1c   # 0x13c


def sum32(b):
    n = len(b) - (len(b) % 4)
    return sum(struct.unpack('<%dI' % (n // 4), b[:n])) & 0xffffffff


def main():
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

    open('/tmp/stage_window.bin', 'wb').write(win)
    open('/tmp/stage_table.bin', 'wb').write(tbl)

    def stage_cmds(content, base=0x380027bd):
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
    with open('/tmp/mww_stage.txt', 'w') as f:
        f.write('\n'.join(cmds))
    print(f"mww commands: {len(cmds)}  (/tmp/mww_stage.txt)")

    tbl_cmds = stage_cmds(tbl)
    with open('/tmp/mww_table.txt', 'w') as f:
        f.write('\n'.join(tbl_cmds))
    print(f"table mww commands: {len(tbl_cmds)}  (/tmp/mww_table.txt)")

    print()
    print("=" * 70)
    print("EXECUTION PLAN (PBASE = sdfs_k FTL offset from recon)")
    print("=" * 70)
    print("1. recon: mdw 0x1000000 0x40 -> boot info -> param_save_addr (part table)")
    print("2. recon: mdw <part_table> 0x100 -> find sdfs_k entry -> PBASE")
    print("3. verify: snandr (PBASE+0x5c80) 0x200  -> compare with stock ring1 bytes")
    print("4. backup: sdfs ring1.act 15556")
    print("5. stage ringtone window: run /tmp/mww_stage.txt (4099 cmds)")
    print(f"6. write:  snandw (PBASE+{WIN_START:#x}) {len(win):#x}")
    print("7. stage table sector: run /tmp/mww_table.txt (131 cmds)")
    print(f"8. write:  snandw PBASE 0x200")
    print("9. verify: sdfs ring1.act 15556 -> compare; make a test call")


if __name__ == '__main__':
    main()
