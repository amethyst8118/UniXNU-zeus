#!/usr/bin/env python3
"""zeus: adapt ios-sweet's NativeAdreno ABI1 transport (sweet_drm.cpp) to UniXNU's NativeAdreno ABI2 on the A730.

Differences (docs/NATIVE-METAL-PLAN.md): ABI2 has no SubmitResources (selector 17 is SetPriority there): a submission is the plain
Submit (5) of up to NativeAdrenoAbiMaxBuffers (128) IBs, and the kernel resolves residency from the process's VM mappings, as Turnip's
darwin backend relies on. ABI2 flags: HostImport 1, OwnedCoherent 2, MemoryPressure 4 (ABI1 used 2 and 4 for SubmitResources and
Compositor4096). ABI2 defines no NativeAdrenoAbiMaxQueues or NativeAdrenoAbiMaxResources: the transport keeps its own limits.
Every edit checks its context and fails loudly if ios-sweet's source changed.
usage: zeus_transport.py MESA_DIR   (after build-sweet-native-metal.py's prepare_mesa copied the transport to src/redwood-drm)"""
import sys
from pathlib import Path


def replace(s, old, new, count=1):
    n = s.count(old)
    if n != count:
        raise SystemExit(f'zeus_transport: expected {count} matches, found {n}: {old[:100]!r}')
    return s.replace(old, new)


