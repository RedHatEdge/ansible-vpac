# vm_templates

Stage 80 (first half). Renders a libvirt domain XML under `/etc/libvirt/qemu-vpac/<name>.xml` on each cluster node for every VM in `vm_catalog` whose `target_host` matches that node.

Companion to `vm_deploy`, which reads those XMLs and defines the domains via libvirt.

## What it does

1. Ensures `/etc/libvirt/qemu-vpac/` exists on each cluster node.
2. Loops over `vm_catalog`; for each entry, if `target_host == inventory_hostname`:
   - Loads `vars/profiles/<profile>.yml` as role-local defaults.
   - Renders `templates/domain.xml.j2` with the catalog entry merged over the profile.
   - Writes the result to `/etc/libvirt/qemu-vpac/<name>.xml`.

The rendered XML includes:

- `<vcpu placement='static'>` and `<cputune>` with `<vcpupin>`, `<emulatorpin>`, `<iothreadpin>` from `pinned_cpus` + `emulator_cpus`
- `<memoryBacking><hugepages>` when the VM opts in, with `<locked/>` + `<nosharepages/>` for RT profiles
- `<cpu mode='host-passthrough' migratable='on'>` (profile default; override-able)
- `<clock><timer name='hpet' present='no'/>` for RT profiles
- `<memballoon model='none'>` for RT profiles — no inflate surprises
- Explicit `<watchdog model='itco' action='none'/>` to neuter q35's auto-injected ITCO watchdog (operator opts in to a real watchdog via `watchdog: true`)
- Disks from `vm.disks[]` with `cache='none' io='native'` by default
- NICs from `vm.nics[]`, optionally with a preserved `<mac>` when `preserve_mac: true`
- `<hostdev>` entries per `vm.pci_passthrough[]` for Windows passthrough VMs

### RT block (rendered when the VM has `rt_priority` set, or its profile sets `rt_priority_default > 0`)

- `<vcpusched scheduler='fifo' priority='X'/>` per vCPU at the per-VM `rt_priority` (40 for VPR, 50 for SSC600 in the reference profiles)
- `<emulatorsched scheduler='fifo' priority='1'/>` and `<iothreadsched iothreads='1' scheduler='fifo' priority='1'/>` so emulator + iothread threads beat SCHED_OTHER without preempting vCPUs
- `<numatune memory mode='strict' nodeset='{{ numa_node }}'/>` — strict pinning so the kernel never silently allocates hugepages from a remote NUMA node on multi-socket hosts
- `<cpu><topology sockets='1' cores='N' threads='1'/>` — suppresses any in-guest hyperthread split so the guest sees N single-thread cores, matching the host-side isolation pattern
- `<cpu><feature policy='require' name='invtsc'/>` — exposes invariant TSC for migratable RT VMs whose guest clock is pinned to TSC
- `<features><apic eoi='on'/><kvm><hint-dedicated state='on'/><poll-control state='off'/><pv-ipi state='on'/></kvm><vmport state='off'/><pmu state='off'/></features>` — the standard KVM-RT features set; `pmu` correctly gates on RT (not on memballoon as in pre-broad-audit code)
- `<clock><timer name='kvmclock' present='yes'/><timer name='tsc' present='yes' mode='native'/>` — without these, the guest falls back to HPET (~3.6 µs per timer read) which dominates jitter
- `<pm><suspend-to-mem enabled='no'/><suspend-to-disk enabled='no'/></pm>` — prevents ACPI sleep paths from being triggered during pacemaker stop sequences

The non-RT (Windows / engineering) profiles render none of the above; they get a plain `<features><acpi/><apic/></features>` block, no numatune, no topology override, no kvmclock/tsc-native timers, no `<pm>` element.

## Profiles

| Profile | Use | RT? | Hugepages | memballoon | Disk cache |
|---|---|---|---|---|---|
| `ssc600` | ABB SSC600-style relay | yes | 1 GiB | none | none |
| `vpr` | VPR / RTAC / RTU | yes | 1 GiB | none | none |
| `windows_passthrough` | Windows engineering WS | no | off | virtio | writeback |

Add more profiles under `vars/profiles/<name>.yml` — the `profile:` field in a `vm_catalog` entry picks one by filename. Catalog entries override any profile default.

## Disk paths

`vm_catalog[].disks[].source` supports two notations:

- `"cephfs:/vms/foo.qcow2"` — the role strips the `cephfs:` prefix and renders `<source file='/vms/foo.qcow2'/>`. Use this for qcow2s that live on the CephFS mount.
- `"/absolute/path.qcow2"` — absolute path rendered as-is.

v1 does **not** create the qcow2 — it must already exist at the path before `vm_deploy` tries to start the VM. Provisioning empty disks + seed ISOs is a follow-up.

## UUID stability

The VM's libvirt UUID is derived from its name via `to_uuid` with a fixed DNS-style namespace. Same name → same UUID across renders, hosts, and re-provisions. Predictable, no per-VM state file to persist.

## Variables

| Name | Default | Notes |
|---|---|---|
| `vm_templates_xml_dir` | `/etc/libvirt/qemu-vpac` | rendered XMLs land here |

Reads from `group_vars/all.yml`: `vm_catalog`.

## Dependencies

- `virtualization` (stage 30) — libvirt/KVM must be installed + running
- `ceph_expand` (stage 60) — `/vms/` mount point exists with CephFS backing
- (Future: `rt_tuning` for the RT profiles' kernel requirements)

## Tags

- `vm`, `vm-templates` — this role
