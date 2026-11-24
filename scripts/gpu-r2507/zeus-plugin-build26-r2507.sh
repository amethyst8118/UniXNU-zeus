#!/bin/bash
# zeus iOS 26.5.2 (23F84): build the NativeAdrenoMetal plug-in (Mesa Turnip + AdrenoMetal) for the ios26 target
# (patches/unixnu/0093, arm64e, deployment 26.0), then pack the GPU add-on (~/zeus-build/gpuaddon26-r2507.bin).
# Run on the Mac after ~/zeus-build26.sh (its UniXNU checkout, with patches/unixnu applied). Copy to ~/ and run from there.
set -euo pipefail
. ~/zeus-tools/env.sh
export DEVELOPER_DIR=~/Applications/Xcode.app/Contents/Developer ZERO_AR_DATE=1
cd ~/zeus-build/UniXNU-r2507
# Device-side Mesa patches (patches/mesa): recorded in the Mesa pin list for this build only (restored on exit, the build's
# patch-identity check needs every Mesa change listed) and applied to the Mesa checkout once.
PINS=scripts/data/adreno-compiler-inputs.json
cp "$PINS" /tmp/adreno-compiler-inputs-r2507.json.orig
trap 'cp /tmp/adreno-compiler-inputs-r2507.json.orig ~/zeus-build/UniXNU-r2507/scripts/data/adreno-compiler-inputs.json' EXIT
# The Mesa patches share hunks (both edit tu_cmd_buffer.cc:1271), so "already applied" is checked for the whole set, in reverse order.
MESA=()
for p in ~/zeus-build/device/patches/mesa/*.patch; do
  [ -e "$p" ] || continue
  dst=patches/mesa/zeus-$(basename "$p"); cp "$p" "$dst"; MESA+=("$PWD/$dst")
  python3 -c 'import json,sys
p,e=sys.argv[1],sys.argv[2]; d=json.load(open(p))
if e not in d["patches"]: d["patches"].append(e)
json.dump(d,open(p,"w"),indent=2)' "$PINS" "$dst"
done
if [ ${#MESA[@]} -gt 0 ]; then
  REV=(); for ((i=${#MESA[@]}-1; i>=0; i--)); do REV+=("${MESA[$i]}"); done
  # git apply checks each patch against the tree as it is, so the set is reversed one by one on a scratch copy of its files.
  S=$(mktemp -d)
  for f in $(cat "${MESA[@]}" | sed -n 's#^+++ b/##p' | sort -u); do mkdir -p "$S/$(dirname "$f")"; cp ".cache/mesa-work/$f" "$S/$f"; done
  applied=1; for p in "${REV[@]}"; do (cd "$S" && patch -s -R -p1 --dry-run < "$p" >/dev/null && patch -s -R -p1 < "$p" >/dev/null) || { applied=0; break; }; done
  rm -rf "$S"
  if [ $applied = 1 ]; then echo "Mesa patches already applied"
  else git -C .cache/mesa-work apply "${MESA[@]}"; echo "applying Mesa patches"; fi
fi
python3 - <<PY
import sys; sys.path.insert(0, "scripts")
import importlib.util
spec = importlib.util.spec_from_file_location("bbr", "scripts/build-boot-runtime.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
print("PLUGIN", m.build_plugin("ios26"), flush=True)
PY
B=build/adreno-metal-plugin26/NativeAdrenoMetal.bundle
xcrun vtool -show-build "$B/NativeAdrenoMetal" 2>/dev/null | grep -E "platform|minos" | head -2 || true
/usr/libexec/PlistBuddy -c 'Print :MinimumOSVersion' "$B/Info.plist"
ADRENO_PLUGIN_DIR=build/adreno-metal-plugin26 python3 scripts/make-gpu-addon.py --publish --no-daemon ~/zeus-build/gpuaddon26-r2507.bin
ls -l ~/zeus-build/gpuaddon26-r2507.bin; shasum -a 256 ~/zeus-build/gpuaddon26-r2507.bin
echo ZEUS_PLUGIN_OK