def main():
    p = Path(sys.argv[1]) / 'src/redwood-drm/sweet_drm.cpp'
    s = p.read_text()
    # Limits ABI1 named and ABI2 does not.
    s = replace(s, 'namespace {\nconstexpr unsigned MaxBO = 4096;',
                'namespace {\n// zeus: ABI2 names neither limit; the transport keeps ABI1\'s.\n'
                'enum : unsigned {NativeAdrenoAbiMaxQueues = 8, NativeAdrenoAbiMaxResources = 1024};\n'
                'constexpr unsigned MaxBO = 4096;')
    # Device check: ABI2, A730 (or any a6xx/a7xx Gallium knows), HostImport only.
    s = replace(s, '    unsigned flags=NativeAdrenoParamsHostImport|NativeAdrenoParamsSubmitResources|NativeAdrenoParamsCompositor4096;\n'
                   '    if(!e && (state.params.abi_version!=1 || gpu!=618 || !state.info.gmem_bytes ||',
                '    unsigned flags=NativeAdrenoParamsHostImport;\n'
                '    state.gpu_id=gpu;\n'
                '    if(!e && (state.params.abi_version!=2 || (gpu!=730 && gpu!=740) || !state.info.gmem_bytes ||')
    s = replace(s, '    fprintf(stderr,"SweetNativeMetal: NativeAdreno ABI1 A618 chip=%#x GMEM=%u scoped residency\\n",chip,state.info.gmem_bytes);',
                '    fprintf(stderr,"ZeusNativeMetal: NativeAdreno ABI2 A%u chip=%#x GMEM=%u flags=%#x\\n",gpu,chip,state.info.gmem_bytes,'
                '(unsigned)state.params.flags);')
    s = replace(s, '    case MSM_PARAM_GPU_ID:p->value=618;break;', '    case MSM_PARAM_GPU_ID:p->value=state.gpu_id;break;')
    s = replace(s, '    bool lost = false, command_active = false;', '    bool lost = false, command_active = false;\n    unsigned gpu_id = 0;')
    # Submission: ABI2's plain Submit; the resource list is validated and kept for the transport's own bookkeeping only.
    s = replace(s, '    if(selector==NativeAdrenoMethodSubmitResources)timings.ioctl+=clock_ns()-began;',
                '    if(selector==NativeAdrenoMethodSubmit)timings.ioctl+=clock_ns()-began;')
    s = replace(s, '    auto req=new(std::nothrow) NativeAdrenoAbiSubmitResources{};',
                '    struct ZeusSubmit {NativeAdrenoAbiSubmit commands;};   // zeus: ABI2 Submit, no residency list\n'
                '    auto req=new(std::nothrow) ZeusSubmit{};')
    s = replace(s, '    req->resource_count=s->nr_bos;req->commands.queue=s->queueid;', '    req->commands.queue=s->queueid;')
    s = replace(s, '        req->resources[i]={b->kernel,access,0};bos[i].presumed=b->iova;', '        (void)access;bos[i].presumed=b->iova;')
    s = replace(s, 'e=call(NativeAdrenoMethodSubmitResources,req,sizeof *req,&reply,sizeof reply);',
                'e=call(NativeAdrenoMethodSubmit,&req->commands,sizeof req->commands,&reply,sizeof reply);', count=3)
    assert 'SubmitResources' not in s.replace('NativeAdrenoAbiMaxResources', ''), 'a SubmitResources use is left'
    # gpum27: IOSurfaceLookup(id) finds no surface that is not global on zeus (Apple's IOSurface kext; ios-sweet's kernel patch 0068
    # changed lookup for surfaces already mapped in the caller). CoreAnimation's framebuffers are not global, so the import failed
    # before any kernel call ("no drawable: ... never made shareable"). The frontend (ZeusNativeQuirks.m) hands the IOSurfaceRef it
    # holds to zeus_surface_known() for the length of each import; lookups try those first, then IOSurfaceLookup.
    s = replace(s, 'int surface_description(uint32_t id, struct redwood_drm_surface *out, IOSurfaceRef *keep=nullptr) {',
                '}   // namespace (closed for the zeus surface registry)\n'
                'static pthread_mutex_t zeus_known_lock = PTHREAD_MUTEX_INITIALIZER;\n'
                'static IOSurfaceRef zeus_known[16];\n'
                'static unsigned zeus_known_hits, zeus_known_misses;\n'
                'extern "C" __attribute__((visibility("default"), used)) void zeus_surface_known(IOSurfaceRef s, int on) {\n'
                '    if(!s)return;\n'
                '    pthread_mutex_lock(&zeus_known_lock);\n'
                '    for(auto &slot:zeus_known) {\n'
                '        if(on && !slot){CFRetain(s);slot=s;break;}\n'
                '        if(!on && slot==s){slot=nullptr;CFRelease(s);break;}\n'
                '    }\n'
                '    pthread_mutex_unlock(&zeus_known_lock);\n'
                '}\n'
                'static IOSurfaceRef zeus_lookup(uint32_t id) {\n'
                '    IOSurfaceRef found=nullptr;\n'
                '    pthread_mutex_lock(&zeus_known_lock);\n'
                '    for(auto slot:zeus_known)if(slot && IOSurfaceGetID(slot)==id){CFRetain(slot);found=slot;break;}\n'
                '    pthread_mutex_unlock(&zeus_known_lock);\n'
                '    if(found){if(zeus_known_hits++<8)fprintf(stderr,"ZeusNativeMetal: IOSurface %u imported from the caller\'s reference\\n",id);return found;}\n'
                '    found=IOSurfaceLookup(id);\n'
                '    if(!found && zeus_known_misses++<16)fprintf(stderr,"ZeusNativeMetal: IOSurfaceLookup(%u) found nothing\\n",id);\n'
                '    return found;\n'
                '}\n'
                'namespace {\n'
                'int surface_description(uint32_t id, struct redwood_drm_surface *out, IOSurfaceRef *keep=nullptr) {')
    s = replace(s, 'IOSurfaceRef s=IOSurfaceLookup(id&~YCbCrConvertFlag);', 'IOSurfaceRef s=zeus_lookup(id&~YCbCrConvertFlag);')
    s = replace(s, '    IOSurfaceRef s=IOSurfaceLookup(id);\n    if (!s) return ENOENT;', '    IOSurfaceRef s=zeus_lookup(id);\n    if (!s) return ENOENT;')
    # gpum28: zeus's user pages are 4 KiB (CA's framebuffer region is 0xc2a000 = its alloc size, not a 16 KiB multiple), so the 16 KiB
    # rounding made the pinned range overrun the surface's VM region and surface_description refused it (EINVAL, no BoImport).
    # The ABI2 kernel imports 4 KiB-aligned ranges. mk_zeus_build.py turns the frontend's matching 16 KiB masks to 4 KiB.
    s = replace(s, 'constexpr uint64_t HostPage = 16384, GuardBytes = HostPage;',
                'constexpr uint64_t HostPage = 4096, GuardBytes = 16384;   // zeus: 4 KiB user pages')
    # Say why a surface is refused (gpum28's refusal was silent).
    s = replace(s, '    if (!e) {\n        *out={id,0,bytes,uint32_t(pixel-base),',
                '    if (e) {static unsigned told; if(told++<16)fprintf(stderr,"ZeusNativeMetal: IOSurface %u description refused (e %d): '
                'base %#llx pixel %#llx bytes %llu pitch %zu planes %zu\\n",id,e,(unsigned long long)base,(unsigned long long)pixel,'
                '(unsigned long long)bytes,pitch,planes);}\n'
                '    if (!e) {\n        *out={id,0,bytes,uint32_t(pixel-base),')
    s = replace(s, '    if(e) {if(b.surface)CFRelease(b.surface);return e;}',
                '    if(e) {static unsigned told; if(told++<16)fprintf(stderr,"ZeusNativeMetal: BO %s of %llu bytes refused (e %d)\\n",'
                's?"import":"alloc",(unsigned long long)bytes,e);\n           if(b.surface)CFRelease(b.surface);return e;}')
    if '#include <pthread.h>' not in s:
        s = '#include <pthread.h>\n' + s
    p.write_text(s)
    print('zeus_transport: adapted', p)
    zeus_mesa(Path(sys.argv[1]))


