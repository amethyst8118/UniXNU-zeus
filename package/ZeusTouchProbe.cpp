// SPDX-License-Identifier: BSD-2-Clause-Patent
// zeus (Xiaomi 12 Pro) touch bring-up, stage by stage (docs/TOUCH-PLAN.md). Boot argument sheng-touch=<n>, as aston's TouchProbe:
//   1  read only: the GCC votes and clock branch of QUP0 SE4 (the touch SPI), the TLMM state of GPIO 16-21 (SPI lines, reset,
//      interrupt) and PM8350C LDO2 (the touch vdd, 1.8 V) through the SPMI observer channel. Nothing is written to any device.
// The STM FTS controller sits on spi@990000 (QUP0 SE4, wrapper 0x9c0000); vdd is pm8350c_l2 (RPMh), avdd pm8008j_l3 on the PM8008
// at I2C 0xd over QUP0 SE5. TWRP on zeus runs both rails (pm8008j_l3 3.304 V, pm8350c_l2 1.8 V). GCC offsets: Linux gcc-waipio.c
// as zhouzhoublm/hakios-unicorn drivers/input/hakios.stfts.c (same SoC family, same SE and pins) quotes them. The GENI engine's own
// registers are not read in stage 1: its bus clocks may be gated, and an unclocked read can stall the interconnect.
//   2  stage 1, then: vote the clocks of QUP0 SE4 and SE5 (lowest DFS preset of at least 4 MHz, root enable), mux GPIO 16-19 to qup4
//      (6 mA, no pull) and GPIO 206-207 to qup5 (2 mA, pull-up), and once a branch reads on, read that engine's identity
//      (GENI_FW_REVISION_RO protocol: 1 SPI, 3 I2C; GENI_IF_DISABLE_RO; FIFO depth). Reset (GPIO 20) and the interrupt (21) are left
//      alone, so the controller stays in reset. Offsets: Linux gcc-sm8450.c (SE4 0x274dc/0x274e4 bit 14, SE5 0x27610/0x27618 bit 15)
//      and pinctrl-sm8450.c (qup4 and qup5 are function 1 on these pins).
#include <IOKit/IOLib.h>
#include <pexpert/pexpert.h>
#include <string.h>
#include "BootResetCore.h"

extern "C" vm_offset_t ml_io_map_unmappable(vm_offset_t phys_addr, vm_size_t size, uint32_t flags);
extern "C" void ml_io_unmap(vm_offset_t addr, vm_size_t sz);

extern "C" {
// Rows for the boot log ('#touch' lines, BootLog.c), filled once by the stage that ran.
char zeus_touch_rows[6][200];
volatile uint32_t zeus_touch_stage = 0;
}

