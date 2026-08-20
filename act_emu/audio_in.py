"""audio_in.py - multi-format audio input -> canonical 16 kHz mono s16 PCM.

Strategy:
  - Any format the local ffmpeg build can read (WAV/MP3/FLAC/OGG/AAC/M4A...)
    goes through ffmpeg -> raw s16le pipe.  ffmpeg is only required for
    non-WAV inputs and for compressed WAV variants.
  - Plain PCM WAV additionally has a pure-Python fallback (stdlib `wave`)
    with linear resampling, so WAV input works without ffmpeg.

The encoder receives: list[int] samples at 16000 Hz, mono, signed 16-bit.
Clipping is clamped, silence is passed through untouched.
"""
import math
import os
import shutil
import subprocess
import wave

TARGET_RATE = 16000


class AudioError(Exception):
    """Raised when an input file cannot be decoded or normalized."""


_FFMPEG = shutil.which('ffmpeg')


def load_audio(path):
    """Decode + normalize an audio file to 16 kHz mono s16 PCM (list[int])."""
    if not isinstance(path, (str, os.PathLike)) or not os.path.exists(path):
        raise AudioError(f'file not found: {path!r}')
    if _try_wav_fallback(path) is not None:
        return _try_wav_fallback(path)
    if _FFMPEG is None:
        raise AudioError(
            f'{path}: only PCM WAV is supported without ffmpeg; install ffmpeg '
            'for MP3/FLAC/OGG/AAC/M4A input')
    return _load_via_ffmpeg(path)


def _try_wav_fallback(path):
    """Decode WAV via the stdlib reader; returns None if it fails."""
    try:
        with wave.open(str(path), 'rb') as w:
            ch, sw, rate, nfr = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            raw = w.readframes(nfr)
        if ch not in (1, 2) or sw not in (1, 2, 3, 4):
            return None
        s = _unpack(raw, sw, ch)
        return _finalize(s, rate)
    except (wave.Error, EOFError, OSError):
        return None


def _unpack(raw, sampwidth, channels):
    """bytes -> list of interleaved ints, mono-folded if stereo."""
    n = len(raw) // sampwidth
    out = []
    if sampwidth == 1:
        import struct
        v = struct.unpack('<%dB' % n, raw)
        conv = [(x - 128) << 8 for x in v]
    elif sampwidth == 2:
        import struct
        conv = list(struct.unpack('<%dh' % n, raw))
    elif sampwidth == 3:
        conv = []
        for i in range(0, len(raw), 3):
            x = raw[i] | (raw[i + 1] << 8) | (raw[i + 2] << 16)
            if x & 0x800000:
                x -= 1 << 24
            conv.append(x >> 8)
    else:  # 4
        import struct
        conv = [x >> 16 for x in struct.unpack('<%di' % n, raw)]
    if channels == 2:
        conv = [(conv[i] + conv[i + 1]) // 2 for i in range(0, len(conv), 2)]
    return conv


def _finalize(samples, rate):
    """Resample to 16 kHz if needed and clamp to s16 range."""
    if rate != TARGET_RATE and samples:
        if rate <= 0:
            raise AudioError('invalid sample rate 0')
        samples = _resample(samples, rate, TARGET_RATE)
    return _clamp(samples)


def _resample(samples, rate_in, rate_out):
    """Linear-interpolation resampler (pure Python, deterministic)."""
    out_len = int(round(len(samples) * rate_out / rate_in))
    if out_len == 0:
        return []
    src = samples
    scale = rate_in / rate_out
    out = []
    for i in range(out_len):
        pos = i * scale
        i0 = int(pos)
        frac = pos - i0
        if i0 + 1 >= len(src):
            out.append(src[-1])
        else:
            out.append(src[i0] + (src[i0 + 1] - src[i0]) * frac)
    return out


def _clamp(samples):
    return [max(-32768, min(32767, int(round(x)))) for x in samples]


def _load_via_ffmpeg(path):
    cmd = [_FFMPEG, '-v', 'error', '-i', str(path),
           '-f', 's16le', '-acodec', 'pcm_s16le', '-ac', '1', '-ar', str(TARGET_RATE), '-']
    try:
        proc = subprocess.run(cmd, capture_output=True, check=False)
    except OSError as e:
        raise AudioError(f'ffmpeg failed to start: {e}')
    if proc.returncode != 0:
        raise AudioError(f'ffmpeg could not decode {path!r}: '
                         f'{proc.stderr.decode("utf-8", "replace").strip()[:200]}')
    import struct
    data = proc.stdout
    n = len(data) // 2
    return list(struct.unpack('<%dh' % n, data))
