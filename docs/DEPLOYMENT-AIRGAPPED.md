# Deployment guide — air-gapped path

For sites where the cluster nodes do not have outbound internet access (utility POCs, substations, isolated datacenters). You run a builder host to produce a custom RHEL 9.7 installer ISO with packages pre-baked, boot nodes from the ISO via iDRAC/IPMI virtual media, then point `site.yml` at a local Satellite / RPM mirror / container registry that you operate.

If your site has internet access from the cluster nodes, the simpler path is [`DEPLOYMENT-CONNECTED.md`](DEPLOYMENT-CONNECTED.md).

## Before you start

You need:

- 3 × target servers (BMCs reachable, hard disks empty, IPMI-over-LAN enabled)
- **A builder host** — can be a dedicated NUC/laptop/VM, or Node A before the cluster is provisioned. Needs: RHEL 9.x, ~40 GB disk, enough RAM to run `osbuild-composer` (4 GB+), and reachability to both the target BMCs and your local mirror/registry.
- **A local RPM mirror or Satellite** that hosts RHEL 9.7 BaseOS, AppStream, HighAvailability, Resilient Storage, and NFV repos. Options: Satellite, reposync to an httpd, or a tool like [pulp](https://pulpproject.org).
- **A local container registry** with the Ceph container image mirrored (default `ceph/ceph:v18` for Reef). Options: a Satellite-managed registry, a standalone `quay.io/quay/quay` or `docker.io/registry`.
- BMC credentials for each node
- An Ansible control workstation with Ansible 2.15+ and Python 3.9+ (can be the builder host itself)

## Step 1 — Bring up the local mirror and registry

Out of scope for this repo, but the shape:

- RPM mirror: `reposync` from Red Hat CDN while you're still connected (typically ~15 GB for the repo set in the default `rhsm_repos`). Serve over HTTP from the builder.
- Container registry: `skopeo copy` the Ceph release tag into your registry. Exact image per `ceph.release` — `ceph/ceph:v18` for Reef, `ceph/ceph:v19` for Squid.
- Ansible collection tarball: `ansible-galaxy collection download -r requirements.yml -p /var/www/html/collections/` (or similar), then target `sources.ansible_collections_local_path` at the resulting tarball.

Write down the URLs — you will paste them into `group_vars/all.yml` in step 3.

## Step 2 — Clone and install collection dependencies on the builder

On the builder, while it still has connectivity (or from the Ansible control workstation if separate):

```bash
git clone https://github.com/RedHatEdge/ansible-vpac.git
cd ansible-vpac
ansible-galaxy collection install -r requirements.yml
```

## Step 3 — Fill in inventory for the air-gapped path

```bash
cp -r inventory/example inventory/mysite
$EDITOR inventory/mysite/hosts.yml
$EDITOR inventory/mysite/group_vars/all.yml
```

Key differences vs the connected path:

```yaml
deployment_mode: "airgapped"

sources:
  repo_source: "local_mirror"            # or satellite
  local_mirror_url: "http://builder.example.local/mirror"
  container_registry: "registry.example.local:5000"
  ansible_collections_source: "local_tarball"
  ansible_collections_local_path: "/opt/ansible-collections.tar.gz"

imagebuilder:
  enabled: true                          # auto-true when deployment_mode: airgapped
  location: "node_a"                     # or external_host
  blueprint_name: "vpac-rhel-9-7"
  # compose_packages has the full default set — extend for site-specific tools
```

Fill in the rest (topology, networking, PTP, RT, Ceph, Pacemaker, STONITH, VM catalog) exactly as in the connected guide.

## Step 4 — Create the vault for secrets

```bash
ansible-vault create inventory/mysite/group_vars/vault.yml
```

Add:

```yaml
vault_bmc_password_node_a: "..."
vault_bmc_password_node_b: "..."
vault_bmc_password_node_c: "..."
vault_hacluster_password: "..."
vault_admin_user_password: "..."         # cloud-init first-boot user
```

## Step 5 — Build the installer ISO + per-node seed ISOs

```bash
ansible-playbook -i inventory/mysite build-installer.yml --ask-vault-pass
```

Produces, under `imagebuilder.output_dir` on the builder host:

- `vpac-installer-9.7.iso` — one ISO with packages + kickstart injected (bootable on every node)
- `seed-<hostname>.iso` — one cloud-init seed ISO per entry in `vpac_nodes`

Expected duration: ~20–30 minutes for a first build.

## Step 6 — Boot each node from the ISO

For each node, via iDRAC / Supermicro IPMI / Crystal web UI:

1. Attach `vpac-installer-9.7.iso` as primary virtual media
2. Attach `seed-<hostname>.iso` as secondary virtual media
3. Set one-time boot to virtual CDROM
4. Power on

The kickstart runs unattended and drops the node into a provisioned state with SSH enabled for the admin user and the pre-baked packages installed. Expected duration: ~15 minutes per node, in parallel.

Confirm each node is reachable:

```bash
ansible -i inventory/mysite vpac_cluster -m ping --ask-vault-pass
```

## Step 7 — Preflight

```bash
ansible-playbook -i inventory/mysite site.yml --tags preflight --ask-vault-pass
```

Air-gapped-mode preflight fails fast if:
- a node is unreachable or sudo is not passwordless
- the local mirror URL doesn't respond
- the local container registry doesn't respond or doesn't have the Ceph image
- the installer-ISO fingerprint is missing on a node (suggesting it was installed from a different medium)
- the declared PTP NIC doesn't exist or is already a bridge member
- heartbeat and management networks overlap
- a BMC doesn't respond to IPMI-over-LAN

Fix any failures before proceeding.

## Step 8 — Full deploy

```bash
ansible-playbook -i inventory/mysite site.yml --ask-vault-pass
```

Expected duration: 30–60 minutes.

## Step 9 — Validate

Same as the connected path:

```bash
ansible-playbook -i inventory/mysite site.yml --tags validate --ask-vault-pass
```

## Re-running individual stages

Works identically to the connected path — all stages are idempotent and tag-addressable. See [`DEPLOYMENT-CONNECTED.md`](DEPLOYMENT-CONNECTED.md) for the tag list.

## Day-2 operations

See [`OPERATIONS.md`](OPERATIONS.md).
