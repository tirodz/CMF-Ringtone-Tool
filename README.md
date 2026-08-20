<p align="center">
  <img src="docs/banner.png" alt="CMF Ringtone Tool — custom ringtones for the CMF Watch Pro 2" width="800"/>
</p>

# 🎵 CMF Ringtone Tool

**Make your CMF Watch Pro 2 play more than the five ringtones it shipped with.**
Pick a sound on your computer, press a button, and it becomes your watch's new ringtone — no cables, no soldering, no warranty voids. The whole `.act` ringtone format is reverse-engineered and documented along the way.

<p align="center">
  <img src="https://media.giphy.com/media/xTkcEQACH24SMPxIQg/giphy.gif" alt="equalizer" width="360"/>
</p>

<p align="center">
  <a href="https://github.com/tirodz/CMF-Ringtone-Tool/stargazers"><img src="https://img.shields.io/github/stars/tirodz/CMF-Ringtone-Tool?color=ff5500" alt="GitHub stars"/></a>
  <a href="https://github.com/tirodz/CMF-Ringtone-Tool/issues"><img src="https://img.shields.io/github/issues/tirodz/CMF-Ringtone-Tool?color=ff5500" alt="GitHub issues"/></a>
  <img src="https://img.shields.io/badge/python-3.x-3776AB?logo=python&logoColor=white" alt="Python 3"/>
  <img src="https://img.shields.io/badge/device-CMF%20Watch%20Pro%202-ff5500" alt="CMF Watch Pro 2"/>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0-ff5500" alt="License: GPL-3.0"/></a>
</p>

<p align="center">
  <img src="https://readme-typing-svg.demolab.com?font=JetBrains+Mono&weight=600&size=20&duration=2500&pause=800&color=FF5500&center=true&vCenter=true&width=700&lines=Decode+.act+ringtones+%E2%86%92+WAV;Encode+MP3+%2F+OGG+%2F+WAV+%E2%86%92+ACT+v4;Splice+stock+tones+into+your+own;Beam+it+to+the+watch+over+BLE+%F0%9F%93%A1" alt="Decode .act ringtones → WAV • Encode MP3/OGG/WAV → ACT v4 • Splice stock tones • Beam it to the watch over BLE"/>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/decoder-%E2%9C%85%20working-brightgreen" alt="Decoder: working"/>
  <img src="https://img.shields.io/badge/encoder-%F0%9F%9A%A7%20in%20progress-orange" alt="Encoder: in progress"/>
  <img src="https://img.shields.io/badge/BLE%20install-%F0%9F%A7%AA%20untested%20on%20hardware-yellow" alt="BLE install: untested on hardware"/>
</p>

<p align="center">
  <img src="https://skillicons.dev/icons?i=python,rust,js,html,css,git,github,tauri&theme=dark" alt="Tech stack: Python, Rust, JavaScript, HTML, CSS, Git, GitHub, Tauri"/><br/>
  <sub><em>Python research tooling today — a small desktop app is in the works</em></sub>
</p>

---

## ✨ What it does

- 🎧 **Decode** any `.act` ringtone back into a normal WAV you can actually listen to
- 🎚️ **Encode** your own audio — WAV today, **MP3 / OGG / FLAC in the works** — into a real `.act` file
- ✂️ **Splice** the stock tones together into new combinations
- 📦 **Repack** the watch's firmware image with a swapped ringtone
- 📡 **Beam it over Bluetooth** — the install path is mapped out and being validated on real hardware

## 🖥️ The app

A small, dark desktop app (CMF vibes, obviously) is coming for Windows — pick a file, watch it convert, done.

<p align="center">
  <img src="https://media.giphy.com/media/l0HUpt2s9Pclgt9Vm/giphy.gif" alt="audio" width="340"/>
</p>

| Feature | State |
|---|---|
| Ringtone picker & preview | 🚧 Mocked UI |
| ACT conversion | 🚧 Backend in progress |
| One-click BLE install | 🧪 Validated offline, first hardware run pending |
| Auto-updates | ✅ Scaffolded |

The workflows already produce a ready-to-run `.exe` on every release tag — the internals land next.

## 🚀 Quick start (command line)

```bash
pip install unicorn capstone bleak

# Listen to a ringtone from the watch
python3 act_emu/act_decode.py ring1.act ring1.wav

# Make your own (any format ffmpeg reads works, WAV goes straight in)
ffmpeg -i mysong.mp3 -ar 16000 -ac 1 mysong.wav
python3 act_emu/act_encode.py mysong.wav mysong.act

# Or splice the stock tones together
python3 act_emu/act_splice.py custom.act ring1.act ring4.act
```

**Two things are deliberately *not* in this repo** (both belong to CMF/Nothing): the Actions decoder library (`a1_act_d.a`) and the stock firmware (`original.bin`). Details in the toolbox below.

## 📖 The nerdy parts

Everything is documented, tested, and reproducible. Click a section to expand it:

<details>
<summary><b>🔍 Compatibility — which devices & firmware does this actually work on?</b></summary>

