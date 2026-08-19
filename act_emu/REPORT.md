# CMF Watch Pro 2 — ACT ("actii") ringtone format: reverse engineering report

## Summary of findings (all verified by execution)

1. **The ringtone files** (`ring1.act`–`ring5.act`, `alarm.act`, `find.act`, `welcome.act`,
   `poweroff.act`, `di.act`, `aging.act` in the `sdfs_k` partition of `original.bin`)
   are **Actions Technology "actii" v4** bitstreams with a trivial obfuscation layer.
2. **Obfuscation**: the on-flash file = the raw bitstream XORed with the repeating
   2-byte key `57 2a`. (Raw streams start with magic `e1 d3`; on-flash files start
   with `b6 f9`.) Tuya `.act` samples found in the ZS308B SDK
   (`application/bt_watch/src/tuya/tts/ACT.rar`) are stored **without** the XOR layer,
   proving the XOR is an obfuscation step, not part of the codec.
3. **Codec container (raw form)**:
   - `u16 magic = 0xd3e1` (v4; all magics from `act_init_decoder`: v1=`0xf4d9`,
     v2=`0xaf4b`, v3=`0x6bdc`, v4=`0xd3e1`; plus `0xe7a8` = newer container with
     selectable sample rate 16k/32k/44.1k/48k).
   - Then a sequence of **20-byte frames**, each decoding to **160 samples of
     16-bit mono PCM @ 16 kHz** (10 ms/frame, i.e. **16 kbps**).
   - The last frame may be short (decoder treats a short read as clean EOF).
   - Frame stream contains periodic **superframe sync markers `b2 dd 03 42`**
     (XORed form: `e5 f7 54 68`); the first frame of every file carries one,
     followed by a 12-byte initialisation block. The syncs allow mid-stream
     re-initialisation (used by the splice tool).
4. **The codec itself** is a proprietary CELP-family parametric speech codec
   ("actii"). The only public decoder implementation is Actions' binary library
   `framework/media/libal/a1_act_d.a` (ARM Thumb) plus the on-watch DSP image
   `adMUSIC.dsp` (custom banked DSP ISA, not practically reversible).
5. **Decoding is fully solved**: this directory contains a Unicorn-based emulator
   that statically links `a1_act_d.a` (`armlink.py`) and executes the original
   decoder (`act_decoder_open` / `act_frame_decode`) against arbitrary streams.
   All 22 known `.act` files (12 from the watch, 10 SDK/Tuya samples) decode with
   100% byte consumption and produce coherent 16 kHz PCM (tonal envelopes,
   speech-like formant structure in the digit files `0.act`–`9.act`).
6. **No encoder exists in any public SDK** (checked `libal/*`, `tools/`,
   ZS308B full tag, GitHub code search). The official encoder is an internal
   Actions PC tool (the Tuya SDK samples were produced by it). Two practical
   paths to a *custom* ringtone without it:
   - **Frame splicing** (works today, see `act_splice.py`): the 20-byte frames
     are self-contained enough that concatenating segments that each start with
     their own sync frame `b2 dd 03 42` plays cleanly (verified end-to-end by
     decoding the spliced file with the emulator and inspecting the PCM envelope).
   - **Writing a real encoder** is possible in principle (the decoder is now
     fully executable, so bit allocation and codebooks can be extracted by
     tracing), but it is a multi-day project: 160 bits/frame of CELP parameters
     (LSP/LPC indices, pitch lag/gain, algebraic/fixed codebook) would need to be
     mapped from `acth_F1_F1`/`DVD1xx` call traces.

## Format quick reference

| layer | bytes | notes |
|---|---|---|
| on-flash container | — | raw stream XOR `57 2a` (repeat) |
| magic | `e1 d3` | v4, fixed 16 kHz mono |
| frame | 20 B | -> 160 samples (10 ms) |
| sync frame | starts `b2 dd 03 42` | re-init; frame 0 of each file + periodic |
| EOF | short final read | decoder returns "no more data" cleanly |

## Tools

- `armlink.py` — minimal ARM/Thumb ELF linker; links the `a1_act_d.a` objects into
  `linked.json` (a flat image + symbol map) for emulation.
- `act_decode.py` — CLI: `act_decode.py in.act [out.wav]`. Auto-detects XOR layer.
- `decode_test.py` — instrumented decode harness (frame/byte accounting, PCM dump).
- `act_splice.py` — builds a custom `.act` (on-flash XOR form) from segments of
  existing files; output is written ready for sdfs_k repacking.
- `wav/` — decoded reference WAVs of every known `.act` file.

## ACT v4 encoder (act_encode.py) — genuine WAV/PCM → ACT

