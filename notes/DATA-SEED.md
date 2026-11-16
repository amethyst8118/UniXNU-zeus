# Writable Data for the iOS 27 bring-up

Date: 2026-11-17.

## Why

With the root mounted from the userdata SHNGROOT windows (`native-ufs-root=1`),
the `zeus-panic-after=120` diagnostic showed:

```
nproc=2   procs: 1:launchd 0:kernel_task
Failed to open swap file 30          (EROFS)
low swap: failed to create swapfile
load_init_program: attempting to load /sbin/launchd
unixnu-sysctl name=lockdown_mode_state / bootargs / ignition_blob
```

launchd starts and then stops in its early ignition/Data phase: nothing writable
is mounted and no daemon is spawned. sheng on the same iPadOS 27.0.1 24A446
profile reached SpringBoard/backboardd/wifid only with a Data seed (its earlier
run without one stopped at "offline scratch Data mount failed: 2").

## What changed

- `configs/zeus_ios27_defconfig`: sheng's owner storage chain
  (`NATIVE_RAM_BLOCK`, `OWNER_CACHE_MOUNT`, `OWNER_CACHE_STARTUP`,
  `OWNER_BOOT_MANIFEST`, `OWNER_DATA_MOUNT`, `OWNER_UNION_SYSTEMOS`) and a
  placeholder `OWNER_DATA_VOLUME_UUID` that `unixnu` rewrites to the seed's UUID.
- `board.toml` `[payload.runtime]`: `scripts/build-boot-runtime.py`, target ios27 /
  24A446, `data_gib = 4`, Setup skipped, no GPU addon, the three IPSW trust caches.
  `[payload.adt]` already had `packed_data_ramdisk` and `persistent_data`.

## Seed

`scripts/make-data-seed.py` copies `private/var` from the IPSW OS volume into a new
APFS volume, keeping file owners, so it runs as root on the build Mac. The first
kernel build prints the exact `sudo python3 ... make-data-seed.py` command. The
seed is installation state: keep it for later builds (a new seed is a new Data
UUID and needs a kernel rebuild). It is a RAM disk, so Data is lost on reboot.

Display, GPU, audio, Wi-Fi and Bluetooth stay off; a SpringBoard start would be
visible only in the `zeus-panic-after` process list.
