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
import struct, math, sys
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
    """160 samples -> (16 LSFs in Hz, lpc coefficients a).

    A G.729-style lag window on the autocorrelation (plus a white-noise floor
    on r[0]) keeps the analysis well-conditioned on periodic/tonal input;
    without it pure sines degenerate into poles on the unit circle, near-zero
    residual, and an empty fixed codebook."""
    n = len(samples)
    r = [sum(samples[i] * samples[i + k] for i in range(n - k)) for k in range(17)]
    w = [1.0 if k == 0 else math.exp(-0.5 * (2 * math.pi * 60 * k / 16000.0) ** 2)
         for k in range(17)]
    rw = [r[k] * w[k] for k in range(17)]
    rw[0] *= 1.000001
    a = levinson(rw, 16)
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


def pitch_candidates(res, max_cands=3, min_corr=0.55):
    """Normalized-autocorrelation pitch search over the decoder's lag range.

    Returns up to `max_cands` distinct (fractional_lag, corr) candidates,
    refined to sub-sample precision with parabolic interpolation.
    """
    n = len(res)
    energy = sum(x * x for x in res)
    if energy < 1e3:
        return []
    hi = min(LAG_MAX, n - 2)
    scores = []
    for lag in range(LAG_MIN, hi + 1):
        num = sum(res[i] * res[i - lag] for i in range(lag, n))
        d1 = sum(x * x for x in res[lag:])
        d0 = sum(x * x for x in res[:n - lag])
        den = math.sqrt(d1 * d0)
        c = num / den if den > 0 else 0.0
        scores.append((lag, c))
    scores.sort(key=lambda t: -t[1])
    top = []
    for lag, c in scores:
        if c < min_corr:
            break
        if all(abs(lag - t[0]) > 4 for t in top):
            # parabolic refinement on the un-normalized autocorr envelope
            if LAG_MIN < lag < hi:
                def ac(k):
                    return sum(res[i] * res[i + k] for i in range(n - k))
                a, b, cc = ac(lag - 1), ac(lag), ac(lag + 1)
                denom = a - 2 * b + cc
                frac = 0.5 * (a - cc) / denom if denom else 0.0
                lag = lag + max(-0.5, min(0.5, frac))
            top.append((lag, c))
        if len(top) >= max_cands:
            break
    return top


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


# ---------------------------------------------------------------------------
# Pitch / adaptive codebook field mappings.
#
# Recovered from DVD162 in the original decoder (a1_act_d.a) and verified
# dynamically by sweeping every field value through the emulated decoder and
# recording the (lag, frac) arguments passed to the adaptive-excitation copy
# routine (DVD190).  "realized lag" is the effective fractional delay the
# decoder applies (lag_int + frac/3, frac in {-1, 0, +1}).
# ---------------------------------------------------------------------------

# DVD106 pitch-gain table (Q13): field (4 bits) -> gain applied to the
# adaptive excitation.  Verified by table dump + dynamic behavior.
PITCH_GAIN_TABLE = [0, 3277, 6553, 8192, 9830, 11468, 12287, 13106,
                    13926, 14745, 15564, 16385, 17202, 18021, 18840, 19660]

LAG_MIN, LAG_MAX = 29, 281  # decoder search bounds (samples @ 16 kHz)


def realized_lag_sf0(v):
    """field 6 (9 bits) -> effective fractional lag applied by the decoder."""
    if v < 390:
        return 29 + (v + 2) / 3.0
    return float(v - 230)


def sf0_field_for_lag(target):
    """Inverse of realized_lag_sf0: best 9-bit field for a target lag."""
    cands = set()
    if target <= 160.5:
        v = round(3 * (target - 29) - 2)
        for vv in (v - 1, v, v + 1):
            if 0 <= vv <= 389:
                cands.add(vv)
    if target >= 158.5:
        v = round(target) + 230
        for vv in (v - 1, v, v + 1):
            if 390 <= vv <= 511:
                cands.add(vv)
    if not cands:
        cands = {0 if target < 29 else 511}
    return min(cands, key=lambda x: abs(realized_lag_sf0(x) - target))


def sf1_base(lag0_int):
    """Decoder clamp derived from DVD162 common tail:
    sl = max(30, lag0_int - 10), capped to 262 (= 281 - 19)."""
    sl = max(30, lag0_int - 10)
    return 262 if sl + 19 > 281 else sl


def realized_lag_sf1(v, lag0_int):
    """field 14 (6 bits) -> effective fractional lag for the second subframe.
    v <= 61: lag1 = base + (v+2)/3 - 1;  v in {62, 63}: lag1 = lag0_int + 1."""
    if v <= 61:
        return sf1_base(lag0_int) + (v + 2) / 3.0 - 1
    return float(lag0_int + 1)


