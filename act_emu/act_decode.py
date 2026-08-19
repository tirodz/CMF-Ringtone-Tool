#!/usr/bin/env python3
"""act_decode.py - Decode Actions Technology ACT ("actii") prompt-tone files to WAV.

Supports both on-flash format (XOR 0x57 0x2a obfuscated, as found in sdfs_k on
CMF Watch Pro 2 / ATS308x devices) and raw v2 format (e1 d3 magic, e.g. Tuya samples).

Usage: act_decode.py input.act [output.wav]

Requires: unicorn (pip install unicorn), linked decoder image produced by armlink.py
from the Actions SDK a1_act_d.a objects (see README).
"""
import os, sys, json, struct, wave
from unicorn import *
from unicorn.arm_const import *

HERE = os.path.dirname(os.path.abspath(__file__))
IMG = json.load(open(os.path.join(HERE, 'linked.json')))
BASE = IMG['base']; CODE = bytes.fromhex(IMG['image']); SYM = IMG['symbols']
RAM_BASE = 0x20000000
RAM_SIZE = 0x200000
STACK_TOP = RAM_BASE + RAM_SIZE - 0x100
XOR_KEY = bytes([0x57, 0x2a])


def deobfuscate(data: bytes) -> bytes:
    if data[:2] == b'\xe1\xd3' or data[:2] == b'\xe7\xa8':
        return data
    return bytes(b ^ XOR_KEY[i % 2] for i, b in enumerate(data))


def decode(data: bytes):
    """Decode ACT bitstream. Returns (pcm_s16le_bytes, n_frames) or None on open failure."""
    mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
    mu.reg_write(UC_ARM_REG_FPEXC, 0x40000000)
    mu.mem_map(BASE, ((len(CODE)+0xfff)//0x1000)*0x1000)
    mu.mem_write(BASE, CODE)
    mu.mem_map(RAM_BASE, RAM_SIZE)
    mu.mem_map(0, 0x1000)
    mu.mem_write(0x140, struct.pack('<I', 0x5128))
    mu.mem_write(0x148, struct.pack('<I', 0x69740000))

    stream = dict(data=data, pos=0)
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
        return None
    pcm = bytearray()
    nframes = 0
    while stream['pos'] < len(data):
        mu.mem_write(outframe, b'\x00'*0x40)
        before = stream['pos']
        r = call(SYM['act_frame_decode'], state, decparams)
        consumed = stream['pos'] - before
        if consumed == 0:
            break
        f = struct.unpack('<8I', mu.mem_read(outframe, 0x20))
        pcm_ptr, nsamp = f[0], f[3]
        if pcm_ptr and nsamp:
            pcm += bytes(mu.mem_read(pcm_ptr, nsamp*2))
        nframes += 1
    return bytes(pcm), nframes


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) > 2 else os.path.splitext(src)[0] + '.wav'
    data = open(src, 'rb').read()
    raw = deobfuscate(data)
    res = decode(raw)
    if not res:
        print('decode failed: not an ACT v2/v4 stream')
        sys.exit(1)
    pcm, nframes = res
    with wave.open(dst, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm)
    print(f'{src}: {nframes} frames, {len(pcm)//2} samples '
          f'({len(pcm)/2/16000:.2f}s) -> {dst}')


if __name__ == '__main__':
    main()
