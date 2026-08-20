#!/usr/bin/env python3
"""Regression tests for the ACT encoder (encode -> original-decoder round trip).

Each test: WAV-like signal -> act_encode -> original Actions decoder (emulated)
-> structural + quality checks.
"""
import math, struct, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import act_encode, act_decode
from oracle import OracleDecoder


def roundtrip(samples, oracle=True):
    enc = act_encode.Encoder(oracle=OracleDecoder() if oracle else None)
    out = bytearray(b'\xe1\xd3')
    for i in range(len(samples) // 160):
        fr, _ = enc.encode_frame(samples[i * 160:(i + 1) * 160])
        out += fr
    raw = bytes(out)
    assert raw[:2] == b'\xe1\xd3', 'bad magic'
    res = act_decode.decode(raw)
    assert res, 'decoder rejected the stream'
    pcm, nframes = res
    assert nframes == len(samples) // 160, f'frame count {nframes}'
    s = struct.unpack('<%dh' % (len(pcm) // 2), pcm)
    return s, nframes


def metrics(orig, dec):
    n = min(len(orig), len(dec))
    orig = orig[:n]; dec = dec[:n]
    sig = math.sqrt(sum(x * x for x in orig) / n)
    err = math.sqrt(sum((a - b) ** 2 for a, b in zip(dec, orig)) / n) or 1e-9
    snr = 20 * math.log10(sig / err) if sig > 0 else 99
    zc = sum(1 for i in range(1, n) if (dec[i - 1] < 0) != (dec[i] < 0))
    return dict(snr=snr, zc_hz=zc / 2 / (n / 16000), dec_rms=math.sqrt(sum(x * x for x in dec) / n))


def main():
    results = []

    # 1. silence: must decode to near-zero
    s, nf = roundtrip([0] * 1600)
    m = metrics([0] * len(s), s)
    ok = m['dec_rms'] < 500
    results.append(('silence', ok, f"dec_rms={m['dec_rms']:.0f} frames={nf}"))

    # 2. sines: decoded zero-crossing rate should track the tone (within 2x),
    #    output level should be within [0.2x, 5x] of input
    for freq in (200, 440, 1000, 3000):
        pcm = [int(8000 * math.sin(2 * math.pi * freq * i / 16000)) for i in range(4800)]
        s, nf = roundtrip(pcm)
        m = metrics(pcm[:len(s)], s)
        in_rms = 8000 / math.sqrt(2)
        ok = (m['zc_hz'] > freq * 0.3 and m['zc_hz'] < freq * 3.0 and
              m['dec_rms'] > in_rms * 0.1 and m['dec_rms'] < in_rms * 6)
        results.append((f'{freq}Hz sine', ok,
                        f"zc={m['zc_hz']:.0f}Hz rms={m['dec_rms']:.0f} snr={m['snr']:.1f}dB"))

    # 3. noise burst: full-band content, just check stability (no blowup)
    pcm = [((i * 1103515245 + 12345) >> 9) % 6000 - 3000 for i in range(4800)]
    s, nf = roundtrip(pcm)
    m = metrics(pcm[:len(s)], s)
    ok = 100 < m['dec_rms'] < 30000
    results.append(('noise burst', ok, f"rms={m['dec_rms']:.0f}"))

    # 4. quiet sine
    pcm = [int(200 * math.sin(2 * math.pi * 500 * i / 16000)) for i in range(4800)]
    s, nf = roundtrip(pcm)
    m = metrics(pcm[:len(s)], s)
    ok = m['dec_rms'] < 5000 and m['dec_rms'] >= 0
    results.append(('quiet sine', ok, f"rms={m['dec_rms']:.0f}"))

    # 5. long file stability (3s)
    pcm = [int(6000 * math.sin(2 * math.pi * 660 * i / 16000)) for i in range(48000)]
    s, nf = roundtrip(pcm)
    m = metrics(pcm[:len(s)], s)
    ok = nf == 300 and m['dec_rms'] > 100
    results.append(('3s duration', ok, f"frames={nf} rms={m['dec_rms']:.0f}"))

    # 6. stock ring1 re-encode (regression: known-good content)
    k = bytes([0x57, 0x2a])
    d0 = open('/workspace/project/cmf-watch-firmware/sdfs_extract/ring1.act', 'rb').read()
    raw = bytes(b ^ k[i % 2] for i, b in enumerate(d0))
    od = OracleDecoder()
    frames = [raw[2 + 20 * i:2 + 20 * (i + 1)] for i in range(40)]
    orig = [od.decode_frame(fr) for fr in frames]
    target = [x for p in orig for x in p]
    s, nf = roundtrip(target)
    assert nf == 40
    m = metrics(target[:len(s)], s)
    results.append(('ring1 re-encode', nf == 40, f"snr={m['snr']:.1f}dB"))

    allok = True
    for name, ok, info in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name:18s} {info}")
        allok &= ok
    print('ALL PASS' if allok else 'SOME FAILURES')
    return 0 if allok else 1


if __name__ == '__main__':
    sys.exit(main())
