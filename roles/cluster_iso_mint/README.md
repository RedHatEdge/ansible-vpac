# cluster_iso_mint

Mints one bootable RHEL 9 installer ISO per cluster node, each with that node's static mgmt IP + hostname baked into its kickstart. Runs on the SA's workstation (`connection: local`) and reuses the podman/docker tooling container from `builder_iso_mint`.

Complements `builder_iso_mint` — together they produce every installer ISO an air-gapped vPAC deployment needs (one for the builder, three for the cluster nodes).

## What it does

1. Resolves and validates the input RHEL 9 DVD, output directory, and SSH public key.
2. Builds the tooling container (`tools/iso-builder/`) — cached no-op if `builder_iso_mint` already built it earlier in the same playbook run or an earlier one.
3. For each entry in `vpac_nodes`:
   - Renders `templates/cluster-ks.cfg.j2` with that node's `hostname` + `mgmt_ip`.
   - Invokes the tooling container to produce `<output_dir>/vpac-node-<hostname>.iso`.
   - Cleans up the staged kickstart.
4. Prints a summary mapping each ISO to its target node.

The kickstart's `ignoredisk --only-use={{ cluster_iso_os_disk }}` explicitly protects every OSD device — Anaconda will not touch them. `ceph_expand` later zaps and formats them as Ceph OSDs.

## Variables

| Name | Default | Notes |
|---|---|---|
| `cluster_iso_input` | `~/Downloads/rhel-9.7-x86_64-dvd.iso` | full DVD, not boot-only |
| `cluster_iso_output_dir` | `{{ playbook_dir }}/../build` | directory; one ISO per node lands here |
| `cluster_iso_tooling_dir` | `{{ playbook_dir }}/../tools/iso-builder` | shared with builder_iso_mint |
| `cluster_iso_tooling_image` | `localhost/vpac-iso-builder:latest` | shared image tag |
| `cluster_iso_ssh_pubkey_path` | `~/.ssh/id_ed25519.pub` | authorized on every cluster node |
| `cluster_iso_admin_user` | `admin` | matches each vpac_nodes entry's ansible_user |
| `cluster_iso_admin_password_hash` | `""` | empty = SSH-key-only |
| `cluster_iso_os_disk` | `sda` | install disk; OSD disks are explicitly protected |
| `cluster_iso_installer_tag` | `vpac-node-ks-v1` | written to `/etc/vpac-installer-tag` |
| `cluster_iso_container_cli` | `podman` | set to `docker` if using Docker Desktop |

Reads from `group_vars/all.yml`: `vpac_nodes`, `site_domain`, `site_timezone`, `site_dns_servers`, `networks.mgmt.{cidr,gateway}`, `networking_defaults.mgmt_bond.members`.

## How the mgmt NIC is picked at install time

The kickstart configures the mgmt IP on `networking_defaults.mgmt_bond.members[0]` — the first physical NIC that will later become a bond member. At install time the bond does not exist yet, so the IP lands on that physical NIC; after `site.yml` runs the `networking` role, nmstate moves the IP into the bond.

If `networking_defaults.mgmt_bond.members` is empty, the kickstart falls back to `--device=link` (first NIC with a cable). This is fine when only the mgmt cable is connected during install; cluttered when all cables are present at install time on multi-NIC hardware.

## Produced files

For a 3-node cluster with hostnames `site1-node-a/b/c`, you get:

```
build/
├── vpac-node-site1-node-a.iso
├── vpac-node-site1-node-b.iso
└── vpac-node-site1-node-c.iso
```

Each ISO is ~13 GB (same size as the source DVD, with the kickstart injected).

## Workstation requirements

Same as `builder_iso_mint`:

- `podman` or `docker`
- `ansible-core` 2.15+
- ~60 GB free disk (13 GB tooling container extract + 3 × 13 GB output ISOs)
- A stock RHEL 9 DVD ISO from access.redhat.com

## Tags

- `iso`, `iso-mint`, `cluster-iso` — this role

## Delivery options

After minting, the SA delivers each ISO to its target node via one of:

1. **BMC virtual media** (iDRAC, iLO, Supermicro IPMI): upload ISO, mount as virtual CDROM, set one-time boot = virtual CDROM, power on. Works remotely.
2. **USB flash**: `sudo dd if=build/vpac-node-<hostname>.iso of=/dev/sdX bs=4M status=progress && sync`. Boot server from USB. On-site SA path.
3. **PXE**: stage ISOs on the builder's httpd (already running for the RPM mirror), configure DHCP to hand different kickstart URLs per MAC. Fastest if PXE infra already exists.

All 3 paths result in the node booting the installer, running the kickstart unattended, powering off, then being booted from disk — at which point it's SSH-reachable at its configured mgmt IP and ready for `site.yml`.

## Limitations

- The kickstart only configures the mgmt network at install time. Storage, station, heartbeat, and PTP networks come up later during the `networking` role in `site.yml`.
- Only the OS disk is partitioned. OSD disks remain untouched — `ceph_expand` handles them.
- The role serializes per-node mints (loop with `include_tasks`). On a workstation with lots of cores + I/O, parallelizing could shave a minute off for large clusters; not implemented.