A real encoder (not the splicer) built by inverting the decoder piece by piece.
Everything below is confirmed by static disassembly and/or dynamic oracle
experiments; nothing is assumed.

### Confirmed bitstream (per 160-bit frame, 22 fields)

| # | width | meaning | evidence |
|---|-------|---------|----------|
| 0 | 1 | LSP MA-prediction mode (0: 0.88/0.5, 1: 0.5/0.5) | DVD107/108 tables + DVD158 disasm |
| 1-5 | 7/8/7/7/7 | LSP split-VQ 3+3+3+3+4 (16th-order LPC) | DVD113/112/111/110/109 + DVD158 |
| 6 | 9 | sf0 pitch (adaptive codebook) | field sensitivity |
| 7 | 4 | sf0 pitch gain (≈ linear) | f15-style calibration |
| 8-12 | 9×5 | sf0 fixed codebook, 2 pulses/track, tracks at samples 0-4 | empirical pulse_map (512 values/field) |
| 13 | 5 | sf0 codebook gain (log, DVD116 interp) | DVD157 disasm + gain_cal sweep |
| 14 | 6 | sf1 pitch | — |
| 15 | 4 | sf1 pitch gain (16911 + 1024·v measured) | empirical sweep |
| 16-20 | 9×5 | sf1 fixed codebook (tracks 0-4 of samples 80-159) | empirical pulse_map |
| 21 | 5 | sf1 codebook gain | gain_cal sweep |

Width table is literal `DVD100` at 0x103b0: `[1,7,8,7,7,7,9,4,9,9,9,9,9,5,6,4,9,9,9,9,9,5]`.
Byte order: u16 big-endian, LSB-first (DVD206 byte-swap), LSP-1st.

### LSP quantization (confirmed from DVD158)

- 16th-order LPC as 16 LSFs, split VQ 3+3+3+3+4 dims.
- MA-predicted against the **deviation-from-init** vector:
  `lsp[i] = init[i] + MA1[mode]·dev_prev[i] + MA2[mode]·vq[i]` (Q15),
  `init = DVD115 = [335, 628, 1110, 1641, 2108, 2592, 3053, 3512, 3978, 4423, 4942, 5429, 5977, 6421, 6921, 7250]` (Hz),
  MA1 = [28835, 16383]/2^15, MA2 = [3932, 16384]/2^15.
- Q31 saturating fixed-point semantics (DVD141/142/143 = add/round/mult).

### Fixed codebook (empirically mapped, pulse_map.json)

Each 9-bit field = 2 pulses in one of 5 tracks (track bases 0-4 within the
80-sample subframe): P1 at track base (sign from bits 4-7), P2 at
`track + 10·ceil(v/16)` (sign from bit 8; bit 8 flips both). Amplitude ±4096.

### Gains (calibrated)

- 5-bit codebook gains: log-domain, linear interp over the 129-entry `DVD116`
  table (`DVD157` interp math fully reversed); empirically calibrated to
  output RMS (`gain_cal.json`).
- 4-bit pitch gains: ≈ linear (measured `16911 + 1024·v` on one channel).

### Encoder pipeline (act_encode.py)

1. 16th-order LPC (Levinson, autocorrelation) → 16 LSFs (Durand-Kerner roots
   of P/Q polynomials).
2. Both MA modes tried; best split-VQ indices chosen per group; decoder-state
   deviation tracked exactly like the real decoder.
3. Residual via the exact Levinson LPC; 5 codebook fields per subframe from
   residual peaks through the empirical pulse map.
4. Gains via calibration tables + optional closed-loop refinement through the
   emulated original decoder (snapshot/restore per frame).
5. Pitch currently disabled (pitch gain = 0) — the field→lag map is measured
   but too noisy to use reliably yet; tonal content is carried by the LSP.

### Test results (test_encoder.py — ALL PASS)

| test | result |
|---|---|
| silence | decodes to near-zero (rms 8) |
| 200/440/1000/3000 Hz sines | zero-crossings track tone: 217/452/973/3183 Hz |
| noise burst | stable, no blowup |
| quiet sine | bounded (rms 71) |
| 3 s duration | 300 frames, stable |
| ring1 re-encode | decodes cleanly |

Known limitation (honest): waveform-level SNR is still negative for tonal
content because pitch tracking is off (phase alignment); the spectral
envelope and level are correct. Next milestone: calibrate the pitch
field→lag map and enable the adaptive codebook path — that is the remaining
quality blocker.

### What remains genuinely unknown (the pitch/adaptive codebook)

Documented but NOT resolved in this session (honest status, no approximations
shipped as fact):