def sf1_field_for_lag(target, lag0_int):
    """Inverse of realized_lag_sf1 for a target lag given this frame's sf0."""
    base = sf1_base(lag0_int)
    def rl(v):
        if v <= 61:
            return base + (v + 2) / 3.0 - 1
        return float(lag0_int + 1)
    return min(range(64), key=lambda v: abs(rl(v) - target))


NO_PITCH_SF0 = 368   # field value used when the adaptive path is disengaged
NO_PITCH_SF1 = 62    # (with pitch gain 0 the lag value is inert anyway)


GAIN_CAL = None


def load_gain_cal():
    global GAIN_CAL
    if GAIN_CAL is None:
        import json, os
        try:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gain_cal.json')
            GAIN_CAL = json.load(open(path))
        except OSError:
            GAIN_CAL = None
    return GAIN_CAL


def gain_field_for(target_ratio, cal, sf):
    """Pick the 5-bit gain field value whose table gain is closest to target_ratio."""
    vals = cal['f13' if sf == 0 else 'f21']
    return min(range(32), key=lambda v: abs(vals[v] - target_ratio))


FIELD_MAX = None  # set lazily from bits


class Encoder:
    def __init__(self, oracle=None, refine=True):
        """oracle: OracleDecoder instance (locked-step verification).
        refine: closed-loop analysis-by-synthesis field refinement."""
        self.oracle = oracle
        self.prev_dev = [0] * 16   # deviation from INIT_LSP (decoder state)
        self.refine = refine
        self._prev_lag = None

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
        # 3. residual through the QUANTIZED decoder-side filter (not the raw
        # analysis LPC) so the excitation matches what the decoder synthesizes.
        a_q = lsf_to_lpc(dec_lsp)
        res = lpc_filter(samples, a_q)
        # 4. pulses from residual peaks per subframe
        cb0 = place_pulses(res[0:80])
        cb1 = place_pulses(res[80:160])
        # 5. gains from RMS ratio (calibrated tables)
        cal = load_gain_cal()
        preview = self.oracle is None

        def gain_for(resseg, sf):
            if cal is None:
                return 16
            target = (sum(x*x for x in resseg) / len(resseg)) ** 0.5
            if target < 1:
                return 0 if preview else 16
            if preview:
                # Empirically, with pulses placed at residual-peak positions,
                # preserving the decoded RMS takes excitation rms ~= 3x the
                # residual rms (peak picking concentrates the residual; the
                # wxMaxima-style unit-gain estimate under-drives by ~30x).
                # The sign/position structure still comes from the residual;
                # the oracle path refines either way.
                return gain_field_for(target / 340.0, cal, sf)
            # oracle path: the raw-rms seed is a starting point for closed-loop
            # refinement, which tunes the field by waveform SNR.
            return gain_field_for(target, cal, sf)
        g0 = gain_for(res[0:80], 0)
        g1 = gain_for(res[80:160], 1)
        # --- pitch / adaptive codebook -------------------------------------
        # Candidate lags from the full-frame residual autocorrelation; with an
        # oracle attached the winner is chosen by waveform SNR against the
        # actual decoded output, otherwise by correlation strength.
        cands = pitch_candidates(res)

        def build(v0, v1, pg):
            return ([m] + lsp_idx + [v0, pg] + cb0 + [g0, v1, pg] + cb1 + [g1])

        trial = []
        trial.append((None, NO_PITCH_SF0, NO_PITCH_SF1, 0))
        for lag, c in cands:
            v0 = sf0_field_for_lag(lag)
            lag0_int = int(realized_lag_sf0(v0))
            v1 = sf1_field_for_lag(lag, lag0_int)
            pg = max(0, min(15, int(round(c * 11))))
            trial.append((lag, v0, v1, pg))

        if self.oracle is not None and trial:
            target = list(samples)
            self._keep = self.oracle.snapshot()
            best = None
            for lag, v0, v1, pg in trial:
                s = self._snr(build(v0, v1, pg), target)
                # pitch-continuity prior: keep the lag close to the previous
                # frame's realized lag so phase locks across the sequence
                # (prevents harmonic mis-lock over time).
                if lag is not None and self._prev_lag is not None and \
                        abs(lag - self._prev_lag) < 1.5:
                    s += 0.6
                if best is None or s > best[0]:
                    best = (s, lag, v0, v1, pg)
            s, lag, v0, v1, pg = best
            if pg > 0:
                for dpg in (-2, -1, 1, 2):
                    pgn = pg + dpg
                    if 0 <= pgn <= 15:
                        s2 = self._snr(build(v0, v1, pgn), target)
                        if s2 > s:
                            s, pg = s2, pgn
            pg0 = pg1 = pg
            if pg > 0:
                self._prev_lag = realized_lag_sf0(v0)
            else:
                self._prev_lag = None
        else:
            if cands:
                lag, c = cands[0]
                v0 = sf0_field_for_lag(lag)
                lag0_int = int(realized_lag_sf0(v0))
                v1 = sf1_field_for_lag(lag, lag0_int)
                pg0 = pg1 = max(0, min(15, int(round(c * 11))))
            else:
                v0, v1, pg0, pg1 = NO_PITCH_SF0, NO_PITCH_SF1, 0, 0

        fields = build(v0, v1, pg0)
        # 6. closed-loop refinement via oracle (if attached)
        if self.oracle is not None:
            fields = self._oracle_refine(fields, list(samples))
            self.oracle.decode_frame(pack_fields(fields))
        return pack_fields(fields), fields

    def _snr(self, fields, target):
        """Trial-decode one frame and return waveform SNR vs target (oracle state
        untouched)."""
        dec = self.oracle.decode_frame(pack_fields(fields))
        self.oracle.restore(self._keep)
        err = sum((a - b) ** 2 for a, b in zip(target, dec)) / len(target)
        sig = sum(x * x for x in target) / len(target)
        return -10 * math.log10(err / sig) if sig > 0 else 99

    # fields eligible for coordinate-descent refinement: cb, pitch, gains
    REFINE_FIELDS = (6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21)
    REFINE_OFFSETS = (256, 128, 64, 32, 16, 8, 4, 2, 1)
    REFINE_PASSES = 2

    def _oracle_refine(self, fields, target):
        """Coordinate descent over cb/pitch/gain fields against decoded SNR.

        Uses one oracle snapshot per frame (state restored after every trial),
        then the caller advances the state with the refined frame.
        """
        import bits
        global FIELD_MAX
        if FIELD_MAX is None:
            FIELD_MAX = [(1 << w) - 1 for w in bits.FIELD_WIDTHS]
        self._keep = self.oracle.snapshot()
        fields = self._oracle_refine_at(fields, target)
        self.oracle.restore(self._keep)
        return fields

    def _oracle_refine_at(self, fields, target):
        best = self._snr(fields, target)
        fields = list(fields)
        for _ in range(self.REFINE_PASSES):
            improved = False
            for fi in self.REFINE_FIELDS:
                lim = FIELD_MAX[fi]
                base_v = fields[fi]
                cands = {base_v + d for d in self.REFINE_OFFSETS} | \
                        {base_v - d for d in self.REFINE_OFFSETS}
                cands.discard(base_v)
                cands = sorted(c for c in cands if 0 <= c <= lim)
                for v in cands:
                    trial = list(fields)
                    trial[fi] = v
                    s = self._snr(trial, target)
                    if s > best:
                        best, fields = s, trial
                        improved = True
                        break
            if not improved:
                break
        return fields


