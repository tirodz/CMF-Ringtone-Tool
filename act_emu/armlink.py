#!/usr/bin/env python3
"""Minimal static linker for ARM (Thumb) ELF32 relocatable objects.

Concatenates sections from all input .o files into a flat image and applies
relocations (ABS32, PREL31, THM_CALL/JUMP24, CALL, MOVW/MOVT, TARGET1).
Outputs JSON: image base, bytes (hex), symbol map.
"""
import struct, sys, json

SHT_SYMTAB = 2
SHT_REL = 9
SHF_ALLOC = 0x2

R_ARM_ABS32 = 2
R_ARM_THM_CALL = 10
R_ARM_CALL = 28
R_ARM_THM_JUMP24 = 30
R_ARM_TARGET1 = 38
R_ARM_V4BX = 40
R_ARM_PREL31 = 42
R_ARM_THM_MOVW = 43
R_ARM_MOVT = 44
R_ARM_THM_MOVW_ABS = 47
R_ARM_THM_MOVT_ABS = 48

class Obj:
    def __init__(self, path):
        self.path = path
        self.data = open(path, 'rb').read()
        self.parse()

    def parse(self):
        d = self.data
        assert d[:4] == b'\x7fELF'
        (self.shoff,) = struct.unpack('<I', d[0x20:0x24])
        self.shentsize, self.shnum, self.shstrndx = struct.unpack('<HHH', d[0x2e:0x34])
        self.sections = []
        for i in range(self.shnum):
            off = self.shoff + i * self.shentsize
            name, stype, flags, addr, offset, size, link, info, align, entsize = struct.unpack('<IIIIIIIIII', d[off:off+40])
            self.sections.append(dict(idx=i, name=name, type=stype, flags=flags,
                                      addr=addr, offset=offset, size=size, link=link,
                                      info=info, align=max(align, 1), entsize=entsize))
        strsec = self.sections[self.shstrndx]
        strtab = d[strsec['offset']:strsec['offset']+strsec['size']]
        for s in self.sections:
            end = strtab.index(b'\x00', s['name'])
            s['sname'] = strtab[s['name']:end].decode()
        self.symbols = []
        for s in self.sections:
            if s['type'] == SHT_SYMTAB:
                strd = d[self.sections[s['link']]['offset']:][:self.sections[s['link']]['size']]
                for off in range(s['offset'], s['offset']+s['size'], 16):
                    nm, val, size, info, other, shndx = struct.unpack('<IIIBBH', d[off:off+16])
                    name = ''
                    if nm:
                        end = strd.index(b'\x00', nm)
                        name = strd[nm:end].decode()
                    self.symbols.append(dict(name=name, value=val, size=size, info=info, shndx=shndx))

    def section_data(self, s):
        if s['type'] == 8:
            return b''
        return self.data[s['offset']:s['offset']+s['size']]

