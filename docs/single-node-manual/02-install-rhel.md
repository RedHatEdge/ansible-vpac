# 02 — Install RHEL 9

Install **RHEL 9.7 or later** on the host. Any installation method works — interactive ISO, kickstart, or PXE. Host tuning occurs in the later steps; a few installation choices simplify them.

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

- Keep **SELinux in Enforcing** mode. The Virtual Protection pattern runs SELinux in Enforcing mode in production. The subsequent steps are compatible with it.
- Set a root password and create an admin user with `sudo`.

## After first boot

Log in over the management NIC and confirm the basics:

```bash
# RHEL version — must be 9.7 or newer
cat /etc/redhat-release

# Confirm SELinux is enforcing
getenforce

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
