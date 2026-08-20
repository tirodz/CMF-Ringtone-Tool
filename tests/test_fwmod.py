"""Offline SDFS/AOTA pipeline tests on synthetic images (no real firmware).

Covers the full chain: validate -> extract -> replace ring1.act -> repair SDFS
sums -> rebuild -> repair AOTA CRCs -> re-extract -> verify -> decode.
"""
import struct

import pytest

import helpers  # noqa: F401
import act_decode
import fwmod

HDR_SIZE = 0x400
DIR_OFF = 0x200


def build_sdfs(files):
    """files: list of (name, bytes) -> sdfs partition image (uncompressed)."""
    table_len = (len(files) + 1) * 0x20
    pos = table_len
    ent = bytearray()
    data = bytearray()
    for name, blob in files:
        data += blob
    for name, blob in files:
        e = bytearray(0x20)
        e[0:len(name)] = name.encode()
        struct.pack_into('<II', e, 12, pos, len(blob))
        struct.pack_into('<I', e, 0x1c, fwmod.sum32_words(blob))
        ent += e
        pos += len(blob)
    part_size = (pos + 0x3f) & ~0x3f
    data += b'\x00' * (part_size - table_len - len(data))
    hdr = bytearray(0x20)
    hdr[0:8] = b'sdfs.bin'
    struct.pack_into('<II', hdr, 12, len(files), part_size)
    struct.pack_into('<II', hdr, 24, fwmod.sum32_words(bytes(ent)),
                     fwmod.sum32_words(bytes(data)))
    return bytes(hdr + ent + data)


def build_aota(parts):
    """parts: list of (name, bytes) -> AOTA image with valid CRCs."""
    payload = bytearray()
    ent = bytearray()
    for name, blob in parts:
        pad = (-len(blob)) % 0x200
        e = bytearray(0x20)
        e[0:len(name)] = name.encode()
        struct.pack_into('<II', e, 0x10, HDR_SIZE + len(payload), len(blob))
        struct.pack_into('<I', e, 0x1c, fwmod.crc32(blob))
        payload += blob + bytes(pad)
        ent += e
    header = bytearray(HDR_SIZE)
    header[0:4] = b'AOTA'
    struct.pack_into('<I', header, 0xc, len(parts))
    struct.pack_into('<I', header, 0x14, HDR_SIZE + len(payload))
    struct.pack_into('<I', header, 0x18, fwmod.crc32(payload))
    header[DIR_OFF:DIR_OFF + len(ent)] = ent
    struct.pack_into('<I', header, 0x4, fwmod.crc32(header[8:HDR_SIZE]))
    return bytes(header) + bytes(payload)


def stock_act():
    """Deterministic valid XOR-form ringtone of arbitrary length."""
    import act_encode
    import math
    enc = act_encode.Encoder()
    sig = [int(2000 * math.sin(2 * math.pi * 500 * i / 16000)) for i in range(4800)]
    raw = bytearray(b'\xe1\xd3')
    for i in range(len(sig) // 160):
        fr, _ = enc.encode_frame(sig[i * 160:(i + 1) * 160])
        raw += fr
    return act_decode.obfuscate(bytes(raw))


@pytest.fixture
def aota_img():
    ring1 = stock_act()
    sdfs = build_sdfs([('ring2.act', b'\xb6\xf9' + b'\x33' * 3000),
                       ('ring1.act', ring1),
                       ('sdfs.txt', b'1234567890')])
    img = build_aota([('app_k.bin', b'\xde\xad' * 2000),
                      ('sdfs_k.bin', fwmod.lzma_pack(sdfs))])
    return img, ring1


def test_validate_source(aota_img):
    img, _ = aota_img
    a = fwmod.Aota(img)
    assert a.verify()          # all CRCs valid
    sdfs = fwmod.Sdfs(fwmod.lzma_unpack(a.get('sdfs_k.bin')))
    names = [f['name'] for f in sdfs.files]
    assert 'ring1.act' in names


def test_replace_rebuild_verify_decode(aota_img, tmp_path):
    img, ring1 = aota_img
    a = fwmod.Aota(img)
    custom = stock_act()[:len(ring1)]
    out = fwmod.rebuild(a, [], {'ring1.act': custom})
    na = fwmod.Aota(out)
    assert na.verify()         # AOTA CRCs repaired and valid
    sdfs = fwmod.Sdfs(fwmod.lzma_unpack(na.get('sdfs_k.bin')))
    # re-extract after rebuild: ring1 must be the custom file
    assert sdfs.get('ring1.act') == custom
    # untouched files byte-for-byte identical
    assert sdfs.get('ring2.act') == b'\xb6\xf9' + b'\x33' * 3000
    assert sdfs.get('sdfs.txt') == b'1234567890'
    assert na.get('app_k.bin') == b'\xde\xad' * 2000
    # resulting ringtone decodes
    res = act_decode.decode(act_decode.deobfuscate(sdfs.get('ring1.act')))
    assert res is not None and res[1] > 0


def test_replace_unknown_file_refused(aota_img):
    img, _ = aota_img
    a = fwmod.Aota(img)
    # replacing a non-existent sdfs entry must fail, not silently pass
    try:
        out = fwmod.rebuild(a, [], {'nope.act': b'xx'})
        sdfs = fwmod.Sdfs(fwmod.lzma_unpack(fwmod.Aota(out).get('sdfs_k.bin')))
        assert sdfs.get('nope.act') is None  # silently dropped -> must not happen
        pytest.fail('unknown sdfs entry was silently dropped')
    except AssertionError:
        raise
    except Exception:
        pass  # acceptable: loud failure


def test_corrupt_source_rejected():
    with pytest.raises(AssertionError):
        fwmod.Aota(b'XXXX' + b'\x00' * 2000)
