# ArmCpu patches

Applied by the build inside `drivers/ArmCpu` (UniXNU/ArmCpu at the gitlink the build pins, BSD-2-Clause-Patent).

- `0001-zeus-smp-park.patch`: bisect boot-arg `armcpu-smp-park=N` for the silent power-off once a second core starts.
  1 parks the secondary core at initCPU entry, before any GIC access; 2 parks it after its GIC, IPI and handler
  setup, just before it is marked running. A parked core never joins the scheduler. Default 0: no change.
