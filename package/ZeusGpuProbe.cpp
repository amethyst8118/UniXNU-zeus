// SPDX-License-Identifier: BSD-2-Clause-Patent
// zeus (SM8450, Adreno 730) GPU power probe, staged like sheng's GpuPowerProbe.cpp. Boot argument gpu-power=<n>, default 0 (off):
//   1  read only: GCC GPU branches, GPUCC GDSCs and clocks, GPU SMMU global space and RBBM_STATUS, each only if the stage before
//      showed its prerequisite on (ZeusGpuPowerBoard.h); then the GMU clock tree and the AOP command DB header
//   2  as 1, plus the one write Linux's gcc-sm8450 probe makes: gcc_gpu_cfg_ahb_clk on, if it is off
//   3  as 2, plus, if CX is off: the gpu_cc_cx_gdsc enable sequence, gpu_cc_ahb_clk and the SMMU vote clock. GX is never touched.
// A read of an unclocked block can reset the phone, so the log says what comes next and waits before each new block: the last line
// on the USB log (zeus_usbeth_host.py) names the block. The run starts late (gpu-power-delay, seconds) so that link is up.
#include <IOKit/IOLib.h>
#include <kern/thread.h>
#include <pexpert/pexpert.h>
extern "C" {
#include "ZeusGpuPowerBoard.h"
vm_offset_t ml_io_map_unmappable(vm_offset_t phys_addr, vm_size_t size, uint32_t flags);
void sheng_gpu_power_probe(void);
}

