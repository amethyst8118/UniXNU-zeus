#!/usr/bin/env python3
"""zeus: derive tools/build-zeus-native-metal.py from ios-sweet's tools/build-sweet-native-metal.py (run on the Mac, in the ios-sweet tree).

Changes, each checked against its context:
- arm64e-apple-ios26.0 (iOS 26 system daemons are arm64e), MinimumOSVersion 26.0;
- the kernel ABI header is zeus's (UniXNU NativeAdreno ABI2, ZEUS_ABI_HEADER), not ios-sweet's reconstructed ABI1;
- after the transport is copied into Mesa, zeus_transport.py adapts it to ABI2 / A730;
- output ZeusNativeMetal.bundle under userspace/out-zeus; the SweetMetal2 legacy alias is not built.
usage: mk_zeus_build.py SWEET_ROOT ZEUS_TRANSPORT_PY"""
import sys
from pathlib import Path

root, transport = Path(sys.argv[1]), Path(sys.argv[2])
s = (root / 'tools/build-sweet-native-metal.py').read_text()


def rep(old, new, count=1):
    global s
    n = s.count(old)
    if n != count:
        raise SystemExit(f'mk_zeus_build: expected {count}, found {n}: {old[:90]!r}')
    s = s.replace(old, new)


rep("WORK=ROOT/'research/sweet-native-metal-build'", "WORK=ROOT/'research/zeus-native-metal-build'")
rep("    spec=importlib.util.spec_from_file_location('sweet_abi',ROOT/'tools/build-sweet-metal2.py')\n"
    "    abi=importlib.util.module_from_spec(spec);spec.loader.exec_module(abi);abi.WORK=WORK\n"
    "    header=abi.kernel_abi_header()\n",
    "    header=Path(os.environ['ZEUS_ABI_HEADER'])   # zeus: UniXNU NativeAdreno ABI2\n")
rep("    drm=mesa/'src/freedreno/drm/meson.build'\n",
    "    run(sys.executable,%r,mesa)   # zeus: ABI1 -> ABI2 / A730\n    drm=mesa/'src/freedreno/drm/meson.build'\n" % str(transport))
rep("flags=['-target','arm64-apple-ios16.0',", "flags=['-target','arm64e-apple-ios26.0',")
rep("cpu = 'arm64'", "cpu = 'arm64e'")
rep("out=ROOT/'userspace/out';", "out=ROOT/'userspace/out-zeus';")
rep("bundle=out/'SweetNativeMetal.bundle'", "bundle=out/'ZeusNativeMetal.bundle'")
rep("executable=bundle/'SweetNativeMetal'", "executable=bundle/'ZeusNativeMetal'")
rep("CFBundleIdentifier='org.sweet.SweetNativeMetal',\n      CFBundleExecutable='SweetNativeMetal'",
    "CFBundleIdentifier='org.zeus.ZeusNativeMetal',\n      CFBundleExecutable='ZeusNativeMetal'")
rep("MinimumOSVersion='16.0'", "MinimumOSVersion='26.0'", count=2)
# Stop after the main bundle's report: drop the legacy SweetMetal2 alias package.
cut = s.index("    # Older kernels select this public class/name.")
end = s.index("\n", s.index("Built ", cut)) if "Built " in s[cut:] else None
tail = s[cut:]
main_end = tail.find("\nif __name__")
s = s[:cut] + "    print('Built',bundle,'(zeus)',flush=True)\n" + tail[main_end:]
out = root / 'tools/build-zeus-native-metal.py'
out.write_text(s)
print('wrote', out)
