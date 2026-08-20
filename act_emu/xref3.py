"""Robust xref scan: ldr/ldr.w literal pools + movw/movt pairs for target strings."""
import struct, re, sys
from capstone import *

import os
ex = open(os.environ.get('CMF_EXTRACTED', 'extracted.bin'), 'rb').read()
APP = 0x800; BASE = 0x10100000; CODE_END = 0x255a00
md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)

def off_to_va(off): return off - APP + BASE
def va_to_off(va): return va - BASE + APP

# find ALL printable strings in the "wewear/file" semantic set + build VA map
targets = {}
region = ex[0x200000:0x255a00]
for m in re.finditer(rb'[ -~]{5,}', region):
    t = m.group().decode()
    if any(k in t for k in ('wewear', 'epo', 'file type', 'file crc', 'file size',
                            'unkown cmd', 'cmd_id', 'h2d', 'd2h', 'ftp')):
        va = off_to_va(0x200000 + m.start())
        targets[va] = t[:48]

print(f"{len(targets)} target strings in window")

hits = []
lit_pool = {}
ins_count = 0
for ins in md.disasm(ex[APP:CODE_END], BASE):
    ins_count += 1
    mn = ins.mnemonic
    if mn.startswith('ldr') and '[pc' in ins.op_str:
        m = re.match(r'(r\d+), \[pc, #(?:0x)?([0-9a-f]+)\]', ins.op_str)
        if not m:
            continue
        imm = int(m.group(2), 16)
        lit = ((ins.address + 4) & ~3) + imm
        lo = va_to_off(lit)
        if 0 <= lo <= len(ex) - 4:
            v, = struct.unpack_from('<I', ex, lo)
            if v in targets:
                hits.append((ins.address, v, targets[v], lit))

print(f"{ins_count} instructions scanned, {len(hits)} ldr-literal hits")
for a, v, t, lit in hits[:120]:
    print(hex(a), '->', hex(v), repr(t))

# movw/movt pairing (within 8 instrs)
print("\n--- movw/movt ---")
last_movw = {}
for ins in md.disasm(ex[APP:CODE_END], BASE):
    if ins.mnemonic == 'movw':
        parts = ins.op_str.split(', ')
        try:
            reg = parts[0]; imm = int(parts[1].replace('#', ''), 16)
        except Exception:
            continue
        last_movw[reg] = (ins.address, imm, 0)
    elif ins.mnemonic == 'movt':
        parts = ins.op_str.split(', ')
        try:
            reg = parts[0]; imm = int(parts[1].replace('#', ''), 16)
        except Exception:
            continue
        if reg in last_movw:
            a, lo, _ = last_movw[reg]
            va = (imm << 16) | lo
            if va in targets:
                print(hex(a), '->', hex(va), repr(targets[va]))
            del last_movw[reg]
