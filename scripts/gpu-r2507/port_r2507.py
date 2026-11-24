#!/usr/bin/env python3
"""Finish the ios26 target (0093) and the zeus plug-in changes (0005) on the R2507 tree. usage: port_r2507.py UNIXNU-r2507"""
import re,sys
from pathlib import Path
r=Path(sys.argv[1])
def ed(name,subs):
    p=r/name;s=p.read_text()
    for a,b in subs:
        if s.count(a)<1:raise SystemExit(f'{name}: no match {a[:80]}')
        if s.count(a)>1:raise SystemExit(f'{name}: many {a[:80]}')
        s=s.replace(a,b)
    p.write_text(s)
ed('scripts/build-adreno-metal-plugin.py',[
 ("choices=('ios', 'ios27', 'macos27')","choices=('ios', 'ios26', 'ios27', 'macos27')"),
 ("IOS27 = args.target in ('ios27', 'macos27')","IOS27 = args.target in ('ios26', 'ios27', 'macos27')"),
 ("{'ios': 'adreno-metal-plugin', 'ios27'","{'ios': 'adreno-metal-plugin', 'ios26': 'adreno-metal-plugin26', 'ios27'"),
])
ed('scripts/build-adreno-metal-bridge.py',[
 ("choices=['ios', 'ios27', 'macos27', 'host']","choices=['ios', 'ios26', 'ios27', 'macos27', 'host']"),
 ("triple = {'ios': 'aarch64-apple-ios', 'ios27'","triple = {'ios': 'aarch64-apple-ios', 'ios26': 'arm64e-apple-ios', 'ios27'"),
 ("ARM64E = args.target in ('ios27', 'macos27')","ARM64E = args.target in ('ios26', 'ios27', 'macos27')"),
])
s=(r/'scripts/build-adreno-metal-bridge.py').read_text()
m=re.findall(r"IPHONEOS_DEPLOYMENT_TARGET='27\.0' if args\.target == 'ios27' else '17\.0'",s)
print('deploy',len(m))
ed('scripts/build-adreno-metal-bridge.py',[(m[0],"IPHONEOS_DEPLOYMENT_TARGET={'ios26': '26.0', 'ios27': '27.0'}.get(args.target, '17.0')")])
ed('scripts/build-boot-runtime.py',[("{'ios27': 'adreno-metal-plugin27', 'macos27': 'adreno-metal-plugin-macos27'}.get(","{'ios26': 'adreno-metal-plugin26', 'ios27': 'adreno-metal-plugin27', 'macos27': 'adreno-metal-plugin-macos27'}.get(")])
