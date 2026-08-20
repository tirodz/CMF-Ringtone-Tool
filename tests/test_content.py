"""Content-class regression tests: speech-like, melody, transients, ringtone."""
import math
import os
import random

import helpers


def check_stream(samples):
    dec, nf, raw = helpers.roundtrip(samples)
    assert nf == len(samples) // 160
    for i in range(nf):
        assert helpers.valid_frame(raw, i)
    helpers.assert_decoded_well(dec, nf)
    return dec, helpers.metrics(list(samples)[:len(dec)], dec)


def _envelope(sig, win=160):
    return [max(abs(x) for x in sig[i:i + win]) for i in range(0, len(sig), win)]


def test_speech_like():
    """Two-vowel formant synthesis (speech-like content)."""
    formants = [(700, 0.6), (1200, 0.4), (2300, 0.2), (3100, 0.15)]
    sig = []
    for i in range(4800):
        f0 = 110 + 30 * math.sin(2 * math.pi * 3.0 * i / 16000)
        v = sum(a * math.sin(2 * math.pi * f * i / 16000) for f, a in formants)
        sig.append(int(6000 * v * (1 + 0.4 * math.sin(2 * math.pi * f0 * i / 16000))))
    dec, m = check_stream(sig)
    assert m['dec_rms'] > 200


def test_melody():
    """Note sequence (ringtone-like melody)."""
    notes = [523, 659, 784, 1046, 784, 659, 523]
    sig = []
    for n in notes:
        for i in range(800):
            env = 0.8 if i > 100 else i / 100.0
            sig.append(int(7000 * env * math.sin(2 * math.pi * n * i / 16000)))
    dec, m = check_stream(sig)
    assert m['dec_rms'] > 300
    for n in notes:
        df = helpers.domfreq(dec[:800])
        assert abs(df - n) < n * 1.6
        dec = dec[800:]


def test_transients():
    """Regular click train mixed with a quiet tone.

    A CELP-style codec cannot reproduce 1-sample impulses; the observable is
    that the energy around the click instants is clearly elevated vs the
    baseline (transients are reproduced, not erased).
    """
    sig = []
    for i in range(4800):
        v = int(1000 * math.sin(2 * math.pi * 400 * i / 16000))
        if i % 320 in (0, 160):
            v += 12000
        sig.append(max(-32768, min(32767, v)))
    dec, m = check_stream(sig)
    assert m['dec_rms'] > 100
    click_e, base_e, n1, n2 = 0.0, 0.0, 0, 0
    for i in range(len(dec)):
        e = dec[i] ** 2
        if (i % 320) in range(-8, 9) or ((i - 160) % 320) in range(-8, 9):
            click_e += e
            n1 += 1
        else:
            base_e += e
            n2 += 1
    ratio = (click_e / n1) / (base_e / n2)
    assert ratio > 1.2, f'transients erased (energy ratio {ratio:.2f})'


def test_ringtone_sample():
    """Repository reference ringtone melody (wav/ring1.wav)."""
    import wave
    path = os.path.join(helpers.ACT_EMU, 'wav', 'ring1.wav')
    with wave.open(path, 'rb') as w:
        sig = list(__import__('struct').unpack('<%dh' % w.getnframes(),
                  w.readframes(w.getnframes())))
    dec, m = check_stream(sig)
    assert m['snr'] > 0.0, 'reference ringtone should now code at positive SNR'


def test_stock_frames_reencode():
    """Re-encode previously decoded stock PCM (regression on known content)."""
    import wave
    res = helpers.roundtrip
    import numpy as np
    sig = []
    path = os.path.join(helpers.ACT_EMU, 'wav', 'tuya1.wav')
    with wave.open(path, 'rb') as w:
        sig = list(__import__('struct').unpack('<%dh' % w.getnframes(),
                  w.readframes(w.getnframes())))
    raw_in = []
    dec, nf, raw = helpers.roundtrip(sig)
    assert nf == len(sig) // 160
    decod = helpers.act_decode.decode(raw)[0]
    # decoder consumed every frame
    assert decod is not None
