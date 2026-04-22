# Deployment guide — air-gapped path

For sites where the cluster nodes do not have outbound internet access (utility POCs, substations, isolated datacenters). You run a **builder host** that acts as the site's local RPM mirror and container registry, so the cluster nodes can install and pull everything they need without ever reaching the internet.

If your site has internet access from the cluster nodes, the simpler path is [`DEPLOYMENT-CONNECTED.md`](DEPLOYMENT-CONNECTED.md).

## The pattern

The builder has internet access for a short window (typically one evening — while the SA is on site with a laptop and a cellular hotspot, or while the builder is temporarily on a routable network). During that window, `01-build-builder.yml` populates the builder's:

- local RPM mirror (`/var/www/html/mirror`, served over HTTP on port 80)
- local container registry (`registry:2` in podman, listening on port 5000)

with everything the cluster will need: RHEL 9 BaseOS + AppStream + HA + resilient-storage + NFV, the RHCS 7 tools repo, and the RHCS 7 container image pulled from `registry.redhat.io`.

After that playbook finishes, the builder is disconnected from the internet (physically unplug, move to the air-gap VLAN, whatever the site requires). For the rest of its life the builder serves the cluster only.

`site.yml` then targets the cluster nodes with `sources.repo_source: local_mirror` and `sources.container_registry: <builder-host>:5000`. Everything comes from the builder; nothing calls out.

## Before you start

You need:

