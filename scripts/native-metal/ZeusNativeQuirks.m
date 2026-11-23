// zeus: what the metal2vulkan plug-in (UniXNU AdrenoMetal + patches/adrenometal/0003) does in the render server and the native
// frontend did not (gpum22, 2026-11-22: backboardd "Failed to allocate [W x H] RGhA framebuffer MTLPixelFormatRGBA16Float", black screen).
// - fb-bgra=1 on 23F84: IOMFBDisplay::framebuffer_pixel_format 0x183ef7874 + 228 = 0x183ef7958 is `tbnz w8, #0` (0x37000608) on
//   CADeviceIsVirtualized's byte, which takes RGhA (RGBA16Float). NOP it so the render server draws 8-bit BGRA, which zeus's display
//   pipes scan out directly (dpu-scanout=2). The slide comes from CARenderServerStart (0x184140ff4). Any other word is left alone.
// - CA_DISABLE_FRAMEBUFFER_COMPRESSION=1, as the plug-in sets it: no Apple framebuffer compression on this GPU.
// Runs when the bundle loads (Metal's discovery loads it before the render server makes its framebuffers).
#import <Foundation/Foundation.h>
#include <dlfcn.h>
#include <libkern/OSCacheControl.h>
#include <objc/runtime.h>
#include <objc/message.h>
#include <mach/mach.h>
#include <ptrauth.h>
#include <stdio.h>
#include <string.h>
#include <sys/sysctl.h>

unsigned zeus_native_fb_result;   // 0 not tried, 0x6E42 patched/already NOP, 0x6E41 other word, 0x6E4x/5x failures

static int zeus_boot_arg(const char *token) {
    char args[1100] = {0};
    size_t size = sizeof args - 1;
    if (sysctlbyname("kern.bootargs", args, &size, NULL, 0) != 0) return 0;
    const size_t n = strlen(token);
    for (const char *at = strstr(args, token); at; at = strstr(at + 1, token))
        if ((at == args || at[-1] == ' ') && (at[n] == 0 || at[n] == ' ')) return 1;
    return 0;
}

static unsigned zeus_patch_branch(uintptr_t site) {
    uint32_t word = 0;
    vm_size_t got = 0;
    if (vm_read_overwrite(mach_task_self(), site, sizeof word, (vm_address_t)&word, &got) != KERN_SUCCESS || got != sizeof word) return 0x6E40;
    if (word == 0xd503201f) return 0x6E42;
    if (word != 0x37000608) return 0x6E41;
    const vm_address_t page = site & ~(uintptr_t)(vm_page_size - 1);
    if (vm_protect(mach_task_self(), page, vm_page_size, FALSE, VM_PROT_READ | VM_PROT_WRITE | VM_PROT_COPY) != KERN_SUCCESS) return 0x6E51;
    const uint32_t nop = 0xd503201f;
    const int wrote = vm_write(mach_task_self(), site, (vm_offset_t)&nop, sizeof nop) == KERN_SUCCESS;
    const int restored = vm_protect(mach_task_self(), page, vm_page_size, FALSE, VM_PROT_READ | VM_PROT_EXECUTE) == KERN_SUCCESS;
    sys_icache_invalidate((void *)site, sizeof nop);
    if (!wrote) return 0x6E52;
    if (!restored) return 0x6E53;
    return 0x6E42;
}

