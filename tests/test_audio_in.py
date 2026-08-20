"""Audio-input layer tests (WAV fallback + ffmpeg-backed formats)."""
import os
import shutil
import subprocess

import helpers
import audio_in
import pytest

FFMPEG = shutil.which('ffmpeg')
SKIP = pytest.mark.skipif(FFMPEG is None, reason='ffmpeg not installed')


def _gen(tmp_path, kind, fmt, extra=None):
    """Generate a 0.5 s test file of the given format via ffmpeg."""
    path = os.path.join(str(tmp_path), f't.{fmt}')
    src = {'s440': 'sine=frequency=440:duration=0.5',
           's1000': 'sine=frequency=1000:duration=0.5'}[kind]
    cmd = [FFMPEG, '-v', 'error', '-f', 'lavfi', '-i', src]
    if extra:
        cmd += extra
    cmd += [path]
    subprocess.run(cmd, check=True)
    return path


def _them(path):
    pcm = audio_in.load_audio(path)
    assert pcm and len(pcm) > 0
    helpers.roundtrip(pcm)
    return pcm


@SKIP
def test_wav(tmp_path):
    _them(_gen(tmp_path, 's440', 'wav', ['-ac', '1']))


@SKIP
def test_wav_stereo_44k(tmp_path):
    """44.1 kHz stereo WAV -> normalized to 16 kHz mono (stdlib fallback)."""
    _them(_gen(tmp_path, 's440', 'wav', ['-ac', '2', '-ar', '44100']))


@SKIP
def test_mp3(tmp_path):
    _them(_gen(tmp_path, 's440', 'mp3'))


@SKIP
def test_flac(tmp_path):
    _them(_gen(tmp_path, 's1000', 'flac'))


@SKIP
def test_ogg(tmp_path):
    _them(_gen(tmp_path, 's1000', 'ogg'))


@SKIP
def test_aac_m4a(tmp_path):
    _them(_gen(tmp_path, 's1000', 'm4a', ['-c:a', 'aac']))


@SKIP
def test_invalid(tmp_path):
    bad = os.path.join(str(tmp_path), 'junk.ogg')
    with open(bad, 'wb') as f:
        f.write(b'not audio')
    if FFMPEG:
        with pytest.raises(audio_in.AudioError):
            audio_in.load_audio(bad)


def test_missing_file():
    with pytest.raises(audio_in.AudioError):
        audio_in.load_audio('does/not/exist.wav')


def test_very_short(tmp_path):
    s = _them  # reuse wrapper
    if FFMPEG:
        path = _gen(tmp_path, 's440', 'mp3', ['-t', '0.004'])
        pcm = audio_in.load_audio(path)
        helpers.roundtrip(pcm)
    else:
        import wave, struct as st
        with wave.open(str(tmp_path / 't.wav'), 'wb') as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(st.pack('<10h', *([2000] * 10)))
        helpers.roundtrip(audio_in.load_audio(str(tmp_path / 't.wav')))
