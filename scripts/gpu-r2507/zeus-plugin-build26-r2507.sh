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
# Each device patch is applied once: a patch that still applies forward is applied, one that does not is taken as applied (the
# build's patch-identity check, validate_recorded_patches, refuses a tree that differs from the pin plus every listed patch). Patches
# share hunks (tu_cmd_buffer.cc:1271), so no set-level reverse check. A device patch upstream's pin list already names is skipped.
MESA=()
for p in ~/zeus-build/device/patches/mesa/*.patch; do
  [ -e "$p" ] || continue
  if python3 -c 'import json,sys; sys.exit(0 if "patches/mesa/"+sys.argv[2] in json.load(open(sys.argv[1]))["patches"] else 1)' "$PINS" "$(basename "$p")"; then
    echo "Mesa $(basename "$p"): in upstream's pin list, skipped"; continue
  fi
  dst=patches/mesa/zeus-$(basename "$p"); cp "$p" "$dst"; MESA+=("$PWD/$dst")
  python3 -c 'import json,sys
p,e=sys.argv[1],sys.argv[2]; d=json.load(open(p))
if e not in d["patches"]: d["patches"].append(e)
json.dump(d,open(p,"w"),indent=2)' "$PINS" "$dst"
  if git -C .cache/mesa-work apply --check "$PWD/$dst" 2>/dev/null; then git -C .cache/mesa-work apply "$PWD/$dst"; echo "applying Mesa $(basename "$p")"
  else echo "Mesa $(basename "$p") already applied"; fi
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
ADRENO_PLUGIN_DIR=build/adreno-metal-plugin26 python3 scripts/make-gpu-addon.py --publish --no-daemon ~/zeus-build/gpuaddon26-r2507.bin
ls -l ~/zeus-build/gpuaddon26-r2507.bin; shasum -a 256 ~/zeus-build/gpuaddon26-r2507.bin
echo ZEUS_PLUGIN_OK
