// SPDX-License-Identifier: BSD-2-Clause-Patent
// The GPU's memory pool outside the kernel's window, after sheng's package/GpuMemoryPool.cpp (refs/UniXNU_UniXNU-sheng 762e796).
// DarwinBoot lists the largest runs of free memory outside the window in /chosen/darwinboot-memory-outside ((base, size) pairs of
// 64-bit values, unused pairs zero; board.toml payload.adt owned_memory_outside_ranges). Each usable part is handed to the GPU driver
// (Adreno740 NativeAdrenoPool), which serves buffer objects from these large physically contiguous runs before it takes 4 KiB pageable
// pages of the window: fewer spans per buffer and fewer SMMU table walks (zeus 2026-11-19: 24012 BOs in 2017609 spans, no pool).
//
// zeus differs from sheng in what it trusts. sheng removes a deny-list of firmware carve-outs from the runs (a CPU store into firmware
// memory is a synchronous external abort). zeus keeps only an allow-list: the memory Android's kernel uses as plain System RAM outside
// XNU's window (logs/phase1/proc_iomem.txt, top-level "System RAM" minus every nested "reserved"; XNU owns 0x840000000 + 0x120000000).
// The low 4 GiB bank (UEFI, the payload, the boot framebuffer at 0xb8000000, modem and DSP carve-outs) is never used.
// gpu-pool-fixed=1: when DarwinBoot lists nothing usable (the UEFI memory map may not describe the high bank), the allow-list itself is
// added. gpu-pool=0 makes the driver ignore the pool.
#include <IOKit/IOLib.h>
#include <IOKit/IORegistryEntry.h>
#include <IOKit/IODeviceTreeSupport.h>
#include <libkern/c++/OSData.h>
#include <pexpert/pexpert.h>
#include <string.h>

extern "C" IOReturn native_adreno_pool_add(uint64_t physical, uint64_t bytes) __attribute__((weak));   // Adreno740 NativeAdrenoPool.cpp
// patches/xnu-12377/zeus-pmap-extra-dram.patch: the run becomes DRAM for the pmap (write-combined → Normal NC, default → write-back)
// instead of Device memory. 0 on success. A run the pmap refuses is never handed to the GPU driver (boot-pool3: Device-GRE pool BOs
// crashed backboardd).
extern "C" int zeus_pmap_add_dram(uint64_t base, uint64_t size) __attribute__((weak));
// For the boot log ([gpu pool] board): [0] runs offered, [1] runs the driver took, [2] KiB the driver took, [3] the last refusal.
extern "C" {volatile uint32_t native_gpu_pool_board[4];}
// KiB of DarwinBoot's runs left out because they are not on the allow-list (BootLog prints it as stock-reserved).
extern "C" {volatile uint32_t native_gpu_pool_reserved_kib;}

namespace {
struct Range {uint64_t base, end;};
// Android /proc/iomem (zeus, OS3.0.3.0): 800000000-8396fffff, 839d00000-83bafffff System RAM; 840000000-97fffffff System RAM with
// 96ef00000.. reserved. XNU's window is 0x840000000..0x960000000.
constexpr Range kAllowed[] = {
    {0x800000000ull, 0x839700000ull},
    {0x839d00000ull, 0x83bb00000ull},
    {0x960000000ull, 0x96ef00000ull},
};

void addRun(uint64_t base, uint64_t end)
{
    base = (base + 4095) & ~4095ull;
    end &= ~4095ull;
    if (end <= base || end - base < (1ull << 20)) return;
    native_gpu_pool_board[0] = native_gpu_pool_board[0] + 1;
    const int dram = zeus_pmap_add_dram ? zeus_pmap_add_dram(base, end - base) : -1;
    if (dram) {
        native_gpu_pool_board[3] = 0xd0000000u | uint32_t(dram & 0xff);
        IOLog("zeus gpu pool: 0x%llx + 0x%llx: pmap refused (%d), not used\n", (unsigned long long)base, (unsigned long long)(end - base), dram);
        return;
    }
    const IOReturn status = native_adreno_pool_add(base, end - base);
    if (status == kIOReturnSuccess) {
        native_gpu_pool_board[1] = native_gpu_pool_board[1] + 1;
        native_gpu_pool_board[2] = native_gpu_pool_board[2] + uint32_t((end - base) >> 10);
    } else {
        native_gpu_pool_board[3] = uint32_t(status);
    }
    IOLog("zeus gpu pool: 0x%llx + 0x%llx: %x\n", (unsigned long long)base, (unsigned long long)(end - base), status);
}

// The parts of [base, end) on the allow-list; the rest is counted as left out.
void addAllowed(uint64_t base, uint64_t end)
{
    uint64_t kept = 0;
    for (const Range &r : kAllowed) {
        const uint64_t lo = base > r.base ? base : r.base, hi = end < r.end ? end : r.end;
        if (hi <= lo) continue;
        kept += hi - lo;
        addRun(lo, hi);
    }
    if (end > base) native_gpu_pool_reserved_kib = native_gpu_pool_reserved_kib + uint32_t(((end - base) - kept) >> 10);
}
}  // namespace

extern "C" void sheng_gpu_pool_probe(void)
{
    if (!native_adreno_pool_add) return;
    unsigned fixed = 0;
    PE_parse_boot_argn("gpu-pool-fixed", &fixed, sizeof fixed);
    IORegistryEntry *chosen = IORegistryEntry::fromPath("/chosen", gIODTPlane);
    if (chosen) {
        OSData *table = OSDynamicCast(OSData, chosen->getProperty("darwinboot-memory-outside"));
        if (table && table->getLength() >= 16) {
            const uint8_t *bytes = static_cast<const uint8_t *>(table->getBytesNoCopy());
            for (unsigned i = 0; i + 16 <= table->getLength(); i += 16) {
                uint64_t base = 0, size = 0;
                memcpy(&base, bytes + i, 8);
                memcpy(&size, bytes + i + 8, 8);
                if (!size || base > UINT64_MAX - size) continue;
                IOLog("zeus gpu pool: DarwinBoot outside run 0x%llx + 0x%llx\n", (unsigned long long)base, (unsigned long long)size);
                addAllowed(base, base + size);
            }
        }
        chosen->release();
    }
    if (!native_gpu_pool_board[1] && fixed == 1)
        for (const Range &r : kAllowed) addRun(r.base, r.end);
}
