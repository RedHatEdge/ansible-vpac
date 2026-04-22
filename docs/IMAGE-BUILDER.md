# Image building (the air-gapped path)

An air-gapped deployment has two separate image-build jobs, both supported by this repo:

1. **Builder installer ISO** — a bootable, kickstart-injected RHEL 9 ISO that installs the builder host unattended. Produced by `playbooks/00-mint-builder-iso.yml` on the SA's workstation via a podman/docker tooling container. Documented in full below.
2. **Cluster-node installer ISO** — a bootable RHEL 9 ISO that installs each cluster node with per-node static IPs, admin user, SSH key. **TBD** — slated for a follow-up commit; same tooling container will drive it with a different kickstart template.

On the **connected** path neither ISO is needed; operators install stock RHEL 9.x on nodes themselves.

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
- It does not touch the cluster nodes (separate ISO, tracked as follow-up work)

## Cluster-node installer ISO — planned

Same tooling container, different kickstart template. Each node in `vpac_nodes` gets one ISO (or one shared ISO + per-node cloud-init seed) with its specific static IP + hostname baked in.

Current status: not yet implemented. Until then, cluster nodes can be installed by any method that produces an SSH-reachable RHEL 9 host with admin + sudo + the correct static IPs — including running the lab's `install-nodes.sh` script, or manually installing stock RHEL and running a site-specific post-install script.

Tracking: roles `cluster_iso_mint` + playbook `00b-mint-cluster-isos.yml` will land in a follow-up commit.
