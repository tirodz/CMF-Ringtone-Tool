"""Scan whole app.bin for u32 literal words pointing at ANY string in the
wewear/epo region (using TRUE string starts), then map back to ldr/movw sites."""
import struct, re
from capstone import *

ex = open('/workspace/project/cmf-watch-firmware/bins/extracted.bin', 'rb').read()
APP = 0x800; BASE = 0x10100000; CODE_END = 0x255a00
def off_to_va(off): return off - APP + BASE

def string_start(off):
    p = off
    while p > 0 and 0x20 <= ex[p-1] < 0x7f:
        p -= 1
    return p

# targets: true starts of strings containing key substrings in 0x200000..0x260000
targets = {}
for m in re.finditer(rb'wewear|epo|file type|file crc|cmd_id|h2d|d2h|ftp|unkown cmd', ex[0x200000:0x255a00]):
    abs_off = 0x200000 + m.start()
    s0 = string_start(abs_off)
    targets.setdefault(s0, None)

print(f"{len(targets)} true string starts")

# 1) whole-image scan for u32 literals pointing at those starts (literal pools & tables)
lit_hits = []
for off in range(APP, CODE_END - 4, 4):
    v, = struct.unpack_from('<I', ex, off)
    if off_to_va(off) == v: continue
    s0 = v - BASE + APP
    if s0 in targets:
        lit_hits.append((off, v))
print(f"{len(lit_hits)} literal/pointer hits")
for o, v in lit_hits[:100]:
    # decode the string
    p = v - BASE + APP
    s = b''
    q = p
    while q < len(ex) and 0x20 <= ex[q] < 0x7f:
        s += ex[q:q+1]; q += 1
    print(hex(o), '->', hex(v), repr(s[:52]))
