#!/usr/bin/env python3
"""Derive the 23F84 IOHIDDevice fixture (scripts/hid-device-23F84-d94ap.json) from the 24A446 one (run on the Mac).

The arm64e diversity of a vtable slot is a hash of the method's mangled name, so it is stable across OS builds: the 23F84
IOHIDDevice vtable is the run of signed pointers in com.apple.iokit.IOHIDFamily whose diversity sequence matches the 24A446
fixture's. The instance size and gMetaClass come from the OSMetaClass registration that names "IOHIDDevice"; the C2 constructor
and D2 destructor are the two functions that load the vtable address (ordered as on 24A446). Every step is checked on 24A446 first,
where the fixture gives the answers. Prints a report; writes the fixture only when every check passes.

usage: derive-hid-device-fixture.py FIXTURE_24A446 KC_24A446 KC_23F84 OUT_JSON
"""
import hashlib
import json
import struct
import sys

sys.path.insert(0, '/Users/afsal/zeus-build/UniXNU/kits/XnuPrelink')
from kc_image import Image  # noqa: E402

KEXT = 'com.apple.iokit.IOHIDFamily'


def require(ok, why):
    if not ok:
        raise SystemExit('STOP: ' + why)


def load(path):
    raw = open(path, 'rb').read()
    whole = Image(raw)
    va, off = whole.entries[KEXT]
    return raw, whole, Image(raw, off)


def find_vtable(whole, kext, want, metas=()):
    fx = whole.fixups()
    data = [s for s in kext.segments if s['name'].startswith('__DATA') and s['size']]
    def run(site):
        slots, i = [], 0
        while True:
            e = fx.get(site + 8 * i)
            if not e or not e[1]:
                break
            slots.append((e[0], e[3]))
            i += 1
        return slots

    candidates = []
    for s in data:
        for site in range(s['va'], s['va'] + s['size'] - 8 * len(want), 8):
            first = fx.get(site)
            if not first or first[3] != want[0]:
                continue
            score = sum(1 for i, d in enumerate(want) if (fx.get(site + 8 * i) or (0, 0, 0, None))[3] == d)
            candidates.append((score, site))
    require(candidates, 'no vtable candidate')
    top = max(score for score, _ in candidates)
    # Subclass vtables repeat the base's slots (same diversities) and continue; the class's own vtable ends with them.
    full = sorted(((len(run(site)), site) for score, site in candidates if score == top))
    print('  candidates with score', top, ':', [(hex(site), n) for n, site in full])
    shortest = [site for n, site in full if n == full[0][0]]
    if len(shortest) > 1 and metas:
        # getMetaClass of the class's own vtable returns its gMetaClass (adrp x0 / add x0 / ret).
        own = []
        for site in shortest:
            for t, _ in run(site)[:16]:
                try:
                    w0, w1, w2 = struct.unpack_from('<3I', whole.read(t, 12))
                except ValueError:
                    continue
                if w0 == 0xd503245f:  # BTI landing pad
                    w0, w1 = w1, w2
                if w0 & 0x9f00001f == 0x90000000 and w1 & 0xffc003ff == 0x91000000:
                    imm = ((w0 >> 5) & 0x7ffff) << 2 | ((w0 >> 29) & 3)
                    if imm & (1 << 20):
                        imm -= 1 << 21
                    if (t & ~4095) + (imm << 12) + ((w1 >> 10) & 0xfff) in metas:
                        own.append(site)
                        break
        print('  getMetaClass returns an "IOHIDDevice" metaclass in', [hex(x) for x in own])
        require(len(own) == 1, 'cannot single out the IOHIDDevice vtable')
        site = own[0]
    else:
        site = shortest[0]
    return site, top, run(site)


def code_words(kext):
    for s in kext.segments:
        if s['name'] == '__TEXT_EXEC' and s['filesize']:
            yield s['va'], kext.data[s['fileoff']:s['fileoff'] + s['filesize']]