// The frontend and its transport explain every refusal on stderr, which a daemon's launchd job discards (gpum23: CoreAnimation's
// "Failed to allocate [1206 x 2622] BGRA framebuffer" 7931 times, no reason seen). Route stderr to the unified log, line by line.
#include <os/log.h>
#include <pthread.h>
#include <unistd.h>
static int zeus_stderr_read = -1;
static void *zeus_stderr_pump(void *unused) {
    (void)unused;
    pthread_setname_np("zeus-native-stderr");
    char buf[1024];
    size_t used = 0;
    for (;;) {
        const ssize_t n = read(zeus_stderr_read, buf + used, sizeof buf - 1 - used);
        if (n <= 0) return NULL;
        used += (size_t)n;
        char *start = buf, *nl;
        while ((nl = memchr(start, '\n', used - (size_t)(start - buf)))) {
            *nl = 0;
            os_log_error(OS_LOG_DEFAULT, "ZeusNativeMetal stderr: %{public}s", start);
            start = nl + 1;
        }
        used -= (size_t)(start - buf);
        memmove(buf, start, used);
        if (used == sizeof buf - 1) {buf[used] = 0; os_log_error(OS_LOG_DEFAULT, "ZeusNativeMetal stderr: %{public}s", buf); used = 0;}
    }
}
// No dup2: backboardd's sandbox kills a process for it (gpum24: "deny(1) syscall-unix 90", backboardd respawned 21 times, nothing drew).
// The frontend writes through stdio (fprintf(stderr, ...)), so stdio's stderr stream is replaced by a funopen stream whose write
// function hands each line to os_log: no file descriptor and no system call of its own. zeus_stderr_pump/zeus_stderr_read stay unused.
static char zeus_line[1024];
static size_t zeus_line_used;
static pthread_mutex_t zeus_line_lock = PTHREAD_MUTEX_INITIALIZER;
static int zeus_stderr_write(void *cookie, const char *data, int length) {
    (void)cookie;
    pthread_mutex_lock(&zeus_line_lock);
    for (int i = 0; i < length; ++i) {
        const char ch = data[i];
        if (ch == '\n' || zeus_line_used == sizeof zeus_line - 1) {
            zeus_line[zeus_line_used] = 0;
            if (zeus_line_used) os_log_error(OS_LOG_DEFAULT, "ZeusNativeMetal stderr: %{public}s", zeus_line);
            zeus_line_used = 0;
            if (ch == '\n') continue;
        }
        zeus_line[zeus_line_used++] = ch;
    }
    pthread_mutex_unlock(&zeus_line_lock);
    return length;
}
// gpum26: the device frontend's refusals (rw_print, "no drawable: <why>") go to stdout, not stderr: the framebuffer import failed
// with no reason seen. stdout gets its own funopen stream and line buffer.
static char zeus_out_line[1024];
static size_t zeus_out_used;
static int zeus_stdout_write(void *cookie, const char *data, int length) {
    (void)cookie;
    pthread_mutex_lock(&zeus_line_lock);
    for (int i = 0; i < length; ++i) {
        const char ch = data[i];
        if (ch == '\n' || zeus_out_used == sizeof zeus_out_line - 1) {
            zeus_out_line[zeus_out_used] = 0;
            if (zeus_out_used) os_log_error(OS_LOG_DEFAULT, "ZeusNativeMetal stdout: %{public}s", zeus_out_line);
            zeus_out_used = 0;
            if (ch == '\n') continue;
        }
        zeus_out_line[zeus_out_used++] = ch;
    }
    pthread_mutex_unlock(&zeus_line_lock);
    return length;
}
static void zeus_route_stderr(void) {
    (void)zeus_stderr_pump;
    FILE *log = funopen(NULL, NULL, zeus_stderr_write, NULL, NULL);
    if (log) {setvbuf(log, NULL, _IOLBF, 0); stderr = log;}
    FILE *out = funopen(NULL, NULL, zeus_stdout_write, NULL, NULL);
    if (out) {setvbuf(out, NULL, _IOLBF, 0); stdout = out;}
}

