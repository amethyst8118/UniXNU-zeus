"""Make patches/xnu-12377/zeus-kcost.patch: kernel cost counters for the board log's #kcost rows (run on the Mac).

usage: python3 mk-kcost-patch.py XNU_WORK_TREE OUT.patch
XNU_WORK_TREE is the build's patched xnu source (out/.../tree/.cache/xnu-work) WITHOUT zeus-kcost.patch applied (git apply -R it
first if a build applied it): the patch is last in the series, so its context is the other patches' result.

Counters (all relaxed atomics; BootLog.c reads them):
- zeus_kcost[kind] = {calls, mach ticks, longest call since the last read, bytes}: 0 user faults that reach vm_fault()
  (arm64 handle_user_abort), 1 madvise(), 2 work_interval_ctl(), 3 vm_object_deactivate_pages() for MADV_FREE_REUSABLE
  (vm_map_reusable_pages: the per-page pmap work, each page's TLB maintenance done at once since pmap_flush is a no-op on arm).
- zeus_kcost_ftype[type] = {calls, ticks}: vm_fault_internal() on user maps by kdebug fault type (1 zero fill, 2 page-in,
  3 copy-on-write, 4 cache hit, 6 guard, 7/8 page-in from file/device, 9 compressor, 10 swap-in).
- zeus_kcost_madv[advice] = {calls, ticks, bytes} by MADV_* code; zeus_kcost_mpid[slot] = {pid, calls, bytes} (32 slots; the
  reader ranks and clears them each round).
"""
import difflib
import sys
from pathlib import Path

tree, out = Path(sys.argv[1]), Path(sys.argv[2])
edits = {}
DECL = 'extern void zeus_kcost_note(unsigned kind, uint64_t ticks, uint64_t bytes);\n'


def edit(rel, pairs):
    src = (tree / rel).read_text()
    new = src
    for old, rep in pairs:
        assert new.count(old) == 1, (rel, old[:70], new.count(old))
        new = new.replace(old, rep)
    edits[rel] = (src, new)


edit('osfmk/arm64/sleh.c', [
    ('static int is_vm_fault(fault_status_t);\n',
     'static int is_vm_fault(fault_status_t);\n'
     '/* zeus #kcost (bsd/kern/sys_work_interval.c): count and time of the user faults that reach vm_fault(). */\n' + DECL),
    ('\t\t\t\t/* We have to fault the page in */\n'
     '\t\t\t\tresult = vm_fault(map, vm_fault_addr, fault_type,\n'
     '\t\t\t\t    /* change_wiring */ FALSE, VM_KERN_MEMORY_NONE, THREAD_ABORTSAFE,\n'
     '\t\t\t\t    /* caller_pmap */ NULL, /* caller_pmap_addr */ 0);\n',
     '\t\t\t\t/* We have to fault the page in */\n'
     '\t\t\t\tconst uint64_t zeus_kcost_t0 = mach_absolute_time();\n'
     '\t\t\t\tresult = vm_fault(map, vm_fault_addr, fault_type,\n'
     '\t\t\t\t    /* change_wiring */ FALSE, VM_KERN_MEMORY_NONE, THREAD_ABORTSAFE,\n'
     '\t\t\t\t    /* caller_pmap */ NULL, /* caller_pmap_addr */ 0);\n'
     '\t\t\t\tzeus_kcost_note(0, mach_absolute_time() - zeus_kcost_t0, 0);\n'),
])

edit('bsd/kern/kern_mman.c', [
    ('int\nmadvise(__unused proc_t p, struct madvise_args *uap, __unused int32_t *retval)\n{\n',
     '/* zeus #kcost (bsd/kern/sys_work_interval.c): madvise() count, time and bytes, by advice and by process. */\n' + DECL +
     'extern void zeus_kcost_madvise(int advice, uint64_t ticks, uint64_t bytes, int pid);\n'
     'extern uint64_t mach_absolute_time(void);\n'
     'static int madvise_zeus_inner(proc_t p, struct madvise_args *uap, int32_t *retval);\n'
     'int\nmadvise(proc_t p, struct madvise_args *uap, int32_t *retval)\n{\n'
     '\tconst uint64_t zeus_kcost_t0 = mach_absolute_time();\n'
     '\tconst int zeus_kcost_error = madvise_zeus_inner(p, uap, retval);\n'
     '\tconst uint64_t zeus_kcost_ticks = mach_absolute_time() - zeus_kcost_t0;\n'
     '\tconst uint64_t zeus_kcost_bytes = VM_SANITIZE_UNSAFE_UNWRAP(uap->len);\n'
     '\tzeus_kcost_note(1, zeus_kcost_ticks, zeus_kcost_bytes);\n'
     '\tzeus_kcost_madvise(uap->behav, zeus_kcost_ticks, zeus_kcost_bytes, proc_pid(p));\n'
     '\treturn zeus_kcost_error;\n'
     '}\n\n'
     'static int\nmadvise_zeus_inner(__unused proc_t p, struct madvise_args *uap, __unused int32_t *retval)\n{\n'),
])

