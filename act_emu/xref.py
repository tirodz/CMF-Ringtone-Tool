"""Scan app.bin for movw/movt pairs referencing strings of interest + pointer tables."""
import struct, re, sys
from capstone import *

import os
ex = open(os.environ.get('CMF_EXTRACTED', 'extracted.bin'), 'rb').read()
APP = 0x800
BASE = 0x10100000
CODE_END = 0x255a00
md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)

def va_to_off(va):
    return va - BASE + APP

def off_to_va(off):
    return off - APP + BASE

# strings of interest
wanted = {}
targets = [b'wewear_ftp_read', b'wewear_h2d_file_start', b'wewear_h2d_file_end_cfm',
           b'wewear_d2h_file_end', b'unknow file type %d', b'epo_rec.bin', b'epo.bin',
           b'wewear_new_h2d_wf_start', b'wewear_yun_h2d_wf_start', b'wewear_new_h2d_wf_end_cfm',
           b'unkown cmd_id:0x%04x', b'epo_file_request', b'audio cmd']
for nm in targets:
    i = ex.find(nm)
    if i >= 0:
        va = off_to_va(i)
        wanted[(va >> 16) & 0xffff, va & 0xffff] = (va, nm.decode())

# disassemble app region; pair consecutive movw/movt to same reg
last_movw = {}
hits = []
for ins in md.disasm(ex[APP:CODE_END], BASE):
    if ins.mnemonic == 'movw':
        parts = ins.op_str.split(', ')
        try:
            reg = parts[0]
            imm = int(parts[1].replace('#', ''), 16)
        except Exception:
            continue
        last_movw = {('reg', reg): (ins.address, imm)}
        for a, (r, imm) in [(ins.address, (reg, imm))]:
            last_movw[reg] = (ins.address, imm)
    elif ins.mnemonic == 'movt':
        parts = ins.op_str.split(', ')
        try:
            reg = parts[0]
            imm = int(parts[1].replace('#', ''), 16)
        except Exception:
            continue
        lo = last_movw.get(reg)
        if not lo:
            continue
        va = (imm << 16) | lo[1]
        if (va >> 16, va & 0xffff) in wanted:
            hits.append((va, wanted[(va >> 16, va & 0xffff)][1], ins.address))
    if not ins.mnemonic.startswith('movw'):
        # clear regs of prior movw after some instructions? keep simple
        pass

print('movw/movt hits:')
for va, nm, addr in hits:
    print(hex(va), nm, 'used at', hex(addr), '(off', hex(addr - BASE + APP) + ')')