# headroom target for peak normalization: the decoder's postfilter can
# overshoot the coded waveform, so full-scale inputs are scaled down a bit.
NORMALIZE_PEAK = 30000


def normalize(samples, peak=NORMALIZE_PEAK):
    """Peak-normalize with clipping protection; silence passes through."""
    if not samples:
        return list(samples)
    mx = max(abs(x) for x in samples)
    if mx == 0:
        return list(samples)
    if mx <= peak:
        return list(samples)
    g = peak / mx
    return [max(-32768, min(32767, int(round(x * g)))) for x in samples]


def encode_file(samples, oracle=None, do_normalize=True):
    """samples: list of ints (16kHz mono) -> raw v2 stream bytes (e1 d3 + frames)."""
    enc = Encoder(oracle)
    out = bytearray(b'\xe1\xd3')
    if do_normalize:
        samples = normalize(samples)
    n = len(samples) // 160
    for i in range(n):
        fr, _ = enc.encode_frame(samples[i*160:(i+1)*160])
        out += fr
    return bytes(out)


if __name__ == '__main__':
    import wave, struct as st
    from oracle import OracleDecoder
    w = wave.open(sys.argv[1], 'rb')
    pcm = st.unpack('<%dh' % w.getnframes(), w.readframes(w.getnframes()))
    out = encode_file(pcm, oracle=OracleDecoder())
    open(sys.argv[2], 'wb').write(out)
    print(f'encoded {len(pcm)//160} frames -> {sys.argv[2]}')