namespace {
constexpr uint64_t kCmdDb = 0x80860000;   // zeus live DT: aop_cmd_db_region@80860000, size 0x20000
constexpr unsigned kWindows = 5;
const char *const kName[kWindows] = {"GCC GPU", "GCC vote", "GPUCC", "GPU SMMU", "GPU"};
const uint64_t kPhys[kWindows] = {GPB_GCC_GPU_PAGE, GPB_GCC_VOTE_PAGE, GPB_GPUCC_BASE, GPB_SMMU_BASE, GPB_GPU_BASE};
const uint32_t kSize[kWindows] = {0x1000, 0x1000, GPB_GPUCC_SIZE, GPB_SMMU_GR0_SIZE, GPB_GPU_WINDOW};
uintptr_t gBase[kWindows];
unsigned gLast = kWindows, gPauseMs = 1000;

uintptr_t mapIo(uint64_t phys, uint32_t size) {return ml_io_map_unmappable(static_cast<vm_offset_t>(phys), size, 7);}
// Plain 32-bit accesses: a hypervisor trap can only emulate the simple forms.
__attribute__((noinline)) uint32_t rd32(uintptr_t at) {
  uint32_t v;
  __asm__ volatile("ldr %w0, [%1]" : "=r"(v) : "r"(at) : "memory");
  return v;
}
__attribute__((noinline)) void wr32(uintptr_t at, uint32_t v) {
  __asm__ volatile("str %w0, [%1]" : : "r"(v), "r"(at) : "memory");
  __asm__ volatile("dsb sy" : : : "memory");
}
void announce(unsigned w) {
  if (w == gLast) return;
  gLast = w;
  printf("ZEUS GPU: next block %s\n", kName[w]);
  IOSleep(gPauseMs);
}
bool valid(unsigned w, uint32_t off) {return w < kWindows && gBase[w] && !(off & 3) && off <= kSize[w] - 4;}
int ioRead(void *, unsigned w, uint32_t off, uint32_t *out) {
  if (!valid(w, off)) return -1;
  announce(w);
  *out = rd32(gBase[w] + off);
  return 0;
}
int ioWrite(void *, unsigned w, uint32_t off, uint32_t value) {
  if (!valid(w, off)) return -1;
  announce(w);
  printf("ZEUS GPU: write %s +0x%x = 0x%08x\n", kName[w], off, value);
  IOSleep(gPauseMs);
  wr32(gBase[w] + off, value);
  return 0;
}
void ioDelay(void *, unsigned us) {IODelay(us);}

void run(unsigned mode) {
  unsigned have = 0;
  for (unsigned i = 0; i < kWindows; ++i) {
    gBase[i] = mapIo(kPhys[i], kSize[i]);
    if (gBase[i]) have |= 1u << i;
  }
  printf("ZEUS GPU: mode %u, windows mapped 0x%x\n", mode, have);
  static gpb_result r;
  static gpb_gmu_clocks k;
  const gpb_io io = {nullptr, ioRead, ioWrite, ioDelay};
  gpb_run(&io, mode, have, &r);
  printf("ZEUS GPU: stages gcc %s cc %s smmu %s gpu %s, io error %d\n", gpb_state_text(r.gcc), gpb_state_text(r.cc),
         gpb_state_text(r.smmu), gpb_state_text(r.gpu), r.io_error);
  printf("ZEUS GPU: gcc cfg_ahb %08x -> %08x memnoc %08x dvm %08x vote %08x\n", r.cfg_ahb, r.cfg_ahb_after, r.memnoc, r.dvm, r.pll_vote);
  printf("ZEUS GPU: gpucc cx %08x -> %08x (hw %08x, powered %u) gx %08x ahb %08x smmu %08x -> %08x (clocks %u) gmu %08x\n", r.cx_before,
         r.cx_gdscr, r.cx_hwctrl, r.powered_cx, r.gx_gdscr, r.cc_ahb, r.cc_smmu_before, r.cc_smmu, r.powered_clocks, r.cc_gmu);
  printf("ZEUS GPU: smmu scr0 %08x id0 %08x id1 %08x id2 %08x gfsr %08x smr valid %u first %x = %08x s2cr %08x\n", r.scr0, r.id0, r.id1,
         r.id2, r.gfsr, r.smr_valid, r.smr_first, r.smr_first_val, r.s2cr_first_val);
  printf("ZEUS GPU: rbbm_status %08x\n", r.rbbm_status);
  gpb_read_gmu_clocks(&io, &r, &k);
  printf("ZEUS GPU: gmu clocks read %d pll1 mode %08x op %08x l %08x user %08x | gmu rcg %08x/%08x hub rcg %08x/%08x\n", k.done, k.pll1_mode,
         k.pll1_opmode, k.pll1_l, k.pll1_user, k.gmu_cmd, k.gmu_cfg, k.hub_cmd, k.hub_cfg);
  printf("ZEUS GPU: branches cx_gmu %08x cxo %08x hub_cx_int %08x demet %08x axi %08x memnoc %08x gpll0 vote %08x\n", k.cx_gmu, k.cxo,
         k.hub_cx_int, k.demet, k.axi, k.memnoc, k.gpll0_vote);
  printf("ZEUS GPU: next block AOP command DB\n");
  IOSleep(gPauseMs);
  if (const uintptr_t db = mapIo(kCmdDb, 0x1000))
    printf("ZEUS GPU: cmd-db version %08x magic %08x (expect 0c0330db)\n", rd32(db), rd32(db + 4));
  printf("ZEUS GPU: done\n");
}

unsigned gMode, gDelayS = 90;
void probeThread(void *, wait_result_t) {
  thread_set_thread_name(current_thread(), "zeus-gpu-probe");
  IOSleep(gDelayS * 1000);
  run(gMode);
  thread_terminate(current_thread());
}
}

extern "C" void sheng_gpu_power_probe(void) {
  PE_parse_boot_argn("gpu-power", &gMode, sizeof(gMode));
  PE_parse_boot_argn("gpu-power-delay", &gDelayS, sizeof(gDelayS));
  PE_parse_boot_argn("gpu-power-pause", &gPauseMs, sizeof(gPauseMs));
  if (!gMode) return;
  if (gMode > 3) gMode = 3;
  thread_t thread;
  if (kernel_thread_start(probeThread, nullptr, &thread) == KERN_SUCCESS) thread_deallocate(thread);
}
