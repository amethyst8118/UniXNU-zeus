#!/usr/bin/env python3
"""zeus: add the GPU probe daemon (adrenoprobed: the unified-log export, drivers/AdrenoMetal/NativeOsLogExport.mm) to a GPU addon as a
launchd job iOS 27 actually starts.

iOS 27's launchd imports its jobs from the precompiled /System/Library/xpc/launchd.plist; the addon's daemons/*.plist are never read.
This merges one owner job (org.unixnu.adrenoprobed, /usr/libexec/adrenoprobed) into the OS image's cache with UniXNU's own
merge_launchd_cache / sign_launchd_cache (scripts/build-boot-runtime.py: every OS entry kept, detached ad-hoc signature that the owner
trust bundle covers), and adds xpc/ and libexec/ to the addon; patches/xnu-12377/zeus-launchd-cache-overlay.patch shows them over
/System/Library/xpc and /usr/libexec.

The merged cache is Apple-derived: it only ever exists in the private build directory and the payload, never in a repository.
usage (on the build Mac, from the UniXNU checkout): zeus-addon-launchd.py IN.bin OS_LAUNCHD.plist PLUGIN_DIR OUT.bin
"""
import importlib.util
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    addon, os_cache, plugin, out = (Path(a).resolve() for a in sys.argv[1:])
    root = Path.cwd()
    spec = importlib.util.spec_from_file_location('bbr', root / 'scripts/build-boot-runtime.py')
    bbr = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(root / 'scripts'))
    spec.loader.exec_module(bbr)
    with tempfile.TemporaryDirectory(prefix='zeus-launchd-') as name:
        work = Path(name)
        (work / 'daemons').mkdir()
        job = {
            'Label': 'org.unixnu.adrenoprobed',
            'ProgramArguments': ['/usr/libexec/adrenoprobed'],
            'RunAtLoad': True,
            'KeepAlive': False,
            'UserName': 'root',
            # As sheng's working Wi-Fi job (scripts/build-wifi-userspace.py): ProcessType, not the cache-internal POSIXSpawnType.
            'ProcessType': 'Interactive',
            'StandardErrorPath': '/dev/null',
        }
        (work / 'daemons/org.unixnu.adrenoprobed.plist').write_bytes(plistlib.dumps(job))
        report = bbr.merge_launchd_cache(os_cache, work / 'daemons', work / 'xpc/launchd.plist')
        report.update(bbr.sign_launchd_cache(work / 'xpc/launchd.plist'))
        (work / 'libexec').mkdir()
        shutil.copy2(plugin / 'adrenoprobed', work / 'libexec/adrenoprobed')
        subprocess.run([sys.executable, str(root / 'scripts/addon-add-dir.py'), str(addon), str(out),
                        'xpc', str(work / 'xpc'), 'libexec', str(work / 'libexec')], check=True)
        print('launchd cache: %d OS jobs kept, added %s, cdhash %s' % (report['original_jobs'], report['added_jobs'], report['cdhash']))


if __name__ == '__main__':
    main()
