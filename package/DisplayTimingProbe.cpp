// SPDX-License-Identifier: BSD-2-Clause-Patent
// Display controller state (boot argument dpu=1..3), from sheng's package/DisplayTimingProbe.cpp (refs/UniXNU_UniXNU-sheng 762e796),
// unchanged but for this comment and zeus's pipe list (8 pipes). A read-only look at what the boot loader left running (dpu=3 also re-latches the VIG pipes on CTL 0).
// zeus facts (device/dt/live-merged.dts, qcom,sde-kms at 0xae00000, qcom,sde-off 0x1000): the MDP block is at 0xae01000 as on sheng; the
// DSI interfaces sit at mdp_phys + 0x36000 / 0x37000 (sde-intf-off, types dp/dsi/dsi/dp) = MDP + 0x35000 / 0x36000, CTL 0..5 at
// mdp_phys + 0x16000 + 0x1000 * n = MDP + 0x15000 + ..., so sheng's offsets name the same blocks. The panel (l2_38_0c_0a) is DSI command
// mode with DSC, so the tear-check and autorefresh words (dpu=2) say how often the hardware sends the framebuffer to it.
#include <IOKit/IOLib.h>
#include <IOKit/IOMemoryDescriptor.h>
#include <IOKit/IODeviceMemory.h>
#include <kern/thread.h>
#include <pexpert/pexpert.h>

// [0] stage (1 mapped, 2 first sample, 3 second sample; negative: the step that failed), [1] MDP hardware version,
// per interface i (0, 1) at [2 + 8 * i]: timing engine enable, config, frame/line counter enable, frame count at the first sample,
// frame count one second later, line count, vsync period (lines), status; [18] microseconds between the two samples; [19] the frame period
// in microseconds, timed on the line counter of interface 1 (0: the counter does not move). The panel's refresh rate is that period's
// inverse, or the frames counted in [18]; the display driver's own refresh tick (boot argument vsync) is a timer and says nothing about it.
extern "C" {volatile uint32_t native_dpu_stats[20];}
// Per pipe (VIG 0..3, DMA 0..5): source size, source address 0, stride 0, source format; [40] the boot framebuffer's physical address
// (low 32 bits), [41] its high bits; per CTL 0..3 at [42 + 3 * n]: layer mixer 0 setting, flush, interface active.
extern "C" {volatile uint32_t native_dpu_pipes[54];}
// dpu=2: VIG 0 and 1 at [10 * k]: source x/y, output size, output x/y, source address 1, unpack pattern, operation mode, UBWC control,
// fetch configuration, address status; CTL 0 at [20]: top, start, fetch pipes active, interface flush, pipes active, layers active, interface
// master; interfaces 1 and 2 at [27 + 2 * i]: tear check enable, autorefresh configuration.
// dpu=3 also writes each VIG pipe's current address back and flushes the two pipes on CTL 0 (the picture cannot change), to time the
// latch: [31] flush before, [32] right after the write, [33] microseconds until the pipe bits clear (all ones: still set after 100 ms),
// [34] flush at the end, [35] interface 1 frame count before, [36] after. Every level: [37] and [38] the horizontal sync control of interfaces
// 1 and 2 (pulse width, period << 16), which with the vsync period names the panel mode the boot loader set.
extern "C" {volatile uint32_t native_dpu_more[40];}

