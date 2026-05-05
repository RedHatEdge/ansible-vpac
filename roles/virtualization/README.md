# virtualization

Stage 30. Prepares each cluster node to host libvirt/KVM VMs with a tuned profile appropriate for its role (RT relay host vs. general compute). Idempotent.

## What it does

1. **Packages** — installs `libvirt`, `libvirt-client`, `qemu-kvm`, `qemu-img`, `virt-install`, `virt-viewer`, `swtpm` + `swtpm-tools` (TPM state init for Windows-11 guests), `edk2-ovmf` (UEFI firmware), `intel-cmt-cat` (`pqos` for Intel CAT cache work), `cabextract` (vendor SW bundle extraction), `cockpit-machines` (Cockpit VM panel), `tuned`, `tuned-profiles-cpu-partitioning`.
2. **libvirtd** — enables and starts `libvirtd` plus its socket units (modular RHEL 9 layout).
3. **Modprobe drop-in** *(Intel-only)* — writes `/etc/modprobe.d/vpac.conf` with `kvm_intel nested=1 enable_apicv=n ple_gap=0 ple_window=0` and `vhost_net experimental_zcopytx=1`, then runs `dracut -f` so the options land in initramfs. PLE-off and APICv-off are required for RT-VM determinism; vhost zero-copy lowers VM network latency. Takes effect at next reboot. Auto-skipped on AMD/unknown CPUs (an equivalent `kvm_amd` drop-in is out of scope for v1). Toggle with `virtualization_write_modprobe`.
4. **Default network removal** *(airgapped only by default)* — destroys + undefines libvirt's built-in `default` NAT network. Connected sites keep `default` active so Windows installer workflows (`virt-install --network default`) work without operator footwork; airgapped sites destroy it. Override `virtualization_remove_default_network` to force.
5. **Libvirt virtual networks** — defines two host-bridge networks via `virsh net-define` / `net-autostart` / `net-start`: `br-mgmt` (over the mgmt bridge) and `station-bus` (over the station bridge — production convention). VM XMLs reference these with `<source network='station-bus'/>` instead of `<source bridge='station-nic'/>`, decoupling vendor VM XMLs from the underlying bridge name. Override the list via `virtualization_libvirt_networks`.
6. **Tuned profile** — applies `virtual-host` (the base KVM-hypervisor profile from BaseOS) to every host. The `rt_tuning` role later installs the RT-specific profiles from the NFV repo and swaps hosts in the `rt_hosts` group to `realtime-virtual-host`. This role keeps the RT concern out — stage 30 only needs packages from BaseOS/AppStream.
7. **isolated_cores** — writes `/etc/tuned/cpu-partitioning-variables.conf` from `rt_tuning.isolated_cpus`. Emits `isolated_cores=...` plus `isolate_managed_irq=Y` (moves kernel-managed IRQs off isolated cores at runtime). The `cpu-partitioning` profile (which `realtime-virtual-host` inherits later) consumes this to emit `isolcpus=`, `nohz_full=`, `rcu_nocbs=` in the kernel cmdline once that profile is active. Writing the config early means the switch in stage 50 is config-ready.
8. **Disable jitter services** — stops + disables `irqbalance`, `ksm`, `ksmtuned`. None are useful for cluster-node workloads and all wake periodically to steal RT jitter — irqbalance silently fights `isolate_managed_irq=Y` by re-balancing IRQs onto isolated cores. Toggle with `virtualization_disable_jitter_services`.
9. **Reboot handler** — if the tuned-driven kernel cmdline OR modprobe initramfs changes, a reboot is required for it to take effect. The handler only actually reboots when `virtualization_auto_reboot: true`; production deploys leave this at the `false` default and schedule reboots manually.
10. **Verify** — asserts `libvirtd` is active, `virsh list` works, the expected tuned profile is the active one, and `virt-host-validate qemu` reports no FAIL lines (warnings are allowed — some hardware variants warn benignly).

Hugepages, `kernel-rt`, cpufreq governor, and RT chrony overrides live in the `rt_tuning` role (stage 50) to keep concerns separated.

## Variables (with defaults)

| Name | Default | Notes |
|---|---|---|
| `virtualization_packages` | see `defaults/main.yml` | extendable per site (don't drop entries) |
| `virtualization_tuned_profile_rt` | `"virtual-host"` | applied to hosts in the `rt_hosts` group (rt_tuning role swaps to `realtime-virtual-host` later) |
| `virtualization_tuned_profile_nonrt` | `"virtual-host"` | applied to everything else |
| `virtualization_auto_reboot` | `false` | flip to `true` for unattended runs; production should leave `false` and reboot manually |
| `virtualization_remove_default_network` | airgapped → true | mode-aware: airgapped destroys, connected keeps |
| `virtualization_libvirt_networks` | `[br-mgmt, station-bus]` | host-bridge libvirt networks for VM XML to reference |
| `virtualization_disable_jitter_services` | `true` | stop + disable irqbalance, ksm, ksmtuned |
| `virtualization_write_modprobe` | `true` | KVM + vhost_net modprobe drop-in; auto-skipped on non-Intel |

Reads `rt_tuning.isolated_cpus` to feed the cpu-partitioning variable, `bridges.{mgmt,station}` for the libvirt network XMLs, and `deployment_mode` for the default-net mode-awareness.

## Tags

- `virt` — everything
- `virt-packages`, `virt-libvirtd`, `virt-modprobe`, `virt-default-net`, `virt-libvirt-networks`, `virt-tuned`, `virt-jitter`, `virt-verify` — individual groups

## Handlers

- `restart libvirtd` — triggered on libvirt config drift (not used directly yet)
- `reboot after kernel cmdline change` — fires on tuned apply; no-op unless `virtualization_auto_reboot: true`

## Dependencies

- `host_baseline` (stage 10) — repos, SELinux, firewalld, `/etc/hosts`
- `networking` (stage 20) — bridges VMs attach to

## Known lab quirks

- In nested VMs the `realtime-virtual-host` tuned profile applies successfully but its kernel-cmdline effects are largely inert (no real RT scheduling possible inside a nested guest). The role still validates the config layer, which is the point.
- The `cpu-partitioning` + `realtime-virtual-host` profiles expect at least 2 isolated cores. In a 4 vCPU lab VM, `isolated_cpus: "2-3"` is fine.
