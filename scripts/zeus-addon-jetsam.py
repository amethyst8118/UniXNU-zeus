#!/usr/bin/env python3
"""zeus: raise jetsam memory limits of OS daemons by shipping an edited jetsam profile in the GPU addon.

launchd reads /System/Library/LaunchDaemons/com.apple.jetsamproperties.<model>.plist (D94 for iPhone17,2). With gpu-addon=1 the kernel
shows the addon's daemons/ over /System/Library/LaunchDaemons as a read-only union (native_gpu_addon_mount, kern_exec.c), so a file of
the same name in daemons/ replaces the sealed one.

Why (2026-11-22, gpum8): with Metal loaded, iconservicesagent sits at 27-32 MB while 23F84's profile gives it InactiveHardMemoryLimit 15
(MB); jetsam killed it twice in 459 s and SpringBoard's synchronous icon XPC froze the UI (Settings). UniXNU-sheng's
reports/ipados27-touch-frame-gaps.md records the same stall.

The profile is Apple-derived: it is read from the mounted OS image and only ever exists in the private build directory and the payload.
usage (on the build Mac, from the UniXNU checkout):
  zeus-addon-jetsam.py IN.bin OS_JETSAM.plist OUT.bin LABEL=InactiveMB[/ActiveMB] [...]
"""
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    if len(sys.argv) < 5:
        sys.exit(__doc__)
    addon, source, out = (Path(a).resolve() for a in sys.argv[1:4])
    profile = plistlib.loads(source.read_bytes())
    overrides = profile['Version4']['Daemon']['Override']
    for spec in sys.argv[4:]:
        label, _, limits = spec.partition('=')
        inactive, _, active = limits.partition('/')
        entry = overrides.setdefault(label, {})
        before = dict(entry)
        entry['InactiveHardMemoryLimit'] = int(inactive)
        if active:
            entry['ActiveSoftMemoryLimit'] = int(active)
        print('jetsam:', label, before, '->', entry)
    with tempfile.TemporaryDirectory(prefix='zeus-jetsam-') as name:
        work = Path(name) / 'daemons'
        work.mkdir()
        (work / source.name).write_bytes(plistlib.dumps(profile, fmt=plistlib.FMT_BINARY))
        root = Path.cwd()
        subprocess.run([sys.executable, str(root / 'scripts/addon-add-dir.py'), str(addon), str(out), 'daemons', str(work)], check=True)
    print('jetsam: wrote', out)


if __name__ == '__main__':
    main()
