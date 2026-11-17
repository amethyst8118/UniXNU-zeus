# ArmCpu patches

Applied by the build inside `drivers/ArmCpu` (UniXNU/ArmCpu at the gitlink the build pins, BSD-2-Clause-Patent).

- `0001-zeus-smp-park.patch`: bisect boot-arg `armcpu-smp-park=N` for the silent power-off once a second core starts.
  1 parks the secondary core at initCPU entry, before any GIC access; 2 parks it after its GIC, IPI and handler
  setup, just before it is marked running. A parked core never joins the scheduler. Default 0: no change.
- `0002-zeus-smp-park-irq.patch`: `armcpu-smp-park=3/4/5` let the secondary core run and join the scheduler with one
  interrupt source taken away: 3 no timer PPI 27 (IPIs work), 4 no IPI (no SPI 1008+core, no SGI 0; timer works),
  5 no interrupts at all (ICC_IGRPEN1_EL1 off after the setup).
- `0003-zeus-smp-delay.patch`: `armcpu-smp-delay=N` (1 to 3600): the secondary core, its GIC and timer set up, waits in
  WFI on its own virtual timer (10 ms steps, interrupts masked) until the counter reads N seconds of uptime, then joins the
  scheduler. The boot core does not wait for it (park1/park2), so the boot log is writing rounds when the core starts to run
  threads. `armcpu_smp_held[core]` counts the wake-ups. (A sleep before `processor_start` did nothing: on iPadOS 27 the
  kernel starts the core through `startCPU` when it registers.) BootLog's `log-period-ms=` shortens the rounds and its
  `[smp]` row prints each core's start step, park/fail code, timer and IPI counts, the IRQ phase and core 1's hold count.
- `0004-zeus-smp-hold-irq.patch`: `armcpu-smp-hold-irq=1` unmasks IRQs around each WFI of that hold, so every timer pop is
  taken as a real exception on the held core (vector, `sleh_irq`, `ArmCpu::interrupt`); for a held core the handler only
  masks the timer and ends the interrupt (no `rtclock_intr`, no scheduler). Splits "the IRQ exception path on a secondary
  core" from "scheduler work on it".
- `0005-zeus-smp-recommend.patch`: `armcpu-smp-recommend=MASK` sets the scheduler's recommended cores
  (`sched_perfcontrol_update_recommended_cores`) from the first start thread. A core outside the mask comes online and
  takes its timer and IPIs in its idle thread, but the scheduler places no thread on it.
- `0006-zeus-smp-ring.patch`: every interrupt on logical core 1 records the interrupted thread (process name, pid, thread
  id, counter time) in a 16-entry ring when it differs from the previous entry. BootLog prints it each round as
  `#smp1 ring` lines (newest first, `name/pid/tid@ms`), which no log group filters out.
- `0007-zeus-smp-bound.patch`: `armcpu-smp-bound=1|2` starts one kernel thread bound to logical core 1 (with
  `armcpu-smp-recommend=1` the only thread there besides its idle thread): 1 a compute loop and `IOSleep (1)`, 2 the same
  plus a 4 MiB memset/memcpy each pass. `armcpu_bound_passes` and `armcpu_bound_cpu` (MPIDR Aff1) count its passes.
- `0008-zeus-smp-bound-panic.patch`: `armcpu-smp-bound=3`: the bound thread panics on core 1 at once (does a
  secondary core's panic stay on screen?).
- `0009-zeus-smp-bound-split.patch`: `armcpu-smp-bound=4` computes forever after the first switch (no block, no timer of
  its own); `=5` loops on `IOSleep (1)` with no work between (block and wake only).
- `0010-zeus-smp-gh-wdt.patch`: `armcpu-gh-wdt=1` records the Gunyah virtual watchdog's status (SMCCC 0x86000006) on each
  secondary core at initCPU and every 64th interrupt of core 1; `=2` also stops it from that core (control 2, as Linux
  `gh_wdt_stop`). BootLog's `#wdt` row prints the writer core's own status next to core 1's.