def decode_refs(kext):
    """(pc, destination register, absolute address) for every adrp+add / adrp+ldr pair, and the raw words."""
    out = []
    for va, code in code_words(kext):
        pages = {}
        for off in range(0, len(code) - 3, 4):
            w, = struct.unpack_from('<I', code, off)
            pc = va + off
            if w & 0x9f000000 == 0x90000000:
                imm = ((w >> 5) & 0x7ffff) << 2 | ((w >> 29) & 3)
                if imm & (1 << 20):
                    imm -= 1 << 21
                pages[w & 31] = (pc & ~4095) + (imm << 12)
            elif w & 0xff800000 == 0x91000000:  # add xd, xn, #imm
                rn, rd = (w >> 5) & 31, w & 31
                if rn in pages:
                    out.append((pc, rd, pages[rn] + ((w >> 10) & 0xfff)))
    return out


def registration(kext, refs, name):
    strs = []
    for s in kext.sections:
        if 'cstring' in s['name']:
            blob = kext.read(s['va'], s['size'])
            i = blob.find(name.encode() + b'\0')
            while i >= 0:
                if i == 0 or blob[i - 1] == 0:
                    strs.append(s['va'] + i)
                i = blob.find(name.encode() + b'\0', i + 1)
    require(strs, f'no "{name}" string')
    hits = []
    for pc, rd, addr in refs:
        if addr in strs and rd == 1:
            # look around for x0 = metaclass (add into x0) and w3 = size (movz), then a bl
            meta = [a for p, r, a in refs if r == 0 and pc - 40 <= p <= pc + 40]
            size = None
            for back in range(-16, 24):
                w, = struct.unpack_from('<I', kext.read(pc + 4 * back, 4))
                if w & 0xffe0001f == 0x52800003:  # movz w3, #imm
                    size = (w >> 5) & 0xffff
            hits.append((pc, meta, size))
    return hits


def ctor_dtor(kext, refs, ztv):
    """C2 loads ZTV (then adds 0x10) and calls the superclass C2; D2 is the next function: `bti; b <superclass D2>`."""
    starts = sorted(kext.function_starts())
    found = set()
    vt_ptr = set()
    for pc, rd, addr in refs:
        if addr == ztv:
            f = max((s for s in starts if s <= pc), default=None)
            if f is None:
                continue
            found.add(f)
            nxt, = struct.unpack_from('<I', kext.read(pc + 4, 4))
            # add xR, xR, #0x10 right after: the object's vtable pointer is ZTV + 0x10 (a constructor or destructor).
            if nxt & 0xffc003ff == 0x91000000 | rd | rd << 5 and (nxt >> 10) & 0xfff == 0x10:
                vt_ptr.add(f)
    pairs = []
    for f in sorted(vt_ptr):
        later = [s for s in starts if s > f]
        if not later:
            continue
        d = later[0]
        if d - f > 0x60:  # C2 is small: super C2, store the vtable pointer, return
            continue
        w0, w1 = struct.unpack_from('<2I', kext.read(d, 8))
        if w0 == 0xd503245f and w1 & 0xfc000000 == 0x14000000:
            pairs.append((f, d))
    return sorted(found), pairs


def derive(fix27, kc, check=None):
    raw, whole, kext = load(kc)
    want = [int(d, 16) for _, d in fix27['slots']]
    refs = decode_refs(kext)
    reg = registration(kext, refs, 'IOHIDDevice')
    metas = {m for _, ms, s in reg if s for m in ms}
    site, score, slots = find_vtable(whole, kext, want, metas)
    loaders, pairs = ctor_dtor(kext, refs, site - 0x10)
    return dict(kext=kext, site=site, score=score, slots=slots, reg=reg, loaders=loaders, pairs=pairs,
                sha=hashlib.sha256(raw).hexdigest())


