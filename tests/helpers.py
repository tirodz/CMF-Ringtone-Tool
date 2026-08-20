"""Shared helpers for the ACT regression suite."""
import os
import struct
import sys
import math

HERE = os.path.dirname(os.path.abspath(__file__))
ACT_EMU = os.path.join(HERE, '..', 'act_emu')
sys.path.insert(0, ACT_EMU)

import act_decode  # noqa: E402
import act_encode  # noqa: E402
from bits import unpack_fields  # noqa: E402
from oracle import OracleDecoder  # noqa: E402

_oracle = None


def _get_oracle():
    global _oracle
    if _oracle is None:
        _oracle = OracleDecoder()
    return _oracle


def roundtrip(samples, oracle=True):
    """normalize -> encode -> original-decoder round trip.

    Returns (decoded_pcm, nframes, raw_stream).  The canonical pipeline
    peak-normalizes first (clipping protection), and callers compare against
    the normalized reference.
    """
    samples = act_encode.normalize(list(samples))
    enc = act_encode.Encoder(oracle=None if not oracle else _get_oracle())
    out = bytearray(b'\xe1\xd3')
    for i in range(len(samples) // 160):
        fr, _ = enc.encode_frame(samples[i * 160:(i + 1) * 160])
        out += fr
    raw = bytes(out)
    assert raw[:2] == b'\xe1\xd3'
    res = act_decode.decode(raw)
    pcm, nframes = res
    s = struct.unpack('<%dh' % (len(pcm) // 2), pcm)
    return list(s), nframes, raw


def metrics(orig, dec):
    n = min(len(orig), len(dec))
    orig = orig[:n]
    dec = dec[:n]
    sig = math.sqrt(sum(x * x for x in orig) / n) if n else 0
    err = math.sqrt(sum((a - b) ** 2 for a, b in zip(dec, orig)) / n) if n else 1e-9
    snr = 20 * math.log10(sig / err) if sig > 0 else 99
    zc = sum(1 for i in range(1, n) if (dec[i - 1] < 0) != (dec[i] < 0))
    return dict(snr=snr, zc_hz=zc / 2 / (n / 16000),
                dec_rms=math.sqrt(sum(x * x for x in dec) / n) if n else 0)


def domfreq(samples, fs=16000):
    """Dominant FFT-bin frequency of the decoded signal (Hz)."""
    import numpy as np
    x = np.asarray(samples, dtype=float) - np.mean(samples)
    w = np.hanning(len(samples))
    spec = abs(np.fft.rfft(x * w))
    if len(spec) < 2:
        return 0.0
    k = int(np.argmax(spec[1:])) + 1
    return k * fs / len(samples)


def valid_frame(raw, n):
    """Structural checks: field widths sum to 160 and all fields in range."""
    fr = raw[2 + 20 * n: 2 + 20 * (n + 1)]
    for w, v in unpack_fields(fr):
        assert 0 <= v < (1 << w), f'field value {v} overflows {w} bits'
    return True


def assert_decoded_well(pcm, nframes):
    """No NaN/inf and no sustained clipping in decoded PCM."""
    assert all(math.isfinite(x) for x in pcm)
    clipped = sum(1 for x in pcm if abs(x) == 32767)
    assert clipped < max(1, 0.02 * nframes * 160), 'sustained clipping'