- 3 × target servers (BMCs reachable, hard disks empty, IPMI-over-LAN enabled)
- **A builder host** — can be a dedicated NUC/laptop/VM, or one of your cluster nodes before it gets provisioned. Needs: RHEL 9.x, ~50 GB free disk for the mirror + registry data, outbound HTTPS to `subscription.rhsm.redhat.com` and `registry.redhat.io` **for the duration of step 2 only**.
- BMC credentials for each cluster node (used by STONITH)
- **An RHSM activation key + org ID** — create one at [access.redhat.com → Subscriptions → Activation Keys](https://access.redhat.com/management/activation_keys) with a subscription that includes RHEL 9 + Red Hat Ceph Storage entitlements
- **A Red Hat registry service account** — create at [access.redhat.com/terms-based-registry](https://access.redhat.com/terms-based-registry/); save the username (shape `<org-id>|<token-name>`) + password, you'll paste them into the vault below. (Not the same as the IAM/API service accounts at `console.redhat.com/iam` — those don't authenticate to `registry.redhat.io`.)
- An Ansible control workstation with Ansible 2.15+ and Python 3.9+ (can be the builder host itself)

## Step 1 — Clone and install collection dependencies

On your Ansible control workstation:

```bash
git clone https://github.com/RedHatEdge/ansible-vpac.git
cd ansible-vpac
ansible-galaxy collection install -r requirements.yml
```

## Step 2 — Create your site inventory

```bash
cp -r inventory/example inventory/mysite
$EDITOR inventory/mysite/hosts.yml
```

`hosts.yml` already has the `builder` group scaffolded (`site1-builder`). Replace with your builder's actual hostname and management IP, same for the three cluster nodes.

## Step 3 — Fill in group_vars/all.yml

```bash
$EDITOR inventory/mysite/group_vars/all.yml
```

Key settings for the air-gapped path:

```yaml
deployment_mode: "airgapped"

sources:
  repo_source: "local_mirror"
  local_mirror_url: "http://site1-builder.example.local/mirror"
  container_registry: "site1-builder.example.local:5000"
  container_registry_insecure: true      # the builder's registry:2 runs plain HTTP
  ansible_collections_source: "local_tarball"
  ansible_collections_local_path: "/opt/ansible-collections.tar.gz"

# rhsm_repos already lists baseos + appstream + HA + resilient-storage + NFV +
# rhceph-7-tools. Add more here only if you need site-specific repos.

ceph:
  release: "7"
  # registry_credentials_file stays null — the local registry is anonymous-read.
  # bootstrap_node, osd_devices, network CIDRs, etc. filled in below.
```

Fill in the rest (node topology, networking, PTP, RT, Pacemaker, STONITH, VM catalog) the same way as the connected guide.

## Step 4 — Create the vault for secrets

```bash
ansible-vault create inventory/mysite/group_vars/vault.yml
```

Add:

```yaml
vault_rhsm_activation_key: "<activation-key>"
vault_rhsm_org_id: "<org-id>"
vault_redhat_registry_username: "<service-account-client-id>"
vault_redhat_registry_password: "<service-account-client-secret>"
vault_bmc_password_node_a: "..."
vault_bmc_password_node_b: "..."
vault_bmc_password_node_c: "..."
vault_hacluster_password: "..."
```

## Step 5 — Bring up the builder (builder needs internet)

**This is the only step where the builder needs outbound internet access.** Plan 30–60 minutes depending on the builder's egress bandwidth.

```bash
ansible-playbook -i inventory/mysite playbooks/01-build-builder.yml \
    --ask-vault-pass
```

What happens:

1. `builder_rhsm` — registers the builder with RHSM, enables the six repos listed in `rhsm_repos`.
2. `builder_mirror` — installs httpd + reposync, reposyncs all enabled repos to `/var/www/html/mirror`, rebuilds repodata to match on-disk packages, opens HTTP in firewalld, and HEAD-probes each repo's `repomd.xml` to confirm it's served.
3. `builder_registry` — installs podman + skopeo, runs `registry:2` on port 5000 with persistent storage, and `skopeo copy`s the RHCS 7 container image from `registry.redhat.io` into it.

At the end, the playbook prints both URLs. Record them — they should match what you set in step 3.

**After this completes, disconnect the builder from the internet.** Keep it connected only to the network where the cluster nodes can reach it.

## Step 6 — Mint the cluster-node installer ISO

The cluster-node installer ISO is produced by a tooling container that runs on any host with podman (Bazzite, Fedora, RHEL, macOS with Docker Desktop, Windows WSL — anywhere).

*This tooling lands in a follow-up commit. Until then, produce the ISO with your existing kickstart pipeline, or install the cluster nodes manually from stock RHEL 9.7 and skip to step 7.*

## Step 7 — Boot each cluster node

For each cluster node, via iDRAC / Supermicro IPMI / Crystal web UI:

1. Attach the installer ISO as virtual media
2. Attach the per-node seed ISO (cloud-init) as secondary virtual media
3. Set one-time boot to virtual CDROM
4. Power on

The kickstart runs unattended. The node comes up with the planned static IP, SSH enabled for the admin user, and the baseline packages installed. Expected duration: ~15 minutes per node, can run in parallel.

Confirm each node is reachable:

```bash
ansible -i inventory/mysite vpac_cluster -m ping --ask-vault-pass
```

## Step 8 — Preflight

```bash
ansible-playbook -i inventory/mysite site.yml --tags preflight --ask-vault-pass
```

Air-gapped-mode preflight fails fast if:

- a node is unreachable or sudo is not passwordless
- the local RPM mirror URL doesn't respond
- the local container registry doesn't respond
- the installer-ISO fingerprint is missing on a node (suggesting it was installed from a different medium)
- the declared PTP NIC doesn't exist or is already a bridge member
- heartbeat and management networks overlap
- a BMC doesn't respond to IPMI-over-LAN

Fix any failures before proceeding.

## Step 9 — Full deploy

```bash
ansible-playbook -i inventory/mysite site.yml --ask-vault-pass
```

Expected duration: 30–60 minutes. Ceph bootstrap pulls the RHCS container image from the builder's local registry; every other package pull hits the builder's HTTP mirror.

## Step 10 — Validate

Same as the connected path:

```bash
ansible-playbook -i inventory/mysite site.yml --tags validate --ask-vault-pass
```

## Day-2 updates

When new RHCS or RHEL packages land, update the air-gapped site by bringing the builder back online briefly and re-running step 5:

```bash
ansible-playbook -i inventory/mysite playbooks/01-build-builder.yml \
    --ask-vault-pass
```

`builder_mirror` is safe to re-run: `reposync` is incremental (skipped per-repo via `creates:` on first-time runs; operators who want a forced refresh can flip `builder_mirror_skip_reposync: false` or remove the `repodata/` directory). `builder_registry` re-runs `skopeo copy` which is idempotent — unchanged images are no-ops.

Disconnect the builder again, then re-run `site.yml` against the cluster with appropriate tags to pick up updates (e.g. `--tags baseline` to re-install RPMs from the refreshed mirror).

## Re-running individual stages

Works identically to the connected path — all stages are idempotent and tag-addressable. See [`DEPLOYMENT-CONNECTED.md`](DEPLOYMENT-CONNECTED.md) for the tag list.

## Day-2 operations

See [`OPERATIONS.md`](OPERATIONS.md).
