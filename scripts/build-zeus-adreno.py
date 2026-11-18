#!/usr/bin/env python3
"""zeus: build sheng's Adreno740 driver core and its kits into build/adreno740/libAdreno740.a.

Modelled on build-adreno740-kits.py (UniXNU-sheng, BSD-2-Clause-Patent). zeus leaves out the A740 parts that do not apply to its
Adreno 730: NativeAdrenoService.cpp and the ShengGmu platform files. package/ZeusGpuProbe.cpp owns GMU and GX start. The archive holds
no firmware. CONFIG_XNU_NATIVE_ADRENO force-loads it into the kernel; nothing in it starts the GPU by itself.
"""
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'build/adreno740'
OUT.mkdir(parents=True, exist_ok=True)
DRIVER = ROOT / 'drivers/Adreno740'
KITS = ROOT / 'kits'
SKIP = {'NativeAdrenoService.cpp', 'NativeAdrenoClient.cpp', 'NativeAdrenoAccelerator.cpp', 'NativeAdrenoGmuConfig.cpp',
        'NativeAdrenoGmuPlatform.cpp', 'NativeAdrenoKernelTime.cpp', 'NativeAdrenoGmuService.cpp', 'NativeAdrenoSmmuService.cpp'}
RECIPES = ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837'
c_recipe = json.loads((RECIPES / 'bsd/DEVELOPMENT/bsd_init.o.json').read_text().strip().rstrip(','))
cxx_recipe = json.loads((RECIPES / 'iokit/DEVELOPMENT/IOPlatformExpert.cpo.json').read_text().strip().rstrip(','))
sources = [p for p in sorted(DRIVER.glob('NativeAdreno*.cpp')) if p.name not in SKIP]
sources += sorted((KITS / 'ArmSmmu4K').glob('ArmSmmu4K*.c')) + [KITS / 'AdrenoPM4/AdrenoPM4.c']
sources += [KITS / 'AdrenoFirmware' / name for name in ('AdrenoHfi.c', 'AdrenoHfiStartup.c', 'AdrenoHfiVotes.c', 'AdrenoFirmware.c')]
includes = [KITS / 'sheng', KITS / 'ArmSmmu4K', KITS / 'AdrenoPM4', KITS / 'AdrenoFirmware', DRIVER, OUT / 'include']
(OUT / 'include').mkdir(exist_ok=True)


def compile_one(source):
    recipe = c_recipe if source.suffix == '.c' else cxx_recipe
    obj = OUT / (source.stem + ('.kit.o' if source.suffix == '.c' else '.o'))
    command, args, i = [], recipe['arguments'], 0
    while i < len(args):
        value = args[i]
        if value == recipe['file']:
            command.append(str(source))
        elif value == '-o':
            command += ['-o', str(obj)]
            i += 1
        elif value in ('-MF', '-MJ', '-MT'):
            i += 1
        elif value == '-Xclang' and args[i + 1].startswith('-W'):
            i += 1
        elif not value.startswith('-W') and value not in ('-MD', '-MP'):
            command.append(value)
        i += 1
    for include in includes:
        command += ['-I', str(include)]
    command += ['-DNATIVE_GPU_BUILD_ID=0x7a657573ULL', '-w']
    result = subprocess.run(command, cwd=recipe['directory'], capture_output=True, text=True)
    if result.returncode:
        raise SystemExit(f'{source}:\n{result.stderr[-2500:]}')
    return obj


objects = [compile_one(source) for source in sources]
# The display's fence wait must keep working while no GPU is published, so ZeusDisplayStubs wraps these two (see the display builder).
llvm = Path(shutil.which('llvm-objcopy') or '/opt/homebrew/opt/llvm/bin/llvm-objcopy').parent
publish = OUT / 'NativeAdrenoPublish.o'
subprocess.run([llvm / 'llvm-objcopy', '--redefine-sym', '_native_adreno_fences=_native_adreno_fences_driver',
                '--redefine-sym', '_native_adreno_fences_detail=_native_adreno_fences_detail_driver',
                # The driver stays inert at boot: its classes register only when the GPU start asks for them.
                '--remove-section=__DATA,__mod_init_func', '--globalize-symbol=__GLOBAL__sub_I_NativeAdrenoPublish.cpp', publish], check=True)
archive = OUT / 'libAdreno740.a'
archive.unlink(missing_ok=True)
subprocess.run(['xcrun', 'ar', 'rcs', archive, *objects], check=True)
print(f'zeus Adreno740 core: {len(objects)} objects in {archive.relative_to(ROOT)}')
