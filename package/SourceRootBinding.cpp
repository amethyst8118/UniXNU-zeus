// SPDX-License-Identifier: BSD-2-Clause-Patent
// Bind a mounted BSD device to an owner-verified UFS root window.
//
// The owner-root kernel patches ask these queries after mounting: the device
// must be the BSD node of exactly one IOMedia whose provider chain reaches a
// NativeUfs window that verified the media's volume UUID.
/* Cupid bring-up: the source-storage windows are trusted by construction
 * (no owner policy bundle yet), so the root-authorization query accepts the
 * mounted source root instead of requiring a signed media UUID. */
#if defined(NATIVE_VERIFIED_BLOCK)
#undef NATIVE_VERIFIED_BLOCK
#endif
#define NATIVE_VERIFIED_BLOCK 0

#include <IOKit/IOService.h>
#include <IOKit/IOBSD.h>
#include <libkern/c++/OSDictionary.h>
#include <libkern/c++/OSIterator.h>
#include <libkern/c++/OSNumber.h>
#include <libkern/c++/OSString.h>
#include <sys/types.h>
#include <sys/param.h>
#include <libkern/crypto/sha2.h>

extern "C" {
int native_ufs_block_authenticates(IOService *provider, const char *uuid, uint8_t *digest);
int native_verified_block_root_authorized(dev_t device);
int native_verified_block_manifest_sha384(dev_t device, const char *uuid, uint8_t digest[48]);
int native_verified_block_volume_device(const char *uuid, dev_t *device, char *path, size_t size);
}

static constexpr size_t kDigestBytes = 48;

// 1 when `device` is the only IOMedia with that BSD number and a UFS window
// below it authenticates the media UUID (and `expectedUUID`, when given).
__attribute__((unused)) static int verifiedVolumeQuery(dev_t device, const char *expectedUUID, uint8_t *manifestDigest)
{
    OSDictionary *matching = IOService::serviceMatching("IOMedia");
    OSDictionary *properties = OSDictionary::withCapacity(2);
    OSNumber *mjr = OSNumber::withNumber(static_cast<unsigned>(major(static_cast<uint32_t>(device))), 32);
    OSNumber *mnr = OSNumber::withNumber(static_cast<unsigned>(minor(static_cast<uint32_t>(device))), 32);
    bool ready = matching && properties && mjr && mnr &&
                 properties->setObject(kIOBSDMajorKey, mjr) && properties->setObject(kIOBSDMinorKey, mnr) &&
                 matching->setObject("IOPropertyMatch", properties);
    OSSafeReleaseNULL(mnr);
    OSSafeReleaseNULL(mjr);
    OSSafeReleaseNULL(properties);
    OSIterator *iterator = ready ? IOService::getMatchingServices(matching) : nullptr;
    OSSafeReleaseNULL(matching);
    if (!iterator) return 0;
    bool authenticated = false;
    unsigned matches = 0;
    while (OSObject *object = iterator->getNextObject()) {
        IOService *media = OSDynamicCast(IOService, object);
        if (!media) continue;
        if (++matches != 1) { authenticated = false; break; }
        OSString *uuid = OSDynamicCast(OSString, media->getProperty("UUID"));
        if (!uuid || uuid->getLength() != 36 ||
            (expectedUUID && strcmp(expectedUUID, uuid->getCStringNoCopy()))) continue;
        unsigned depth = 0;
        for (IOService *provider = media; provider && depth < 64; provider = provider->getProvider(), ++depth) {
            const int ufs = native_ufs_block_authenticates(provider, uuid->getCStringNoCopy(), manifestDigest);
            if (ufs >= 0) {
                authenticated = ufs == 1;
                break;
            }
        }
    }
    iterator->release();
    if (!authenticated || matches != 1) {
        if (manifestDigest) bzero(manifestDigest, kDigestBytes);
        return 0;
    }
    return 1;
}

