# 02 — Install RHEL

Install **RHEL 9.7 or later** — or **RHEL 10.2 or later**, on which this guide has been verified end-to-end in the field. Any installation method works — interactive ISO, kickstart, or PXE. The procedure is the same on both major versions except in the places listed below. Host tuning occurs in the later steps; a few installation choices simplify them.

## RHEL 9 and RHEL 10

The procedure differs between the major versions only in these places; each step repeats its own note:

| Where | RHEL 9 | RHEL 10 |
|---|---|---|
| Step 03 — repository labels | `rhel-9-for-x86_64-*`, `codeready-builder-for-rhel-9-*` | `rhel-10-for-x86_64-*`, `codeready-builder-for-rhel-10-*` |
| Step 06 — custom tuned profile directory | `/etc/tuned/vpac-rt-host/` | `/etc/tuned/profiles/vpac-rt-host/` |
| Step 08 — `cpupower.service` | shipped in `kernel-tools`; not used by this guide | not shipped |

These are the same on both releases and need no per-release handling:
- SELinux is disabled with the `selinux=0` kernel argument (step 04).
- libvirt runs as modular daemons (step 06).
- `linuxptp` is 4.x, which sends PTP v2.1 by default, hence `ptp_minor_version 0` (step 07).
- `ksm`/`ksmtuned` come only from the optional `ksmtuned` package (step 08).

## Base environment

- Choose a **minimal** or **server** base environment. Do not install a desktop environment; additional services increase jitter and attack surface.
- Do **not** select the "Virtualization Host" package group in the installer. libvirt/KVM is installed deliberately in step 04 so the package set is known and minimal.

## Partitioning

- A standard layout on the local disk is sufficient. Leave room under `/var/lib/libvirt/images` (or the chosen VM disk location) for the **~30 GiB** SSC600 image plus working space — budget 60 GiB+ for that filesystem.
- To place the VM image on its own filesystem, create it now; this avoids relocating files later.

## Network during install

- Configure only the **management** interface during installation, enough for SSH access. Leave the station bus, process bus, and PTP NICs unconfigured; they are set up in step 05.
- A temporary hostname is acceptable; step 04 sets the final one.

## Security

- Leave **SELinux** at the installer default. Step 04 disables it.
- Set a root password and create an admin user with `sudo`.

## After first boot

Log in over the management NIC and confirm the basics:

```bash
# RHEL version — must be 9.7+ (or 10.2+)
cat /etc/redhat-release

# List every network interface and its state — record which physical
# ports map to management / station / process / PTP for step 05
ip -br link

# Confirm virtualization extensions are present and enabled in firmware
lscpu | grep -i -E 'vmx|svm'
ls /dev/kvm
```

If `/dev/kvm` is missing or `lscpu` shows no `vmx`/`svm` flag, VT-x is disabled in the BIOS — enable it (step 01).

Record the interface-name-to-role mapping; step 05 requires it.

Continue to [03 — Register and enable repos](03-register-and-repos.md).
