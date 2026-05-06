# rt_tuning

Stage 50. Turns each `rt_hosts` cluster node into a real-time hypervisor: installs `kernel-rt`, swaps tuned to `realtime-virtual-host` (with the variables file written BEFORE activation), pins the cmdline / sysctls / governor that cpu-partitioning alone doesn't cover, mounts `/sys/fs/resctrl` for Intel CAT, and applies the RT chrony overrides on relay-hosting nodes.

This role is the **prerequisite** that makes `vm_templates`'s RT XML block do something at the host level. Without it, `<vcpusched scheduler='fifo'>` falls back to SCHED_OTHER on the standard kernel.

## Skip rules

Hosts NOT in the `rt_hosts` group end_play immediately. Single-node deployments without an `rt_hosts` group skip the role entirely.

## What it does (in order)

1. **Install RT package set** — `kernel-rt`, `tuned-profiles-realtime`, `tuned-profiles-nfv-host`, `realtime-tests`, `dnf-plugins-core`, `python3-dnf-plugin-versionlock`, `intel-cmt-cat`. (`kernel-rt-kvm` used to be in the list but Red Hat lets its build lag behind `kernel-rt`'s in the NFV repo, so the pair regularly fails to depsolve — the KVM-for-RT bits are now part of `kernel-rt-modules-{core,extra}` which `kernel-rt` itself pulls in.) Promote kernel-rt to default boot via `grubby --set-default-index <rt-index>` (the index lookup uses `awk ... | head -1` rather than `awk exit + pipefail` to avoid a SIGPIPE-induced exit 141). Versionlock the stock `kernel`/`kernel-core`/`kernel-modules`; explicitly UN-versionlock `kernel-rt` family.
2. **RT cmdline knobs** via grubby — `default_hugepagesz=1G`, `idle=poll`, four c-state knobs, `intel_pstate=disable`, `rdt=cmt,l3cat,l3cdp,mba`, `iomem=relaxed`, `intel_iommu=on`, `iommu=pt`, `ipv6.disable=1`. Optional `audit=0` (gated by `rt_tuning_disable_audit`). **Defense-in-depth**: removes `selinux=0` if anywhere on cmdline.
3. **`/etc/sysctl.d/vpac-rt.conf`** — `kernel.nmi_watchdog=0`, `kernel.sched_rt_runtime_us` (default `-1`), and `vm.nr_hugepages` from the SAME `rt_tuning_nr_hugepages` value used on the cmdline. Single source of truth.
4. **`realtime-virtual-host-variables.conf` THEN `tuned-adm profile`** — variables file MUST be written before profile activation; tuned reads `isolated_cores` at activation time to generate the kernel cmdline. (Documented field deployment Bug #5.)
5. **`sys-fs-resctrl.mount`** systemd unit — mounts `/sys/fs/resctrl` at boot so `pqos -e/-a` (called by SSC600 startup) doesn't silently fail.
6. **RT chrony overrides** — relay hosts only. `lock_all`, `sched_priority 60`, `combinelimit 0` written via `blockinfile` to `/etc/chrony.conf`. From documented vendor (ABB) Engineering Manual.
7. **cpufreq governor** — `cpupower frequency-set -g performance` live, plus `vpac-cpufreq.service` systemd oneshot to re-apply on boot. **Both gated** on `/sys/devices/system/cpu/cpu0/cpufreq` actually existing — hypervisor CPUs that don't expose a cpufreq driver (typical for nested KVM) skip both tasks rather than failing with cpupower's rc=237.
8. **Verify** — flushes any queued reboot handler FIRST (so a notified reboot fires before the assertions), then asserts `uname -r` ends `+rt`, tuned profile is `realtime-virtual-host`, isolated cpus match, `sched_rt_runtime_us` matches, `HugePages_Total` matches. With `rt_tuning_auto_reboot: true` (lab/unattended) the reboot happens here and verify passes in one site.yml run; with the production default `false`, the reboot stays queued for the operator and verify fails on first install — re-run with `--tags rt-verify` after the controlled reboot.

## Variables (with defaults)

| Name | Default | Notes |
|---|---|---|
| `rt_tuning_packages` | see `defaults/main.yml` | extendable per site |
| `rt_tuning_nr_hugepages` | `rt_tuning.nr_hugepages_override \| default(10)` | single source of truth for cmdline + sysctl |
| `rt_tuning_cmdline_args` | see `defaults/main.yml` | operator-overridable list |
| `rt_tuning_disable_audit` | `false` | flip true to add `audit=0` |
| `rt_tuning_sched_rt_runtime_us` | `-1` | RT throttling; `-1` = off |
| `rt_tuning_auto_reboot` | `false` | flip true for unattended labs |
| `rt_tuning_is_relay_host` | `inventory_hostname in groups.get('rt_hosts', [])` | gates the RT chrony block |

Reads from `group_vars/all.yml`: `rt_tuning.{isolated_cpus, hugepage_size, nr_hugepages_override, cpu_governor, sched_rt_runtime_us}`, `rt_chrony.{lock_all, sched_priority, combinelimit}`.

## BIOS prerequisites (manual — document in `docs/HARDWARE-BOM.md`)

System Profile: Performance · C-States: Disabled · USB Legacy: Disabled · Memory Patrol Scrub: Disabled · Hyperthreading: Disabled · Intel VT-d: Enabled · Snoop Mode: Home Snoop.

## Coordination with other roles

- **virtualization (stage 30)** owns: `tuned` package, profile-swap mechanism, `cpu-partitioning-variables.conf` template, `isolate_managed_irq=Y`, irqbalance/ksm/ksmtuned disable, qemu hook, sanlock host-side, KVM modprobe drop-in, `intel-cmt-cat` package.
- **rt_tuning (this role)** adds: RT kernel, RT cmdline knobs, RT-specific tuned profile + variables file, RT sysctls, resctrl systemd mount, RT chrony drop-in, cpufreq governor.
- **host_baseline (stage 10)** owns chrony base config and `selinux=permissive`. rt_tuning layers RT chrony settings via blockinfile and refuses to add `selinux=0`.
- **vm_templates (stage 80)** assumes rt_tuning is active.

## Tags

- `rt` — everything
- `rt-install`, `rt-cmdline`, `rt-sysctl`, `rt-tuned`, `rt-intelcat`, `rt-chrony`, `rt-cpufreq`, `rt-verify` — sub-steps

## Handlers

- `reboot after kernel cmdline change` — same name as virtualization role's, so the two coalesce. Gated by `rt_tuning_auto_reboot`.
- `restart chronyd` — fires on RT chrony block change.