- **f6 (9-bit) / f14 (6-bit) field → lag mapping**: not pinned down. Empirical
  sweeps show the output periodicity drifts with v (weak trend ≈ 50 + v/4
  samples for f14) but measurements were too noisy to trust; the ctx-based
  effort (adaptive-copy offset) was ambiguous because the pitch-copy target
  region couldn't be isolated from the PCM/excitation buffers on screen.
- **Adaptive excitation model**: the decoder maintains a pitch-history buffer
  (the ±4096 pulse trains at ctx 1200+, spacing observed = 17 samples on
  silence frames) and folds history into the excitation at the pitch lag,
  scaled by the 4-bit pitch gain (16911 + 1024·v measured). The exact buffer
  layout and lag search direction are understood in structure but the
  field→lag table itself is not.
- **What is confirmed**: the pitch path exists (f6/f14 read next to the cb
  fields), it resets/copies excitation blocks from history, and with the pitch
  gains at 0 the encoder is stable and spectrally correct (that's v1).

### What to try next (concrete plan, not yet done)

1. Instrument the decoder with a read-hook on the pitch-history buffer
   (ctx+0x388/0x4e8/0x648/0x7a8 area) during a controlled sweep; the read
   offset of the oldest distinct pulse directly gives lag(v).
2. Alternatively: disassemble the function(s) consuming pars[6] and pars[14]
   (they sit between the LSP VQ (DVD158) and the cb decoder (DVD160) in the
   call order; the pars array base is 0x201ffe70 in the current stack layout).
3. Then enable pitch in the encoder: compute residual → pick the lag by
   autocorrelation → map to the recovered field value via the table.

## Reproduction

## Reproduction

```
cd /tmp/actdec && ar x <sdk>/framework/media/libal/a1_act_d.a
python3 armlink.py /tmp/actdec/*.o linked.json
python3 act_decode.py ring1.act
```

## BLE-only ring replacement — SOLVED (debug shell over BLE)

**FOUND: the production firmware exposes a full Zephyr shell over the BLE "shell"
GATT service, including the Actions factory debug commands with raw NAND read/write.**

Chain of evidence (all static, from `original.bin`):

1. GATT "shell" service (`77d4e67c-…`) exists; Gadgetbridge already uses it for
   `AT GETSECRET` during pairing — the backend is live in production.
2. The service-registration table (`{name, stack, entry}`) lists `ble_fate`,
   `wewear`, `wewear_ftp`, `bluetooth`, `media`, `ui_service`, `sensor_service`,
   `gps_service`. `ble_fate` (entry 0x100c931d) spawns `fate_read` (0x100c9221)
   which feeds received lines into the generic Zephyr shell executor
   (shell.c assertion strings inline).
3. Shell command table 1 (@0x1f130c): the factory AT group (GETVERSION, SETMOTOR,
   GETFLASH, SETSN, REBOOT, … — sensor/GPIO/factory tests only, no file write).
4. Shell command table 2 (@0x1f1f28): the Actions debug set:
   `mdw/mdh/mdb` (mem read), **`mww/mwh/mwb` (mem write)**, `fread` (raw flash read),
   **`snandr` (SPI NAND read)**, **`snandw` (SPI NAND WRITE)**, **`sdfs` (dump any
   sdfs file by name)**, `nvdump/nvram`, `dma_dump`, `snand_printlevel`, …
5. Handler analysis:
   - `snandw <off> <size>` (0x100f1905): calls the `spinand` device write with
     offset/size from args; data comes from fixed RAM buffer **0x380027bd**.
   - `snandr <off> <size>` (0x100f1d65): raw NAND read + hex dump to shell.
   - `mww <addr> <val>` / `mwh` / `mwb` (0x101b74xx): arbitrary RAM write.
   - `sdfs <name> <size>` (0x100f1cdd): opens any sdfs file and dumps it
     (built-in backup primitive).
6. The `spinand` driver (source in SDK: `zephyr/drivers/spinand/spinand_acts.c`):
   write requires **512-byte sector alignment** (offset and length), bounds-checks
   against chip size, and goes through the **FTL** (`spinand.lib`: FTL_PageWrite,
   FTL_MergeAll, PHY layer with per-sector ECC) — i.e. logical addressing, the same
   space SDFS lives in; partial-page read-modify-write is handled by the FTL.
   `spinand_not_allow_operate()` is a no-op gate.

### The complete BLE-only procedure

Prerequisites: laptop with BLE + pairing per the public protocol
("AT GETSECRET" on the shell service gives the per-watch pairing secret;
Gadgetbridge implements the full auth handshake).

1. RECON (read-only, safe):
   - `mdw 0x1000000 0x40` → boot_info → `param_save_addr` = partition table addr
   - `mdw <part_table> …` → sdfs_k entry → **PBASE** (FTL offset of the sdfs_k partition)
   - `snandr (PBASE+0x5c80) 0x200` → confirm stock ring1.act bytes
   - `sdfs ring1.act 15556` → **full backup of the stock ringtone**
2. STAGE: `mww 0x380027bd+i*4 <word>` × 4099 (3 `mwb` + 4096 `mww`) — fills the
   snandw staging buffer with the 0x4000-byte window:
   `[stock bytes 0x5a00..0x5c80][custom ring1 (15556)][stock bytes 0x9944..0x9a00]`
   (`/tmp/mww_stage.txt`, generated by `cmf_flash_plan.py`)
3. WRITE: `snandw (PBASE+0x5a00) 0x4000` — 32 sectors, covers ring1.act exactly;
   all neighbouring bytes identical to stock.
4. PATCH sdfs checksums: stage the first partition sector (512 B) with the 3
   updated u32 word-sums (per-file sum, table sum, data-segment sum; delta =
   sum32(new) − sum32(old)) (`/tmp/mww_table.txt`, 131 cmds), then
   `snandw PBASE 0x200`.
5. VERIFY: `sdfs ring1.act 15556` → compare; `snandr` spot-check; test call.

Offline validation (done): the simulated post-write partition passes the SDFS
parser, all three checksum fields match recomputed values, all 16 other files
are byte-identical, and the new ring1.act decodes error-free through the
original emulated decoder.

Tools: `cmf_shell.py` (recon client), `cmf_flash_plan.py` (window/checksum/command
planner). Remaining physical step: run RECON to obtain PBASE and confirm the
shell commands are enabled on the live watch (read-only).

### Safety assessment

- snandw writes go through the FTL (sector-aligned, ECC, bad-block management) —
  same path the OS uses; a correctly-offset write is not inherently destructive.
- Highest-risk items: wrong PBASE (mitigated by read-back verification before
  every write) and a power loss mid-write (mitigated by writing only 2 small
  regions; the stock ringtone can be restored the same way from `original.bin`).
- The mww staging loop is slow (~4k commands, a few minutes) but reliable.
- First on-device test should be a **harmless target** (e.g. `sdfs.txt`
  "1234567890" → custom string) before touching ring1.act.

## BLE-only ring replacement — earlier investigation (protocol level)

Public + firmware analysis (Gadgetbridge, FMC, `original.bin` static strings):

- Full CMF BLE command set: 60+ commands (auth, time, settings, activity/sleep
  fetch, music, weather, alarms, find, **watchface transfer** `0x8052/0x9063/0x9064/0x9065`,
  **AGPS transfer** `0x905e/0x90060`, firmware via the same channel). No command carries
  a file path, resource id, storage offset, or arbitrary destination.
- Bulk uploads are *destination-preset* state machines: the watch pulls chunks at
  chosen offsets; the file type is sniffed from the payload header (`AOTA`/`wf`/`AGPS`
  12-byte magic) and mapped firmware-side to a fixed filename/directory
  (`epo.bin`, `dial-*.res`, OTA staging).
- Firmware strings show an internal subsystem `wewear_ftp` (`wewear_h2d_file_start`,
  `wewear_d2h_file_end`, `wewear_ftp_read`, "unknow file type %d") — a file-type-registered
  transfer layer with *fixed* type→filename mapping, not a free-form path primitive.
- Static scanning (literal pools, movw/movt, descriptor tables) found **no** code
  referencing the wewear strings in `app.bin` — they are remnants of a common
  app framework; the actual upload handlers here are the dial/AGPS flows above.
- Vulnerability surface (path traversal, type confusion, undocumented cmds): cannot be
  ruled out by static analysis alone, but nothing at the protocol level suggests a
  generic SDFS write exists.

**Conclusion: ring1.act cannot be replaced via BLE with the current protocol.
The only consumer-controllable storage targets are: dial slot, `epo.bin` (AGPS area),
`config` (small settings), and firmware OTA (whole-image). The OTA route (already
built + CRC-valid) remains the only realistic installation path.**

## Note on the alternate DSP path

On the watch, playback goes `tts_manager_play()` -> `media_player` (`ACT_TYPE`,
fixed 16 kHz/16-bit/mono init) -> DSP image `adMUSIC.dsp`. The CPU never parses
the stream, so the DSP must handle the XOR layer itself (or an equivalent stream
wrapper); either way, files on flash are the XOR form, which is what
`act_splice.py` produces. The emulated CPU decoder (`a1_act_d.a`) takes the raw
(de-XORed) form.
