# UniXNU zeus — iOS 26 on the Xiaomi 12 Pro

> **Work in progress — nothing boots on zeus yet.** Stage 0: reproduce the
> iOS 26 arm64e kernel build in CI with zeus's board identity.

UniXNU device repository for the **Xiaomi 12 Pro** (`zeus`, Qualcomm **SM8450**,
Adreno 730), targeting **iOS 26.5.2 (23F84, arm64e)** with the iPhone17,2 / D94AP
identity.

## Origin

This repository starts from [obaosp/UniXNU-cupid](https://github.com/obaosp/UniXNU-cupid)
at commit `2ddaf9b` (2026-11-10), the last commit of its iOS 26 line before it moved
to iOS 27. cupid (Xiaomi 12) has the same SoC, the same Gunyah hypervisor and the same
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
| UEFI | `uefi-cupid` | none yet; CI borrows `uefi-cupid` for packaging only |
| userdata start LBA | measured | **unverified** (cupid's value, see `board.toml`) |

## CI

`.github/workflows/macos.yml` (GitHub `macos-26` runner, Xcode 26): installs the
iOS 26 profile, resolves the configuration, fetches the sources, builds the zeus DTB,
builds and prelinks the kernel, and uploads text logs. Later steps (package,
firmware, payload, boot image) need the `UNIXNU_UNIQUE_CHIP_ID` secret and are
expected to fail until it is set.

**Do not flash or `fastboot boot` CI images on the phone before they are reviewed**
(see the parent workspace's AGENTS.md).

Released under the BSD-2-Clause-Patent License, as upstream.
