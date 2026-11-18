// SPDX-License-Identifier: BSD-2-Clause-Patent
// zeus (Xiaomi 12 Pro) power and volume buttons, boot argument sheng-buttons=1, sent as the Consumer Power, Volume Increment and
// Volume Decrement bits of the HID input device's report 2 (drivers/ShengHidInput, the native_buttons_set_sink sink).
// The devicetree facts (device/dt/live-merged.dts): power is the PMK8350 PON (SPMI SID 0, pon_hlos@1300 = PPID 0x013, interrupt 7
// "pwrkey", KEY_POWER), volume down its RESIN (interrupt 6, KEY_VOLUMEDOWN), volume up the PM8350 GPIO 6 (SID 1, pinctrl@8800, so
// peripheral 0x8d = PPID 0x18d; gpio-keys KEY_VOLUMEUP, active low with a pull-up). Unlike aston, zeus's volume keys are not swapped.
// A PON or GPIO peripheral's RT_STS (0x10) holds the live level of each of its interrupts: bit 7 power, bit 6 resin; GPIO bit 0.
//
// Read only and polled, unlike aston's ButtonProbe.cpp (interrupts through the SPMI arbiter, the PDC and an SPI): nothing is written to
// the PMIC or the arbiter, and no interrupt is armed (an SPI-routed IPI powered the phone off under Gunyah; device SPIs are unproven).
// Every 20 ms one observer-channel read per peripheral; a change of the pressed set goes to the sink.
// Arbiter layout as the mainline spmi-pmic-arb driver: version 5 maps APIDs at core + 0x900 + 4n (owner cnfg + 0x700 + 4n, observer
// channel 0x10000 * EE + 0x80 * APID), version 7 at core + 0x2000 + 4n (owner cnfg + 4n, observer 0x8000 * EE + 0x20 * APID). The
// version register (core + 0) picks one. BootResetCore.h knows version 7 only, which is why the touch probe's stage 1 lookup of
// pm8350c_l2 found no APID on this SoC.
#include <IOKit/IOLib.h>
#include <kern/thread.h>
#include <pexpert/pexpert.h>
#include <string.h>

extern "C" vm_offset_t ml_io_map_unmappable(vm_offset_t phys_addr, vm_size_t size, uint32_t flags);
extern "C" void ml_io_unmap(vm_offset_t addr, vm_size_t sz);

extern "C" {
// The '#touch' rows of the boot log (ZeusTouchProbe.cpp). Row 1 is free once the touch report thread runs; this probe rewrites it
// every second, so the touch probe clearing its one-time rows does not lose it.
extern char zeus_touch_rows[12][200];
// [0] stage (1 mapped, 2 found, 3 polling; negative: the failed step), [1] polls, [2] reports sent, [3] pressed mask, [4] PON APID,
// [5] GPIO APID, [6] arbiter version, [7] APID count, [8] last read error, [9] read errors. BootLog.c prints these as its 'B' row.
volatile int32_t native_buttons_stats[10];
}