def main():
    fixture27, kc27, kc23, out = sys.argv[1:5]
    f27 = json.load(open(fixture27))
    print('== check on 24A446')
    a = derive(f27, kc27)
    vt27 = int(f27['vtable'], 16)
    print('vtable', hex(a['site']), 'fixture', hex(vt27), 'score', a['score'], '/', len(f27['slots']), 'run', len(a['slots']))
    require(a['site'] == vt27, 'the diversity search does not find the 24A446 vtable')
    require([hex(t) for t, _ in a['slots'][:len(f27['slots'])]] == [t for t, _ in f27['slots']], '24A446 slot targets differ')
    print('registration', [(hex(p), [hex(m) for m in ms], hex(s) if s else None) for p, ms, s in a['reg']])
    meta27 = int(f27['bindings']['__ZN11IOHIDDevice10gMetaClassE'], 16)
    require(any(meta27 in ms and s == int(str(f27['instance_size']), 0) for _, ms, s in a['reg']), '24A446 metaclass/size not found')
    print('ZTV loaders', [hex(x) for x in a['loaders']], 'C2/D2 pairs', [(hex(x), hex(y)) for x, y in a['pairs']])
    c2 = int(f27['bindings']['__ZN11IOHIDDeviceC2EPK11OSMetaClass'], 16)
    d2 = int(f27['bindings']['__ZN11IOHIDDeviceD2Ev'], 16)
    require(a['pairs'] == [(c2, d2)], '24A446: the C2/D2 rule does not give the fixture pair')

    print('== derive on 23F84')
    b = derive(f27, kc23)
    print('vtable', hex(b['site']), 'score', b['score'], '/', len(f27['slots']), 'run', len(b['slots']))
    print('registration', [(hex(p), [hex(m) for m in ms], hex(s) if s else None) for p, ms, s in b['reg']])
    print('ZTV loaders', [hex(x) for x in b['loaders']], 'C2/D2 pairs', [(hex(x), hex(y)) for x, y in b['pairs']])
    n = len(f27['slots'])
    same = sum(1 for i in range(min(n, len(b['slots']))) if b['slots'][i][1] == int(f27['slots'][i][1], 16))
    print('diversity matches by position', same, '/', n)
    regs = [(ms, s) for _, ms, s in b['reg'] if s and ms]
    require(len(regs) >= 1, '23F84 registration size not found')
    require(len({m for ms, _ in regs for m in ms}) == 1, '23F84: more than one IOHIDDevice metaclass candidate')
    require(len(b['pairs']) == 1, '23F84: not exactly one C2/D2 pair')
    b_c2, b_d2 = b['pairs'][0]
    result = {
        'kernelcache_sha256': b['sha'],
        'collection_slide': f27['collection_slide'],
        'vtable': hex(b['site']),
        'instance_size': hex(regs[0][1]),
        'slots': [[hex(t), hex(d)] for t, d in b['slots'][:n]],
        'bindings': {
            '__ZN11IOHIDDeviceC2EPK11OSMetaClass': hex(b_c2),
            '__ZN11IOHIDDeviceD2Ev': hex(b_d2),
            '__ZN11IOHIDDevice10gMetaClassE': hex(regs[0][0][0]),
            '__ZTV11IOHIDDevice': hex(b['site'] - 0x10),
        },
        'source': 'derived 2026-11-22 from hid-device-24A446-d94ap.json for iPhone17,2 23F84 by tools/derive-hid-device-fixture.py: '
                  'vtable by slot-diversity match in com.apple.iokit.IOHIDFamily, instance size and gMetaClass from the '
                  'OSMetaClass registration of "IOHIDDevice", C2 = the function that loads ZTV and is followed by D2 (`bti; b` to the superclass destructor); '
                  'every step checked against the 24A446 fixture first',
    }
    print(json.dumps({k: v for k, v in result.items() if k != 'slots'}, indent=1))
    require(same == n and len(b['slots']) >= n, f'23F84 slot diversities differ from 24A446 ({same}/{n}): the class layout changed')
    json.dump(result, open(out, 'w'), indent=1)
    print('wrote', out)


if __name__ == '__main__':
    main()