| Device / firmware | Status |
|---|---|
| **CMF Watch Pro 2** (Actions ATS3089C, Zephyr) | ✅ Target device. All analysis and validation done against its stock firmware. All 12 stock tones decode cleanly. |
| Tuya ZS308B SDK `.act` samples | ✅ All 10 decode cleanly (same codec, no XOR layer) |
| Other ATS308x-based watches | 🧪 Very likely the same codec, but **untested** — don't assume |
| Other CMF / Nothing devices | ❓ Unknown — no claims until tested |

Tried this on other hardware? Open an issue and share what you found — even "it did nothing" is useful data.
</details>

<details>
<summary><b>🧩 What on earth is `.act`?</b></summary>

`.act` is an **Actions Technology "actii"** audio bitstream — a proprietary, CELP-family parametric *speech* codec that CMF/Nothing (mis)uses for ringtones. A few things make it unusual:

- **It's obfuscated, but charmingly badly.** Every byte on flash is XORed with a repeating 2-byte key (`57 2a`). Raw streams start with the magic `e1 d3`; on-flash files start with `b6 f9`. That's the whole "protection".
- **It's tiny.** 16 kHz, mono, **16 kbps** — 20-byte frames, each carrying 10 ms of audio (160 samples). A whole ringtone is about 15 KB.
- **Nobody publishes an encoder.** The only public decoder is a compiled ARM library (`a1_act_d.a`) buried in the Actions SDK. So this repo runs the original decoder *under CPU emulation*, and builds an encoder by reverse-engineering what the decoder expects.

Each 160-bit frame packs 22 fields: LSP spectral-envelope indices (split-VQ), pitch lag/gain for two subframes, and a 2-pulses-per-track algebraic fixed codebook with log-domain gains. The full, evidence-linked field map is in `act_emu/REPORT.md`.
</details>

<details>
<summary><b>🛠️ The toolbox</b></summary>

| Tool | What it does | Status |
|---|---|---|
| `act_emu/act_decode.py` | `.act` → WAV, using the *original* Actions decoder running under Unicorn CPU emulation. Auto-detects the XOR layer. | ✅ All 22 known files decode 100% |
| `act_emu/act_encode.py` | Audio → ACT v4 encoder, built by inverting the decoder field by field. WAV/PCM input today; **MP3/OGG/FLAC and friends in progress** (via format conversion) | 🚧 Works, quality improving |
| `act_emu/act_splice.py` | Builds valid custom `.act` files by splicing segments of stock tones | ✅ Verified end-to-end |
| `act_emu/fwmod.py` | Extract / replace / rebuild the `sdfs_k` partition and AOTA firmware container with valid CRC32s | ✅ Round-trip byte-for-byte verified |
| `act_emu/cmf_shell.py` | BLE debug-shell client for the watch (read-only recon) | 🧪 Pending first on-device run |
| `act_emu/cmf_flash_plan.py` | Offline planner: generates the exact `mww`/`snandw` command lists for a BLE-only ringtone swap | ✅ Offline plan validated |
| `act_emu/test_encoder.py` | Encoder regression suite (encode → original decoder round-trip) | ✅ All tests pass |
| `act_emu/armlink.py` + friends | The archaeology kit: ARM/Thumb ELF linker, disassembler, tracer, oracle harness used to crack the format | 🔬 Research tooling |
</details>

<details>
<summary><b>🎚️ How the encoder works (and where it still struggles)</b></summary>

There's no official encoder, so `act_encode.py` is a genuine analysis-by-synthesis encoder built from scratch:

1. **Spectral envelope** — 16th-order LPC (Levinson) → line spectral frequencies → quantized with the decoder's exact split-VQ tables and MA-prediction, tracking decoder state bit-for-bit.
2. **Excitation** — the LPC residual is mapped onto the fixed codebook's 2-pulses-per-track structure using an empirically extracted pulse map (512 values per field, measured from the real decoder).
3. **Gains** — the decoder's log-domain interpolation table was fully reversed and calibrated against output loudness, with optional closed-loop refinement through the emulated original decoder.
4. **Pitch (adaptive codebook)** — currently **disabled**. The field→lag map is measured but too noisy to trust yet, so tonal content is carried by the spectral envelope alone.

**The honest status:** everything the encoder produces decodes cleanly through the *original* Actions decoder — tones come out at the right pitch, right duration, right loudness, and silence stays silent. But waveform-level SNR for tonal content is still negative, because without the pitch track the decoder can't phase-align voiced sounds. That's the remaining quality blocker, and it's the active work item. For a wrist-speaker ringtone it's already closer than you'd think — check `artifacts/melody_in.wav` vs `artifacts/melody_out.wav`.
</details>

<details>
<summary><b>✅ Testing & validation</b></summary>

Nothing here is "should work" — it's all executed and measured:

- 🧪 **Decoder**: 22/22 known `.act` files (12 from the watch, 10 SDK samples) decode with 100% byte consumption and coherent PCM.
- 🧪 **Encoder** (`test_encoder.py`, all passing): silence stays silent (RMS 8), 200/440/1000/3000 Hz sines come out at 217/452/973/3183 Hz, noise bursts stay stable, a 3-second clip encodes to 300 frames without drift, and a stock ringtone survives a full re-encode round-trip.
- 🧪 **Firmware packer**: modified AOTA images re-parse, all CRC32s valid, round-trip byte-for-byte identical.
- 🧪 **BLE flash plan**: the simulated post-write partition passes the real SDFS parser, all three checksum fields match, all 16 untouched files stay byte-identical, and the new ringtone decodes error-free.
</details>

