// SPDX-License-Identifier: BSD-2-Clause-Patent
// zeus: lockdownd (iOS 27.0.1 24A446, /usr/libexec/lockdownd sha256 b42e3a6d…) without the optional FairPlay/CESM bring-up.
//
// lockdownd's startup (lockstart_local) logs "_load_product_type: old school Certs" and "dirtied by changing -UseRaptorCerts", then
// calls create_cesm_context (0x100012758, obfuscated FairPlay hardware code) and the CESM certificate extraction (0x1000074e8);
// on hardware without Apple's FairPlay the first never returns (zeus /logs/lockdownd.log: every boot ends after -UseRaptorCerts),
// so lockdownd never posts com.apple.mobile.lockdown, configd's USBDeviceConfiguration waits for it forever and the USB device
// controller is never configured. When the extraction returns NULL, lockdownd's own code takes its "no CESM certificate" branch
// (cbz x0 at 0x100012570) and carries on. Same two edits as UniXNU-diting userland/usb_services18.py does for iPadOS 18 22H374
// (configure_native_binary): the init call becomes a NOP, the extraction call `mov x0, #0`. No pairing, trust or TLS code changes.
//
// The file on the sealed OS volume is not touched. The startup code (0x100011xxx) shares the 16 KiB page with these calls, so by the
// time lockdownd reaches them (about 5 s after "Starting Up") the page is resident, code-signature validated and mapped. The words
// are rewritten in that physical page through the kernel's physical aperture (no user mapping becomes writable, so the page keeps
// its validated state), after checking all five words of the sequence; then the instruction cache is synchronised for the page.
#include <kern/task.h>
#include <vm/pmap.h>
#include <arm/caches_internal.h>
#include <stdint.h>

extern vm_map_address_t phystokv(pmap_paddr_t pa);
extern ppnum_t pmap_find_phys(pmap_t map, addr64_t va);

#define LOCKDOWN_TEXT_VMADDR 0x100000000ull
#define LOCKDOWN_SITE        0x100012560ull     // mov x0,x21 / bl create_cesm_context / mov x0,x21 / bl extract / cbz x0

static const uint32_t original[5] = {0xaa1503e0u, 0x9400007du, 0xaa1503e0u, 0x97ffd3dfu, 0xb4000260u};
static const uint32_t nop = 0xd503201fu, mov_x0_0 = 0xd2800000u;

// 0 patched now, 1 already patched, -1 no load address yet, -2 page not mapped yet, -3 the code differs (another build): left alone.
int
zeus_lockdown_patch_task(task_t task, uint64_t *base_out, uint32_t *page_out)
{
	const mach_vm_address_t base = task->mach_header_vm_address;
	if (base_out) {
		*base_out = base;
	}
	if (base == 0) {
		return -1;
	}
	const addr64_t va = base - LOCKDOWN_TEXT_VMADDR + LOCKDOWN_SITE;
	const ppnum_t pn = pmap_find_phys(get_task_pmap(task), va);
	if (page_out) {
		*page_out = pn;
	}
	if (pn == 0) {
		return -2;
	}
	volatile uint32_t *w = (volatile uint32_t *)(phystokv(((pmap_paddr_t)pn << PAGE_SHIFT) + (va & PAGE_MASK)));
	if (w[0] == original[0] && w[1] == nop && w[2] == original[2] && w[3] == mov_x0_0 && w[4] == original[4]) {
		return 1;
	}
	for (unsigned i = 0; i < 5; i++) {
		if (w[i] != original[i]) {
			return -3;
		}
	}
	w[1] = nop;
	w[3] = mov_x0_0;
	__asm__ volatile ("dsb sy" ::: "memory");
	cache_sync_page(pn);
	return 0;
}
