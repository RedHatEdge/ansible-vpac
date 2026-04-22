# Image building (the air-gapped path)

An air-gapped deployment has two separate image-build jobs, both supported by this repo:

1. **Builder installer ISO** — a bootable, kickstart-injected RHEL 9 ISO that installs the builder host unattended. Produced by `playbooks/00-mint-builder-iso.yml` on the SA's workstation via a podman/docker tooling container. Documented below.
2. **Cluster-node installer ISOs** — one bootable RHEL 9 ISO per entry in `vpac_nodes`, each with that node's static mgmt IP + hostname baked in. Produced by `playbooks/00b-mint-cluster-isos.yml` using the same tooling container. Documented after the builder section.

On the **connected** path neither set of ISOs is needed; operators install stock RHEL 9.x on nodes themselves.

## Builder installer ISO (`00-mint-builder-iso.yml`)

### The problem this solves

A Red Hat SA on their laptop (running whatever OS — Bazzite, Fedora, RHEL, macOS, Windows with Docker Desktop) needs a way to produce a custom RHEL 9 installer that, when booted on the builder hardware, yields an SSH-reachable RHEL host with static networking and admin sudo ready — **without** requiring the SA to have RHEL-specific tooling installed locally, and **without** any manual steps at install time (no console entries, no "next-next-next").

### How it works

`00-mint-builder-iso.yml` runs on the SA's workstation (`connection: local`) and invokes the `builder_iso_mint` role. The role:

1. Validates the input: RHEL 9 DVD ISO (SA-provided, downloaded from access.redhat.com), the SA's SSH public key.
2. Builds a local podman/docker container image from `tools/iso-builder/Containerfile` (Fedora base + `lorax` + `xorriso`). Build caches, so re-runs are near-instant.
3. Renders `roles/builder_iso_mint/templates/builder-ks.cfg.j2` into a RHEL kickstart, filling in static IP / gateway / DNS / hostname / admin user from the inventory's `builder` group and `networks.mgmt` block.
4. Runs the container with the source ISO, kickstart, and output dir mounted. The container's `mkksiso` injects the kickstart and rewrites both BIOS (`isolinux.cfg`) and UEFI (`EFI/BOOT/grub.cfg`) boot entries to load it automatically.
5. Outputs `build/vpac-builder-installer.iso` (or whatever `builder_iso_output` is set to).

### Why a tooling container

`mkksiso` ships with RHEL/Fedora's `lorax` package and needs a full Linux userspace to run. Packaging it into a container means:

