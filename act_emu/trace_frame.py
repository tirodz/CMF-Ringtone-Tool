import os
import struct, json, sys
from unicorn import *
from unicorn.arm_const import *
IMG = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'linked.json')))
BASE = IMG['base']; CODE = bytes.fromhex(IMG['image']); SYM = IMG['symbols']
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from emu_act import Stream, RAM_BASE, RAM_SIZE, STACK_TOP

k = bytes([0x57, 0x2a])
d0 = open(os.environ.get('CMF_RING1', 'ring1.act'), 'rb').read()
data = bytes(b ^ k[i%2] for i, b in enumerate(d0[:400]))  # first ~20 frames

mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
mu.reg_write(UC_ARM_REG_FPEXC, 0x40000000)
mu.mem_map(BASE, ((len(CODE)+0xfff)//0x1000)*0x1000)
mu.mem_write(BASE, CODE)
mu.mem_map(RAM_BASE, RAM_SIZE)
mu.mem_map(0, 0x4000)
mu.mem_write(0x140, struct.pack('<I', 0x5128))
mu.mem_write(0x148, struct.pack('<I', 0x69740000))
stream = Stream(data)
state = RAM_BASE; openargs = RAM_BASE+0x4000; streamctx = RAM_BASE+0x4100
outframe = RAM_BASE+0x4400; decparams = RAM_BASE+0x4500
READFN = 0x9000000
mu.mem_map(READFN & ~0xfff, 0x1000)
mu.mem_write(READFN & ~1, b'\x70\x47')
mu.mem_write(streamctx, struct.pack('<I', READFN|1))
mu.mem_write(openargs+0x40, struct.pack('<I', streamctx))
mu.mem_write(openargs+0x4c, struct.pack('<I', state))
mu.mem_write(decparams+0x10, struct.pack('<I', outframe))
stub_addr = {a & ~1: n for n, a in IMG['stubs'].items()}
DVD = ['DVD133','DVD163','DVD165','DVD206','acth_F1_F1','acth_F5_21','DVD134','DVD164','DVD166']
watch = {SYM.get(n, 0) & ~1: n for n in DVD if SYM.get(n)}
log = []
def hook(mu, a, s, u):
    a2 = a & ~1
    if a2 in stub_addr:
        name = stub_addr[a2]
        if name in ('memset','__aeabi_memclr4','__aeabi_memclr','__aeabi_memset4'):
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
                if c == b'\x00': break
            mu.mem_write(dst, s); mu.reg_write(UC_ARM_REG_R0, dst)
        else:
            mu.reg_write(UC_ARM_REG_R0, 0)
        mu.reg_write(UC_ARM_REG_PC, mu.reg_read(UC_ARM_REG_LR))
        return
    if a2 == (READFN & ~1):
        r0 = mu.reg_read(UC_ARM_REG_R0); r1 = mu.reg_read(UC_ARM_REG_R1); r2 = mu.reg_read(UC_ARM_REG_R2)
        n = r1*r2
        chunk = stream.data[stream.pos:stream.pos+n]
        mu.mem_write(r0, chunk); stream.log.append((stream.pos, len(chunk))); stream.pos += len(chunk)
        mu.reg_write(UC_ARM_REG_R0, len(chunk))
        mu.reg_write(UC_ARM_REG_PC, mu.reg_read(UC_ARM_REG_LR))
        return
    if a2 in watch and (not log or log[-1][0] != a2):
        log.append((a2, mu.reg_read(UC_ARM_REG_R0), mu.reg_read(UC_ARM_REG_R1),
                    mu.reg_read(UC_ARM_REG_R2), mu.reg_read(UC_ARM_REG_R3)))
mu.hook_add(UC_HOOK_CODE, hook)
def call(addr, r0, r1=0, r2=0, r3=0, cnt=2000000):
    mu.reg_write(UC_ARM_REG_SP, STACK_TOP)
    mu.reg_write(UC_ARM_REG_R0, r0); mu.reg_write(UC_ARM_REG_R1, r1)
    mu.reg_write(UC_ARM_REG_R2, r2); mu.reg_write(UC_ARM_REG_R3, r3)
    RET = RAM_BASE + 0x7000
    mu.reg_write(UC_ARM_REG_LR, RET | 1)
    mu.emu_start(addr | 1, RET, count=cnt)
    return mu.reg_read(UC_ARM_REG_R0)
st = call(SYM['act_decoder_open'], openargs)
r = call(SYM['act_frame_decode'], state, decparams)
print('decode ret:', r, 'stream:', stream.log)
for a, *args in log:
    print(watch[a], [hex(x) for x in args])
