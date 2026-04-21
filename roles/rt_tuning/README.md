# rt_tuning

**Status: stub — not yet implemented.**

Stage 50 of the vPAC site deployment.

## Planned behavior

- Install `kernel-rt`, `tuned-profiles-nfv`, `tuned-profiles-realtime`
- Apply `realtime-virtual-host` tuned profile if not already active
- Set sysctls: `nmi_watchdog=0`, `sched_rt_runtime_us` from `rt_tuning.sched_rt_runtime_us` (-1 disables throttling)
- cpufreq governor: `performance` (from `rt_tuning.cpu_governor`)
- Kernel cmdline: ensure `idle=poll`, `intel_pstate=disable`, `iommu=pt`, `isolcpus=`, `nohz_full=`, `rcu_nocbs=` all line up with `rt_tuning.isolated_cpus`
- On hosts in the `rt_hosts` inventory group: render a chrony drop-in with the vendor RT overrides (`lock_all`, elevated `sched_priority`, `combinelimit=0`) from `rt_chrony.*`
- Reboot handler fires when kernel cmdline changes

## Dependencies

- `virtualization` (stage 30) — tuned profile and hugepages set up there; this role extends the same profile with RT-specific overrides
- `ptp_timesync` (stage 40) — chrony-related settings coordinate with the PTP-authoritative decision

## Tags

- `rt` — full role (currently a no-op stub)
