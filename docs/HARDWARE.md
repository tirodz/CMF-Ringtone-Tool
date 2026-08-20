# Manual hardware test procedure — CMF Watch Pro 2

Everything below is executed **manually** with a real watch within BLE reach.
The implementation-level enforcer of the safety gates below is
`act_emu/ble_ringtone.RingtoneManager`; mocked transport tests live in
`tests/test_ble_mock.py`.

The system never guesses addresses and never continues after a mismatch.

## Supported firmware

The procedure only targets firmware versions in `act_emu/fw_registry.py`
(currently **1.0.0.73** only).  Any other version must abort with:

```
Unsupported firmware version — no write performed.
```

## 1. Read-only recon (harless by design)

```bash
python3 act_emu/cmf_shell.py --address XX:XX:XX:XX:XX:XX shell
```

1. `AT GETVERSION` → confirm a registry-supported version (else: stop).
2. `mdw 0x1000000 0x40` → boot_info → `param_save_addr` (partition table ptr).
3. `mdw <part table> 0x100` → find the `sdfs_k` entry → `PBASE`.
4. Backup: `sdfs ring1.act 15556` → save as `ring1_backup.act`.
5. Verify layout: `snandr (PBASE+0x5c80) 0x200` must XOR-decode to `e1 d3` +
   valid frames; entry-8 checksum must match the file.

6. Snapshot the desired test target (registry `test_file`, on 1.0.0.73:
   `sdfs.txt`, 16 bytes) so the harmless-write step has a ground truth.

All of the above uses only read commands (`AT GETVERSION`, `mdw`, `sdfs`,
`snandr`).

## 2. Harmless write (registry-selected non-critical test target)

Purpose: prove the write path end-to-end without risking the ringtone.

On 1.0.0.73 the registered test target is `sdfs.txt` (16 bytes at a static
offset).  Write the exact stock bytes back, and verify the round trip:

1. `sdfs sdfs.txt 16` → confirm current content (expected `1234567890`).
2. Stage the same 16 bytes to the staging buffer (`mwb`/`mww`).
3. `snandw (PBASE + test_file_offset) 0x200` (sector window covering it).
4. `sdfs sdfs.txt 16` → bytes match the pre-write content.

If (1) does not match the registered expectation, **abort**.  If (4) fails,
restore is N/A (content unchanged), but do not proceed to the ringtone step.

## 3. Real ringtone replacement

Order of operations (each step must succeed before the next):

```text
audio → ACT (act_encode) → XOR form (act_decode.obfuscate)
→ backup (sdfs, saved to disk)
→ safety preflight (size, XOR header, current-content match)
→ stage data (mwb/mww into the staging buffer)
→ write ring1 window (snandw)
→ write patched table sector (checksum repair)
→ verify (snandr read-back, byte-for-byte)
→ ringtone test (see below)
```

For a scripted run, `act_emu/cmf_flash_plan.py original.bin custom.act`
generates the exact command lists plus a printed plan.  The same planner is
what the mocked transport tests replay, so the generated commands are
deterministic.

Requirements the manager enforces before any write:

- firmware is in the registry (otherwise the exact message above; abort)
- custom content size == registry slot size
- custom content is the on-flash (XOR) form, header `b6 f9`
- a backup was taken and the on-flash head matches the last confirmed content

## 4. Ringtone test trigger (experimental status: **unsupported/unknown**)

We have _not_ found a guaranteed shell command that plays a ringtone.
Evidence candidates referenced by firmware handles (`jx_ring_player`,
`btcall_ring`) are listed in `act_emu/REPORT.md` — they correspond to
incoming-call flows, not a standalone "play" command.

Until a verified trigger exists, the only user-level check is placing a real
incoming call (or using the watch's built-in ringtone selector if firmware
exposes one) and listening.  Tests must document whatever is observed;
nobody should claim playback support that has not been demonstrated.

## 5. Restore

1. Write the backup obtained in step 1 back using the same pipeline
   (`RingtoneManager.restore`, or replay with the backup as input).
2. `snandr` read-back must equal the backup byte-for-byte.
3. If a previous write was interrupted mid-window, restore skips the
   current-content preflight — that's exactly the state it repairs.

### Failure handling

| Failure                | Handling                                                        |
|------------------------|-----------------------------------------------------------------|
| Connection loss         | Write is aborted at the failing command; retry or run restore.  |
| Interrupted transfer    | Only the currently written window/sector can be partial; restore rewrites both deterministically. |
| Verification failure    | Post-write read-back mismatch → run restore immediately.        |
| Checksum mismatch       | Table-sector write precedes any acceptance; re-run the table step or restore. |
| Unsupported firmware    | Refused before any write with the exact message above.          |

## Non-goals

No GUI, no EXE, no installer yet — this procedure is the validation gate
before any of that is packaged.
