#!/usr/bin/env python3
"""Emulate the Actions ACT (v2) CPU decoder from a1_act_d.a under Unicorn.

Feeds a candidate .act stream through act_decoder_open + act_frame_decode,
logging every stream read (offset/len) and every decoded PCM frame.
"""
import json, struct, sys
from unicorn import *
from unicorn.arm_const import *

IMG = json.load(open('/tmp/actdec/linked.json'))
BASE = IMG['base']
CODE = bytes.fromhex(IMG['image'])
SYM = IMG['symbols']

RAM_BASE = 0x20000000
RAM_SIZE = 0x100000
STACK_TOP = RAM_BASE + RAM_SIZE - 0x100
CHIPREG_BASE = 0x0  # acth_F5_21 reads [0x140],[0x148]

STATE_SIZE = 0x2000

class Stream:
    def __init__(self, data):
        self.data = data
        self.pos = 0
        self.log = []

def run(candidate: bytes, max_frames=4000, verbose=False):
    mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
    mu.reg_write(UC_ARM_REG_FPEXC, 0x40000000)
    mu.mem_map(BASE, ((len(CODE)+0xfff)//0x1000)*0x1000)
    mu.mem_write(BASE, CODE)
    mu.mem_map(RAM_BASE, RAM_SIZE)
    mu.mem_map(CHIPREG_BASE, 0x1000)
    # chip id values for acth_F5_21: ([0x148]&0xffff0000)|([0x140]&0xffff) == 0x69745128
    mu.mem_write(0x140, struct.pack('<I', 0x5128))
    mu.mem_write(0x148, struct.pack('<I', 0x69740000))

    state = RAM_BASE                 # 0x1928-byte decoder state
    openargs = RAM_BASE + 0x4000     # struct with [0x40]=stream ctx, [0x4c]=state
    streamctx = RAM_BASE + 0x4100    # [0]=read fn ptr
    readbuf = RAM_BASE + 0x4200
    outframe = RAM_BASE + 0x4400     # decode result struct
    decparams = RAM_BASE + 0x4500    # [0x10]=outframe

    stream = Stream(candidate)

    STUB_RET = RAM_BASE + 0x6000     # bx lr stub
    mu.mem_write(STUB_RET, b'\x70\x47')  # bx lr (thumb)

    def do_read(dst, cnt, size, ctx):
        n = cnt * size
        chunk = stream.data[stream.pos:stream.pos+n]
        mu.mem_write(dst, chunk)
        stream.log.append((stream.pos, len(chunk)))
        stream.pos += len(chunk)
        return len(chunk)

    stub_addr = {}
    for name, a in IMG.get('stubs', {}).items():
        stub_addr[a & ~1] = name

    def hook_code(mu, address, size, user_data):
        a = address & ~1
        if a in stub_addr:
            name = stub_addr[a]
            sp = mu.reg_read(UC_ARM_REG_SP)
            ret = mu.reg_read(UC_ARM_REG_LR)
            if name in ('memset', '__aeabi_memset4', '__aeabi_memclr4', '__aeabi_memclr'):
                dst = mu.reg_read(UC_ARM_REG_R0)
                n = mu.reg_read(UC_ARM_REG_R2)
                mu.mem_write(dst, b'\x00'*n)
                mu.reg_write(UC_ARM_REG_R0, dst)
            elif name == 'memcpy':
                dst = mu.reg_read(UC_ARM_REG_R0)
                src = mu.reg_read(UC_ARM_REG_R1)
                n = mu.reg_read(UC_ARM_REG_R2)
                mu.mem_write(dst, mu.mem_read(src, n))
                mu.reg_write(UC_ARM_REG_R0, dst)
            elif name == 'strcpy':
                dst = mu.reg_read(UC_ARM_REG_R0)
                src = mu.reg_read(UC_ARM_REG_R1)
                s = b''
                while True:
                    c = mu.mem_read(src, 1)
                    src += 1
                    s += c
                    if c == b'\x00':
                        break
                mu.mem_write(dst, s)
                mu.reg_write(UC_ARM_REG_R0, dst)
            else:
                mu.reg_write(UC_ARM_REG_R0, 0)
            mu.reg_write(UC_ARM_REG_PC, ret)
            return
        if a == (READFN & ~1):
            r0 = mu.reg_read(UC_ARM_REG_R0)
            r1 = mu.reg_read(UC_ARM_REG_R1)
            r2 = mu.reg_read(UC_ARM_REG_R2)
            n = do_read(r0, r1, r2, 0)
            mu.reg_write(UC_ARM_REG_R0, n)
            mu.reg_write(UC_ARM_REG_PC, mu.reg_read(UC_ARM_REG_LR))
            return

    READFN = 0x9000000
    mu.mem_map(READFN & ~0xfff, 0x1000)
    mu.mem_write(READFN & ~1, b'\x70\x47')
    mu.mem_write(streamctx, struct.pack('<I', READFN | 1))
    mu.mem_write(openargs + 0x40, struct.pack('<I', streamctx))
    mu.mem_write(openargs + 0x4c, struct.pack('<I', state))
    mu.mem_write(decparams + 0x10, struct.pack('<I', outframe))

    mu.hook_add(UC_HOOK_CODE, hook_code)

    def call(addr, r0, r1=0, r2=0, r3=0, timeout_insns=2000000):
        mu.reg_write(UC_ARM_REG_SP, STACK_TOP)
        mu.reg_write(UC_ARM_REG_R0, r0)
        mu.reg_write(UC_ARM_REG_R1, r1)
        mu.reg_write(UC_ARM_REG_R2, r2)
        mu.reg_write(UC_ARM_REG_R3, r3)
        end_marker = STACK_TOP - 0x800  # unmapped-ish sentinel in mapped RAM is fine
        RET_SENT = RAM_BASE + 0x7000
        mu.mem_write(RET_SENT, b'\x00\xbf')  # nop; we stop by address match
        mu.reg_write(UC_ARM_REG_LR, RET_SENT | 1)
        mu.emu_start(addr | 1, RET_SENT, count=timeout_insns)
        return mu.reg_read(UC_ARM_REG_R0)

    st = call(SYM['act_decoder_open'], openargs)
    if not st:
        return dict(ok=False, stage='open', reads=list(stream.log))
    out = dict(ok=True, stage='open', reads=list(stream.log))
    # dump state fields
    sb = mu.mem_read(state, 0x20)
    out['state_hdr'] = sb.hex()

    frames = []
    for i in range(max_frames):
        mu.mem_write(outframe, b'\x00' * 0x40)
        before = stream.pos
        try:
            r = call(SYM['act_frame_decode'], st, decparams)
        except UcError as e:
            out['err'] = str(e)
            break
        consumed = stream.pos - before
        f = struct.unpack('<8I', mu.mem_read(outframe, 0x20))
        pcm_ptr, a1, a2, nch, nsmpl = f[0], f[1], f[2], f[3], f[4]
        frames.append(dict(i=i, ret=r, consumed=consumed, pcm=pcm_ptr,
                           fields=f, reads=stream.log[-2:] if consumed else []))
        if verbose and i < 10:
            print('frame', frames[-1])
        if consumed == 0:
            break
        if stream.pos >= len(candidate):
            break
    out['frames'] = frames
    out['total_read'] = stream.pos
    out['candidate_len'] = len(candidate)
    return out

if __name__ == '__main__':
    path = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else 'raw'
    data = open(path, 'rb').read()
    if mode == 'xor572a':
        k = bytes([0x57, 0x2a])
        data = bytes(b ^ k[i % 2] for i, b in enumerate(data))
    res = run(data, max_frames=50, verbose=True)
    print(json.dumps({k: v for k, v in res.items() if k != 'frames'}, indent=1))
    if 'frames' in res:
        for f in res['frames'][:12]:
            print(f)