def edit(path, old, new, count=1):
    s = path.read_text()
    path.write_text(replace(s, old, new, count))


def zeus_mesa(mesa):
    """gpum29 (first native picture): icons and widget images never imported. iconservicesagent/SpringBoard render icons into small
    RGBA16Float IOSurfaces (e.g. 120x120, 960-byte rows); fd_resource_from_handle wants rows aligned to gmem_align_w (16) pixels, which
    for 8-byte texels is 128 bytes, because a GMEM resolve writes whole tile columns. IOSurface rows are 64-byte aligned, the GPU's own
    linear pitch unit. Such an import is accepted at 64 bytes and marked, and any batch drawing into a marked resource renders in sysmem
    (no tile resolve). Also: SpringBoard's VideoToolbox compute shader bitcasts <4 x i16> to <2 x i32>, which the AIR frontend refused;
    any bitcast whose total width matches is a nir_extract_bits."""
    fd = mesa / 'src/gallium/drivers/freedreno'
    edit(fd / 'freedreno_resource.h', '   bool valid;\n',
         '   bool valid;\n   bool zeus_narrow_pitch; /* zeus: imported with 64-byte rows under the GMEM pitch; drawn in sysmem */\n')
    edit(fd / 'freedreno_resource.c',
         '   if (rsc->layout.pitch0 < (prsc->width0 * rsc->layout.cpp) ||\n'
         '       fd_resource_pitch(rsc, 0) != rsc->layout.pitch0)\n      goto fail;\n',
         '   /* zeus: IOSurface rows are 64-byte aligned (the GPU\'s linear pitch unit); accept them and keep GMEM away */\n'
         '   if (fd_resource_pitch(rsc, 0) != rsc->layout.pitch0 && !(rsc->layout.pitch0 & 63)) {\n'
         '      rsc->layout.pitchalign = 6;\n      rsc->zeus_narrow_pitch = true;\n   }\n'
         '   /* zeus (gpum29: 7 GPU faults 0x900..0x1c00 bytes past CA\'s 1206x2622 framebuffers): GMEM load/resolve move 4-row\n'
         '    * blocks, so a pass over an imported surface whose height is not a multiple of 4 touches rows past its pages */\n'
         '   if (prsc->height0 & 3)\n      rsc->zeus_narrow_pitch = true;\n\n'
         '   if (rsc->layout.pitch0 < (prsc->width0 * rsc->layout.cpp) ||\n'
         '       fd_resource_pitch(rsc, 0) != rsc->layout.pitch0)\n      goto fail;\n')
    edit(fd / 'freedreno_gmem.c',
         '      if (psurf->u.tex.first_layer < psurf->u.tex.last_layer)\n         sysmem = true;\n',
         '      if (psurf->u.tex.first_layer < psurf->u.tex.last_layer)\n         sysmem = true;\n'
         '      if (psurf->texture && fd_resource(psurf->texture)->zeus_narrow_pitch)\n         sysmem = true;   /* zeus */\n')
    edit(mesa / 'src/compiler/air/air_to_nir.c',
         '      else\n         return air_error(c, "instruction: a bitcast from %u x %u bits to %u x %u bits, "',
         '      else if (sn * sb == dn * db && sb >= 8 && db >= 8) {\n'
         '         nir_def *zeus_src = x;   /* zeus: any same-width repacking (VTMTSComputeFunction: <4 x i16> -> <2 x i32>) */\n'
         '         res = nir_extract_bits(&c->b, &zeus_src, 1, 0, dn, db);\n'
         '      } else\n         return air_error(c, "instruction: a bitcast from %u x %u bits to %u x %u bits, "')
    print('zeus_transport: Mesa edits (import pitch, sysmem for narrow imports, bitcast) applied')


if __name__ == '__main__':
    main()
