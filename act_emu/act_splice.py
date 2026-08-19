#!/usr/bin/env python3
"""act_splice.py - Build a custom .act prompt-tone file from segments of existing ones.

The ACT v4 stream is made of independent 20-byte frames (160 samples @16kHz each);
segments spliced at silence frames and starting at each file's own sync frame
(b2 dd 03 42) play cleanly without an encoder.

Usage:
  act_splice.py out.act seg1.act seg2.act ...   (segments are whole stock files)
  The output is written in the on-flash (XOR-obfuscated) form used by sdfs_k.
"""
import sys

XOR_KEY = bytes([0x57, 0x2a])
MAGIC = b'\xe1\xd3'
SILENCE_FRAME = bytes.fromhex('6e0f' * 10)


def deobfuscate(data: bytes) -> bytes:
    if data[:2] in (b'\xe1\xd3', b'\xe7\xa8'):
        return data
    return bytes(b ^ XOR_KEY[i % 2] for i, b in enumerate(data))


def obfuscate(data: bytes) -> bytes:
    return bytes(b ^ XOR_KEY[i % 2] for i, b in enumerate(data))


def full_frames(raw: bytes):
    """Return list of 20-byte frames (dropping magic and any short tail)."""
    body = raw[2:]
    n = len(body) // 20
    return [body[20*i:20*(i+1)] for i in range(n)]


def build(segments, pre_silence=0, gap_silence=0, tail_silence=0):
    # NOTE: pure "6e 0f" frames are NOT decoded as silence by a freshly opened
    # decoder - silence must come from donor files' natural quiet regions.
    # Best results: splice whole files (each begins with its own sync/init frame
    # b2 dd 03 42, so the decoder re-initializes at every splice point).
    out = bytearray(MAGIC)
    out += SILENCE_FRAME * pre_silence
    for seg in segments:
        out += b''.join(full_frames(seg))
        out += SILENCE_FRAME * gap_silence
    out += SILENCE_FRAME * tail_silence
    return bytes(out)


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    dst = sys.argv[1]
    segments = [deobfuscate(open(p, 'rb').read()) for p in sys.argv[2:]]
    raw = build(segments)
    open(dst, 'wb').write(obfuscate(raw))
    n = (len(raw) - 2) // 20
    print(f'{dst}: {len(raw)} bytes, {n} frames ({n*0.01:.2f}s @16kHz)')


if __name__ == '__main__':
    main()
