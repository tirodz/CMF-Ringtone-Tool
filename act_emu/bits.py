"""ACT v4 bitstream primitives (confirmed via DVD150 dynamic trace).

Frame = 160 bits in 22 fields, read from u16 little-endian words, LSB-first.
"""
import struct

# confirmed by DVD150 trace on ring1 frame 0
FIELD_WIDTHS = [1, 7, 8, 7, 7, 7, 9, 4, 9, 9, 9, 9, 9, 5, 6, 4, 9, 9, 9, 9, 9, 5]
assert sum(FIELD_WIDTHS) == 160


def bits_of(frame20: bytes):
    """Yield (bit_index, value) for the 160-bit frame.

    Confirmed via DVD206+DVD150 traces: the decoder byte-swaps each u16
    (big-endian words), then reads bits LSB-first within each word."""
    words = struct.unpack('>10H', frame20)
    pos = 0
    for w in words:
        for b in range(16):
            yield pos, (w >> b) & 1
            pos += 1


def unpack_fields(frame20: bytes):
    """Split the frame into the 22 fields; returns list of (width, value)."""
    bits = [v for _, v in bits_of(frame20)]
    out = []
    pos = 0
    for w in FIELD_WIDTHS:
        v = 0
        for i in range(w):
            v |= bits[pos + i] << i
        out.append((w, v))
        pos += w
    return out


def pack_fields(values):
    """Inverse of unpack_fields: values (list of 22 ints) -> 20-byte frame."""
    assert len(values) == len(FIELD_WIDTHS)
    bits = [0] * 160
    pos = 0
    for w, v in zip(FIELD_WIDTHS, values):
        assert 0 <= v < (1 << w), (w, v)
        for i in range(w):
            bits[pos + i] = (v >> i) & 1
        pos += w
    words = []
    for i in range(10):
        w = 0
        for b in range(16):
            w |= bits[i * 16 + b] << b
        words.append(w)
    return struct.pack('>10H', *words)


FIELD_NAMES = [
    'f01_sync?', 'f02', 'f03', 'f04', 'f05', 'f06',
    'sf0_pitch?', 'sf0_signs?', 'sf0_c1', 'sf0_c2', 'sf0_c3', 'sf0_c4', 'sf0_c5', 'sf0_gain?',
    'sf1_pitch?', 'sf1_signs?', 'sf1_c1', 'sf1_c2', 'sf1_c3', 'sf1_c4', 'sf1_c5', 'sf1_gain?',
]

if __name__ == '__main__':
    k = bytes([0x57, 0x2a])
    d = open('/workspace/project/cmf-watch-firmware/sdfs_extract/ring1.act', 'rb').read()
    raw = bytes(b ^ k[i % 2] for i, b in enumerate(d))
    for fi in [0, 1, 2, 9, 10, 30, 100, 300, 500, 776]:
        fr = raw[2 + 20 * fi:2 + 20 * (fi + 1)]
        if len(fr) < 20:
            break
        fields = unpack_fields(fr)
        print(f"frame {fi}: " + ' '.join(f"{v:3d}" for _, v in fields))
    # round-trip test
    fr = raw[2:22]
    assert pack_fields([v for _, v in unpack_fields(fr)]) == fr
    print('pack/unpack round-trip: OK')
