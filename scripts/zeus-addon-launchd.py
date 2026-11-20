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
import os
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
        # Test only: ZEUS_LAUNCHD_DROP=label[,label…] removes those OS jobs from the merged cache, to see whether launchd runs from this
        # cache at all (a dropped daemon that still runs means launchd uses another copy).
        # boot-dropcam (2026-11-20) proved launchd runs from this cache (cameracaptured gone) while it still never exec'd the added job.
        # ZEUS_LAUNCHD_REPLACE=label runs /usr/libexec/adrenoprobed in place of that OS job's program (the job keeps its other keys), so
        # the daemon starts the way launchd already starts that job.
        drop = [label for label in os.environ.get('ZEUS_LAUNCHD_DROP', '').split(',') if label]
        replace = os.environ.get('ZEUS_LAUNCHD_REPLACE', '')
        if drop or replace:
            cache = work / 'xpc/launchd.plist'
            merged = plistlib.loads(cache.read_bytes())
            jobs = merged['LaunchDaemons']
            gone = [key for key, job in jobs.items() if job.get('Label') in drop]
            # A label may own more than one job (com.apple.CommCenter: CommCenter.plist and CommCenter-noncellular.plist); all go.
            if {jobs[key].get('Label') for key in gone} != set(drop):
                sys.exit('ZEUS_LAUNCHD_DROP: not every label is an OS job')
            for key in gone:
                del jobs[key]
            if replace:
                hosts = [job for job in jobs.values() if job.get('Label') == replace]
                if len(hosts) != 1:
                    sys.exit('ZEUS_LAUNCHD_REPLACE: no single OS job ' + replace)
                hosts[0].pop('Program', None)
                hosts[0]['ProgramArguments'] = ['/usr/libexec/adrenoprobed']
            cache.write_bytes(plistlib.dumps(merged, fmt=plistlib.FMT_XML, sort_keys=False))
            subprocess.run(['plutil', '-convert', 'binary1', str(cache)], check=True)
            print('launchd cache: dropped', gone, 'replaced', replace or '-')
        report.update(bbr.sign_launchd_cache(work / 'xpc/launchd.plist'))
        (work / 'libexec').mkdir()
        shutil.copy2(plugin / 'adrenoprobed', work / 'libexec/adrenoprobed')
        # The job's own plist goes to daemons/ too (shown over /System/Library/LaunchDaemons by the kernel's add-on daemon mount): the
        # cache keys the job by that path, and launchd ran its cache loader but never started a job whose plist path does not exist
        # (boot-trace1 2026-11-20: [launch trace] adrenoprobed 0/0, launchd_cache_loader 1/1). sheng's Wi-Fi add-on ships its plist there.
        subprocess.run([sys.executable, str(root / 'scripts/addon-add-dir.py'), str(addon), str(out),
                        'xpc', str(work / 'xpc'), 'libexec', str(work / 'libexec'), 'daemons', str(work / 'daemons')], check=True)
        print('launchd cache: %d OS jobs kept, added %s, cdhash %s' % (report['original_jobs'], report['added_jobs'], report['cdhash']))


if __name__ == '__main__':
    main()
