// SPDX-License-Identifier: BSD-2-Clause-Patent
// zeus (Xiaomi 12 Pro, SM8450) USB bring-up, stage by stage (docs/USB-PLAN.md). Boot argument zeus-usb=<n>:
//   1  read only: the state ABL/UEFI leave the USB block in after `fastboot boot`. GCC (always readable): the USB30 GDSC, the USB
//      branches, the RCGs and the resets; then, only when their reset reads released, the SNPS femto HS PHY registers; then, only
//      when the GDSC is on and the master and NoC branches run, the DWC3 global and device registers through the core window.
//      An unclocked or reset block is never read: such a read can stall the interconnect. Nothing is written to any device.
// Facts: Linux gcc-sm8450.c (v6.12): usb30_prim_gdsc 0x49004; branches master 0x49018, sleep 0x49020, mock_utmi 0x49024,
// cfg_noc_usb3_prim_axi 0x49084, aggre_usb3_prim_axi 0x49088, usb3_prim_phy_aux 0x4905c, phy_com_aux 0x49060, phy_pipe 0x49064,
// eusb3_0_clkref_en 0x9c00c; RCGs master 0x49028, mock_utmi 0x49040; BCRs USB30_PRIM 0x49000, QUSB2PHY_PRIM 0x22000,
// USB3_PHY_PRIM 0x60000, USB3PHY_PHY_PRIM 0x60004, USB3_DP_PHY_PRIM 0x60008, USB_PHY_CFG_AHB2PHY 0x7a000. Linux
// phy-qcom-snps-femto-v2.c: UTMI_CTRL0 0x3c, UTMI_CTRL5 0x50 (POR bit 1), HS_PHY_CTRL_COMMON0 0x54 (SIDDQ bit 2, FSEL 6:4),
// COMMON1 0x58, COMMON2 0x5c, HS_PHY_CTRL1 0x60, CTRL2 0x64, OVERRIDE_X0..X3 0x6c..0x78, CFG0 0x94, REFCLK_CTRL 0xa0.
// zeus DT: dwc3 core 0xa600000 (Apple window +0xc000), femto PHY 0x88e3000 with param-override (0xe7@0x6c, 0x0c@0x70, 0x14@0x74).
// DWC3 (Linux core.h): GCTL 0xc110, GSNPSID 0xc120, GUID 0xc128, GUSB2PHYCFG 0xc200, GUSB3PIPECTL 0xc2c0, DCFG 0xc700,
// DCTL 0xc704, DSTS 0xc70c.
#include <IOKit/IOLib.h>
#include <kern/thread.h>
#include <pexpert/pexpert.h>
#include <string.h>

extern "C" vm_offset_t ml_io_map_unmappable(vm_offset_t phys_addr, vm_size_t size, uint32_t flags);

extern "C" {
// '#usb' rows 4-6 of the boot log's touch rows (ZeusTouchProbe.cpp; free once the touch report thread runs, before this probe).
extern char zeus_touch_rows[12][200];
volatile uint32_t zeus_usb_stage;
}

