#!/usr/bin/env python3
"""zeus: build sheng's Adreno740 driver core and its kits into build/adreno740/libAdreno740.a.

Modelled on build-adreno740-kits.py (UniXNU-sheng, BSD-2-Clause-Patent). zeus leaves out the A740 parts that do not apply to its
Adreno 730: NativeAdrenoService.cpp and the ShengGmu platform files. package/ZeusGpuProbe.cpp owns GMU and GX start. The archive holds
no firmware. CONFIG_XNU_NATIVE_ADRENO force-loads it into the kernel; nothing in it starts the GPU by itself.
"""
import json
import os
import shutil
import subprocess
import sys
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


# Diagnostic: ZEUS_ADRENO_SET=none links only the symbols the display stubs need; =pad adds inert code and data of the driver's size,
# to separate the driver's content from the kernel layout change; =noaccel keeps the driver core without the staged accelerator;
# =nopublish also leaves out the publisher, submit and user-client objects; =noktv keeps them but hides the publisher's
# allocation-type records from the kernel's boot-time scan; =isolate keeps all driver data out of the kernel's shared data sections.
ADRENO_SET = os.environ.get('ZEUS_ADRENO_SET', 'all')
archive = OUT / 'libAdreno740.a'


def isolate_data(obj):
    # The driver's writable and constant data stays in its own __DATA sections. With it mixed into the kernel's shared sections, the
    # linked kernel panicked in the zone allocator on the phone (2026-11-19); the same objects with these sections separated booted.
    data = bytearray(obj.read_bytes())
    for old, new in ((b'__common', b'__zadr_common'), (b'__bss', b'__zadr_bss'), (b'__data', b'__zadr_data'), (b'__const', b'__zadr_const')):
        name = old.ljust(16, b'\0') + b'__DATA'.ljust(16, b'\0')
        at = data.find(name)
        while at >= 0:
            data[at:at + 16] = new.ljust(16, b'\0')
            at = data.find(name, at + 32)
    obj.write_bytes(data)


if '--finish' in sys.argv:
    if ADRENO_SET == 'all':
        late = ROOT / 'build/adreno740-accelerator/NativeAdrenoAccelerator.late.o'
        isolate_data(late)
        subprocess.run(['xcrun', 'ar', 'r', archive, late], check=True)
        print('zeus Adreno740: accelerator data isolated')
    if ADRENO_SET in ('none', 'pad', 'noaccel', 'nopublish', 'noktv', 'isolate'):
        subprocess.run(['xcrun', 'ar', 'd', archive, 'NativeAdrenoAccelerator.late.o', 'original-bindings.o', 'original-addresses.o'], check=True)
        print('zeus Adreno740 diagnostic: accelerator objects removed')
    raise SystemExit(0)
def diagnostic_stub():
    stub = OUT / 'ZeusAdrenoNone.c'
    stub.write_text('#include <stdbool.h>\n#include <stdint.h>\nvoid *native_adreno_published_service(void) {return 0;}\n'
                    'bool native_adreno_fences_driver(uint32_t *a, uint32_t *b) {return false;}\n'
                    'bool native_adreno_fences_detail_driver(uint32_t *a, uint32_t *b, uint32_t *c) {return false;}\n'
                    'int native_gpu_trace_enabled(void) {return 0;}\n'
                    'void native_gpu_trace_record(uint32_t e, uint32_t p, const uint64_t *v, uint32_t c) {}\n' +
                    ('__asm__(".text\\n.p2align 2\\n.globl _zeus_adreno_pad_text\\n_zeus_adreno_pad_text:\\n.fill 47800,4,0xd503201f\\n");\n'
                     '__attribute__((used)) char zeus_adreno_pad_common[21504];\n'
                     '__attribute__((used)) static char zeus_adreno_pad_bss[16828];\n'
                     '__attribute__((used)) char zeus_adreno_pad_data[296] = {1};\n' if ADRENO_SET == 'pad' else ''))
    return compile_one(stub)


if ADRENO_SET in ('none', 'pad'):
    obj = diagnostic_stub()
    archive.unlink(missing_ok=True)
    subprocess.run(['xcrun', 'ar', 'rcs', archive, obj], check=True)
    print('zeus Adreno740 diagnostic: no driver objects')
    raise SystemExit(0)
if ADRENO_SET == 'nopublish':
    sources = [p for p in sources if p.name not in ('NativeAdrenoPublish.cpp', 'NativeAdrenoSubmit.cpp', 'NativeAdrenoUserClient.cpp')]
objects = [compile_one(source) for source in sources]
if ADRENO_SET == 'nopublish':
    objects.append(diagnostic_stub())
if ADRENO_SET != 'nopublish':
    # The display's fence wait must keep working while no GPU is published, so ZeusDisplayStubs wraps these two (see the display builder).
    llvm = Path(shutil.which('llvm-objcopy') or '/opt/homebrew/opt/llvm/bin/llvm-objcopy').parent
    publish = OUT / 'NativeAdrenoPublish.o'
    subprocess.run([llvm / 'llvm-objcopy', '--redefine-sym', '_native_adreno_fences=_native_adreno_fences_driver',
                    '--redefine-sym', '_native_adreno_fences_detail=_native_adreno_fences_detail_driver',
                    # The driver stays inert at boot: its classes register only when the GPU start asks for them.
                    '--remove-section=__DATA,__mod_init_func', '--globalize-symbol=__GLOBAL__sub_I_NativeAdrenoPublish.cpp', publish], check=True)
if ADRENO_SET in ('all', 'isolate'):
    for obj in objects:
        isolate_data(obj)
if ADRENO_SET == 'noktv':
    data = bytearray(publish.read_bytes())
    for old, new in ((b'__kalloc_type', b'__zeus_ktype'), (b'__kalloc_var', b'__zeus_kvar')):
        name = old.ljust(16, b'\0') + b'__DATA_CONST'.ljust(16, b'\0')
        at = data.find(name)
        if at < 0 or data.find(name, at + 1) >= 0:
            raise SystemExit(f'expected one {old!r} section in NativeAdrenoPublish.o')
        data[at:at + 16] = new.ljust(16, b'\0')
    publish.write_bytes(data)
archive.unlink(missing_ok=True)
subprocess.run(['xcrun', 'ar', 'rcs', archive, *objects], check=True)
print(f'zeus Adreno740 core: {len(objects)} objects in {archive.relative_to(ROOT)}')