namespace {
constexpr uint64_t kMdp = 0xae01000, kMdpBytes = 0x8f000;
constexpr uint32_t kIntf[2] = {0x35000, 0x36000};
constexpr uint32_t kTimingEnable = 0x6c, kConfig = 0x04, kCountEnable = 0xa8, kFrameCount = 0xac, kLineCount = 0xb0,
                   kVsyncPeriod = 0x0c, kStatus = 0x26c, kHsyncControl = 0x08;
// zeus (SM8450, DPU 8.1) has 4 VIG and 4 DMA pipes (live DT qcom,sde-sspp-off 0x5000..0xb000, 0x25000..0x2b000 from mdp_phys);
// sheng's DMA 4 and 5 (0x2c000, 0x2e000) do not exist here and reading them stalled the phone (2026-11-19), so slots 8 and 9 stay 0.
constexpr uint32_t kPipes[10] = {0x4000, 0x6000, 0x8000, 0xa000, 0x24000, 0x26000, 0x28000, 0x2a000, 0, 0};
constexpr unsigned kPipeCount = 8;
constexpr uint32_t kSrcSize = 0x00, kSrc0 = 0x14, kStride0 = 0x24, kFormat = 0x30;
constexpr uint32_t kCtl = 0x15000, kCtlStride = 0x1000, kCtlLayer0 = 0x00, kCtlFlush = 0x18, kCtlIntfActive = 0xf4;
constexpr uint32_t kPipeMore[9] = {0x08, 0x0c, 0x10, 0x18, 0x34, 0x38, 0x44, 0x48, 0x70};
constexpr uint32_t kCtlMore[7] = {0x14, 0x1c, 0xfc, 0x110, 0x12c, 0x130, 0x134};
constexpr uint32_t kTearCheck = 0x284, kAutorefresh = 0x2b4;
unsigned gLevel;
// dpu-autorefresh=N (zeus): the boot loader leaves interface 1's autorefresh on with a frame count of 60 (INTF_TEAR_AUTOREFRESH_CONFIG
// 0x8000003c), so the command-mode panel gets the framebuffer once per 60 tear pulses: 1 Hz measured (2026-11-19) while the render server
// swaps at 60 Hz. N (1..255) replaces the count, only on an interface whose autorefresh is already enabled. native_dpu_autorefresh:
// [0] interface 1 before, [1] after, [2] interface 2 before, [3] after, [4] frames counted in one second after the change.
unsigned gAutorefresh;
}  // namespace
extern "C" {volatile uint32_t native_dpu_autorefresh[5];}
namespace {

// The frame period in microseconds on an interface's line counter: the shortest time between two wraps seen within 100 ms. A wrap is a count
// that fell below half the one before it; one missed while the thread was off the CPU only makes an interval longer, so the shortest
// is the period. 0 when the counter does not move.
uint32_t framePeriodUs(volatile uint32_t *mdp, uint32_t intf) {
    const uint64_t began = mach_absolute_time();
    uint64_t lastWrap = 0;
    uint32_t best = 0, previous = mdp[(intf + kLineCount) / 4];
    for (;;) {
        const uint32_t line = mdp[(intf + kLineCount) / 4];
        const uint64_t now = mach_absolute_time();
        uint64_t ns = 0;
        absolutetime_to_nanoseconds(now - began, &ns);
        if (ns > 100000000ull) break;
        if (line < previous / 2) {
            if (lastWrap) {
                uint64_t gap = 0;
                absolutetime_to_nanoseconds(now - lastWrap, &gap);
                const uint32_t us = uint32_t(gap / 1000);
                if (us && (!best || us < best)) best = us;
            }
            lastWrap = now;
        }
        previous = line;
        IODelay(10);
    }
    return best;
}

void dpuThread(void *, wait_result_t) {
    thread_set_thread_name(current_thread(), "dpu");
    if (gAutorefresh) {
        IODeviceMemory *early = IODeviceMemory::withRange(kMdp, kMdpBytes);
        IOMemoryMap *emap = early ? early->map(kIOMapAnywhere | kIOMapInhibitCache) : nullptr;
        if (early) early->release();
        if (emap) {
            volatile uint32_t *m = reinterpret_cast<volatile uint32_t *>(emap->getVirtualAddress());
            for (unsigned i = 0; i < 2; ++i) {
                volatile uint32_t *reg = &m[(kIntf[i] + kAutorefresh) / 4];
                const uint32_t before = *reg;
                native_dpu_autorefresh[2 * i] = before;
                if (before & 0x80000000u) {*reg = 0x80000000u | gAutorefresh; __asm__ volatile("dsb sy" ::: "memory");}
                native_dpu_autorefresh[2 * i + 1] = *reg;
            }
            const uint32_t f0 = m[(kIntf[0] + kFrameCount) / 4];
            IOSleep(1000);
            native_dpu_autorefresh[4] = m[(kIntf[0] + kFrameCount) / 4] - f0;
            IOLog("zeus-dpu: autorefresh i1 %#x -> %#x, i2 %#x -> %#x, interface 1 sent %u frames in 1 s
", native_dpu_autorefresh[0],
                  native_dpu_autorefresh[1], native_dpu_autorefresh[2], native_dpu_autorefresh[3], native_dpu_autorefresh[4]);
            emap->release();
        }
        if (!gLevel) return;
    }
    // After the render server has taken the display (the first desktop frame comes at about two minutes).
    IOSleep(150000);
    IODeviceMemory *memory = IODeviceMemory::withRange(kMdp, kMdpBytes);
    IOMemoryMap *map = memory ? memory->map(kIOMapAnywhere | kIOMapInhibitCache) : nullptr;
    if (memory) memory->release();
    if (!map) {native_dpu_stats[0] = uint32_t(-1); return;}
    volatile uint32_t *mdp = reinterpret_cast<volatile uint32_t *>(map->getVirtualAddress());
    auto rd = [&](uint32_t offset) {return mdp[offset / 4];};
    native_dpu_stats[0] = 1;
    native_dpu_stats[1] = rd(0);
    for (unsigned i = 0; i < 2; ++i) {
        const uint32_t b = kIntf[i];
        volatile uint32_t *s = &native_dpu_stats[2 + 8 * i];
        s[0] = rd(b + kTimingEnable); s[1] = rd(b + kConfig); s[2] = rd(b + kCountEnable); s[3] = rd(b + kFrameCount);
        s[5] = rd(b + kLineCount); s[6] = rd(b + kVsyncPeriod); s[7] = rd(b + kStatus);
    }
    native_dpu_stats[0] = 2;
    const uint64_t began = mach_absolute_time();
    IOSleep(1000);
    uint64_t elapsed = 0;
    for (unsigned i = 0; i < 2; ++i) native_dpu_stats[2 + 8 * i + 4] = rd(kIntf[i] + kFrameCount);
    absolutetime_to_nanoseconds(mach_absolute_time() - began, &elapsed);
    native_dpu_stats[18] = uint32_t(elapsed / 1000);
    native_dpu_stats[19] = framePeriodUs(mdp, kIntf[0]);
    for (unsigned i = 0; i < 2; ++i) native_dpu_more[37 + i] = rd(kIntf[i] + kHsyncControl);
    {
        // One line for the system log, which keeps it without the display log group: the rate by both measurements, in thousandths of a hertz.
        const uint32_t frames = native_dpu_stats[6] - native_dpu_stats[5], us = native_dpu_stats[18], period = native_dpu_stats[19];
        const uint32_t counted = us ? uint32_t(uint64_t(frames) * 1000000000ull / us) : 0, timed = period ? 1000000000u / period : 0;
        IOLog("sheng-dpu: interface 1 counted %u frames in %u us (%u.%03u Hz), frame period %u us (%u.%03u Hz), hsync %#x vsync period %u, "
              "timing %u counters %u\n", frames, us, counted / 1000, counted % 1000, period, timed / 1000, timed % 1000, native_dpu_more[37],
              native_dpu_stats[8], native_dpu_stats[2], native_dpu_stats[4]);
    }
    for (unsigned p = 0; p < kPipeCount; ++p) {
        native_dpu_pipes[4 * p] = rd(kPipes[p] + kSrcSize); native_dpu_pipes[4 * p + 1] = rd(kPipes[p] + kSrc0);
        native_dpu_pipes[4 * p + 2] = rd(kPipes[p] + kStride0); native_dpu_pipes[4 * p + 3] = rd(kPipes[p] + kFormat);
    }
    PE_Video video = {};
    if (!PE_current_console(&video)) {native_dpu_pipes[40] = uint32_t(video.v_baseAddr); native_dpu_pipes[41] = uint32_t(uint64_t(video.v_baseAddr) >> 32);}
    for (unsigned c = 0; c < 4; ++c) {
        const uint32_t b = kCtl + c * kCtlStride;
        native_dpu_pipes[42 + 3 * c] = rd(b + kCtlLayer0); native_dpu_pipes[43 + 3 * c] = rd(b + kCtlFlush);
        native_dpu_pipes[44 + 3 * c] = rd(b + kCtlIntfActive);
    }
    if (gLevel >= 2) {
        for (unsigned k = 0; k < 2; ++k)
            for (unsigned f = 0; f < 9; ++f) native_dpu_more[10 * k + f] = rd(kPipes[k] + kPipeMore[f]);
        for (unsigned f = 0; f < 7; ++f) native_dpu_more[20 + f] = rd(kCtl + kCtlMore[f]);
        for (unsigned i = 0; i < 2; ++i) {
            native_dpu_more[27 + 2 * i] = rd(kIntf[i] + kTearCheck); native_dpu_more[28 + 2 * i] = rd(kIntf[i] + kAutorefresh);
        }
    }
    if (gLevel >= 3) {
        native_dpu_more[35] = rd(kIntf[0] + kFrameCount);
        native_dpu_more[31] = rd(kCtl + kCtlFlush);
        for (unsigned k = 0; k < 2; ++k) mdp[(kPipes[k] + kSrc0) / 4] = rd(kPipes[k] + kSrc0);
        __asm__ volatile("dsb sy" ::: "memory");
        mdp[(kCtl + kCtlFlush) / 4] = 0x3;
        __asm__ volatile("dsb sy" ::: "memory");
        const uint64_t start = mach_absolute_time();
        native_dpu_more[32] = rd(kCtl + kCtlFlush);
        uint32_t waited = 0xffffffffU;
        for (unsigned spin = 0; spin < 2000; ++spin) {
            if (!(rd(kCtl + kCtlFlush) & 0x3)) {
                uint64_t ns = 0;
                absolutetime_to_nanoseconds(mach_absolute_time() - start, &ns);
                waited = uint32_t(ns / 1000);
                break;
            }
            IODelay(50);
        }
        native_dpu_more[33] = waited;
        native_dpu_more[34] = rd(kCtl + kCtlFlush);
        native_dpu_more[36] = rd(kIntf[0] + kFrameCount);
    }
    native_dpu_stats[0] = 3;
    map->release();
}
}  // namespace

extern "C" void sheng_dpu_probe(void) {
    unsigned enabled = 0, autorefresh = 0;
    if (!PE_parse_boot_argn("dpu", &enabled, sizeof enabled) || enabled > 3) enabled = 0;
    if (!PE_parse_boot_argn("dpu-autorefresh", &autorefresh, sizeof autorefresh) || autorefresh > 255) autorefresh = 0;
    if ((!enabled && !autorefresh) || native_dpu_stats[0]) return;
    gLevel = enabled;
    gAutorefresh = autorefresh;
    thread_t thread = nullptr;
    if (kernel_thread_start(dpuThread, nullptr, &thread) == KERN_SUCCESS) thread_deallocate(thread);
    else native_dpu_stats[0] = uint32_t(-7);
}
