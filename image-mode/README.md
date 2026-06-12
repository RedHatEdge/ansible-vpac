# Image mode (bootc) — single-node vPAC host

An alternative way to build a single-node vPAC protection host: instead of
installing RHEL and converging it with the package-mode roles, the operating
system is defined as a **RHEL image-mode (bootc) container image** and deployed
transactionally. Updates become `bootc upgrade` (atomic, with rollback) rather
than a re-run of the playbooks.

This path reaches the **same OS end-state** as
[`docs/single-node-manual/`](../docs/single-node-manual/) — a real-time-tuned
KVM host ready to run a protection relay VM. It is **single-node only**: no
Ceph, Pacemaker, corosync, or STONITH (those apply to 3-node clusters).

For the build/boot pipeline, the on-node network topology, and the deploy
sequence as diagrams, see [`ARCHITECTURE.md`](ARCHITECTURE.md).

## What is baked into the image

Everything static, from chapters 04/06/08 of the manual guide:

- the KVM-host package set (qemu-kvm, libvirt, virtiofsd, swtpm, OVMF, …)
- the `kernel-rt` real-time kernel (replacing the stock kernel)
- RT/isolation/hugepage kernel arguments (`kargs.d/10-vpac-rt.toml`)
- the `realtime-virtual-host` tuned profile, selected
- the RT scheduling-throttle sysctl, the resctrl mount, the `performance`
  governor, and the supporting service enablement
- `linuxptp` and chrony packages and units

## What stays runtime (per node, after boot)

Site- and hardware-specific identity cannot be baked. See
[`runtime/README.md`](runtime/README.md):

- hostname and network identity (bonds, bridges, VLANs, IPs, the PTP NIC)
- the **isolated-core indices** — the baked kargs use an example range; override
  per CPU topology with `bootc kargs`
- the relay VM (vendor disk image + libvirt domain) — deployed with the existing
  `vm_templates` / `vm_deploy` roles
- validation (`cyclictest`, `virt-host-validate`, PTP offset)

## Layout

```
Containerfile          the single-node RT KVM-host image
kargs.d/               baked default RT/isolation/hugepage kernel args
files/                 static config copied into the image (sysctl, units, tuned)
bib/config.toml        bootc-image-builder install config (admin user, fs layout)
build.sh               podman build + push, and the bootc-image-builder recipe
runtime/               post-boot networking + relay-VM deployment notes
```

## Build

Base image pull requires a `registry.redhat.io` login with a **terms-based
registry service account**.

```bash
# Connected (push to quay.io or another registry):
./build.sh connected quay.io/yourorg/vpac-node:9.7

# Air-gapped (base image already mirrored into the local registry):
./build.sh airgapped registry.example.internal/vpac-node:9.7
```

`build.sh` prints the `bootc-image-builder` invocation to turn the image into an
ISO / qcow2 / raw for booting a node.

## Status

Lab-verified through single-node build and boot. The `kernel-rt` swap is
confirmed — a booted node reports an `…rt…` kernel from `uname -r`, with
`isolcpus`, 1 GiB hugepages, and the `tuned` real-time profile all active. The
image builds clean (`bootc container lint`) in both connected and air-gapped
modes, the generated qcow2 boots as a UEFI VM, the runtime networking applies
via nmstate, and a relay VM renders, defines, and starts on the host.

Not yet verified: checks that require bare metal (the CPU frequency governor and
`resctrl` are inert under virtualization), and a `cyclictest` real-time baseline
(needs the RT package repo available to the node). Booting a real vendor
protection VM is the next validation step.
