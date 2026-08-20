"""Signal-class regression tests: silence, tones, sweeps, noise, impulse."""
import math
import random

import helpers


def check_stream(samples, nframes_expected=None):
    dec, nf, raw = helpers.roundtrip(samples)
    assert nf == (nframes_expected if nframes_expected is not None
                  else len(samples) // 160)
    for i in range(nf):
        assert helpers.valid_frame(raw, i)
    helpers.assert_decoded_well(dec, nf)
    return dec, helpers.metrics(list(samples)[:len(dec)], dec)


def test_silence():
    dec, m = check_stream([0] * 1600)
    assert m['dec_rms'] < 500


def test_near_silence():
    dec, m = check_stream([3] * 1600)
    assert m['dec_rms'] < 1500


def test_impulse():
    sig = [20000] + [0] * 1599
    dec, m = check_stream(sig)
    assert m['dec_rms'] > 0


def test_dc_offset():
    sig = [800] * 1600
    dec, m = check_stream(sig)
    assert m['dec_rms'] > 0


TONES = [(200, 0.3, 1.6), (440, 0.3, 1.6), (1000, 0.3, 1.6), (3000, 0.3, 1.6)]

# (freq id, snr bound, domfreq relative tolerance)
def test_tones():
    for freq, snr_min, tol in TONES:
        sig = [int(8000 * math.sin(2 * math.pi * freq * i / 16000))
               for i in range(3200)]
        dec, m = check_stream(sig)
        df = helpers.domfreq(dec)
        assert abs(df - freq) < freq * tol, f'{freq}Hz: domfreq {df}'
        assert m['snr'] > snr_min, f'{freq}Hz: snr {m["snr"]:.1f}'


def test_sweep():
    sig = [int(6000 * math.sin(2 * math.pi * (200 + 2800 * i / 3200) * i / 16000))
           for i in range(3200)]
    dec, m = check_stream(sig)
    assert m['dec_rms'] > 100


def test_noise():
    rnd = random.Random(12345)
    sig = [rnd.randint(-4000, 4000) for _ in range(3200)]
    dec, m = check_stream(sig)
    assert m['dec_rms'] > 50


def test_quiet_sine():
    sig = [int(200 * math.sin(2 * math.pi * 500 * i / 16000)) for i in range(3200)]
    dec, m = check_stream(sig)
    assert m['dec_rms'] < 5000 and m['dec_rms'] >= 0


def test_loud_sine():
    sig = [int(32000 * math.sin(2 * math.pi * 500 * i / 16000)) for i in range(3200)]
    dec, m = check_stream(sig)
    assert m['dec_rms'] > 500
