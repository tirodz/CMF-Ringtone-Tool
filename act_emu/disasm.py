"""Disassemble a function from extracted.bin by runtime VA."""
import struct, sys
from capstone import *

ex = open('/workspace/project/cmf-watch-firmware/bins/extracted.bin', 'rb').read()
BASE = 0x10000000; APP = 0x800
def va_to_off(va): return va - BASE + APP
md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)

def dis(va, size=0x200, start_rel=0):
    off = va_to_off(va & ~1)
    code = ex[off + start_rel : off + start_rel + size]
    for ins in md.disasm(code, (va & ~1) + start_rel):
        extra = ''
        if ins.mnemonic.startswith('ldr') and '[pc' in ins.op_str:
            try:
                imm = int(ins.op_str.split('#')[-1].rstrip(']').replace('0x',''), 16)
                lit = ((ins.address + 4) & ~3) + imm
                lo = va_to_off(lit)
                if 0 <= lo <= len(ex) - 4:
                    v, = struct.unpack_from('<I', ex, lo)
                    s = b''; q = v - BASE + APP
                    while q < len(ex) and 0x20 <= ex[q] < 0x7f:
                        s += ex[q:q+1]; q += 1
                    extra = f' ; = {hex(v)} {s[:40]!r}' if len(s) >= 3 else f' ; = {hex(v)}'
            except Exception:
                pass
        elif ins.mnemonic in ('movw', 'movt'):
            extra = ''
        print(f'{ins.address:08x}: {ins.mnemonic:10s} {ins.op_str:32s}{extra}')
        if ins.mnemonic == 'pop' and 'pc' in ins.op_str:
            pass

if __name__ == '__main__':
    va = int(sys.argv[1], 16)
    size = int(sys.argv[2], 0) if len(sys.argv) > 2 else 0x200
    dis(va, size)