edit('osfmk/vm/vm_fault.c', [
    ('\tKDBG_RELEASE(\n'
     '\t\t(VMDBG_CODE(DBG_VM_FAULT_INTERNAL)) | DBG_FUNC_START,\n'
     '\t\t((uint64_t)trace_vaddr >> 32),\n'
     '\t\ttrace_vaddr,\n'
     '\t\t(map == kernel_map));\n',
     '\tKDBG_RELEASE(\n'
     '\t\t(VMDBG_CODE(DBG_VM_FAULT_INTERNAL)) | DBG_FUNC_START,\n'
     '\t\t((uint64_t)trace_vaddr >> 32),\n'
     '\t\ttrace_vaddr,\n'
     '\t\t(map == kernel_map));\n'
     '\t/* zeus #kcost (bsd/kern/sys_work_interval.c): user faults by type, timed from here to the trace point at the end. */\n'
     '\textern uint64_t mach_absolute_time(void);\n'
     '\tconst uint64_t zeus_kcost_t0 = mach_absolute_time();\n'),
    ('\tif (rtfault) {\n'
     '\t\tvm_record_rtfault(cthread, fstart, trace_vaddr, type_of_fault);\n'
     '\t}\n',
     '\tif (rtfault) {\n'
     '\t\tvm_record_rtfault(cthread, fstart, trace_vaddr, type_of_fault);\n'
     '\t}\n'
     '\tif (original_map != kernel_map) {\n'
     '\t\textern void zeus_kcost_fault_type(unsigned type, uint64_t ticks);\n'
     '\t\tzeus_kcost_fault_type((unsigned)type_of_fault, mach_absolute_time() - zeus_kcost_t0);\n'
     '\t}\n'),
])

edit('osfmk/vm/vm_map.c', [
    ('\t\t\tvm_object_deactivate_pages(object, obj_start, obj_size,\n'
     '\t\t\t    1, TRUE /*reusable_pages*/,\n'
     '\t\t\t    kill_no_write, ctx->vmlc_map->pmap, pmap_offset);\n',
     '\t\t\textern uint64_t mach_absolute_time(void);\n'
     '\t\t\tconst uint64_t zeus_kcost_t0 = mach_absolute_time();\n'
     '\t\t\tvm_object_deactivate_pages(object, obj_start, obj_size,\n'
     '\t\t\t    1, TRUE /*reusable_pages*/,\n'
     '\t\t\t    kill_no_write, ctx->vmlc_map->pmap, pmap_offset);\n'
     '\t\t\t{\n'
     '\t\t\t\t/* zeus #kcost (bsd/kern/sys_work_interval.c): the per-page work of MADV_FREE_REUSABLE. */\n'
     '\t\t\t\textern void zeus_kcost_note(unsigned kind, uint64_t ticks, uint64_t bytes);\n'
     '\t\t\t\tzeus_kcost_note(3, mach_absolute_time() - zeus_kcost_t0, obj_size);\n'
     '\t\t\t}\n'),
])