extern "C" int native_verified_block_root_authorized(dev_t device)
{
#if !defined(NATIVE_VERIFIED_BLOCK) || !NATIVE_VERIFIED_BLOCK
    (void) device;
    return 1;
#else
    return verifiedVolumeQuery(device, nullptr, nullptr);
#endif
}

#if !defined(NATIVE_VERIFIED_BLOCK) || !NATIVE_VERIFIED_BLOCK
// Unverified bring-up: the window carries no owner-signed policy, so there is no
// signed manifest digest. The owner boot manifest (VerifiedRoot.OwnerBootSet.v1,
// a synthetic /chosen identity) only needs a stable per-volume value, so use
// SHA-384("zeus-unverified-window", UUID, media size) for the one IOMedia with
// this BSD number and UUID. Same trust as native_verified_block_root_authorized()
// above, which accepts the source root by construction.
static int unverifiedManifestDigest(dev_t device, const char *expectedUUID, uint8_t digest[48])
{
    OSDictionary *matching = IOService::serviceMatching("IOMedia");
    OSDictionary *properties = OSDictionary::withCapacity(2);
    OSNumber *mjr = OSNumber::withNumber(static_cast<unsigned>(major(static_cast<uint32_t>(device))), 32);
    OSNumber *mnr = OSNumber::withNumber(static_cast<unsigned>(minor(static_cast<uint32_t>(device))), 32);
    bool ready = matching && properties && mjr && mnr &&
                 properties->setObject(kIOBSDMajorKey, mjr) && properties->setObject(kIOBSDMinorKey, mnr) &&
                 matching->setObject("IOPropertyMatch", properties);
    OSSafeReleaseNULL(mnr);
    OSSafeReleaseNULL(mjr);
    OSSafeReleaseNULL(properties);
    OSIterator *iterator = ready ? IOService::getMatchingServices(matching) : nullptr;
    OSSafeReleaseNULL(matching);
    if (!iterator) return 0;
    unsigned matches = 0;
    uint64_t bytes = 0;
    bool found = false;
    while (OSObject *object = iterator->getNextObject()) {
        IOService *media = OSDynamicCast(IOService, object);
        if (!media) continue;
        if (++matches != 1) { found = false; break; }
        OSString *uuid = OSDynamicCast(OSString, media->getProperty("UUID"));
        OSNumber *size = OSDynamicCast(OSNumber, media->getProperty("Size"));
        if (!uuid || uuid->getLength() != 36 || strcmp(expectedUUID, uuid->getCStringNoCopy())) continue;
        bytes = size ? size->unsigned64BitValue() : 0;
        found = true;
    }
    iterator->release();
    if (!found || matches != 1) return 0;
    static const char domain[] = "zeus-unverified-window";
    SHA384_CTX context;
    SHA384_Init(&context);
    SHA384_Update(&context, domain, sizeof(domain));
    SHA384_Update(&context, expectedUUID, 36);
    SHA384_Update(&context, &bytes, sizeof(bytes));
    SHA384_Final(digest, &context);
    return 1;
}
#endif

extern "C" int native_verified_block_manifest_sha384(dev_t device, const char *uuid, uint8_t digest[48])
{
    if (!digest) return 0;
    bzero(digest, kDigestBytes);
    if (!uuid || strlen(uuid) != 36) return 0;
#if !defined(NATIVE_VERIFIED_BLOCK) || !NATIVE_VERIFIED_BLOCK
    return unverifiedManifestDigest(device, uuid, digest);
#else
    return verifiedVolumeQuery(device, uuid, digest);
#endif
}

