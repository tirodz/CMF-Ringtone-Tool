#!/usr/bin/env python3
"""fwmod.py - Extract, modify, rebuild, and verify a CMF Watch Pro 2 AOTA image.

Pipeline: AOTA -> extract entries -> unpack sdfs_k partition -> per-file replace
-> rebuild sdfs table (sum32 checksums) -> LZMA-chunk recompress (official
'LZMA' + 16-byte header + python lzma XZ per 0x8000 chunk) -> rebuild AOTA
header/FAT/CRCs -> verify round trip.

Usage:
  python3 fwmod.py original.bin out.bin --sdfs-replace ring1.act=custom.act
  [--list-sdfs] [--dry-run]
"""
import argparse, struct, lzma, binascii, sys

HDR_SIZE = 0x400
DIR_OFF = 0x200
ALIGN = 0x200
LZMA_MAGIC = 0x414d5a4c
CHUNK = 0x8000

def crc32(b, c=0):
    return binascii.crc32(b, c) & 0xffffffff


class Aota:
    def __init__(self, data):
        assert data[:4] == b'AOTA'
        self.data = data
        self.nfiles = struct.unpack('<I', data[0xc:0x10])[0]
        self.payload_end = struct.unpack('<I', data[0x14:0x18])[0]
        self.entries = []
        for i in range(self.nfiles):
            e = data[DIR_OFF + i*0x20 : DIR_OFF + (i+1)*0x20]
            name = e[0:12].split(b'\x00')[0].decode()
            off, size = struct.unpack('<II', e[0x10:0x18])
            crc = struct.unpack('<I', e[0x1c:0x20])[0]
            self.entries.append(dict(name=name, off=off, size=size, crc=crc))

    def get(self, name):
        e = next(e for e in self.entries if e['name'] == name)
        return self.data[e['off']:e['off']+e['size']]

    def verify(self):
        hdr_crc = struct.unpack('<I', self.data[4:8])[0]
        assert crc32(self.data[8:HDR_SIZE]) == hdr_crc, 'header crc'
        plc = struct.unpack('<I', self.data[0x18:0x1c])[0]
        assert crc32(self.data[HDR_SIZE:self.payload_end]) == plc, 'payload crc'
        for e in self.entries:
            assert crc32(self.data[e['off']:e['off']+e['size']]) == e['crc'], e['name']
        return True


def lzma_pack(raw):
    """Official __build_lzma_image: per 0x8000 chunk, 16B header + python XZ."""
    out = bytearray()
    for i in range(0, len(raw), CHUNK):
        chunk = raw[i:i+CHUNK]
        xz = lzma.compress(chunk)
        out += struct.pack('<IIII', LZMA_MAGIC, 16, len(xz), len(chunk)) + xz
    return bytes(out)


def lzma_unpack(blob):
    out = bytearray()
    while blob:
        magic, hdr_size, xz_len, raw_len = struct.unpack('<IIII', blob[:16])
        assert magic == LZMA_MAGIC
        xz = blob[hdr_size:hdr_size+xz_len]
        out += lzma.decompress(xz)
        blob = blob[hdr_size+xz_len:]
    return bytes(out)


