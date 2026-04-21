# virtualization

**Status: stub — not yet implemented.**

Stage 30 of the vPAC site deployment.

## Planned behavior

- Install libvirt, `qemu-kvm`, `virt-install`, `swtpm`, `edk2-ovmf`, `tuned`
- Enable and start `libvirtd`
- Remove libvirt's `default` NAT network (VMs attach to `br-mgmt` / `br-station` instead)
- Apply the `realtime-virtual-host` tuned profile (or `virtual-host` for non-RT nodes)
- Allocate hugepages from `rt_tuning.nr_hugepages_override` if set, otherwise compute from `vm_catalog`
- Write kernel cmdline (via tuned) with `isolcpus=`, `nohz_full=`, `rcu_nocbs=` derived from `rt_tuning.isolated_cpus`
- Set `hugepage_size` (1G or 2M) consistently across kernel cmdline and sysctl
- Reboot-and-wait pattern for kernel-cmdline changes (via handler)

## Dependencies

- `host_baseline` (stage 10) — needs SELinux, firewalld, and repos in place
- `networking` (stage 20) — needs bridges before libvirt networks can attach

## Tags

- `virt` — full role (currently a no-op stub)
