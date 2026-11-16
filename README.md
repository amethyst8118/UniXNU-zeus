# UniXNU zeus — iOS 27 on the Xiaomi 12 Pro

> **Work in progress.** The iOS 26 kernel already reached the kext stage on zeus
> (branch `main`); this branch moves to iOS 27 to reuse cupid's much larger
> prelink fixture. Nothing reaches userland yet.

UniXNU device repository for the **Xiaomi 12 Pro** (`zeus`, Qualcomm **SM8450**,
Adreno 730), targeting **iOS 27.0.1 (24A446, arm64e)** with the iPhone17,2 / D94AP
identity.

## Origin

Branch `ios27` is [obaosp/UniXNU-cupid](https://github.com/obaosp/UniXNU-cupid)'s iOS 27
line (from `ab8d7e9`, 2026-11-15: 158 reviewed kernel bindings + 14 data bindings) with the zeus
board on top. Branch `main` keeps the earlier iOS 26 (23F84) work, which booted XNU on zeus to the
kext stage on 2026-11-16. cupid (Xiaomi 12) has the same SoC, the same Gunyah hypervisor and the same
ABL handoff. Everything under `profiles/`, `patches/`, `package/`, `tools/` and the
prelink fixtures is obaosp's work (BSD-2-Clause-Patent). `notes/` keeps cupid's
on-device findings (first UEFI boot, Gunyah PAC trap, the iOS 26 AppleImage4 panic),
which apply to zeus as well.

## What differs from cupid

| | cupid | zeus |
|---|---|---|
| Panel | 1080x2400 | **1440x3200** `l2_38_0c_0a` DSC command mode |
| Device tree | built from LineageOS sources | **live FDT** dumped from the phone (`dts/live/zeus-live.dtb`), `/chosen` stripped by `scripts/build-zeus-dtb.sh` |
| Amplifiers | 2x CS35L41 | 4x CS35L41 |
| UEFI | `uefi-cupid` | [`uefi-zeus`](https://github.com/amethyst8118/uefi-zeus) `zeusPkg` (1440x3200) |
| userdata start LBA | measured | **unverified** (cupid's value, see `board.toml`) |
| UFS root boot args | `native-source-storage=1 native-ufs-root=1` | **off** until an iOS root is written to userdata |
| Payload label | `CUPIDESP` | `ZEUSESP` (ramdisk patch 0002 searches for it) |

## CI

`.github/workflows/macos.yml` (GitHub `macos-26` runner, Xcode 26): installs the
iOS 27 profile, resolves the configuration, fetches the sources, builds the zeus DTB,
builds and prelinks the kernel, and uploads text logs. Later steps (package,
firmware, payload, boot image) need the `UNIXNU_UNIQUE_CHIP_ID` secret and are
expected to fail until it is set.

**Do not flash or `fastboot boot` CI images on the phone before they are reviewed**
(see the parent workspace's AGENTS.md).

Released under the BSD-2-Clause-Patent License, as upstream.
