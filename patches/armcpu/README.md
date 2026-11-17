# ArmCpu patches

Applied by the build inside `drivers/ArmCpu` (UniXNU/ArmCpu at the gitlink the build pins, BSD-2-Clause-Patent).

- `0001-zeus-smp-park.patch`: bisect boot-arg `armcpu-smp-park=N` for the silent power-off once a second core starts.
  1 parks the secondary core at initCPU entry, before any GIC access; 2 parks it after its GIC, IPI and handler
  setup, just before it is marked running. A parked core never joins the scheduler. Default 0: no change.
- `0002-zeus-smp-park-irq.patch`: `armcpu-smp-park=3/4/5` let the secondary core run and join the scheduler with one
  interrupt source taken away: 3 no timer PPI 27 (IPIs work), 4 no IPI (no SPI 1008+core, no SGI 0; timer works),
  5 no interrupts at all (ICC_IGRPEN1_EL1 off after the setup).
- `0003-zeus-smp-delay.patch`: `armcpu-smp-delay=N` (1 to 600 s) holds a secondary core's start thread N seconds before
  `processor_start`, so the boot log is already writing rounds when the core joins (`log-period-ms=` in BootLog.c shortens
  them; BootLog's `[smp]` row prints each core's start step, park/fail code, timer and IPI counts and the IRQ phase).
