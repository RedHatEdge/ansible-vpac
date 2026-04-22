# builder_iso_mint

Mints a bootable, kickstart-injected RHEL 9 installer ISO for the builder host. Runs on the SA's workstation (not on any target host) via `connection: local` and uses a locally-built podman/docker tooling container.

Together with `01-build-builder.yml`, this gives a Red Hat SA a true zero-to-cluster story: download a RHEL 9 DVD ISO, fill in the inventory, run `00-mint-builder-iso.yml` → `01-build-builder.yml` → `site.yml`, and you have a working vPAC cluster at an air-gapped utility site.

## What it does

1. Sanity-checks `builder_iso_input` (the SA's downloaded RHEL 9 DVD) and `builder_iso_ssh_pubkey_path`.
2. Builds the tooling container from `tools/iso-builder/Containerfile` (idempotent — re-runs hit podman's layer cache).
3. Renders `templates/builder-ks.cfg.j2` to a staging path, filling in:
   - static IP from the builder's `ansible_host`
   - netmask derived from `networks.mgmt.cidr`
   - gateway + DNS from `networks.mgmt.gateway` + `site_dns_servers`
   - hostname from `<inventory_name>.<site_domain>`
   - admin user from the builder's `ansible_user`
   - SSH pubkey from `builder_iso_ssh_pubkey_path`
4. Runs the tooling container with the input ISO + kickstart + output dir mounted; `mkksiso` does the actual ISO rework.
5. Cleans up the staged kickstart.

## Variables

| Name | Default | Notes |
|---|---|---|
| `builder_iso_input` | `~/Downloads/rhel-9.7-x86_64-dvd.iso` | full DVD, not boot-only |
| `builder_iso_output` | `{{ playbook_dir }}/../build/vpac-builder-installer.iso` | output path |
| `builder_iso_tooling_dir` | `{{ playbook_dir }}/../tools/iso-builder` | where Containerfile lives |
| `builder_iso_tooling_image` | `localhost/vpac-iso-builder:latest` | local image tag |
| `builder_iso_kickstart_path` | `/tmp/vpac-builder-ks.cfg` | staging, removed on success |
| `builder_iso_ssh_pubkey_path` | `~/.ssh/id_ed25519.pub` | authorized on the minted builder |
| `builder_iso_admin_password_hash` | `""` | empty = SSH-key-only (locked password) |
| `builder_iso_os_disk` | `sda` | disk to install to (`vda` for libvirt, `nvme0n1` for NVMe) |
| `builder_iso_installer_tag` | `vpac-builder-ks-v1` | written to `/etc/vpac-installer-tag` |
| `builder_iso_container_cli` | `podman` | set to `docker` on macOS / Windows if you use Docker Desktop |

Reads from `group_vars/all.yml`: `site_domain`, `site_timezone`, `site_dns_servers`, `networks.mgmt.{cidr,gateway}`, plus `hostvars[groups['builder'][0]]`.

## Workstation requirements

- podman or docker (any recent version; rootless podman works)
- ansible-core 2.15+
- the `ansible.utils` collection (for the `ipaddr('netmask')` filter used in the kickstart template)
- ≥ 20 GB free disk space (tooling image ~1 GB, ISO extract ~13 GB, final ISO ~13 GB)

The tooling container is built from source; no registry auth is required to pull the base image (`registry.access.redhat.com/ubi9/ubi` is public).

## Tags

- `iso`, `iso-mint` — this role

## Limitations

- Currently only produces a single builder installer ISO per run. Minting the cluster-node installer ISOs (which embed per-node static IPs) is a follow-up that will layer on top of the same tooling container with a different kickstart template.
- The kickstart defaults to BIOS-compatible partitioning via `autopart --type=lvm`, which also handles UEFI. For hardware that needs a specific partitioning scheme, edit the template.
- `mkksiso` requires the source ISO to be unmodified stock RHEL 9. Custom composed ISOs (e.g. those already produced by image-builder with a kickstart baked in) may not re-mint cleanly.
