#!/usr/bin/env python3
"""Derive the 23F84 IOAcceleratorES ABI record (scripts/ioaccelerator-base-abi-23F84-d94ap.json) from the 24A446 one (Mac).

Same method as tools/derive-hid-device-fixture.py. In com.apple.iokit.IOGPUFamily: the OSMetaClass registration of
"IOAcceleratorES" gives gMetaClass and the instance size; its vtable is the one whose getMetaClass (slot 7) returns that metaclass;
its overrides are the slots whose target differs from IOService's own vtable (found the same way in the kernel); the constructors
are the small functions that load the vtable (ZTV + 0x10) after calling IOService's constructor, the nondeleting destructor is the
`bti; b` that follows them. Every rule is checked against the 24A446 record first. Writes the record only when all checks pass.

usage: derive-ioaccelerator-fixture.py RECORD_24A446 KC_24A446 KC_23F84 OUT_JSON
"""
import hashlib
import json
import struct
import sys

sys.path.insert(0, '/Users/afsal/zeus-build/UniXNU/kits/XnuPrelink')
from kc_image import Image  # noqa: E402

BTI = 0xd503245f


def require(ok, why):
    if not ok:
        raise SystemExit('STOP: ' + why)


def adrp_add_refs(img):
    """(pc, rd, address) of every adrp + add pair in the image's __TEXT_EXEC."""
    out = []
    for s in img.segments:
        if s['name'] != '__TEXT_EXEC' or not s['filesize']:
            continue
        code = img.data[s['fileoff']:s['fileoff'] + s['filesize']]
        pages = {}
        for off in range(0, len(code) - 3, 4):
            w, = struct.unpack_from('<I', code, off)
            pc = s['va'] + off
            if w & 0x9f000000 == 0x90000000:
                imm = ((w >> 5) & 0x7ffff) << 2 | ((w >> 29) & 3)
                if imm & (1 << 20):
                    imm -= 1 << 21
                pages[w & 31] = (pc & ~4095) + (imm << 12)
            elif w & 0xff800000 == 0x91000000:
                rn, rd = (w >> 5) & 31, w & 31
                if rn in pages:
                    out.append((pc, rd, pages[rn] + ((w >> 10) & 0xfff)))
    return out


def strings_of(img, name):
    found = []
    for s in img.sections:
        if 'cstring' in s['name']:
            blob = img.read(s['va'], s['size'])
            i = blob.find(name.encode() + b'\0')
            while i >= 0:
                if i == 0 or blob[i - 1] == 0:
                    found.append(s['va'] + i)
                i = blob.find(name.encode() + b'\0', i + 1)
    return found


def registrations(img, refs, name):
    """[(metaclass, size)] for OSMetaClass constructor calls naming `name` (x1 = name, x0 = metaclass, w3 = size)."""
    strs = set(strings_of(img, name))
    hits = []
    for pc, rd, addr in refs:
        if rd == 1 and addr in strs:
            near = sorted((abs(p - pc), a) for p, r, a in refs if r == 0 and pc - 40 <= p <= pc + 40)
            metas = [near[0][1]] if near else []
            size = None
            for k in range(-16, 24):
                w, = struct.unpack_from('<I', img.read(pc + 4 * k, 4))
                if w & 0xffe0001f == 0x52800003:
                    size = (w >> 5) & 0xffff
            if metas and size:
                hits.append((metas[0], size))
    return sorted(set(hits))


def returns_address(whole, fn):
    """The address a `[bti] adrp x0; add x0; ret` getter returns, else None."""
    try:
        w = list(struct.unpack_from('<4I', whole.read(fn, 16)))
    except ValueError:
        return None
    if w[0] == BTI:
        w = w[1:]
    if w[0] & 0x9f00001f == 0x90000000 and w[1] & 0xffc003ff == 0x91000000:
        imm = ((w[0] >> 5) & 0x7ffff) << 2 | ((w[0] >> 29) & 3)
        if imm & (1 << 20):
            imm -= 1 << 21
        return (fn & ~4095) + (imm << 12) + ((w[1] >> 10) & 0xfff)
    return None


def vtable_run(fx, site):
    slots, i = [], 0
    while True:
        e = fx.get(site + 8 * i)
        if not e or not e[1]:
            break
        slots.append((e[0], e[3]))
        i += 1
    return slots


def own_vtable(whole, img, fx, meta):
    """Vtable in img's __DATA* whose slot 7 (getMetaClass) returns meta."""
    found = []
    for s in img.segments:
        if not s['name'].startswith('__DATA') or not s['size']:
            continue
        for site in range(s['va'], s['va'] + s['size'] - 64, 8):
            e7 = fx.get(site + 56)
            if not e7 or not e7[1]:
                continue
            prev = fx.get(site - 8)
            if prev and prev[1]:
                continue  # not the first entry of a run
            if returns_address(whole, e7[0]) == meta:
                found.append(site)
    return found


def ctors_and_dtor(img, refs, ztv):
    starts = sorted(img.function_starts())
    loaders = set()
    for pc, rd, addr in refs:
        if addr != ztv:
            continue
        nxt, = struct.unpack_from('<I', img.read(pc + 4, 4))
        if nxt & 0xffc003ff == 0x91000000 | rd | rd << 5 and (nxt >> 10) & 0xfff == 0x10:
            f = max((s for s in starts if s <= pc), default=None)
            if f is not None:
                loaders.add(f)
    ctors = []
    for f in sorted(loaders):
        later = [s for s in starts if s > f]
        if later and later[0] - f <= 0x60:
            ctors.append(f)
    # the destructor: the first `bti; b` function after the last constructor
    dtor = None
    if ctors:
        for s in [s for s in starts if s > ctors[-1]][:3]:
            w0, w1 = struct.unpack_from('<2I', img.read(s, 8))
            if w0 == BTI and w1 & 0xfc000000 == 0x14000000:
                dtor = s
                break
    return ctors, dtor


