#!/usr/bin/env python3
"""Full ACT v2 decode loop under Unicorn; tests candidate streams."""
import struct, json, sys
from unicorn import *
from unicorn.arm_const import *

IMG = json.load(open('/tmp/actdec/linked.json'))
BASE = IMG['base']; CODE = bytes.fromhex(IMG['image']); SYM = IMG['symbols']
RAM_BASE = 0x20000000
RAM_SIZE = 0x200000
STACK_TOP = RAM_BASE + RAM_SIZE - 0x100

def run(candidate: bytes, max_frames=100000, collect_pcm=False):
    mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
    mu.reg_write(UC_ARM_REG_FPEXC, 0x40000000)
    mu.mem_map(BASE, ((len(CODE)+0xfff)//0x1000)*0x1000)
    mu.mem_write(BASE, CODE)
    mu.mem_map(RAM_BASE, RAM_SIZE)
    mu.mem_map(0, 0x1000)
    mu.mem_write(0x140, struct.pack('<I', 0x5128))
    mu.mem_write(0x148, struct.pack('<I', 0x69740000))

    stream = dict(data=candidate, pos=0, log=[])
    state = RAM_BASE
    openargs = RAM_BASE + 0x8000
    streamctx = RAM_BASE + 0x8100
    outframe = RAM_BASE + 0x8400
    decparams = RAM_BASE + 0x8500

    READFN = 0x9000000
    mu.mem_map(READFN & ~0xfff, 0x1000)
    mu.mem_write(READFN & ~1, b'\x70\x47')
    mu.mem_write(streamctx, struct.pack('<I', READFN | 1))
    mu.mem_write(openargs + 0x40, struct.pack('<I', streamctx))
    mu.mem_write(openargs + 0x4c, struct.pack('<I', state))
    mu.mem_write(decparams + 0x10, struct.pack('<I', outframe))

    stub_addr = {a & ~1: n for n, a in IMG['stubs'].items()}

    def hook(mu, a, s, u):
        a2 = a & ~1
        if a2 in stub_addr:
            name = stub_addr[a2]
            if name in ('memset', '__aeabi_memclr4', '__aeabi_memclr', '__aeabi_memset4'):
                dst = mu.reg_read(UC_ARM_REG_R0); n = mu.reg_read(UC_ARM_REG_R2)
                mu.mem_write(dst, b'\x00'*n); mu.reg_write(UC_ARM_REG_R0, dst)
            elif name == 'memcpy':
                dst = mu.reg_read(UC_ARM_REG_R0); src = mu.reg_read(UC_ARM_REG_R1); n = mu.reg_read(UC_ARM_REG_R2)
                mu.mem_write(dst, mu.mem_read(src, n)); mu.reg_write(UC_ARM_REG_R0, dst)
            elif name == 'strcpy':
                dst = mu.reg_read(UC_ARM_REG_R0); src = mu.reg_read(UC_ARM_REG_R1)
                s = b''
                while True:
                    c = mu.mem_read(src, 1); src += 1; s += c
                    if c == b'\x00':
                        break
                mu.mem_write(dst, s); mu.reg_write(UC_ARM_REG_R0, dst)
            else:
                mu.reg_write(UC_ARM_REG_R0, 0)
            mu.reg_write(UC_ARM_REG_PC, mu.reg_read(UC_ARM_REG_LR))
            return
        if a2 == (READFN & ~1):
            r0 = mu.reg_read(UC_ARM_REG_R0); r1 = mu.reg_read(UC_ARM_REG_R1); r2 = mu.reg_read(UC_ARM_REG_R2)
            n = r1*r2
            chunk = stream['data'][stream['pos']:stream['pos']+n]
            mu.mem_write(r0, chunk)
            stream['log'].append((stream['pos'], len(chunk)))
            stream['pos'] += len(chunk)
            mu.reg_write(UC_ARM_REG_R0, len(chunk))
            mu.reg_write(UC_ARM_REG_PC, mu.reg_read(UC_ARM_REG_LR))

    mu.hook_add(UC_HOOK_CODE, hook)

    def call(addr, r0, r1=0, r2=0, r3=0, cnt=5000000):
        mu.reg_write(UC_ARM_REG_SP, STACK_TOP)
        mu.reg_write(UC_ARM_REG_R0, r0); mu.reg_write(UC_ARM_REG_R1, r1)
        mu.reg_write(UC_ARM_REG_R2, r2); mu.reg_write(UC_ARM_REG_R3, r3)
        RET = RAM_BASE + 0x7000
        mu.reg_write(UC_ARM_REG_LR, RET | 1)
        mu.emu_start(addr | 1, RET, count=cnt)
        return mu.reg_read(UC_ARM_REG_R0)

    st = call(SYM['act_decoder_open'], openargs)
    if not st:
        return dict(ok=False, stage='open', reads=list(stream['log']))

    frames = []
    pcm_all = bytearray()
    err = None
    for i in range(max_frames):
        mu.mem_write(outframe, b'\x00'*0x40)
        before = stream['pos']
        try:
            r = call(SYM['act_frame_decode'], state, decparams)
        except UcError as e:
            err = str(e)
            break
        consumed = stream['pos'] - before
        f = struct.unpack('<8I', mu.mem_read(outframe, 0x20))
        pcm_ptr, nsamp = f[0], f[3]
        frames.append(dict(i=i, ret=r, consumed=consumed, pcm_ptr=pcm_ptr, n=nsamp))
        if collect_pcm and pcm_ptr and nsamp:
            pcm_all += bytes(mu.mem_read(pcm_ptr, nsamp*2))
        if consumed == 0:
            break
        if stream['pos'] >= len(candidate):
            break
    return dict(ok=True, nframes=len(frames), total=stream['pos'], clen=len(candidate),
                err=err, frames=frames[:20], reads=stream['log'][:10],
                pcm=bytes(pcm_all))

if __name__ == '__main__':
    path = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else 'raw'
    data = open(path, 'rb').read()
    if mode == 'xor572a':
        k = bytes([0x57, 0x2a])
        data = bytes(b ^ k[i % 2] for i, b in enumerate(data))
    res = run(data, collect_pcm=True)
    print(json.dumps({k: v for k, v in res.items() if k not in ('frames', 'pcm', 'reads')}, indent=1))
    print('first reads:', res.get('reads'))
    for f in res.get('frames', [])[:10]:
        print(f)
    if res.get('pcm'):
        import wave
        with wave.open('/tmp/decoded.wav', 'wb') as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            w.writeframes(res['pcm'])
        print('wrote /tmp/decoded.wav', len(res['pcm']), 'bytes')
