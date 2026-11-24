GPU plug-in on upstream's newer runtime (UniXNU 8542980, AdrenoMetal c9ebd2f, metal2vulkan 46e7cc96 "R2507"), built beside the
pinned tree (~/zeus-build/UniXNU-r2507 on the Mac; the kernel stays on UniXNU 71400b5 / Adreno740 54b41b8, same driver ABI).
unixnu-8542980.patch: the ios26 target. adrenometal-c9ebd2f.patch: patches/adrenometal 0001-0005 ported, plus bridge patch 0065 (read_pages
on iOS). Upstream's bridge patch 0064 (macOS unmapped pages) is left out. zeus-plugin-build26-r2507.sh builds gpuaddon26-r2507.bin.