// zeus bring-up diagnostic: one line per IOMedia ("bsd uuid content size leaf"),
// for the owner-cache mount panic (patches/xnu-12377/zeus-owner-cache-diag.patch).
extern "C" size_t zeus_iomedia_summary(char *buf, size_t size)
{
    if (!buf || !size) return 0;
    buf[0] = 0;
    OSDictionary *matching = IOService::serviceMatching("IOMedia");
    OSIterator *iterator = matching ? IOService::getMatchingServices(matching) : nullptr;
    OSSafeReleaseNULL(matching);
    if (!iterator) {
        strlcpy(buf, "no IOMedia\n", size);
        return strlen(buf);
    }
    size_t used = 0;
    unsigned count = 0;
    while (OSObject *object = iterator->getNextObject()) {
        IOService *media = OSDynamicCast(IOService, object);
        if (!media) continue;
        ++count;
        if (used + 120 >= size) continue;
        OSString *name = OSDynamicCast(OSString, media->getProperty(kIOBSDNameKey));
        OSString *uuid = OSDynamicCast(OSString, media->getProperty("UUID"));
        OSString *content = OSDynamicCast(OSString, media->getProperty("Content"));
        OSNumber *bytes = OSDynamicCast(OSNumber, media->getProperty("Size"));
        OSObject *leaf = media->getProperty("Leaf");
        int n = snprintf(buf + used, size - used, "%s %s %s %llu%s\n",
                         name ? name->getCStringNoCopy() : "-", uuid ? uuid->getCStringNoCopy() : "-",
                         content ? content->getCStringNoCopy() : "-",
                         bytes ? (unsigned long long)(bytes->unsigned64BitValue() >> 20) : 0ULL,
                         leaf == kOSBooleanTrue ? "M leaf" : "M");
        if (n > 0) used += static_cast<size_t>(n) < size - used ? static_cast<size_t>(n) : size - used - 1;
    }
    iterator->release();
    if (used + 24 < size) used += static_cast<size_t>(snprintf(buf + used, size - used, "IOMedia count %u\n", count));
    return used;
}

extern "C" int native_verified_block_volume_device(const char *uuid, dev_t *device, char *path, size_t size)
{
    if (!uuid || !device || !path || size < 6 || strlen(uuid) != 36) return 0;
    *device = NODEV;
    path[0] = 0;
    OSDictionary *matching = IOService::serviceMatching("IOMedia");
    OSDictionary *properties = OSDictionary::withCapacity(1);
    OSString *wanted = OSString::withCString(uuid);
    bool ready = matching && properties && wanted && properties->setObject("UUID", wanted) &&
                 matching->setObject("IOPropertyMatch", properties);
    OSSafeReleaseNULL(wanted);
    OSSafeReleaseNULL(properties);
    OSIterator *iterator = ready ? IOService::getMatchingServices(matching) : nullptr;
    OSSafeReleaseNULL(matching);
    if (!iterator) return 0;
    unsigned matches = 0;
    bool authenticated = false;
    dev_t candidate = NODEV;
    char candidatePath[64] = {};
    while (OSObject *object = iterator->getNextObject()) {
        IOService *media = OSDynamicCast(IOService, object);
        if (!media) continue;
        if (++matches != 1) { authenticated = false; break; }
        OSString *identity = OSDynamicCast(OSString, media->getProperty("UUID"));
        OSString *name = OSDynamicCast(OSString, media->getProperty(kIOBSDNameKey));
        OSNumber *mjr = OSDynamicCast(OSNumber, media->getProperty(kIOBSDMajorKey));
        OSNumber *mnr = OSDynamicCast(OSNumber, media->getProperty(kIOBSDMinorKey));
        if (!identity || strcmp(identity->getCStringNoCopy(), uuid) || !name || !mjr || !mnr ||
            !name->getLength() || name->getLength() >= sizeof(candidatePath) - 6 || name->getLength() >= size - 6 ||
            mjr->unsigned64BitValue() > 255 || mnr->unsigned64BitValue() > 0xffffff) continue;
        candidate = static_cast<dev_t>(makedev(mjr->unsigned32BitValue(), mnr->unsigned32BitValue()));
        if (!native_verified_block_root_authorized(candidate)) continue;
        snprintf(candidatePath, sizeof(candidatePath), "/dev/%s", name->getCStringNoCopy());
        authenticated = true;
    }
    iterator->release();
    if (!authenticated || matches != 1) return 0;
    *device = candidate;
    strlcpy(path, candidatePath, size);
    return 1;
}
