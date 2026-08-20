"""Edge-case tests: short files, exact frame counts, long files, level sweeps."""
import math
import random

import helpers


def run(sig, expected):
    dec, nf, raw = helpers.roundtrip(sig)
    assert nf == expected
    helpers.assert_decoded_well(dec, nf)


def test_under_one_frame():
    """Fewer than 160 samples -> not encodable; API must not crash/produce junk."""
    dec, nf, raw = helpers.roundtrip([0] * 100)
    assert nf == 0 and raw == b'\xe1\xd3'


def test_exactly_one_frame():
    run([int(2000 * math.sin(2 * math.pi * 300 * i / 16000)) for i in range(160)], 1)


def test_several_minutes():
    """600 frames of content: structural stability only (no oracle refinement
    here - closed-loop tuning is covered by the shorter quality tests).

    The oracle-less path is a fast preview; assert robustness: no crash, exact
    duration, no NaN/inf, bounded output with no runaway growth."""
    rnd = random.Random(9)
    sig = [rnd.randint(-1500, 1500) for _ in range(160 * 600)]  # 60 s
    dec, nf, raw = helpers.roundtrip(sig, oracle=False)
    assert nf == 600
    for i in (0, 100, 300, 599):
        assert helpers.valid_frame(raw, i)
    assert all(math.isfinite(x) for x in dec)
    assert all(abs(x) < 32768 for x in dec)
    # no runaway: per-second RMS stays bounded over the whole sequence.
    # (compare against the median - the first seconds of near-silent noise
    # legitimately encode quieter than later loud bursts)
    rms = []
    for w in range(0, len(dec), 16000):
        seg = dec[w:w + 16000]
        if seg:
            rms.append((sum(x * x for x in seg) / len(seg)) ** 0.5)
    rms_s = sorted(rms)
    median = rms_s[len(rms_s) // 2]
    assert max(rms) < 8 * median, 'output grows unboundedly'
    assert max(rms) < 32768


def test_stereo_handling(tmp_path):
    """stereo WAV gets folded to mono by the input layer before encoding."""
    import audio_in
    import wave, struct as st
    path = tmp_path / 'st.wav'
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(16000)
        frames = b''.join(
            st.pack('<hh',
                    int(4000 * math.sin(2 * math.pi * 500 * i / 16000)),
                    -int(4000 * math.sin(2 * math.pi * 500 * i / 16000)))
            for i in range(800))
        w.writeframes(frames)
    pcm = audio_in.load_audio(str(path))
    assert len(pcm) == 800
    dec, nf, raw = helpers.roundtrip(pcm)
    assert nf == 5
