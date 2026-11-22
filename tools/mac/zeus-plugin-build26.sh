#!/bin/bash
# zeus iOS 26.5.2 (23F84): build the NativeAdrenoMetal plug-in (Mesa Turnip + AdrenoMetal) for the ios26 target
# (patches/unixnu/0093, arm64e, deployment 26.0), then pack the GPU add-on (~/zeus-build/gpuaddon26.bin).
# Run on the Mac after ~/zeus-build26.sh (its UniXNU checkout, with patches/unixnu applied). Copy to ~/ and run from there.
set -euo pipefail
. ~/zeus-tools/env.sh
export DEVELOPER_DIR=~/Applications/Xcode.app/Contents/Developer ZERO_AR_DATE=1
cd ~/zeus-build/UniXNU
git -C ~/zeus-build/device fetch -q origin ios26 && git -C ~/zeus-build/device checkout -q --detach FETCH_HEAD
echo "device @ $(git -C ~/zeus-build/device rev-parse --short HEAD)"
# The ios26 target: in the checkout once a full build has applied patches/unixnu; applied here otherwise.
P=~/zeus-build/device/patches/unixnu/0093-adreno-metal-ios26-target.patch
if git apply --reverse --check "$P" 2>/dev/null; then echo "0093 already applied"; else git apply "$P"; echo "applying 0093"; fi
# Device-side AdrenoMetal plug-in patches (patches/adrenometal), on a clean submodule checkout.
git -C drivers/AdrenoMetal reset -q --hard
for p in ~/zeus-build/device/patches/adrenometal/*.patch; do [ -e "$p" ] || continue; echo "applying AdrenoMetal $(basename "$p")"; git -C drivers/AdrenoMetal apply "$p"; done
# Device-side Mesa patches (patches/mesa): recorded in the Mesa pin list for this build only (restored on exit, the build's
# patch-identity check needs every Mesa change listed) and applied to the Mesa checkout once.
PINS=scripts/data/adreno-compiler-inputs.json
cp "$PINS" /tmp/adreno-compiler-inputs.json.orig
trap 'cp /tmp/adreno-compiler-inputs.json.orig ~/zeus-build/UniXNU/scripts/data/adreno-compiler-inputs.json' EXIT
for p in ~/zeus-build/device/patches/mesa/*.patch; do
  [ -e "$p" ] || continue
  dst=patches/mesa/zeus-$(basename "$p"); cp "$p" "$dst"
  python3 -c 'import json,sys
p,e=sys.argv[1],sys.argv[2]; d=json.load(open(p))
if e not in d["patches"]: d["patches"].append(e)
json.dump(d,open(p,"w"),indent=2)' "$PINS" "$dst"
  if git -C .cache/mesa-work apply --reverse --check "$PWD/$dst" 2>/dev/null; then echo "Mesa $(basename "$p") already applied"
  else git -C .cache/mesa-work apply "$PWD/$dst"; echo "applying Mesa $(basename "$p")"; fi
done
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
ADRENO_PLUGIN_DIR=build/adreno-metal-plugin26 python3 scripts/make-gpu-addon.py --publish --no-daemon ~/zeus-build/gpuaddon26.bin
ls -l ~/zeus-build/gpuaddon26.bin; shasum -a 256 ~/zeus-build/gpuaddon26.bin
echo ZEUS_PLUGIN_OK
