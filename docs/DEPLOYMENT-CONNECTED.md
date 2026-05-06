# Deployment guide — connected path

For sites where the cluster nodes have outbound internet access (lab, greenfield, internet-connected datacenter). Package RPMs come from RHSM; container images (Red Hat Ceph Storage) come from `registry.redhat.io`. No builder host is required — you install RHEL on the nodes yourself.

If your site cannot reach the internet from the cluster nodes, use [`DEPLOYMENT-AIRGAPPED.md`](DEPLOYMENT-AIRGAPPED.md) instead.

## Before you start

You need:

- 3 × RHEL 9.7+ hosts, freshly installed, reachable via SSH with passwordless sudo
- BMC credentials (iDRAC or IPMI) for each node, IPMI-over-LAN enabled in the BMC
- Active RHEL subscription (activation key + org ID) or a Satellite you can reach, with these repo entitlements:
  - `rhel-9-for-x86_64-{baseos,appstream,highavailability}-rpms` — base + HA add-on
  - `rhel-9-for-x86_64-nfv-rpms` — `kernel-rt` for `rt_tuning`
  - `codeready-builder-for-rhel-9-x86_64-rpms` (CRB) — `libvirt-daemon-plugin-sanlock` (sanlock-on-RBD chain)
  - `rhceph-7-tools-for-rhel-9-x86_64-rpms` — Red Hat Ceph Storage
- **A Red Hat registry service account** — `cephadm bootstrap` pulls the RHCS container image from `registry.redhat.io`, which requires authentication. Create one at [access.redhat.com/terms-based-registry](https://access.redhat.com/terms-based-registry/); save the username (shape `<org-id>|<token-name>`) + password for the vault file below. (Not the IAM/API service accounts at `console.redhat.com/iam` — those don't authenticate to `registry.redhat.io`.)
- An Ansible control workstation with Ansible 2.15+ and Python 3.9+
- Physical network cabling matching the five-network layout in [`ARCHITECTURE.md`](ARCHITECTURE.md)

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
2. **Deployment mode** — set `deployment_mode: connected`
3. **Sources** — leave defaults (`repo_source: rhsm`, `container_registry: registry.redhat.io`, `ansible_collections_source: galaxy`), or switch `repo_source` to `satellite` and set `satellite_url` if you use one
4. **RHEL subscription** — activation key + org ID
5. **Node topology** — one `vpac_nodes` entry per node with management, storage, station, heartbeat, and BMC IPs
6. **Networking** — the five network CIDRs and VLANs; per-host NIC names under `networking_defaults` (override per-host in `host_vars/<node>.yml` if hardware is mixed)
7. **PTP** — domain, transport, profile; `ptp_is_authoritative: true` if this site is the canonical clock source
8. **RT tuning** — isolated CPUs (match your VM pinning), hugepage size, cpu governor
9. **Ceph** — per-node `osd_devices` (must be empty block devices), FSID left null on first run
10. **Pacemaker** — cluster name, `hacluster_password` from vault
11. **STONITH** — `fence_agent: fence_ipmilan` (default), per-node BMC type in `vpac_nodes[].bmc_type`
12. **VM catalog** — the VMs to deploy with pinning, hugepages, NICs, disks

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
vault_rhsm_activation_key: "..."
vault_rhsm_org_id: "..."
# Registry service account from https://access.redhat.com/terms-based-registry/
# — needed when cephadm bootstrap pulls the RHCS image from registry.redhat.io
# directly (connected mode). Write the same values into a JSON file and point
# ceph.registry_credentials_file at it.
vault_redhat_registry_username: "<org-id>|<token-name>"
vault_redhat_registry_password: "..."
```

Reference them from `all.yml` as `"{{ vault_bmc_password_node_a }}"` etc. Run subsequent commands with `--ask-vault-pass`.

## Step 5 — Preflight

```bash
ansible-playbook -i inventory/mysite site.yml --tags preflight --ask-vault-pass
```

Connected-mode preflight fails fast if:
- a node is unreachable or sudo is not passwordless
- RHEL version is not 9.x
- `subscription.rhsm.redhat.com` (or the configured Satellite) is unreachable
- the supplied RHSM activation key doesn't authenticate
- the declared PTP NIC doesn't exist or is already a bridge member
- heartbeat and management networks overlap
- a BMC doesn't respond to IPMI-over-LAN
- multiple PTP grandmasters are observed on the domain

Fix any failures before proceeding.

## Step 6 — Full deploy

```bash
ansible-playbook -i inventory/mysite site.yml --ask-vault-pass
```

Expected duration: 30–60 minutes for a fresh deployment, depending on hardware and network speed (most time is spent on Ceph OSD creation and backfill).

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

See [`OPERATIONS.md`](OPERATIONS.md).
