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

| Profile | Use | RT? | Firmware | Hugepages | memballoon | Disk cache |
|---|---|---|---|---|---|---|
| `ssc600` | ABB SSC600-style relay | yes | BIOS | 1 GiB | none | none |
| `vpr` | VPR / RTAC / RTU | yes | BIOS | 1 GiB | none | none |
| `windows_passthrough` | Windows-10 / engineering WS | no | BIOS | off | virtio | writeback |
| `windows_uefi` | Windows-11 (SecureBoot + TPM 2.0 + Hyper-V) | no | UEFI/OVMF | off | virtio | writeback |

Add more profiles under `vars/profiles/<name>.yml` — the `profile:` field in a `vm_catalog` entry picks one by filename. Catalog entries override any profile default.

### Windows-11 (windows_uefi) details

- `firmware: efi` triggers `<os firmware='efi'>` plus `<loader>` (read-only pflash) and `<nvram>` (per-VM, templated from stock VARS the first time the domain starts).
- `nvram_dir` (default `/vms/nvram`) must exist on a shared filesystem so NVRAM persists across live migration. Operator pre-creates it.
- `firmware: efi` also emits `<features><smm state='on'/></features>` (required for SecureBoot).
- `hyperv_enlightenments` is a map; each entry value is either a bool (renders `<name state='on|off'/>`) or a dict carrying `state` plus extra attributes (e.g. `spinlocks: { state: on, retries: 8191 }`).
- `hypervclock: true` adds `<timer name='hypervclock' present='yes'/>`.
- `tpm: { model: tpm-crb, version: 2.0 }` adds the swtpm device. swtpm + swtpm-tools + edk2-ovmf packages must be on every host that may run the VM (already in `virtualization_packages` defaults).
- `clock_offset: localtime` instead of the default `utc`.

## Disk shapes

`vm_catalog[].disks[]` accepts two shapes:

**File-backed disks** (`source` is a string):
- `"cephfs:/vms/foo.qcow2"` — the role strips the `cephfs:` prefix and renders `<source file='/vms/foo.qcow2'/>`. Use this for qcow2s that live on the CephFS mount.
- `"/absolute/path.qcow2"` — absolute path rendered as-is.
- Per-disk knobs: `format` (`qcow2` default), `cache`, `io`, `bus`, `device` (`disk` default; set to `cdrom` for installer media), `readonly`.

**RBD-backed disks** (`type: rbd` plus `pool` + `image`):
- Renders `<disk type='network' protocol='rbd'>` with one `<host name='<storage_ip>' port='6789'/>` per node from `vpac_nodes[*].storage_ip`.
- `<auth username='libvirt'>` (computed from `ceph.libvirt_cephx_user`, with the `client.` prefix stripped) referencing `ceph.libvirt_secret_uuid` via `<secret type='ceph'/>`. The secret itself is defined per-node by `ceph_expand/tasks/libvirt_secret.yml`.
- Per-disk knobs: `cache` (default `none`), `io` (default `threads` on RT, `native` otherwise), `discard` (default `unmap`), `bus` (default `virtio`), `target_dev`, `device`, `readonly`.
- On RT VMs the disk's `<driver>` element also carries `iothread='1'` so completion offloads to the iothread thread (which is FIFO-1 pinned to the emulator core set).
- Shorthand: `source: "rbd:<pool>/<image>"` URI form is also accepted in place of the explicit `type:`/`pool:`/`image:` triple.

v1 does **not** create the qcow2 or RBD image — they must already exist before `vm_deploy` tries to start the VM. Empty-disk provisioning is a follow-up.

## NIC shapes

`vm_catalog[].nics[]` accepts three shapes:

- `network: <name>` — references a libvirt virtual network defined by `virtualization` stage 30 (e.g. `br-mgmt`, `station-bus`). **Preferred** — decouples the VM XML from the underlying host bridge name so a bridge rename doesn't break VMs.
- `bridge: <name>` — raw host bridge attachment. Legacy fallback for bridges not declared as libvirt networks.
- `host_dev: <nic>` — process-bus macvtap passthrough (`<interface type='direct' mode='bridge'>`). For relay VMs receiving GOOSE/SV at sub-millisecond cadence; goes direct to the wire, skipping the host bridge. Override mode via `macvtap_mode` (default `bridge` — VEPA depends on the adjacent switch doing 802.1Qbg reflective relay, which most don't; observed in the field as SV multicast loss). Conflicts with the dedicated PTP NIC — preflight catches that.

Optional per-NIC `queues: N` enables multi-queue virtio-net (`<driver name='vhost' queues='N'/>`). Useful for RT VMs receiving high-rate unicast/multicast bursts where single-queue virtio bottlenecks at ~1 Gbps. Set `queues` to the vCPU count of the VM.

## Filesystem shares (virtiofs)

`vm_catalog[].filesystems[]` accepts entries of `{ host_path, guest_tag }`. The role renders a `<filesystem type='mount' accessmode='passthrough'><driver type='virtiofs'/><source dir='host_path'/><target dir='guest_tag'/></filesystem>` per entry. The guest mounts via `mount -t virtiofs <guest_tag> /mnt/<x>`.

Requires the profile to set `memory_access_shared: true` (which renders `<memoryBacking><access mode='shared'/></memoryBacking>`). Without that, virtiofsd refuses to start because guest memory must be shareable to qemu helpers. The `ssc600` profile already sets it for the vendor PTP-status share pattern.

## Operator UX devices (default on)

Every VM gets:

- `<channel type='unix' name='org.qemu.guest_agent.0'/>` — qemu-guest-agent socket. Without it, `virsh shutdown` cannot send the ACPI signal that gracefully stops the guest, and pacemaker's stop op hits its timeout and escalates to forced kill. Toggle off per-profile via `qemu_guest_agent: false`.
- `<rng model='virtio'><backend model='random'>/dev/urandom</backend></rng>` — virtio RNG. Without it, RT guests with sshd / TLS workloads block on entropy starvation right after boot. Toggle off per-profile via `rng: false`.

## Sanlock lease

When `vm_catalog[].lease_offset` is set, the role emits:

- A `<lease>` element inside `<devices>` referencing the sanlock lockspace at `/dev/rbd/<first rbd pool>/sanlock-leases` (overridable per-VM via `lease_path`). The `<lockspace>` is `__LIBVIRT__DISKS__` matching `qemu-sanlock.conf` defaults; the `<key>` is the VM name.
- A `<seclabel type='none'/>` at domain scope so libvirt's dynamic SELinux labelling does not collide with the shared RBD lockspace block device.

`lease_offset` is per-VM, **must be 1 MiB-aligned and unique across the cluster** (operator allocates and tracks; a future preflight will validate uniqueness). Without a `lease_offset`, the VM gets neither `<lease>` nor `<seclabel>` and will refuse to start once `virtualization_lock_manager` is flipped to `sanlock` — file-backed cephfs VMs (qcow2 on shared FS via flock) do not need the sanlock contract.

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
