#!/usr/bin/env python3
"""Stage the IOAcceleratorES ABI of the iPhone17,2 24A446 kernelcache before build-adreno740-accelerator.py runs.

The record comes from UniXNU-aston (MoyuZJ912/UniXNU-aston ios27 282c1d6, BSD-2-Clause-Patent), which boots the same kernelcache.
"""
import json
from pathlib import Path

KERNELCACHE = 'fa07057bf34f33af1ec26681ad0c87c13e5aaa548a300740eddb0e92876e5f95'
record = json.loads(Path(__file__).resolve().with_name('ioaccelerator-base-abi-24A446-d94ap.json').read_text())
if record['kernelcache_sha256'] != KERNELCACHE:
    raise SystemExit('The accelerator ABI record does not name the zeus kernelcache')
target = Path.cwd() / 'build/gpu-analysis/ioaccelerator-base-abi-24A446.json'
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(record, indent=2) + '\n')
print(f'staged the iPhone17,2 accelerator ABI as {target}')
