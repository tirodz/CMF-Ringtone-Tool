#!/usr/bin/env python3
"""act_encode.py - WAV/PCM -> ACT v4 encoder (analysis-by-synthesis).

Structure (all confirmed by reversing the original decoder):
  frame = 160 bits, 22 fields:
    [0]     1 bit  : LSP MA-prediction mode (0/1)
    [1..5]  36 bits: LSP split-VQ 3+3+3+3+4 (DVD113/112/111/110/109)
    [6]     9 bits : sf0 pitch (oracle-searched)
    [7]     4 bits : sf0 pitch gain (linear: 16911 + 1024*v)
    [8..12] 45 bits: sf0 fixed codebook (5 fields x 9 bits, 2 pulses/track)
    [13]    5 bits : sf0 gain (DVD116 log interp table)
    [14]    6 bits : sf1 pitch
    [15]    4 bits : sf1 pitch gain
    [16..20]45 bits: sf1 fixed codebook
    [21]    5 bits : sf1 gain
"""
import struct, math, sys, os
import tables
from bits import pack_fields, unpack_fields

MA1 = [28835, 16383]   # Q15: [0.88, 0.5]
MA2 = [3932, 16384]    # Q15: [0.12, 0.5]

VQ_TABLES = [tables.DVD113, tables.DVD112, tables.DVD111, tables.DVD110, tables.DVD109]
VQ_DIMS = [3, 3, 3, 3, 4]


def levinson(r, order):
    """Autocorrelation -> LPC coefficients (order), double precision."""
    a = [0.0] * (order + 1)
    a[0] = 1.0
    e = r[0]
    if e <= 0:
        return a
    for i in range(1, order + 1):
        acc = r[i]
        for j in range(1, i):
            acc += a[j] * r[i - j]
        k = -acc / e
        anew = a[:]
        for j in range(1, i):
            anew[j] = a[j] + k * a[i - j]
        anew[i] = k
        a = anew
        e *= (1 - k * k)
        if e <= 0:
            break
    return a


def _dk_roots(coeffs, iters=200):
    """Durand-Kerner: all roots of a real polynomial (leading coeff first)."""
    import cmath
    n = len(coeffs) - 1
    if n <= 0:
        return []
    def peval(z):
        acc = 0j
        for c in coeffs:
            acc = acc * z + c
        return acc
    roots = [cmath.exp(2j * math.pi * k / n) * (0.4 + 0.9j) for k in range(n)]
    for _ in range(iters):
        new = []
        for i, r in enumerate(roots):
            denom = 1
            for j, r2 in enumerate(roots):
                if i != j:
                    denom *= (r - r2)
            if abs(denom) < 1e-12:
                denom = 1e-12
            new.append(r - peval(r) / (coeffs[0] * denom))
        roots = new
    return roots


def lpc_to_lsf(a):
    """16th-order LPC -> 16 line spectral frequencies (Hz, 0..8000).
    P(z) = A(z) + z^-16 A(1/z), Q(z) = A(z) - z^-16 A(1/z); unit-circle roots."""
    p = [a[i] + a[16 - i] for i in range(17)]
    q = [a[i] - a[16 - i] for i in range(17)]
    lsf = []
    for poly in (p, q):
        for r in _dk_roots(poly):
            if abs(abs(r) - 1) < 0.02:
                ang = math.atan2(r.imag, r.real)
                if 0 < ang < math.pi:
                    lsf.append(ang * 8000 / math.pi)
    lsf = sorted(set(round(x, 1) for x in lsf))
    while len(lsf) < 16:
        lsf.append(lsf[-1] + 100 if lsf else 300)
    return lsf[:16]


def frame_lsf(samples):
    """160 samples -> (16 LSFs in Hz, lpc coefficients a)."""
    n = len(samples)
    r = [sum(samples[i] * samples[i + k] for i in range(n - k)) for k in range(17)]
    a = levinson(r, 16)
    return lpc_to_lsf(a), a


def lsf_to_q(lsf_hz):
    """Convert Hz LSFs to the codec's internal domain (Q15 cos? plain Hz x?)."""
    return [int(round(f)) for f in lsf_hz]