COUNTERS = r'''/*
 * zeus #kcost: kernel cost counters, read by the board log (package/BootLog.c, the #kcost rows); made by
 * UniXNU-zeus scripts/mk-kcost-patch.py, which lists the counters. gpum74/75: SpringBoard took ~14,000 soft faults a second while
 * swiping, and madvise ran 3,600 times a second at 57 us, advising ~800 MB/s.
 */
extern uint64_t mach_absolute_time(void);
uint64_t zeus_kcost[8][4];
uint64_t zeus_kcost_ftype[16][2];
uint64_t zeus_kcost_madv[16][3];
uint64_t zeus_kcost_mpid[32][3];
void zeus_kcost_note(unsigned kind, uint64_t ticks, uint64_t bytes);
void zeus_kcost_fault_type(unsigned type, uint64_t ticks);
void zeus_kcost_madvise(int advice, uint64_t ticks, uint64_t bytes, int pid);
void
zeus_kcost_note(unsigned kind, uint64_t ticks, uint64_t bytes)
{
	if (kind >= 8) {
		return;
	}
	__atomic_fetch_add(&zeus_kcost[kind][0], 1, __ATOMIC_RELAXED);
	__atomic_fetch_add(&zeus_kcost[kind][1], ticks, __ATOMIC_RELAXED);
	__atomic_fetch_add(&zeus_kcost[kind][3], bytes, __ATOMIC_RELAXED);
	uint64_t longest = __atomic_load_n(&zeus_kcost[kind][2], __ATOMIC_RELAXED);
	while (ticks > longest &&
	    !__atomic_compare_exchange_n(&zeus_kcost[kind][2], &longest, ticks, 1, __ATOMIC_RELAXED, __ATOMIC_RELAXED)) {
	}
}
void
zeus_kcost_fault_type(unsigned type, uint64_t ticks)
{
	type &= 15;
	__atomic_fetch_add(&zeus_kcost_ftype[type][0], 1, __ATOMIC_RELAXED);
	__atomic_fetch_add(&zeus_kcost_ftype[type][1], ticks, __ATOMIC_RELAXED);
}
void
zeus_kcost_madvise(int advice, uint64_t ticks, uint64_t bytes, int pid)
{
	const unsigned a = (unsigned)advice & 15;
	__atomic_fetch_add(&zeus_kcost_madv[a][0], 1, __ATOMIC_RELAXED);
	__atomic_fetch_add(&zeus_kcost_madv[a][1], ticks, __ATOMIC_RELAXED);
	__atomic_fetch_add(&zeus_kcost_madv[a][2], bytes, __ATOMIC_RELAXED);
	/* by process: the slot holding pid, else the first free one (the reader clears them each round); full: dropped */
	for (unsigned i = 0, s = (unsigned)pid % 32; i < 32; ++i, s = (s + 1) % 32) {
		uint64_t have = __atomic_load_n(&zeus_kcost_mpid[s][0], __ATOMIC_RELAXED);
		if (have == 0) {
			uint64_t expected = 0;
			if (!__atomic_compare_exchange_n(&zeus_kcost_mpid[s][0], &expected, (uint64_t)pid + 1, 0, __ATOMIC_RELAXED, __ATOMIC_RELAXED)) {
				have = expected;
			} else {
				have = (uint64_t)pid + 1;
			}
		}
		if (have == (uint64_t)pid + 1) {
			__atomic_fetch_add(&zeus_kcost_mpid[s][1], 1, __ATOMIC_RELAXED);
			__atomic_fetch_add(&zeus_kcost_mpid[s][2], bytes, __ATOMIC_RELAXED);
			return;
		}
	}
}

'''

edit('bsd/kern/sys_work_interval.c', [
    ('int\nwork_interval_ctl(__unused proc_t p, struct work_interval_ctl_args *uap,\n    __unused int32_t *retval)\n{\n',
     COUNTERS +
     'static int work_interval_ctl_zeus_inner(proc_t p, struct work_interval_ctl_args *uap, int32_t *retval);\n'
     'int\nwork_interval_ctl(proc_t p, struct work_interval_ctl_args *uap, int32_t *retval)\n{\n'
     '\tconst uint64_t zeus_kcost_t0 = mach_absolute_time();\n'
     '\tconst int zeus_kcost_error = work_interval_ctl_zeus_inner(p, uap, retval);\n'
     '\tzeus_kcost_note(2, mach_absolute_time() - zeus_kcost_t0, 0);\n'
     '\treturn zeus_kcost_error;\n'
     '}\n\n'
     'static int\nwork_interval_ctl_zeus_inner(__unused proc_t p, struct work_interval_ctl_args *uap,\n    __unused int32_t *retval)\n{\n'),
])

text = ''
for rel, (a, b) in edits.items():
    diff = difflib.unified_diff(a.splitlines(True), b.splitlines(True), f'a/{rel}', f'b/{rel}', n=3)
    text += f'diff --git a/{rel} b/{rel}\n' + ''.join(diff)
head = ('zeus #kcost: kernel cost counters (user faults by type, madvise by advice and process, MADV_FREE_REUSABLE page work,\n'
        'work_interval_ctl) for the board log. Generated by scripts/mk-kcost-patch.py.\n\n')
out.write_text(head + text)
print('ok', out, len(text))