def sum32_words(b):
    n = len(b) - (len(b) % 4)
    return sum(struct.unpack('<%dI' % (n//4), b[:n])) & 0xffffffff


class Sdfs:
    """sdfs partition: 32B entries; entry0 = header (count/partition size/sums)."""
    ENTRY_SZ = 0x20

    def __init__(self, raw):
        self.raw = raw
        self.count = struct.unpack('<I', raw[12:16])[0]
        self.part_size = struct.unpack('<I', raw[16:20])[0]
        self.files = []
        for i in range(self.count):
            e = raw[(i+1)*self.ENTRY_SZ:(i+2)*self.ENTRY_SZ]
            if not e or not e[0:1]: break
            name = e[0:12].split(b'\x00')[0].decode()
            off, size = struct.unpack('<II', e[12:20])
            self.files.append(dict(name=name, off=off, size=size,
                                   raw=e, data=raw[off:off+size]))

    def table_offset(self):
        return (len(self.files)+1) * self.ENTRY_SZ

    def build(self, replacements):
        """replacements: {name: bytes}; preserves original entry order."""
        files = [dict(f) for f in self.files]
        off = self.table_offset()
        total = 0
        for f in files:
            if f['name'] in replacements:
                f['data'] = replacements[f['name']]
            f['off'] = off
            f['size'] = len(f['data'])
            off += f['size']
            total = 0x240 + off if False else 0  # placeholder
        # partition layout: table = (count+1)*0x20, then data segment
        table = bytearray()
        data = bytearray()
        data_base = (len(files)+1) * self.ENTRY_SZ
        pos = data_base
        entry_bytes = bytearray()
        for f in files:
            e = bytearray(0x20)
            nm = f['name'].encode()[:12]
            e[0:len(nm)] = nm
            struct.pack_into('<II', e, 12, pos, f['size'])
            struct.pack_into('<I', e, 20, 0)
            struct.pack_into('<I', e, 28, sum32_words(f['data']))
            entry_bytes += e
            data += f['data']
            pos += f['size']
        part_size = (pos + 0x3f) & ~0x3f
        data += b'\x00' * (part_size - pos)
        sum_data = sum32_words(data)
        sum_table = sum32_words(entry_bytes)
        hdr = bytearray(0x20)
        hdr[0:8] = b'sdfs.bin'
        struct.pack_into('<II', hdr, 12, len(files), part_size)
        struct.pack_into('<II', hdr, 24, sum_table, sum_data)
        return bytes(hdr + entry_bytes + data)

    def get(self, name):
        f = next((f for f in self.files if f['name'] == name), None)
        return None if f is None else f['data']


def rebuild(aota, replacements, sdfs_replacements):
    """Rebuild the image with per-file replacements inside sdfs_k.

    Validates the source image first and refuses unknown sdfs entry names -
    unknown layouts are never silently modified.
    """
    aota.verify()
    new_parts = {}
    if sdfs_replacements:
        sdfs = Sdfs(lzma_unpack(aota.get('sdfs_k.bin')))
        known = {f['name'] for f in sdfs.files}
        unknown = set(sdfs_replacements) - known
        if unknown:
            raise KeyError(f'sdfs entries not present in source: {sorted(unknown)}')
        rebuilt = sdfs.build(sdfs_replacements)
        new_parts['sdfs_k.bin'] = lzma_pack(rebuilt)
    out = bytearray(aota.data[:DIR_OFF] + aota.data[DIR_OFF + aota.nfiles*0x20:HDR_SIZE])
    payload = bytearray()
    dir_bytes = bytearray()
    for e in aota.entries:
        content = new_parts.get(e['name'], aota.get(e['name']))
        pad = (-len(content)) % ALIGN
        en = bytearray(0x20)
        nm = e['name'].encode()[:12]
        en[0:len(nm)] = nm
        struct.pack_into('<II', en, 0x10, HDR_SIZE + len(payload), len(content))
        struct.pack_into('<I', en, 0x1c, crc32(content))
        payload += content + bytes(pad)
        dir_bytes += en
    header = bytearray(out[:DIR_OFF])
    header[DIR_OFF:DIR_OFF+len(dir_bytes)] = b'\x00' * (HDR_SIZE - DIR_OFF)
    # insert dir at 0x200
    full_header = bytearray(aota.data[:DIR_OFF])
    full_header += dir_bytes
    full_header += b'\x00' * (HDR_SIZE - DIR_OFF - len(dir_bytes))
    struct.pack_into('<I', full_header, 0x14, HDR_SIZE + len(payload))
    struct.pack_into('<I', full_header, 0x18, crc32(payload))
    struct.pack_into('<I', full_header, 0x4, crc32(full_header[8:HDR_SIZE]))
    return bytes(full_header) + bytes(payload)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('input')
    ap.add_argument('--out')
    ap.add_argument('--replace', action='append', default=[],
                    help='sdfs file replacement: name=path')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--dump', choices=['sdfs'])
    args = ap.parse_args()
    aota = Aota(open(args.input, 'rb').read())
    if args.list:
        aota.verify()
        print('AOTA entries:')
        for e in aota.entries:
            print(f"  {e['name']:12s} {e['size']:#x}")
        sdfs = Sdfs(lzma_unpack(aota.get('sdfs_k.bin')))
        print('sdfs files:')
        for f in sdfs.files:
            print(f"  {f['name']:12s} {len(f['data']):#x}")
        return
    replacements = {}
    for spec in args.replace:
        name, path = spec.split('=', 1)
        replacements[name] = open(path, 'rb').read()
    out = rebuild(aota, [], replacements)
    # verify round trip
    naota = Aota(out)
    naota.verify()
    if replacements:
        sdfs = Sdfs(lzma_unpack(naota.get('sdfs_k.bin')))
        for name, data in replacements.items():
            got = sdfs.get(name)
            assert got == data, f'{name} round-trip mismatch'
            print(f'  verified {name}: {len(data)} bytes OK')
    if args.out:
        open(args.out, 'wb').write(out)
        print(f'wrote {args.out} ({len(out)} bytes), all CRCs valid')
    else:
        print('rebuild+verify OK (dry run)')


if __name__ == '__main__':
    main()