def lsp_decode(indices, mode, prev_dev):
    """VQ indices + mode + prev deviation -> absolute LSP vector (16, Hz-ish).
    Formula confirmed from DVD158: lsp[i] = init[i] + MA1*dev_prev[i] + MA2*vq[i]
    (Q15 arithmetic; DVD143 = acc + A*B saturated)."""
    ma1, ma2 = MA1[mode], MA2[mode]
    out = []
    pos = 0
    for tab, dim in zip(VQ_TABLES, VQ_DIMS):
        idx = indices[len(out) // 4] if False else None
    devs = []
    idx_pos = 0
    pos = 0
    for tab, dim in zip(VQ_TABLES, VQ_DIMS):
        v = tab[indices[idx_pos] * dim:(indices[idx_pos] + 1) * dim]
        for k in range(dim):
            out.append(INIT_LSP[pos] + ((ma1 * prev_dev[pos]) >> 15) + ((ma2 * v[k]) >> 15))
            pos += 1
        idx_pos += 1
    return out


def quantize_lsp(lsf_int, prev_dev, mode):
    """Find VQ indices for target LSFs given prev deviation state.
    Returns (indices, decoded_lsp)."""
    ma1, ma2 = MA1[mode], MA2[mode]
    indices = []
    decoded = []
    pos = 0
    for tab, dim in zip(VQ_TABLES, VQ_DIMS):
        target = lsf_int[pos:pos + dim]
        n_entries = len(tab) // dim
        best, best_err = 0, None
        for idx in range(n_entries):
            v = tab[idx * dim:(idx + 1) * dim]
            dec = [INIT_LSP[pos + k] + ((ma1 * prev_dev[pos + k]) >> 15)
                   + ((ma2 * v[k]) >> 15) for k in range(dim)]
            err = sum((dec[k] - target[k]) ** 2 for k in range(dim))
            if best_err is None or err < best_err:
                best, best_err = idx, err
        indices.append(best)
        decoded += [INIT_LSP[pos + k] + ((ma1 * prev_dev[pos + k]) >> 15)
                    + ((ma2 * tab[best * dim + k]) >> 15) for k in range(dim)]
        pos += dim
    return indices, decoded


SILENCE_FIELDS = [1, 7, 110, 15, 92, 61, 368, 13, 387, 493, 224, 123, 440, 30, 32, 11, 61, 220, 15, 439, 387, 13]


def lsf_to_lpc(lsf_hz):
    """16 LSFs (Hz) -> LPC coefficients a[0..16] (standard LSF-to-LPC)."""
    m = 16
    ws = [math.pi * f / 8000.0 for f in lsf_hz]  # radians 0..pi
    p = ws[0::2]
    q = ws[1::2]
    def poly_from_roots(roots):
        # product of (1 - 2cos(w) z^-1 + z^-2)
        poly = [1.0]
        for w in roots:
            c = 2 * math.cos(w)
            new = [0.0] * (len(poly) + 2)
            for i, v in enumerate(poly):
                new[i] += v
                new[i + 1] += -c * v
                new[i + 2] += v
            poly = new
        return poly
    P = poly_from_roots(p)   # length 17, symmetric
    Q = poly_from_roots(q)
    # A(z) = (P(z) + Q(z)) / 2  (palindromic reconstruction)
    a = [(P[i] + Q[i]) / 2 for i in range(m + 1)]
    # normalize so a[0] = 1
    return [x / a[0] for x in a]


def lpc_filter(samples, a):
    """Analysis filter: residual[n] = s[n] + sum a_i s[n-i]."""
    out = []
    for n in range(len(samples)):
        acc = samples[n]
        for i in range(1, 17):
            if n - i >= 0:
                acc += a[i] * samples[n - i]
        out.append(acc)
    return out


def residual_autocorr_lag(res, lo=18, hi=143):
    """Estimate pitch lag from residual autocorrelation."""
    def ac(lag):
        n = len(res) - lag
        return sum(res[i] * res[i + lag] for i in range(n)) / n if n > 0 else 0
    return max(range(lo, min(hi, len(res) - 1)), key=ac)


def place_pulses(residual, n_tracks=5, window=80):
    """Given an 80-sample residual subframe, choose 5 codebook fields.

    Each field = 2 pulses: P1 at track base (sign from bits 4-7),
    P2 at track+10..track+160 (sign from bit 8). Returns 5 field values.
    Strategy: find the largest |residual| peaks; for each of the 5 tracks
    (base = t samples), find the peak nearest each track grid and encode.
    """
    fields = []
    used = [False] * window
    peaks = sorted(range(window), key=lambda i: -abs(residual[i]))
    for t in range(n_tracks):
        # find the biggest unused peak at position >= t with pos % 10 in track grid
        best = None
        for i in peaks:
            if used[i] or i < t:
                continue
            if (i - t) % 10 == 0 or True:
                best = i
                break
        if best is None:
            fields.append(0)
            continue
        # encode: P2 offset = best - t; v = ceil offset/10 mapping
        off = best - t
        if off <= 0:
            v = 0
        else:
            # P2 pos = t + 10*ceil(v/16) -> want 10*ceil(v/16) ~= off
            k = max(0, min(31, (off + 9) // 10 - 1))
            v = 1 + 16 * k
            # sign of P2 (bit 8) and P1 (bits 4-7)
            if residual[best] < 0:
                v |= 0x100
        fields.append(v)
        used[best] = True
    return fields

# empirical pulse codebook: field value -> (p1off, s1, p2off, s2) in track units
# (from pulse_map.json: pos2 = base + 10*ceil(v/16) for v>=1; signs from bits)
def build_pulse_map():
    """Field value -> (p1_sign, p2_offset10, p2_sign) using the decoded structure."""
    m = {}
    for v in range(512):
        if v == 0:
            m[v] = (1, 0, 1)     # coincident double pulse
            continue
        if v == 256:
            m[v] = (-1, 0, 1)    # coincident, P1 flipped => net 0? (empirically zero)
            continue
        p2 = ((v + 15) >> 4)     # ceil(v/16) in 10-sample units
        s1 = -1 if (v >> 4) & 0xF else 1
        # bit8 flips BOTH signs (observed v=264/320/384/416/448/480)
        s2 = 1
        if v & 0x100:
            s2 = -1
            s1 = -s1
        m[v] = (s1, p2, s2)
    m[256] = (1, 0, -1)  # net zero
    m[511] = (1, 0, -1)
    return m


# decoder's initial LSP vector (DVD115) - Hz
INIT_LSP = [335, 628, 1110, 1641, 2108, 2592, 3053, 3512, 3978, 4423, 4942, 5429, 5977, 6421, 6921, 7250]


GAIN_CAL = None


def load_gain_cal():
    global GAIN_CAL
    if GAIN_CAL is None:
        import json
        try:
            GAIN_CAL = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gain_cal.json')))
        except OSError:
            GAIN_CAL = None
    return GAIN_CAL


def gain_field_for(target_ratio, cal, sf):
    """Pick the 5-bit gain field value whose table gain is closest to target_ratio."""
    vals = cal['f13' if sf == 0 else 'f21']
    return min(range(32), key=lambda v: abs(vals[v] - target_ratio))


class Encoder:
    def __init__(self, oracle=None):
        """oracle: OracleDecoder instance (locked-step verification)."""
        self.oracle = oracle
        self.prev_dev = [0] * 16   # deviation from INIT_LSP (decoder state)

    def encode_frame(self, samples, mode=0):
        """160 samples -> 20-byte v4 frame."""
        # 1. LPC analysis -> LSF in Hz (+ exact LPC)
        lsf, a = frame_lsf(samples)
        # 2. LSP VQ (with MA prediction), try both modes, keep better
        best = None
        for m in (mode, 1 - mode):
            idx, dec = quantize_lsp(lsf, self.prev_dev, m)
            err = sum((x - y) ** 2 for x, y in zip(lsf, dec))
            if best is None or err < best[0]:
                best = (err, m, idx, dec)
        err, m, lsp_idx, dec_lsp = best
        self.prev_dev = [d - i for d, i in zip(dec_lsp, INIT_LSP)]
        # 3. residual via the exact Levinson LPC of the target
        res = lpc_filter(samples, a)
        # 4. pulses from residual peaks per subframe
        cb0 = place_pulses(res[0:80])
        cb1 = place_pulses(res[80:160])
        # 5. gains from RMS ratio (calibrated tables)
        cal = load_gain_cal()
        def gain_for(resseg, sf):
            target = (sum(x*x for x in resseg) / len(resseg)) ** 0.5
            if cal is None or target < 1:
                return 16
            # pulse excitation unit energy ~ 4096 per pulse; estimate gain needed
            return gain_field_for(target, cal, sf)
        g0 = gain_for(res[0:80], 0)
        g1 = gain_for(res[80:160], 1)
        # pitch disabled (pitch tracking not yet calibrated): fields 6/14 keep
        # harmless values, pitch gains (7/15) are zeroed
        fields = ([m] + lsp_idx + [368, 0] + cb0 + [g0, 32, 0] + cb1 + [g1])
        fr = pack_fields(fields)
        # 6. closed-loop gain refinement via oracle (if attached)
        if self.oracle is not None:
            target_rms = (sum(x*x for x in samples) / len(samples)) ** 0.5
            if target_rms > 1:
                for _ in range(3):
                    snap = self.oracle.snapshot()
                    dec = self.oracle.decode_frame(fr)
                    got = (sum(x*x for x in dec) / len(dec)) ** 0.5
                    self.oracle.restore(snap)
                    if got < 1:
                        break
                    ratio = target_rms / got
                    import math as _m
                    db = 20 * _m.log10(ratio)
                    step0 = max(1, round(db / 3.0))
                    step1 = max(1, round(db / 3.5))
                    fields[13] = max(0, min(31, fields[13] + (step0 if ratio > 1 else -step0)))
                    fields[21] = max(0, min(31, fields[21] + (step1 if ratio > 1 else -step1)))
                    fr = pack_fields(fields)
                # final decode advances oracle state with the tuned frame
                self.oracle.decode_frame(fr)
        return fr, fields


def encode_file(samples, oracle=None):
    """samples: list of ints (16kHz mono) -> raw v2 stream bytes (e1 d3 + frames)."""
    enc = Encoder(oracle)
    out = bytearray(b'\xe1\xd3')
    n = len(samples) // 160
    for i in range(n):
        fr, _ = enc.encode_frame(samples[i*160:(i+1)*160])
        out += fr
    return bytes(out)


if __name__ == '__main__':
    import wave, struct as st
    w = wave.open(sys.argv[1], 'rb')
    pcm = st.unpack('<%dh' % w.getnframes(), w.readframes(w.getnframes()))
    enc = Encoder()
    out = bytearray(b'\xe1\xd3')
    for i in range(len(pcm) // 160):
        fr, fields = enc.encode_frame(pcm[i*160:(i+1)*160])
        out += fr
    open(sys.argv[2], 'wb').write(out)
    print(f'encoded {len(pcm)//160} frames -> {sys.argv[2]}')
