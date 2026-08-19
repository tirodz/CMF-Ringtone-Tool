"""oracle.py - persistent original-decoder oracle with state save/restore."""
import struct, json
from unicorn import *
from unicorn.arm_const import *

IMG = json.load(open('/workspace/project/act_emu/linked.json'))
BASE = IMG['base']; CODE = bytes.fromhex(IMG['image']); SYM = IMG['symbols']
RAM_BASE = 0x20000000; RAM_SIZE = 0x200000; STACK_TOP = RAM_BASE + RAM_SIZE - 0x100


class OracleDecoder:
    """One persistent emulated decoder instance; decodes sequentially (state evolves)."""

    def __init__(self):
        mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        mu.reg_write(UC_ARM_REG_FPEXC, 0x40000000)
        mu.mem_map(BASE, ((len(CODE)+0xfff)//0x1000)*0x1000)
        mu.mem_write(BASE, CODE)
        mu.mem_map(RAM_BASE, RAM_SIZE)
        mu.mem_map(0, 0x1000)
        mu.mem_write(0x140, struct.pack('<I', 0x5128))
        mu.mem_write(0x148, struct.pack('<I', 0x69740000))
        self.mu = mu
        self.stream = dict(data=b'', pos=0)
        self.state = RAM_BASE
        self.openargs = RAM_BASE + 0x8000
        self.streamctx = RAM_BASE + 0x8100
        self.outframe = RAM_BASE + 0x8400
        self.decparams = RAM_BASE + 0x8500
        READFN = 0x9000000
        mu.mem_map(READFN & ~0xfff, 0x1000)
        mu.mem_write(READFN & ~1, b'\x70\x47')
        mu.mem_write(self.streamctx, struct.pack('<I', READFN | 1))
        mu.mem_write(self.openargs + 0x40, struct.pack('<I', self.streamctx))
        mu.mem_write(self.openargs + 0x4c, struct.pack('<I', self.state))
        mu.mem_write(self.decparams + 0x10, struct.pack('<I', self.outframe))
        stub_addr = {a & ~1: n for n, a in IMG['stubs'].items()}
        stream = self.stream

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
        self.open()

    def call(self, addr, r0, r1=0, r2=0, r3=0, cnt=20000000):
        mu = self.mu
        mu.reg_write(UC_ARM_REG_SP, STACK_TOP)
        mu.reg_write(UC_ARM_REG_R0, r0); mu.reg_write(UC_ARM_REG_R1, r1)
        mu.reg_write(UC_ARM_REG_R2, r2); mu.reg_write(UC_ARM_REG_R3, r3)
        RET = RAM_BASE + 0x7000
        mu.reg_write(UC_ARM_REG_LR, RET | 1)
        mu.emu_start(addr | 1, RET, count=cnt)
        return mu.reg_read(UC_ARM_REG_R0)

    def open(self):
        self.stream['data'] = b'\xe1\xd3'
        self.stream['pos'] = 0
        st = self.call(SYM['act_decoder_open'], self.openargs)
        assert st

    def decode_frame(self, frame20):
        """Decode one 20-byte frame in the current state; returns 160 samples."""
        self.stream['data'] = self.stream['data'][:self.stream['pos']] + frame20
        self.call(SYM['act_frame_decode'], self.state, self.decparams)
        f = struct.unpack('<8I', self.mu.mem_read(self.outframe, 0x20))
        pcm_ptr, nsamp = f[0], f[3]
        pcm = bytes(self.mu.mem_read(pcm_ptr, nsamp * 2)) if pcm_ptr and nsamp else b''
        return struct.unpack('<%dh' % (len(pcm)//2), pcm)

    def snapshot(self):
        return (self.mu.context_save(), bytes(self.mu.mem_read(RAM_BASE, RAM_SIZE)),
                self.stream['pos'])

    def restore(self, snap):
        ctx, ram, pos = snap
        self.mu.context_restore(ctx)
        self.mu.mem_write(RAM_BASE, ram)
        self.stream['pos'] = pos
