#!/usr/bin/env bash
# Build the zeus firmware device tree (the one appended to boot.img).
#
# LineageOS has no zeus overlay source, so the authoritative input is the live
# FDT ABL handed to Android on the phone (/sys/firmware/fdt, dumped from TWRP on
# 2026-11-16, HyperOS OS3.0.3.0.VLBINXM): dts/live/zeus-live.dtb.
# The Linux-only /chosen properties (bootargs, initrd) are removed; ABL writes
# its own /chosen when it boots our image.
#
# Output: build/zeus/zeus.dtb (git-ignored).
set -euo pipefail
DEVICE=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SRC=${UNIXNU_ZEUS_LIVE_FDT:-$DEVICE/dts/live/zeus-live.dtb}
OUT=$DEVICE/build/zeus
WORK=$DEVICE/.cache/dtb-work

need() { command -v "$1" >/dev/null || { echo "missing tool: $1" >&2; exit 1; }; }
need dtc
need fdtput

[ -f "$SRC" ] || { echo "live FDT not found: $SRC" >&2; exit 1; }
mkdir -p "$OUT" "$WORK"
cp "$SRC" "$OUT/zeus.dtb"

for prop in bootargs linux,initrd-start linux,initrd-end kaslr-seed; do
  fdtput -d "$OUT/zeus.dtb" /chosen "$prop" 2>/dev/null || true
done

echo "== verify"
dtc -I dtb -O dts "$OUT/zeus.dtb" > "$WORK/zeus.dts" 2>/dev/null
grep -q 'Zeus based on Qualcomm' "$WORK/zeus.dts" || { echo "not a zeus tree" >&2; exit 1; }
for needle in cs35l41 fts@0 splash_region; do
  grep -q "$needle" "$WORK/zeus.dts" || { echo "missing node: $needle" >&2; exit 1; }
done
echo "zeus.dtb: $(stat -c%s "$OUT/zeus.dtb" 2>/dev/null || stat -f%z "$OUT/zeus.dtb") bytes, sha256 $(shasum -a 256 "$OUT/zeus.dtb" | cut -d' ' -f1)"
