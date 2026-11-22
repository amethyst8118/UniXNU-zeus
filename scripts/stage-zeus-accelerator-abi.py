#!/usr/bin/env python3
"""Stage the IOAcceleratorES ABI records of the iPhone17,2 kernelcaches before build-adreno740-accelerator.py runs.

build-adreno740-accelerator.py reads build/gpu-analysis/ioaccelerator-base-abi-<BUILD>.json (patches/unixnu/0092: BUILD 23F84 on
iOS 26, 24A446 on 27). Records:
- ioaccelerator-base-abi-24A446-d94ap.json: from UniXNU-aston (MoyuZJ912/UniXNU-aston ios27 282c1d6, BSD-2-Clause-Patent).
- ioaccelerator-base-abi-23F84-d94ap.json: derived 2026-11-22 by tools/derive-ioaccelerator-fixture.py (checked against 24A446).
"""
import json
from pathlib import Path

KERNELCACHES = {
    '24A446': 'fa07057bf34f33af1ec26681ad0c87c13e5aaa548a300740eddb0e92876e5f95',
    '23F84': '2276364d028954a1152e721820f243f9400ce5d4b13c6d57f021f010afd1eabf',
}
here = Path(__file__).resolve().parent
target_dir = Path.cwd() / 'build/gpu-analysis'
target_dir.mkdir(parents=True, exist_ok=True)
for build, kernelcache in KERNELCACHES.items():
    source = here / f'ioaccelerator-base-abi-{build}-d94ap.json'
    record = json.loads(source.read_text())
    if record['kernelcache_sha256'] != kernelcache:
        raise SystemExit(f'{source.name} does not name the zeus {build} kernelcache')
    target = target_dir / f'ioaccelerator-base-abi-{build}.json'
    target.write_text(json.dumps(record, indent=2) + '\n')
    print(f'staged the iPhone17,2 {build} accelerator ABI as {target}')
