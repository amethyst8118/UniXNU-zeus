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
    p.write_text(s)
    print('zeus_transport: adapted', p)


if __name__ == '__main__':
    main()