// gpum26: what the refused framebuffer surface looks like (first 8 refusals): its mapping, size, rows and the descriptor, so the
// frontend's checks (SweetNativeDrawable.m rwd_texture_for_surface) and the transport's (sweet_drm.cpp surface_description: the
// mapping must be readable and writable) can be read against it.
#include <IOSurface/IOSurfaceRef.h>
static id (*zeus_orig_surface_texture)(id, SEL, id, void *, unsigned long);
static unsigned zeus_surface_refusals;
static void (*zeus_known)(IOSurfaceRef, int);
static id zeus_surface_texture(id self, SEL sel, id d, void *surface, unsigned long plane) {
    // gpum27: the transport cannot IOSurfaceLookup a surface that is not global; it gets the caller's own reference for this call
    // (libSweetNativeBackend's zeus_surface_known, zeus_transport.py). The import retains what it keeps.
    static int looked;
    if (!looked) {looked = 1; zeus_known = (void (*)(IOSurfaceRef, int))dlsym(RTLD_DEFAULT, "zeus_surface_known");
                  os_log_error(OS_LOG_DEFAULT, "ZeusNativeMetal: surface registry %d", zeus_known != NULL);}
    if (zeus_known && surface) zeus_known((IOSurfaceRef)surface, 1);
    id texture = zeus_orig_surface_texture(self, sel, d, surface, plane);
    if (zeus_known && surface) zeus_known((IOSurfaceRef)surface, 0);
    if (texture || !surface || zeus_surface_refusals++ >= 8) return texture;
    IOSurfaceRef s = (IOSurfaceRef)surface;
    const uintptr_t base = (uintptr_t)IOSurfaceGetBaseAddress(s);
    vm_address_t region = base & ~(uintptr_t)16383; vm_size_t length = 0;
    vm_region_basic_info_data_64_t info = {0};
    mach_msg_type_number_t count = VM_REGION_BASIC_INFO_COUNT_64;
    mach_port_t object = MACH_PORT_NULL;
    const kern_return_t kr = vm_region_64(mach_task_self(), &region, &length, VM_REGION_BASIC_INFO_64, (vm_region_info_t)&info, &count, &object);
    if (object) mach_port_deallocate(mach_task_self(), object);
    unsigned long (*u)(id, SEL) = (unsigned long (*)(id, SEL))objc_msgSend;
    os_log_error(OS_LOG_DEFAULT, "ZeusNativeMetal: refused IOSurface %u fmt %#x %zux%zu bpr %zu bpe %zu planes %zu alloc %zu base %#lx "
                 "region kr %d %#lx+%#lx prot %d/%d | desc fmt %lu type %lu %lux%lux%lu arr %lu mips %lu samples %lu usage %#lx storage %lu plane %lu",
                 IOSurfaceGetID(s), IOSurfaceGetPixelFormat(s), IOSurfaceGetWidth(s), IOSurfaceGetHeight(s), IOSurfaceGetBytesPerRow(s),
                 IOSurfaceGetBytesPerElement(s), IOSurfaceGetPlaneCount(s), IOSurfaceGetAllocSize(s), (unsigned long)base,
                 kr, (unsigned long)region, (unsigned long)length, info.protection, info.max_protection,
                 u(d, sel_registerName("pixelFormat")), u(d, sel_registerName("textureType")), u(d, sel_registerName("width")),
                 u(d, sel_registerName("height")), u(d, sel_registerName("depth")), u(d, sel_registerName("arrayLength")),
                 u(d, sel_registerName("mipmapLevelCount")), u(d, sel_registerName("sampleCount")), u(d, sel_registerName("usage")),
                 u(d, sel_registerName("storageMode")), plane);
    return texture;
}
static void zeus_surface_hook(void) {
    Class device = objc_getClass("SweetNativeMetalDevice");
    Method m = device ? class_getInstanceMethod(device, sel_registerName("newTextureWithDescriptor:iosurface:plane:")) : NULL;
    if (m) zeus_orig_surface_texture = (id (*)(id, SEL, id, void *, unsigned long))method_setImplementation(m, (IMP)zeus_surface_texture);
    os_log_error(OS_LOG_DEFAULT, "ZeusNativeMetal: surface refusal hook %d", m != NULL);
}