def link(paths, base=0x10000):
    objs = [Obj(p) for p in paths]
    addr = base
    global_syms = {}
    layout = []
    for o in objs:
        for s in o.sections:
            if not (s['flags'] & SHF_ALLOC):
                continue
            if s['size'] == 0:
                s['load'] = addr
                continue
            addr = (addr + s['align'] - 1) & ~(s['align'] - 1)
            s['load'] = addr
            layout.append((o, s, addr))
            addr += s['size']
    o_idx = {id(o): i for i, o in enumerate(objs)}
    sym_map = {}
    for oi, o in enumerate(objs):
        for si, sym in enumerate(o.symbols):
            shndx = sym['shndx']
            if shndx == 0 or shndx >= 0xff00:
                continue
            sec = o.sections[shndx]
            if 'load' not in sec:
                continue
            a = sec['load'] + sym['value']
            is_func = (sym['info'] & 0xf) == 2
            if is_func:
                a |= 1
            sym_map[(oi, si)] = a
            if (sym['info'] >> 4) >= 1 and sym['name'] and sym['name'] not in global_syms:
                global_syms[sym['name']] = a

    # assign stub addresses for undefined symbols at end of image
    # (emulator hooks these addresses; within Thumb BL range)
    addr = (addr + 3) & ~3
    STUB_BASE = addr
    stubs = {}
    for o in objs:
        for sym in o.symbols:
            if sym['shndx'] == 0 and sym['name'] and sym['name'] not in global_syms and sym['name'] not in stubs:
                stubs[sym['name']] = STUB_BASE + 4 * len(stubs) | 1
    addr = STUB_BASE + 4 * len(stubs) + 4

    img = bytearray(addr - base)
    for o, s, a in layout:
        img[a-base:a-base+len(o.section_data(s))] = o.section_data(s)

    for o in objs:
        oi = objs.index(o)
        for s in o.sections:
            if s['type'] != SHT_REL:
                continue
            tgt = o.sections[s['info']]
            if 'load' not in tgt:
                continue
            tdata_off = tgt['load'] - base
            for off in range(s['offset'], s['offset']+s['size'], 8):
                r_off, r_info = struct.unpack('<II', o.data[off:off+8])
                r_type = r_info & 0xff
                r_sym = r_info >> 8
                sym = o.symbols[r_sym]
                if sym['shndx'] == 0:
                    if sym['name'] in global_syms:
                        Sfull = global_syms[sym['name']]
                    elif sym['name'] in stubs:
                        Sfull = stubs[sym['name']]
                    elif sym['name']:
                        raise KeyError('undef ' + sym['name'])
                    else:
                        raise KeyError('undef sym')
                else:
                    Sfull = sym_map[(oi, r_sym)]
                S = Sfull & ~1
                P = tgt['load'] + r_off
                po = tdata_off + r_off
                if r_type in (R_ARM_ABS32, R_ARM_TARGET1):
                    A = struct.unpack('<I', img[po:po+4])[0]
                    img[po:po+4] = struct.pack('<I', (Sfull + A) & 0xffffffff)
                elif r_type == R_ARM_PREL31:
                    A = struct.unpack('<I', img[po:po+4])[0]
                    if A & 0x80000000:
                        A -= 0x100000000
                    img[po:po+4] = struct.pack('<I', (S + A - P) & 0x7fffffff)
                elif r_type in (R_ARM_THM_CALL, R_ARM_THM_JUMP24):
                    hw1, hw2 = struct.unpack('<HH', img[po:po+4])
                    s_bit = (hw1 >> 10) & 1
                    imm10 = hw1 & 0x3ff
                    j1 = (hw2 >> 13) & 1
                    j2 = (hw2 >> 11) & 1
                    imm11 = hw2 & 0x7ff
                    i1 = (~(j1 ^ s_bit)) & 1
                    i2 = (~(j2 ^ s_bit)) & 1
                    imm = (s_bit << 24) | (i1 << 23) | (i2 << 22) | (imm10 << 12) | (imm11 << 1)
                    if s_bit:
                        imm -= (1 << 25)
                    # GNU as Thumb convention: encoded imm is either placeholder (-4)
                    # or already section-relative; in both cases the correct linked
                    # displacement is S_even - (P + 4).
                    val = (Sfull & ~1) - (P + 4)
                    s2 = 1 if val < 0 else 0
                    v = val >> 1
                    imm11n = v & 0x7ff
                    imm10n = (v >> 11) & 0x3ff
                    i1n = (v >> 21) & 1
                    i2n = (v >> 22) & 1
                    j1n = (~(i1n ^ s2)) & 1
                    j2n = (~(i2n ^ s2)) & 1
                    nhw1 = (hw1 & 0xf800) | (s2 << 10) | imm10n
                    nhw2 = (hw2 & 0xd000) | (j1n << 13) | (j2n << 11) | imm11n
                    img[po:po+4] = struct.pack('<HH', nhw1, nhw2)
                elif r_type == R_ARM_CALL:
                    cur = struct.unpack('<I', img[po:po+4])[0]
                    A = cur & 0x00ffffff
                    if A & 0x800000:
                        A -= 0x1000000
                    offn = (S + (A << 2) - P - 8) >> 2
                    img[po:po+4] = struct.pack('<I', (cur & 0xff000000) | (offn & 0x00ffffff))
                elif r_type == R_ARM_V4BX:
                    pass
                else:
                    print('unhandled reloc type', r_type, 'in', o.path, file=sys.stderr)
    allsyms = dict(global_syms)
    allsyms.update(stubs)
    return base, bytes(img), allsyms, stubs

if __name__ == '__main__':
    paths = sys.argv[1:-1]
    base, img, syms, stubs = link(paths)
    json.dump({'base': base, 'image': img.hex(), 'symbols': syms, 'stubs': stubs}, open(sys.argv[-1], 'w'))
    print('linked %d bytes, %d symbols -> %s' % (len(img), len(syms), sys.argv[-1]))
