# Deployment guide

This is the short picker. Pick the path that matches your site and follow the detailed guide for it:

- **Connected** — you have outbound internet and want to install RHEL on the nodes yourself (USB, PXE, Satellite, etc.). See [`DEPLOYMENT-CONNECTED.md`](DEPLOYMENT-CONNECTED.md).
- **Air-gapped** — utility POC / substation / any site without outbound internet. You will build a custom RHEL installer ISO on a builder host and boot the nodes from it. See [`DEPLOYMENT-AIRGAPPED.md`](DEPLOYMENT-AIRGAPPED.md).

Both paths use the same `site.yml` and the same inventory; the one variable that differs is `deployment_mode: connected | airgapped`.

---

## Common prerequisites (both paths)

- 3 × RHEL 9.x hosts that will be the cluster
- BMC credentials (iDRAC or IPMI) for each node, IPMI-over-LAN enabled in the BMC
- An Ansible control workstation with Ansible 2.15+ and Python 3.9+
- Physical network cabling matching the five-network layout in `ARCHITECTURE.md`
- SSH reachability to each node with passwordless sudo for the deploy user (post-install)

## Step 1 — Clone and install collection dependencies

```bash
git clone https://github.com/RedHatEdge/ansible-vpac.git
cd ansible-vpac
ansible-galaxy collection install -r requirements.yml
```

## Step 2 — Create your site inventory

```bash
cp -r inventory/example inventory/mysite
```

Edit `inventory/mysite/hosts.yml` — replace `site1-node-a/b/c` with your hostnames, update `ansible_host` IPs and `ansible_user`.

## Step 3 — Fill in group_vars/all.yml

Edit `inventory/mysite/group_vars/all.yml`. The file is heavily annotated; work top to bottom:

1. **Site identity** — name, domain, DNS, timezone
2. **Deployment mode** — `deployment_mode: connected | airgapped`
3. **Sources** — `sources.repo_source` (rhsm / satellite / local_mirror), `sources.container_registry`, `sources.ansible_collections_source` — override only if the defaults for your mode don't fit
4. **Subscription** (if `sources.repo_source` is `rhsm` or `satellite`) — activation key, org ID, server URL
5. **Node topology** — one `vpac_nodes` entry per node with management, storage, station, heartbeat, and BMC IPs
6. **Networking** — the five network CIDRs and VLANs; per-host NIC names under `networking_defaults` (override per-host in `host_vars/<node>.yml` if hardware is mixed)
7. **PTP** — domain, transport, profile; `ptp_is_authoritative: true` if this site is the canonical clock source
8. **RT tuning** — isolated CPUs (match your VM pinning), hugepage size, cpu governor
9. **Ceph** — per-node `osd_devices` (must be empty block devices), FSID left null on first run, `container_image` left null unless your local registry uses a non-standard path
10. **Pacemaker** — cluster name, `hacluster_password` from vault
11. **STONITH** — `fence_agent` (`fence_ipmilan` for real BMCs); per-node BMC type in `vpac_nodes[].bmc_type`
12. **VM catalog** — the VMs to deploy with pinning, hugepages, NICs, disks
13. **Image builder** — defaults derived from `deployment_mode`; extend `compose_packages` for site-specific tools

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
```

Reference them from `all.yml` as `"{{ vault_bmc_password_node_a }}"` etc. Run subsequent commands with `--ask-vault-pass`.

## Step 5 — Preflight

```bash
ansible-playbook -i inventory/mysite site.yml --tags preflight --ask-vault-pass
```

Fails fast if:
- a node is unreachable or sudo is not passwordless
- RHEL version is not 9.x
- the declared PTP NIC doesn't exist or is already a bridge member
- heartbeat and management networks overlap
- a BMC doesn't respond to IPMI-over-LAN
- multiple PTP grandmasters are observed on the domain

Fix any failures before proceeding.

## Step 6 — Full deploy

```bash
ansible-playbook -i inventory/mysite site.yml --ask-vault-pass
```

Expected duration: 30-60 minutes for a fresh deployment, depending on hardware and network speed (most time is spent on Ceph OSD creation and backfill).

## Step 7 — Validate

```bash
ansible-playbook -i inventory/mysite site.yml --tags validate --ask-vault-pass
```

Checks:
- `pcs status` — all nodes Online, no failed actions, STONITH devices registered
- `ceph -s` — HEALTH_OK, all OSDs up, no stuck PGs
- PTP offset — under `validate.ptp_max_offset_ns` (default 1000 ns)
- cyclictest tail latency on RT hosts — under `validate.cyclictest_max_latency_us` (default 120 µs)
- STONITH dry-run against a drained node (optional; skip with `--skip-tags stonith-dryrun`)

Report lands at `/var/log/vpac-validate-<timestamp>.txt` on the control node.

## Running individual stages

After a successful first deploy you can re-run any stage in isolation:

```bash
# Re-apply networking (e.g. after adding a VLAN)
ansible-playbook -i inventory/mysite site.yml --tags networking

# Add a new VM from the catalog
ansible-playbook -i inventory/mysite site.yml --tags vm

# Re-validate only
ansible-playbook -i inventory/mysite site.yml --tags validate
```

All stages are idempotent; re-running should produce no unexpected changes on a healthy cluster.

## Adding a node post-deploy

See [`OPERATIONS.md`](OPERATIONS.md#adding-a-node).