namespace {
constexpr uint64_t kGcc = 0x100000, kGccSize = 0xa0000, kPhy = 0x88e3000, kDwcWindow = 0xa60c000;

volatile uint32_t *map(uint64_t phys, uint64_t size) {
  return reinterpret_cast<volatile uint32_t *>(ml_io_map_unmappable(static_cast<vm_offset_t>(phys), static_cast<vm_size_t>(size), 7));
}
inline bool branchOn(uint32_t cbcr) {return !(cbcr & 0x80000000u);}   // CLK_OFF clear

void stage1() {
  volatile uint32_t *gcc = map(kGcc, kGccSize);
  if (!gcc) {snprintf(zeus_touch_rows[4], sizeof zeus_touch_rows[4], "#usb u1 gcc not mapped"); return;}
  auto g = [&](uint32_t off) {return gcc[off / 4];};
  const uint32_t gdsc = g(0x49004);
  const uint32_t master = g(0x49018), sleepCbcr = g(0x49020), utmi = g(0x49024), noc = g(0x49084), aggre = g(0x49088);
  const uint32_t aux = g(0x4905c), comAux = g(0x49060), pipeCbcr = g(0x49064), clkref = g(0x9c00c);
  const uint32_t bcrUsb = g(0x49000), bcrHs = g(0x22000), bcrSs = g(0x60000), bcrSsPhy = g(0x60004), bcrDp = g(0x60008);
  const uint32_t bcrAhb = g(0x7a000);
  snprintf(zeus_touch_rows[4], sizeof zeus_touch_rows[4],
           "#usb u1 gdsc %08x (on %u) | bcr usb30 %x hsphy %x ss %x ssphy %x dp %x ahb2phy %x | rcg master %08x/%08x utmi %08x/%08x",
           gdsc, gdsc >> 31, bcrUsb & 1, bcrHs & 1, bcrSs & 1, bcrSsPhy & 1, bcrDp & 1, bcrAhb & 1, g(0x49028), g(0x4902c), g(0x49040),
           g(0x49044));
  snprintf(zeus_touch_rows[5], sizeof zeus_touch_rows[5],
           "#usb u1 cbcr master %08x sleep %08x utmi %08x noc %08x aggre %08x aux %08x comaux %08x pipe %08x clkref %08x",
           master, sleepCbcr, utmi, noc, aggre, aux, comAux, pipeCbcr, clkref);

  char phy[96] = "phy skipped (reset)";
  if (!(bcrHs & 1) && !(bcrAhb & 1)) {
    volatile uint32_t *p = map(kPhy, 0x1000);
    if (p) {
      snprintf(phy, sizeof phy, "phy c0 %02x c5 %02x cm0 %02x cm1 %02x cm2 %02x c1 %02x c2 %02x x %02x %02x %02x cfg %02x ref %02x",
               p[0x3c / 4] & 0xff, p[0x50 / 4] & 0xff, p[0x54 / 4] & 0xff, p[0x58 / 4] & 0xff, p[0x5c / 4] & 0xff, p[0x60 / 4] & 0xff,
               p[0x64 / 4] & 0xff, p[0x6c / 4] & 0xff, p[0x70 / 4] & 0xff, p[0x74 / 4] & 0xff, p[0x94 / 4] & 0xff, p[0xa0 / 4] & 0xff);
    } else {
      snprintf(phy, sizeof phy, "phy not mapped");
    }
  }
  char core[100] = "core skipped (unpowered or unclocked)";
  if ((gdsc >> 31) && !(bcrUsb & 1) && branchOn(master) && branchOn(noc)) {
    volatile uint32_t *d = map(kDwcWindow, 0x1000);   // core + 0xc000 .. 0xcfff
    if (d) {
      auto r = [&](uint32_t off) {return d[(off - 0xc000) / 4];};
      snprintf(core, sizeof core, "dwc3 id %08x gctl %08x u2 %08x u3 %08x dcfg %08x dctl %08x dsts %08x", r(0xc120), r(0xc110),
               r(0xc200), r(0xc2c0), r(0xc700), r(0xc704), r(0xc70c));
    } else {
      snprintf(core, sizeof core, "core not mapped");
    }
  }
  snprintf(zeus_touch_rows[6], sizeof zeus_touch_rows[6], "#usb u1 %s | %s", phy, core);
  for (unsigned i = 4; i <= 6; ++i) IOLog("%s\n", zeus_touch_rows[i]);
}

void usbThread(void *, wait_result_t) {
  thread_set_thread_name(current_thread(), "zeus-usb");
  stage1();
  zeus_usb_stage = 1;
  // The rows are a snapshot; keep them in every log round.
  char saved[3][200];
  for (unsigned i = 0; i < 3; ++i) memcpy(saved[i], zeus_touch_rows[4 + i], sizeof saved[i]);
  for (;;) {
    IOSleep(2000);
    for (unsigned i = 0; i < 3; ++i) if (!zeus_touch_rows[4 + i][0]) memcpy(zeus_touch_rows[4 + i], saved[i], sizeof saved[i]);
  }
}
}  // namespace

extern "C" void sheng_usb_probe(void) {
  static uint32_t ran;
  unsigned stage = 0;
  if (ran || !PE_parse_boot_argn("zeus-usb", &stage, sizeof stage) || stage == 0) return;
  ran = 1;
  thread_t thread = nullptr;
  if (kernel_thread_start(usbThread, nullptr, &thread) == KERN_SUCCESS) thread_deallocate(thread);
}