namespace {
constexpr uint64_t kCore = 0xc400000, kCoreSize = 0x3000, kObs = 0xc440000, kCnfg = 0xc42d000, kCnfgSize = 0x4000;
constexpr uint32_t kPonPpid = 0x013, kGpioPpid = 0x18d;
constexpr uint8_t kRtSts = 0x10, kPowerBit = 1u << 7, kResinBit = 1u << 6, kGpioBit = 1u << 0;
// Report 2 bits (NativeHidInput): 1 Power, 2 Volume Increment, 3 Volume Decrement.
constexpr uint32_t kReportPower = 1u << 1, kReportVolumeUp = 1u << 2, kReportVolumeDown = 1u << 3;

struct Layout {uint32_t apidMap, ownerTable, obsApidStride;};
constexpr Layout kV5 = {0x900, 0x700, 0x80}, kV7 = {0x2000, 0x000, 0x20};
// EE 0 is the HLOS on zeus (spmi@c400000 qcom,ee = <0>), so the observer channel offset has no EE term.

volatile uint32_t *gCore, *gCnfg, *gObs;
uint64_t gObsSize;
Layout gLayout;
uint32_t gVersion, gApids;
int gPon = -1, gGpio = -1;
uint8_t gRawPon, gRawGpio = 0xff;
void (*volatile gSink)(void *, uint32_t);
void *volatile gSinkContext;

volatile uint32_t *map(uint64_t phys, uint64_t size) {
  return reinterpret_cast<volatile uint32_t *>(ml_io_map_unmappable(static_cast<vm_offset_t>(phys), static_cast<vm_size_t>(size), 7));
}
void fail(int step, int error) {native_buttons_stats[8] = error; native_buttons_stats[0] = -step;}

// The APID that maps ppid: the first one, replaced by one EE 0 owns (as the mainline driver picks it); -1 when there is none.
int findApid(uint32_t ppid) {
  int found = -1;
  for (uint32_t i = 0; i < gApids; ++i) {
    const uint32_t m = gCore[(gLayout.apidMap + 4 * i) / 4];
    if (!m || ((m >> 8) & 0xfff) != ppid) continue;
    const uint32_t owner = gCnfg[(gLayout.ownerTable + 4 * i) / 4] & 7u;
    if (found < 0 || owner == 0) found = static_cast<int>(i);
  }
  return found;
}

// One byte of the peripheral behind apid through EE 0's observer channel: EXT_READL (opcode 1), one byte, wait for DONE.
int read8(int apid, uint8_t reg, uint8_t *value) {
  volatile uint32_t *ch = gObs + (gLayout.obsApidStride * static_cast<uint32_t>(apid)) / 4;
  ch[0] = (1u << 27) | (static_cast<uint32_t>(reg) << 4);
  for (unsigned n = 0; n < 100000; ++n) {
    const uint32_t s = ch[0x08 / 4];
    if (s & 1u) {
      if (s & 0xeu) return 2;                    // failed, denied or dropped
      *value = static_cast<uint8_t>(ch[0x18 / 4]);
      return 0;
    }
  }
  return 1;
}

bool setup() {
  gCore = map(kCore, kCoreSize);
  gCnfg = map(kCnfg, kCnfgSize);
  if (!gCore || !gCnfg) {fail(1, -12); return false;}
  gVersion = gCore[0];
  gApids = gCore[0x04 / 4] & 0x7ffu;
  native_buttons_stats[6] = static_cast<int32_t>(gVersion);
  native_buttons_stats[7] = static_cast<int32_t>(gApids);
  if (gVersion >= 0x70000000u && gVersion < 0x80000000u) gLayout = kV7;
  else if (gVersion >= 0x50000000u && gVersion < 0x70000000u) gLayout = kV5;
  else {fail(2, -45); return false;}             // a layout this code does not know
  if (!gApids || gApids > 1024) {fail(3, -22); return false;}
  native_buttons_stats[0] = 1;
  gPon = findApid(kPonPpid);
  gGpio = findApid(kGpioPpid);
  native_buttons_stats[4] = gPon;
  native_buttons_stats[5] = gGpio;
  if (gPon < 0) {fail(4, -2); return false;}
  const uint32_t top = static_cast<uint32_t>(gPon > gGpio ? gPon : gGpio) + 1;
  gObsSize = (static_cast<uint64_t>(gLayout.obsApidStride) * top + 0xfff) & ~0xfffull;
  gObs = map(kObs, gObsSize);
  if (!gObs) {fail(5, -12); return false;}
  native_buttons_stats[0] = 2;
  return true;
}

uint32_t pressed() {
  uint32_t mask = 0;
  uint8_t pon = 0, gpio = 0xff;
  int rc = read8(gPon, kRtSts, &pon);
  if (!rc) {
    gRawPon = pon;
    if (pon & kPowerBit) mask |= kReportPower;
    if (pon & kResinBit) mask |= kReportVolumeDown;
  }
  if (gGpio >= 0) {
    const int rcGpio = read8(gGpio, kRtSts, &gpio);
    if (!rcGpio) {
      gRawGpio = gpio;
      if (!(gpio & kGpioBit)) mask |= kReportVolumeUp;   // active low
    }
    if (!rc) rc = rcGpio;
  }
  if (rc) {native_buttons_stats[8] = rc; native_buttons_stats[9] = native_buttons_stats[9] + 1;}
  return mask;
}

void publishRow() {
  snprintf(zeus_touch_rows[1], sizeof zeus_touch_rows[1],
           "#btn st %d arb %08x apids %u pon %d gpio %d | raw pon %02x gpio %02x | mask %x rep %d polls %d | err %d/%d sink %d",
           native_buttons_stats[0], gVersion, gApids, gPon, gGpio, gRawPon, gRawGpio, native_buttons_stats[3], native_buttons_stats[2],
           native_buttons_stats[1], native_buttons_stats[8], native_buttons_stats[9], gSink != nullptr);
}

void buttonsThread(void *, wait_result_t) {
  thread_set_thread_name(current_thread(), "zeus-buttons");
  if (!setup()) {
    for (;;) {publishRow(); IOSleep(1000);}       // keep the failure visible in every round
  }
  native_buttons_stats[0] = 3;
  uint32_t last = 0;
  for (uint32_t polls = 1;; ++polls) {
    native_buttons_stats[1] = static_cast<int32_t>(polls);
    const uint32_t mask = pressed();
    const bool changed = mask != last;
    if (changed) {
      last = mask;
      native_buttons_stats[3] = static_cast<int32_t>(mask);
      void (*sink)(void *, uint32_t) = __atomic_load_n(&gSink, __ATOMIC_ACQUIRE);
      if (sink) {sink(gSinkContext, mask); native_buttons_stats[2] = native_buttons_stats[2] + 1;}
    }
    if (changed || polls % 50 == 0) publishRow();
    IOSleep(20);
  }
}
}  // namespace

// NativeHidInput registers its report-2 sender here when it starts (the combined device, not the touch-only one).
extern "C" void native_buttons_set_sink(void (*sink)(void *, uint32_t), void *context) {
  gSinkContext = context;
  __atomic_store_n(&gSink, sink, __ATOMIC_RELEASE);
}

extern "C" void sheng_buttons_probe(void) {
  unsigned enabled = 0;
  if (!PE_parse_boot_argn("sheng-buttons", &enabled, sizeof enabled) || enabled != 1 || native_buttons_stats[0]) return;
  native_buttons_stats[0] = 1;
  thread_t thread = nullptr;
  if (kernel_thread_start(buttonsThread, nullptr, &thread) == KERN_SUCCESS) thread_deallocate(thread);
  else fail(7, -12);
}
