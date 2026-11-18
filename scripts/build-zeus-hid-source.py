#!/usr/bin/env python3
"""Compile the owned IOHIDDevice (ShengHidInput/NativeHidInput.cpp) against the pinned public IOHIDFamily source for zeus.

A trimmed copy of UniXNU scripts/build-sheng-source-drivers.py: only the ShengHidInput component, no Novatek/S3910 touch
transport (zeus has its own ZeusTouchProbe) and so no touch-firmware dependency. The source-API build (-DNATIVE_HID_SOURCE_API=1)
leaves the IOHIDDevice vtable and symbols undefined; build-zeus-legacy-display-27.py binds them to the fileset's original
IOHIDDevice through the hid-device fixture. Produces build/zeus-hid/NativeHidInput.o.

The refer/IOHIDFamily and refer/IOStorageFamily checkouts are already at their pins (the tree's fetch step placed them); this
script does not re-pin them, so it needs no OS profile. UNIXNU_XNU_ARCH / UNIXNU_IOS_VERSION come from the kernel build env.

usage: build-zeus-hid-source.py --tree TREE --device DEVICE
"""
import argparse
import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_helper(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def head(source):
    return subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()


def compile_command(record, source, output, includes, extra):
    arguments = record['arguments']
    command = [arguments[0], *includes]
    index = 1
    replaced = False
    while index < len(arguments):
        value = arguments[index]
        if value == record['file']:
            command.append(str(source))
            replaced = True
        elif value == '-o':
            command.extend(['-o', str(output)])
            index += 1
        elif value in ('-MF', '-MT', '-MQ', '-MJ'):
            index += 1
        elif value in ('-MD', '-MMD', '-MP'):
            pass
        elif value == '-Xclang' and arguments[index + 1].startswith('-W'):
            index += 1
        elif not value.startswith('-W'):
            command.append(value)
        index += 1
    if not replaced:
        raise SystemExit('Kernel compilation record does not contain its source argument')
    command += ['-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-c99-designator', '-Wno-reorder-init-list',
                '-Wno-deprecated-enum-enum-conversion', '-Wno-unnecessary-virtual-specifier']
    command += ['-mgeneral-regs-only', *extra]
    return command


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tree', required=True, type=Path)
    parser.add_argument('--device', required=True, type=Path)
    args = parser.parse_args()
    tree, device = args.tree.resolve(), args.device.resolve()
    os.environ.setdefault('UNIXNU_XNU_ARCH', 'arm64e')
    os.environ.setdefault('UNIXNU_IOS_VERSION', '27.0')
    target = load_helper('zeus_hid_target', ROOT / 'scripts/kernel_target.py')
    headers = load_helper('zeus_hid_headers', ROOT / 'scripts/prepare-storage-headers.py')
    storage = (tree / 'refer/IOStorageFamily').resolve()
    hid = (tree / 'refer/IOHIDFamily').resolve()
    recipe = tree / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOPlatformExpert.cpo.json'
    record = json.loads(recipe.read_text().strip().rstrip(','))
    target.check_object((Path(record['directory']) / 'IOPlatformExpert.cpo').read_bytes(), 'Kernel compilation recipe')
    out = tree / 'build/zeus-hid'
    out.mkdir(parents=True, exist_ok=True)
    report = headers.prepare(storage, head(storage), out / 'headers')
    headers.pinned_source(hid, head(hid))
    include = out / 'headers/include'
    for source_dir, relative in ((hid / 'IOHIDFamily', 'IOKit/hid'), (hid / 'IOHIDSystem/IOKit/hidsystem', 'IOKit/hidsystem')):
        destination = include / relative
        destination.mkdir(parents=True, exist_ok=True)
        for path in sorted(source_dir.glob('*.h')):
            shutil.copyfile(path, destination / path.name)
    sdk = Path(report['sdk'])
    sdk_kernel = sdk / 'System/Library/Frameworks/Kernel.framework/Versions/A/Headers'
    includes = ['-I', str(include), '-idirafter', str(sdk_kernel)]
    extra = ['-I', str(tree / '.cache/xnu-work/osfmk'), '-DNATIVE_HID_SOURCE_API=1']
    source = device / 'drivers/ShengHidInput/NativeHidInput.cpp'
    output = out / 'NativeHidInput.o'
    output.unlink(missing_ok=True)
    command = compile_command(record, source, output, includes, extra)
    result = subprocess.run(command, cwd=record['directory'], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (out / 'NativeHidInput.log').write_text(result.stdout)
    if result.returncode:
        raise SystemExit(f'NativeHidInput.cpp: compile failed\n{result.stdout[-5000:]}')
    target.check_object(output.read_bytes(), 'NativeHidInput.cpp')
    undefined = subprocess.check_output(['xcrun', 'nm', '-j', '-u', str(output)], text=True).splitlines()
    print(f'zeus HID: compiled NativeHidInput.o ({target.TRIPLE}), {len(undefined)} undefined symbols to bind')


if __name__ == '__main__':
    main()