namespace {
enum : uint64_t {
  GccBase = 0x100000, GccQup = GccBase + 0x27000, GccVotePage = GccBase + 0x62000,
  TlmmBase = 0x0f100000, TlmmStride = 0x1000,
  SpmiCore = 0xc400000, SpmiCoreSize = 0x3000, SpmiObsrvr = 0xc440000, SpmiCnfg = 0xc42d000, SpmiCnfgSize = 0x4000,
};
enum : uint32_t {
  VoteOffset = 0x008,                         // GCC + 0x62008: the application processor's branch votes
  QupMAhb = 0x004, QupSAhb = 0x008,           // GCC + 0x27004 / 0x27008 (CBCR; bit 31 = off)
  Se4Cbcr = 0x4dc, Se4Cmd = 0x4e4, Se4Cfg = 0x4e8, Se4Dfsr = 0x4f8, Se4PerfCfg = 0x500,
  Ldo2Ppid = 0x241,                           // PM8350C (SID 2), LDO2 = peripheral 0x41
};

volatile uint32_t *map(uint64_t phys, uint64_t size) {
  return reinterpret_cast<volatile uint32_t *>(ml_io_map_unmappable(static_cast<vm_offset_t>(phys), static_cast<vm_size_t>(size), 7));
}
void unmap(volatile uint32_t *va, uint64_t size) {
  if (va) ml_io_unmap(reinterpret_cast<vm_offset_t>(va), static_cast<vm_size_t>(size));
}

// The APID that serves PPID for this EE (as br_find does for the SDAM), or -1.
int findApid(const br_arbiter &a, uint32_t ppid, int *owner) {
  const uint32_t apids = a.core[BR_FEATURES / 4] & BR_FEATURES_APIDS_MASK;
  int found = -1;
  for (uint32_t i = 0; i < apids && i < 1024; ++i) {
    const uint32_t m = a.core[(BR_APID_MAP + 4 * i) / 4];
    if (!m || ((m >> 8) & 0xfff) != ppid) continue;
    const int o = static_cast<int>(a.cnfg[(BR_CNFG_OWNER + 4 * i) / 4] & 7u);
    if (found < 0 || o == a.ee) {found = static_cast<int>(i); *owner = o;}
  }
  return found;
}

void stage1(void) {
  // Clocks.
  volatile uint32_t *qup = map(GccQup, 0x1000), *vote = map(GccVotePage, 0x1000);
  if (qup && vote) {
    const uint32_t v = vote[VoteOffset / 4];
    char dfs[96] = "";
    unsigned k = 0;
    for (unsigned l = 0; l < 8 && k < sizeof dfs - 12; ++l) k += snprintf(dfs + k, sizeof dfs - k, " %x", qup[(Se4PerfCfg + 4 * l) / 4]);
    snprintf(zeus_touch_rows[0], sizeof zeus_touch_rows[0],
             "#touch s1 gcc vote %08x (qup0 m_ahb %u s_ahb %u core %u core2x %u se4 %u) cbcr m_ahb %08x s_ahb %08x se4 %08x",
             v, v >> 6 & 1, v >> 7 & 1, v >> 8 & 1, v >> 9 & 1, v >> 14 & 1, qup[QupMAhb / 4], qup[QupSAhb / 4], qup[Se4Cbcr / 4]);
    snprintf(zeus_touch_rows[1], sizeof zeus_touch_rows[1], "#touch s1 se4 cmd %08x cfg %08x dfsr %08x perf%s",
             qup[Se4Cmd / 4], qup[Se4Cfg / 4], qup[Se4Dfsr / 4], dfs);
  } else {
    snprintf(zeus_touch_rows[0], sizeof zeus_touch_rows[0], "#touch s1 gcc not mapped");
  }
  unmap(qup, 0x1000);
  unmap(vote, 0x1000);

  // Pins: CFG ([1:0] pull, [5:2] function, [8:6] drive, [9] output enable) and IO (bit 0 in, bit 1 out) of GPIO 16..21.
  char pins[200] = "#touch s1 tlmm";
  unsigned k = (unsigned)strlen(pins);
  for (unsigned pin = 16; pin <= 21 && k < sizeof pins - 32; ++pin) {
    volatile uint32_t *p = map(TlmmBase + TlmmStride * pin, 0x1000);
    if (!p) {k += snprintf(pins + k, sizeof pins - k, " %u:?", pin); continue;}
    const uint32_t cfg = p[0], io = p[1];
    k += snprintf(pins + k, sizeof pins - k, " %u:f%u/p%u/oe%u/in%u/out%u", pin, cfg >> 2 & 0xf, cfg & 3, cfg >> 9 & 1, io & 1, io >> 1 & 1);
    unmap(p, 0x1000);
  }
  strlcpy(zeus_touch_rows[2], pins, sizeof zeus_touch_rows[2]);

  // PM8350C LDO2 through this EE's observer channel: STATUS1 (bit 7 VREG_READY), VSET_LB/UB (mV), MODE_CTL, EN_CTL (bit 7 enable).
  br_arbiter a = {};
  a.ee = 0;
  a.core = map(SpmiCore, SpmiCoreSize);
  a.cnfg = map(SpmiCnfg, SpmiCnfgSize);
  int owner = -1, apid = -1;
  volatile uint32_t *obs = nullptr;
  uint64_t obsSize = 0;
  if (a.core && a.cnfg) apid = findApid(a, Ldo2Ppid, &owner);
  if (apid >= 0) {
    obsSize = ((uint64_t)BR_OBS_APID_STRIDE * ((uint64_t)apid + 1) + 0xfff) & ~0xfffull;
    obs = map(SpmiObsrvr, obsSize);
  }
  if (obs) {
    uint8_t st = 0xff, lb = 0xff, ub = 0xff, mode = 0xff, en = 0xff;
    const int rc = br_read8(obs, &a, apid, 0x08, &st) | br_read8(obs, &a, apid, 0x40, &lb) << 4 | br_read8(obs, &a, apid, 0x41, &ub) << 8 |
                   br_read8(obs, &a, apid, 0x45, &mode) << 12 | br_read8(obs, &a, apid, 0x46, &en) << 16;
    snprintf(zeus_touch_rows[3], sizeof zeus_touch_rows[3],
             "#touch s1 pm8350c_l2 apid %d owner %d rc %x status1 %02x (ready %u) vset %u mV mode %02x en_ctl %02x (en %u)", apid, owner, rc,
             st, st >> 7 & 1, (unsigned)lb | (unsigned)ub << 8, mode, en, en >> 7 & 1);
  } else {
    snprintf(zeus_touch_rows[3], sizeof zeus_touch_rows[3], "#touch s1 pm8350c_l2 not reachable (core %d cnfg %d apid %d)", a.core != nullptr,
             a.cnfg != nullptr, apid);
  }
  unmap(obs, obsSize);
  unmap(a.core, SpmiCoreSize);
  unmap(a.cnfg, SpmiCnfgSize);
  for (unsigned i = 0; i < 4; ++i) IOLog("%s\n", zeus_touch_rows[i]);
}

// XO 19.2 MHz, GPLL0 600 MHz, GPLL0 even 300 MHz (gcc_parent_map_0); CFG [4:0] 2*div-1, [10:8] source, [13:12] mode.
uint32_t rcgRate(uint32_t cfg, uint32_t m, uint32_t notNMinusM) {
  static const uint32_t parent[8] = {19200000, 600000000, 0, 0, 0, 0, 300000000, 0};
  uint64_t hz = parent[cfg >> 8 & 7];
  const uint32_t div = cfg & 0x1f;
  if (div) hz = hz * 2 / (div + 1);
  if (cfg >> 12 & 3) {
    const uint32_t n = (~notNMinusM & 0xffff) + (m & 0xffff);
    if (n) hz = hz * (m & 0xffff) / n;
  }
  return static_cast<uint32_t>(hz);
}

// One engine: vote its branch, pick a DFS preset, enable the root, wait for the branch. Fills row; returns true when it reads on.
bool engineClock(volatile uint32_t *qup, volatile uint32_t *vote, uint32_t cbcr, uint32_t cmd, uint32_t bit, const char *name, char *row,
                 unsigned cap, uint32_t *hzOut) {
  const uint32_t dfsr = cmd + 0x14, perfCfg = cmd + 0x1c, perfM = cmd + 0x5c, perfN = cmd + 0x9c;
  uint32_t hz = 0, level = 0;
  if (qup[dfsr / 4] & 1) {
    for (uint32_t l = 0; l < 8; ++l) {
      const uint32_t r = rcgRate(qup[(perfCfg + 4 * l) / 4], qup[(perfM + 4 * l) / 4], qup[(perfN + 4 * l) / 4]);
      if (r >= 4000000 && (hz == 0 || r < hz)) {hz = r; level = l;}
    }
  }
  vote[VoteOffset / 4] = vote[VoteOffset / 4] | (1u << bit);
  qup[cmd / 4] = qup[cmd / 4] | 2;            // CMD_RCGR.ROOT_EN
  bool on = false;
  for (unsigned i = 0; i < 1000 && !on; ++i) {
    on = !(qup[cbcr / 4] & 0x80000000u);
    if (!on) IODelay(10);
  }
  snprintf(row, cap, "%s clock %s cbcr %08x root %08x dfs %u preset %u = %u Hz", name, on ? "on" : "OFF", qup[cbcr / 4], qup[cmd / 4],
           qup[dfsr / 4] & 1, level, hz);
  *hzOut = hz;
  return on;
}

void muxPin(unsigned pin, unsigned function, unsigned pull, unsigned ma) {
  volatile uint32_t *p = map(TlmmBase + TlmmStride * pin, 0x1000);
  if (!p) return;
  p[0] = pull | function << 2 | (ma / 2 - 1) << 6;   // input direction; the engine drives its own lines
  unmap(p, 0x1000);
}

// GENI identity of the engine at phys (read only after its clock is on).
void engineIdentity(uint64_t phys, char *row, unsigned cap, const char *name) {
  volatile uint32_t *se = map(phys, 0x4000);
  if (!se) {snprintf(row, cap, "%s not mapped", name); return;}
  const uint32_t fw = se[0x068 / 4], ifd = se[0x064 / 4], hw0 = se[0xe24 / 4], st = se[0x040 / 4], dma = se[0x258 / 4];
  snprintf(row, cap, "%s fw %08x (protocol %u) if_disable %u fifo %u words hw0 %08x status %08x dma_mode %u", name, fw, fw >> 8 & 0xff, ifd & 1,
           hw0 >> 16 & 0x3f, hw0, st, dma & 1);
  unmap(se, 0x4000);
}

void stage2(void) {
  volatile uint32_t *qup = map(GccQup, 0x1000), *vote = map(GccVotePage, 0x1000);
  if (!qup || !vote) {
    snprintf(zeus_touch_rows[4], sizeof zeus_touch_rows[4], "#touch s2 gcc not mapped");
    unmap(qup, 0x1000);
    unmap(vote, 0x1000);
    return;
  }
  char a[100], b[100], c[110], d[110];
  uint32_t hz4 = 0, hz5 = 0;
  const bool on4 = engineClock(qup, vote, Se4Cbcr, 0x4e4, 14, "se4", a, sizeof a, &hz4);
  const bool on5 = engineClock(qup, vote, 0x610, 0x618, 15, "se5", b, sizeof b, &hz5);
  unmap(qup, 0x1000);
  unmap(vote, 0x1000);
  for (unsigned pin = 16; pin <= 19; ++pin) muxPin(pin, 1, 0, 6);
  muxPin(206, 1, 3, 2);
  muxPin(207, 1, 3, 2);
  if (on4) engineIdentity(0x990000, c, sizeof c, "se4"); else snprintf(c, sizeof c, "se4 identity skipped (clock off)");
  if (on5) engineIdentity(0x994000, d, sizeof d, "se5"); else snprintf(d, sizeof d, "se5 identity skipped (clock off)");
  snprintf(zeus_touch_rows[4], sizeof zeus_touch_rows[4], "#touch s2 %s | %s", a, b);
  snprintf(zeus_touch_rows[5], sizeof zeus_touch_rows[5], "#touch s2 %s | %s", c, d);
  IOLog("%s\n%s\n", zeus_touch_rows[4], zeus_touch_rows[5]);
}
}  // namespace

extern "C" void native_touch_probe(void) {
  static uint32_t ran;
  uint32_t stage = 0;
  if (ran || !PE_parse_boot_argn("sheng-touch", &stage, sizeof stage) || stage == 0) return;
  ran = 1;
  zeus_touch_stage = stage;
  stage1();
  if (stage >= 2) stage2();
}
