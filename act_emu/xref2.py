"""Find all code references (via parallel literal pools) to the wewear string region."""
import struct, re
from capstone import *

import os
ex = open(os.environ.get('CMF_EXTRACTED', 'extracted.bin'), 'rb').read()
APP = 0x800; BASE = 0x10100000
CODE_END = 0x255a00
md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)

# string window of interest (VA ranges)
WIN = (0x10320000, 0x10326000)

hits = []
for ins in md.disasm(ex[APP:CODE_END], BASE):
    if ins.mnemonic == 'ldr' and '[pc,' in ins.op_str:
        m = re.match(r'r\d+, \[pc, (0x[0-9a-f]+)\]', ins.op_str)
        if not m:  # ldr rX, [pc, #imm]
            m2 = re.match(r'(r\d+), \[pc, #(\d+)\]', ins.op_str)
            if not m2:
                continue
            imm = int(m2.group(2))
            lit = (ins.address + 4) & ~3 + imm
        else:
            lit = (ins.address + 4) & ~3 + int(m.group(1), 16)
        if APP <= (lit - BASE + APP) < CODE_END:
            pass
        voff = lit - BASE + APP
        if 0 <= voff <= len(ex) - 4:
            v, = struct.unpack_from('<I', ex, voff)
            if WIN[0] <= v < WIN[1]:
                hits.append((ins.address, lit, v, voff))

print('code refs to 0x1032xxxx window:', len(hits))
for a, lit, v, voff in hits[:80]:
    # show the target string
    s = b''
    p = v - BASE + APP
    while p < len(ex) and 0x20 <= ex[p] < 0x7f:
        s += ex[p:p+1]; p += 1
    print(hex(a), '->', hex(v), repr(s[:46]))
