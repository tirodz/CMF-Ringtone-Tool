# CMF Ringtone Tool

Reverse engineering of the CMF Watch Pro 2 (Actions ATS3089C / Zephyr) ringtone
format, and tooling to decode, splice, package, and install a custom ringtone.

## Headline results

- **ACT format cracked**: the watch's `*.act` ringtones are Actions "actii" v4
  bitstreams (16 kHz, mono, 16 kbps, 20-byte frames -> 160 samples each),
  XOR-obfuscated with the repeating 2-byte key `57 2a`. All 22 known `.act`
  files decode cleanly through the original Actions decoder executed under
  Unicorn emulation (see `act_emu/REPORT.md`).
- **Decoder**: `act_emu/act_decode.py` — any `.act` -> WAV (auto-detects the
  XOR layer).
- **Splicer**: `act_emu/act_splice.py` — builds valid custom `.act` files from
  segments of stock tones (verified end-to-end through the emulated decoder).
- **Firmware packer**: `act_emu/fwmod.py` — extract/replace/rebuild the
  `sdfs_k` partition and the AOTA container with valid CRC32s
  (round-trip verified byte-for-byte).
- **BLE-only install path**: the production firmware exposes a Zephyr debug
  shell over BLE with `mdw/mww` (RAM read/write), `snandr`/`snandw` (raw NAND
  read/write through the FTL), and `sdfs` (file dump). See
  `act_emu/REPORT.md` for the full chain of evidence and the exact procedure.
  `act_emu/cmf_shell.py` (recon client) + `act_emu/cmf_flash_plan.py`
  (offline planner generating the mww/snandw command lists in `artifacts/`).

## Layout

```
act_emu/        tools + full technical report (act_emu/REPORT.md)
act_emu/wav/    decoded reference WAVs of every known stock .act
artifacts/      generated example: custom ring1.act + staging command lists
```

## Upstream sources (not vendored here)

This project builds on public third-party work; clone them separately:

- https://github.com/whatotter/cmf-watch-firmware @ fd8c708 — stock firmware + extract script
- https://github.com/freethinkel/fmc @ 59ec4cd — BLE watchface protocol research
- https://github.com/Freeyourgadget/Gadgetbridge @ a0948ee — CMF BLE protocol implementation
- https://github.com/lvgl/lv_port_actions_technology @ 26f51e5 — Actions SDK (decoder lib, spinand driver)
- https://github.com/ambraglow/cmfparser @ 6781fc6
- https://github.com/purrrock/ATS3085S_firmware_packer @ e9c3fda
- https://github.com/Viper7000/ATS3085S_firmware_unpacker @ 92fda90

The CMF stock firmware (`original.bin`) and any modified firmware image are
deliberately **not** committed: they are proprietary to CMF/Nothing and large.
The tools here operate on a locally-obtained copy.

## Status / safety

All analysis and validation has been done offline against the stock firmware
image. Nothing has been flashed to the physical watch; the BLE write path
is documented but the on-device steps (recon -> stage -> write -> verify) are
pending a first, read-only recon run against a live watch.
