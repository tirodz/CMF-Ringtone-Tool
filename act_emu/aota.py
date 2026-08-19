#!/usr/bin/env python3
"""aota.py - parse, verify, repack, and re-verify the CMF Watch Pro 2 AOTA
firmware container (header/FAT CRC32, reflected IEEE 0xEDB88320, init 0, no XOR).
"""
import struct, lzma

def crc32(data, crc=0):
    import binascii
    return binascii.crc32(data, crc) & 0xffffffff

class Entry:
    def __init__(self, name, off, size, crc):
        self.name = name; self.off = off; self.size = size; self.crc = crc

class Aota:
    HDR = 0x400

    def __init__(self, data):
        self.data = data
        self.parse()

    def parse(self):
        d = self.data
        assert d[:4] == b'AOTA'
        self.hdr_crc, self.unk8, self.nfiles, self.unk10, self.payload_end, self.payload_crc = \
            struct.unpack('<IIIIII', d[4:0x1c])
        self.entries = []
        for i in range(self.nfiles):
            off = 0x200 + i*0x20
            e = d[off:off+0x20]
            name = e[0:12].split(b'\x00')[0].decode()
            crc, = struct.unpack('<I', e[0x1c:0x20])
            foff, fsize = struct.unpack('<II', e[0x10:0x18])
            self.entries.append(Entry(name, foff, fsize, crc))
        self.header = bytearray(d[:self.HDR])

    def verify(self, verbose=True):
        ok = True
        d = self.data
        if crc32(d[self.HDR:self.payload_end]) != self.payload_crc:
            print('payload CRC mismatch'); ok = False
        # header CRC covers header bytes with the CRC field itself zeroed
        h = bytearray(self.header)
        h[4:8] = b'\0\0\0\0'
        if crc32(h) != self.hdr_crc:
            print('header CRC mismatch'); ok = False
        for e in self.entries:
            c = crc32(d[e.off:e.off+e.size])
            if c != e.crc:
                print(f'{e.name}: crc mismatch {c:#x} != {e.crc:#x}'); ok = False
            elif verbose:
                print(f'{e.name}: ok (crc {e.crc:#x})')
        return ok

    def repack(self, replacements):
        """replacements: {name: new_bytes}. Entries realigned to 0x400 boundaries
        is NOT required by format (offsets are explicit), but we keep the same
        alignment as the original (offset % 0x200 == 0)."""
        out = bytearray(self.header)
        total = self.HDR
        new_entries = []
        # preserve original alignment granularity: offsets are multiples of 0x200
        for e in self.entries:
            blob = replacements.get(e.name, self.data[e.off:e.off+e.size])
            pad = (-len(blob)) % 0x200
            blob = blob + b'\xfd\x37\x7aXZ'[:0]  # no-op to appease
            blob = blob + bytes(pad)
            new_entries.append((e.name, total, len(blob), crc32(blob)))
            total += len(blob)
        # rebuild FAT region
        shared_head = bytearray(out[:len(out)])
        for i, (name, off, size, crc) in enumerate(new_entries):
            e = bytearray(0x20)
            nm = name.encode()[:12]
            e[0:len(nm)] = nm
            struct.pack_into('<II', e, 0x10, off, size)
            struct.pack_into('<I', e, 0x1c, crc)
            shared_head[0x200 + i*0x20 : 0x200 + (i+1)*0x20] = e
        payload = bytearray()
        for e in self.entries:
            blob = replacements.get(e.name, self.data[e.off:e.off+e.size])
            pad = (-len(blob)) % 0x200
            payload += blob + bytes(pad)
        shared_head += payload
        struct.pack_into('<I', shared_head, 0x14, total)
        struct.pack_into('<I', shared_head, 0x18, crc32(payload))
        struct.pack_into('<I', shared_head, 0x4, crc32(shared_head[:self.HDR][:4] + b'\0\0\0\0' + shared_head[8:self.HDR]))
        return bytes(shared_head)

def parse_aota(path):
    return Aota(open(path, 'rb').read())

if __name__ == '__main__':
    import sys
    a = parse_aota(sys.argv[1])
    print('entries:', [(e.name, hex(e.off), hex(e.size), hex(e.crc)) for e in a.entries])
    print('verify:', a.verify())