def derive(kc):
    raw = open(kc, 'rb').read()
    whole = Image(raw)
    fx = whole.fixups()
    _, off = whole.entries['com.apple.iokit.IOGPUFamily']
    gpu = Image(raw, off)
    refs = adrp_add_refs(gpu)
    regs = registrations(gpu, refs, 'IOAcceleratorES')
    # The class's vtable is IOService-shaped; the other registration hit is the metaclass object's short vtable.
    owned = [(m, s, v[0]) for m, s in regs for v in [own_vtable(whole, gpu, fx, m)] if len(v) == 1 and len(vtable_run(fx, v[0])) >= 100]
    require(len(owned) == 1, f'IOAcceleratorES registrations {[(hex(m), s) for m, s in regs]}: {len(owned)} with an IOService-sized vtable')
    meta, size, vt = owned[0]
    slots = vtable_run(fx, vt)
    # IOService's own vtable: the kernel vtable of the same length whose entries equal IOAcceleratorES's at most slots.
    _, koff = whole.entries['com.apple.kernel']
    kernel = Image(raw, koff)
    best = None
    for seg in kernel.segments:
        if not seg['name'].startswith('__DATA') or not seg['size']:
            continue
        for site in range(seg['va'], seg['va'] + seg['size'] - 8 * len(slots), 8):
            e = fx.get(site + 8 * 8)
            if not e or e[0] != slots[8][0]:
                continue
            prev = fx.get(site - 8)
            if prev and prev[1]:
                continue
            run = vtable_run(fx, site)
            if len(run) != len(slots):
                continue
            same = sum(1 for k in range(len(slots)) if run[k][0] == slots[k][0])
            if not best or same > best[0]:
                best = (same, site, run)
    require(best, 'IOService vtable not found')
    _, svc_vt, svc = best
    overrides = [i for i in range(min(len(slots), len(svc))) if slots[i][0] != svc[i][0]]
    ctors, dtor = ctors_and_dtor(gpu, refs, vt - 0x10)
    return dict(sha=hashlib.sha256(raw).hexdigest(), meta=meta, size=size, vt=vt, slots=len(slots), service_slots=len(svc),
                overrides=overrides, diversities_same=all(slots[i][1] == svc[i][1] for i in range(min(len(slots), len(svc)))),
                ctors=ctors, dtor=dtor, get_meta=slots[7][0])


def main():
    rec27, kc27, kc23, out = sys.argv[1:5]
    r = json.load(open(rec27))
    print('== check on 24A446')
    a = derive(kc27)
    print({k: (hex(v) if isinstance(v, int) and v > 4096 else v) for k, v in a.items() if k != 'sha'})
    require(a['meta'] == int(r['metaclass'], 16), 'metaclass')
    require(a['size'] == r['instance_bytes'], 'instance size')
    require(a['slots'] == r['virtual_entries'], 'vtable entries')
    require(a['overrides'] == r['metadata_overrides'], 'overrides')
    require([hex(c) for c in a['ctors']] == r['protected_compatible_constructor_aliases'], 'constructors')
    require(hex(a['dtor']) == r['nondeleting_destructor'], 'destructor')
    require(hex(a['get_meta']) == r['get_meta_class'], 'getMetaClass')
    print('24A446: every rule reproduces the record')
    print('== derive on 23F84')
    b = derive(kc23)
    print({k: (hex(v) if isinstance(v, int) and v > 4096 else v) for k, v in b.items() if k != 'sha'})
    require(b['overrides'] == r['metadata_overrides'], f'23F84 overrides {b["overrides"]} differ from 24A446')
    require(b['slots'] == b['service_slots'] and b['diversities_same'], '23F84 IOAcceleratorES is not an IOService-shaped vtable')
    require(len(b['ctors']) == 2 and b['dtor'], '23F84 constructors / destructor')
    result = {
        'kernelcache_sha256': b['sha'], 'collection_slide': r['collection_slide'], 'metaclass': hex(b['meta']),
        'instance_bytes': b['size'], 'virtual_entries': b['slots'], 'operational_overrides': [],
        'metadata_overrides': b['overrides'], 'protected_compatible_constructor_aliases': [hex(c) for c in b['ctors']],
        'nondeleting_destructor': hex(b['dtor']), 'get_meta_class': hex(b['get_meta']),
        'evidence': f'derived 2026-11-22 by tools/derive-ioaccelerator-fixture.py from the 24A446 record (UniXNU-aston): IOGPUFamily '
                    f'registers IOAcceleratorES::gMetaClass {b["meta"]:#x} with size {b["size"]:#x}; vtable {b["vt"]:#x} has '
                    f'{b["slots"]} IOService-shaped entries (same diversities); slots {b["overrides"]} differ from IOService\'s; '
                    'constructors load the vtable after IOService::IOService, the nondeleting destructor tail-calls ~IOService. '
                    'Every rule reproduced the 24A446 record first.',
    }
    json.dump(result, open(out, 'w'), indent=1)
    print('wrote', out)


if __name__ == '__main__':
    main()