// UIKit's in-process animation manager asks its CADisplayLink for 20..120 a second, preferred 30: a home-screen swipe settles at
// 30 fps. UniXNU-zizhan (A730, iPadOS 18) dropped those requests for that link only: moving 57 -> 95 fps, late frame gaps 33 -> 8.3 ms
// (docs/progress/2026-11-04-displaylink-device.md; from hakios 7caa457). Other display links keep their rates. The class name changed
// across releases (UIViewInProcessAnimationManager, UIKit.InProcessAnimationManager); both are matched and every drop is counted.
// zeus-dl=0 turns it off.
#include <objc/runtime.h>
#include <objc/message.h>
typedef struct {float minimum, maximum, preferred;} zeus_frame_rate_range;
static id (*zeus_orig_link_with)(id, SEL, id, SEL);
static void (*zeus_orig_link_fps)(id, SEL, long);
static void (*zeus_orig_link_range)(id, SEL, zeus_frame_rate_range);
static char zeus_link_key;
static unsigned zeus_link_marked, zeus_link_dropped;
static int zeus_link_full(id link) {return objc_getAssociatedObject(link, &zeus_link_key) != nil;}
static id zeus_link_with(id self, SEL sel, id target, SEL action) {
    id link = zeus_orig_link_with(self, sel, target, action);
    if (link && target) {
        const char *name = object_getClassName(target);
        if (strstr(name, "InProcessAnimationManager")) {
            objc_setAssociatedObject(link, &zeus_link_key, (id)kCFBooleanTrue, OBJC_ASSOCIATION_ASSIGN);
            if (zeus_link_marked++ < 4) NSLog(@"ZeusNativeMetal: display link for %s runs at the panel's rate", name);
        }
    }
    return link;
}
static void zeus_link_fps(id self, SEL sel, long fps) {
    if (zeus_link_full(self)) {fps = 0; ++zeus_link_dropped;}
    zeus_orig_link_fps(self, sel, fps);
}
static void zeus_link_range(id self, SEL sel, zeus_frame_rate_range range) {
    if (zeus_link_full(self)) {
        if (zeus_link_dropped++ < 4) NSLog(@"ZeusNativeMetal: dropped display link rate %.0f..%.0f preferred %.0f", range.minimum, range.maximum, range.preferred);
        range = (zeus_frame_rate_range){0, 0, 0};
    }
    zeus_orig_link_range(self, sel, range);
}
static void zeus_display_links(void) {
    if (zeus_boot_arg("zeus-dl=0")) return;
    Class link = objc_getClass("CADisplayLink");
    if (!link) return;
    Method made = class_getClassMethod(link, sel_registerName("displayLinkWithTarget:selector:"));
    Method fps = class_getInstanceMethod(link, sel_registerName("setPreferredFramesPerSecond:"));
    Method range = class_getInstanceMethod(link, sel_registerName("setPreferredFrameRateRange:"));
    if (made) zeus_orig_link_with = (id (*)(id, SEL, id, SEL))method_setImplementation(made, (IMP)zeus_link_with);
    if (fps) zeus_orig_link_fps = (void (*)(id, SEL, long))method_setImplementation(fps, (IMP)zeus_link_fps);
    if (range) zeus_orig_link_range = (void (*)(id, SEL, zeus_frame_rate_range))method_setImplementation(range, (IMP)zeus_link_range);
    NSLog(@"ZeusNativeMetal: display link hooks in %s: made %d fps %d range %d", getprogname(), made != NULL, fps != NULL, range != NULL);
}

__attribute__((constructor)) static void zeus_native_quirks(void) {
    zeus_route_stderr();
    zeus_display_links();
    zeus_surface_hook();
    setenv("CA_DISABLE_FRAMEBUFFER_COMPRESSION", "1", 1);
    if (strcmp(getprogname(), "backboardd") || !zeus_boot_arg("fb-bgra=1")) return;
    char build[32] = {0};
    size_t size = sizeof build - 1;
    if (sysctlbyname("kern.osversion", build, &size, NULL, 0) != 0 || strcmp(build, "23F84")) return;
    void *start = dlsym(RTLD_DEFAULT, "CARenderServerStart");
    if (!start) {zeus_native_fb_result = 0x6E43; return;}
    const uintptr_t slide = (uintptr_t)ptrauth_strip(start, ptrauth_key_function_pointer) - 0x184140ff4;
    zeus_native_fb_result = zeus_patch_branch(0x183ef7958 + slide);
    NSLog(@"ZeusNativeMetal: fb-bgra framebuffer branch result %#x", zeus_native_fb_result);
}