- The SA's workstation OS doesn't matter (Bazzite's `rpm-ostree` immutability, macOS, Windows, etc.).
- The ISO-rework toolchain version is pinned (Fedora 41's mkksiso) so output is reproducible across SAs.
- No Red Hat registry authentication is required to pull the base image — `registry.fedoraproject.org/fedora:41` is public.

The Containerfile is built locally from source; we do not publish a pre-built image.

### Workstation requirements

- `podman` or `docker` (any recent version; rootless podman works)
- `ansible-core` 2.15+
- The `ansible.utils` collection (for a filter used during kickstart rendering — dropped if not present)
- ~20 GB free disk space (the tooling image is ~1 GB, ISO extract ~13 GB, output ISO ~13 GB)
- A stock RHEL 9 DVD ISO downloaded from access.redhat.com (the boot-only ISO will not work — the minted installer needs every package it may install available offline)

### Invocation

```bash
# From the ansible-vpac repo root, with your site inventory filled in
ansible-playbook -i inventory/mysite playbooks/00-mint-builder-iso.yml
```

Override any defaults on the command line with `-e`:

```bash
ansible-playbook -i inventory/mysite playbooks/00-mint-builder-iso.yml \
    -e builder_iso_input=/path/to/rhel-9.7-x86_64-dvd.iso \
    -e builder_iso_output=/path/to/output/builder.iso \
    -e builder_iso_os_disk=nvme0n1
```

Common overrides: `builder_iso_os_disk` (`sda` for typical servers, `vda` for libvirt VMs, `nvme0n1` for NVMe), `builder_iso_admin_password_hash` (SHA-512-crypt if you want console login; default is SSH-key-only).

See [`roles/builder_iso_mint/README.md`](../roles/builder_iso_mint/README.md) for the full variable list.

### What the minted ISO does

Boot the minted ISO on your builder hardware (USB flash via `dd`, BMC virtual media, PXE, whatever matches your site). The installer runs unattended:

1. Reads the injected kickstart from the ISO.
2. Partitions the configured disk (`autopart --type=lvm` — handles both BIOS and UEFI).
3. Installs RHEL 9 minimal + `cloud-init`, `openssh-server`, `sudo`, `python3`, `tar`.
4. Writes the admin user's authorized_keys + a `NOPASSWD: ALL` sudoers file.
5. Marks `/etc/vpac-installer-tag` with the fingerprint (for preflight to detect later).
6. Powers off.

Boot again from the installed disk, and:

- The host is at the static IP configured in inventory
- SSH reachable as the admin user with your key
- Passwordless sudo works
- No other configuration — it's a clean RHEL 9 waiting for `01-build-builder.yml`

### What the minted ISO does NOT do

- It does not register the host with RHSM (that's `01-build-builder.yml`'s job — by then the SA will provide vault-supplied credentials)
- It does not pre-install the RPM mirror, container registry, or anything cluster-specific (same reason — next stage)
- It does not touch the cluster nodes — each cluster node gets its own ISO from `00b-mint-cluster-isos.yml` (documented below)

## Cluster-node installer ISOs (`00b-mint-cluster-isos.yml`)

Same tooling container as the builder track, different kickstart template. For each entry in `vpac_nodes`, produces one bootable installer ISO with that node's static mgmt IP + hostname + admin-user SSH-key config baked in.

### Invocation

```bash
ansible-playbook -i inventory/mysite playbooks/00b-mint-cluster-isos.yml
```

Output lands at `{{ cluster_iso_output_dir }}/vpac-node-<hostname>.iso` (default: `build/`).

### What differs from the builder kickstart

- **Per-node loop**: one kickstart rendered per entry in `vpac_nodes`, each with that node's `hostname` and `mgmt_ip`.
- **OSD-disk protection**: the kickstart's `ignoredisk --only-use={{ cluster_iso_os_disk }}` restricts Anaconda to the OS disk only. Every other block device — crucially the OSDs — is left completely untouched. `ceph_expand` zaps and reformats them later.
- **Mgmt-NIC selection**: `networking_defaults.mgmt_bond.members[0]` names the NIC the static IP lands on at install time. Before the `networking` role runs there's no bond, so the IP sits on that physical NIC; after `site.yml`, nmstate moves it into the bond.
- **Installer tag**: `/etc/vpac-installer-tag` = `vpac-node-ks-v1` (distinguishable from the builder's `vpac-builder-ks-v1` in preflight and diagnostics).

### Variables

See [`roles/cluster_iso_mint/README.md`](../roles/cluster_iso_mint/README.md) for the full list.

### Disk usage note

Each output ISO is ~13 GB. A 3-node cluster produces ~39 GB of output + ~13 GB of tooling-container extract during the mint. **Do not point `cluster_iso_output_dir` at a tmpfs** (like `/tmp` on modern Fedora/Bazzite hosts, where `/tmp` is a RAM-backed filesystem sized relative to system RAM). Default is the repo-local `build/` directory on your real disk.

### Delivery to target hardware

Same three paths as the builder ISO:

1. **BMC virtual media** (iDRAC, iLO, Supermicro IPMI): upload each `vpac-node-<hostname>.iso` to its target node's BMC, mount as virtual CDROM, one-time boot = virtual CDROM, power on.
2. **USB flash**: `sudo dd if=build/vpac-node-<hostname>.iso of=/dev/sdX bs=4M status=progress && sync`, plug into the server, boot from USB.
3. **PXE**: stage ISOs on the builder's httpd and configure DHCP to hand different kickstart URLs per MAC.

All 3 paths end in an unattended install; the node powers off when done, boot-from-disk comes up SSH-reachable at its configured mgmt IP.

### Order in the overall deploy

`00b-mint-cluster-isos.yml` runs after `01-build-builder.yml` has completed (the builder is serving the local RPM mirror + container registry). Once all cluster nodes are booted from their ISOs and SSH-reachable, `site.yml` does the actual vPAC provisioning.