<details>
<summary><b>📡 Putting it on the watch — BLE status</b></summary>

**✅ Confirmed (static analysis + offline execution):**
- The production firmware exposes a **full Zephyr debug shell over BLE** — including `mdw`/`mww` (RAM read/write), `snandr`/`snandw` (raw NAND read/write through the FTL), and `sdfs` (dump any file by name — a built-in backup primitive).
- The complete ringtone-swap procedure (recon → stage → write → patch checksums → verify) has been planned command-by-command, and the simulated result passes every offline check.

**🧪 Experimental / not yet verified:**
- **Nothing has been flashed to a physical watch.** Not once. The on-device steps are pending a first, read-only recon run to confirm the debug commands are actually enabled on live hardware and to read out the real partition offset (`PBASE`).
- The normal CMF BLE protocol (the one Gadgetbridge speaks) has **no** generic file-write command — the debug shell is the only known BLE write path, which is why it's experimental territory.

In short: the road is mapped and the car is built, but it hasn't left the garage yet. 🚗
</details>

<details>
<summary><b>📁 Repository structure</b></summary>

```
act_emu/          All the tools + the full technical report (act_emu/REPORT.md)
act_emu/wav/      Decoded reference WAVs of every known stock .act
artifacts/        Generated example: custom ring1.act + BLE staging command lists
app/              The desktop app (Tauri — mocked UI, backend landing next)
docs/             README assets (banner)
```
</details>

## ⚠️ Safety first

Playing with raw flash writes is inherently risky. If you go near the write path:

- **Back up first.** `sdfs ring1.act 15556` dumps the stock ringtone straight off the watch; keep it (and a stock firmware dump) somewhere safe before changing anything.
- **Writes go through the FTL** (sector-aligned, ECC, bad-block management) — the same path the OS itself uses, so a correctly-placed write isn't inherently destructive. A *misplaced* one can be.
- **Biggest risks:** a wrong partition offset (mitigated: read-back verification before every write) and power loss mid-write (mitigated: only two small regions are touched, and the stock tone can be restored the same way).
- **First on-device test should be harmless** — e.g. change the `sdfs.txt` test string, not the ringtone.
- You do this **at your own risk**. Bricking your watch is unlikely if you follow the procedure carefully, but "unlikely" is not "impossible".

## 🤝 Contributing

Contributions are very welcome — this is a research project and there's plenty left to do:

- 🎚️ Calibrate the pitch field→lag map and enable the adaptive codebook (the encoder quality blocker)
- ⌚ Test decoding/tools against other ATS308x devices and firmware versions
- 📡 Carefully validate the BLE write path on real hardware (read-only recon first!)
- 📝 Docs, tests, cleanups — all good

Open an issue before big changes so we don't duplicate effort. Please keep the tone of this repo: **claims backed by execution, and clear labels on anything unverified.**

## 🙏 Credits

This project stands on public third-party work — huge thanks to all of them (not vendored here, clone separately):

- [whatotter/cmf-watch-firmware](https://github.com/whatotter/cmf-watch-firmware) @ `fd8c708` — stock firmware + extract script
- [freethinkel/fmc](https://github.com/freethinkel/fmc) @ `59ec4cd` — BLE watchface protocol research
- [Freeyourgadget/Gadgetbridge](https://github.com/Freeyourgadget/Gadgetbridge) @ `a0948ee` — CMF BLE protocol implementation
- [lvgl/lv_port_actions_technology](https://github.com/lvgl/lv_port_actions_technology) @ `26f51e5` — Actions SDK (decoder lib, SPI NAND driver)
- [ambraglow/cmfparser](https://github.com/ambraglow/cmfparser) @ `6781fc6`
- [purrrock/ATS3085S_firmware_packer](https://github.com/purrrock/ATS3085S_firmware_packer) @ `e9c3fda`
- [Viper7000/ATS3085S_firmware_unpacker](https://github.com/Viper7000/ATS3085S_firmware_unpacker) @ `92fda90`

The ACT decoder binary (`a1_act_d.a`) is © Actions Technology; the CMF firmware is © CMF/Nothing. Both remain the property of their respective owners and are used here solely for interoperability research.

## 📄 License

**GPL-3.0** — free software: use it, break it, improve it, share it. The full text lives in [`LICENSE`](LICENSE).

Third-party components and upstream projects listed in [Credits](#-credits) remain under their own licenses. The ACT decoder binary is © Actions Technology; the CMF firmware and trademarks are © CMF/Nothing.

---

<p align="center">
  <em>Not affiliated with, endorsed by, or connected to Nothing Technology or CMF.<br/>
  "CMF", "Nothing" and the watch itself belong to their respective owners.<br/>
  Made with ☕, a disassembler, and an unhealthy amount of curiosity.</em>
</p>
