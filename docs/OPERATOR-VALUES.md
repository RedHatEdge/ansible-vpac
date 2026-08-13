# Operator values — what to fill in, how to obtain it, what can hurt you

> First time here? [`QUICKSTART.md`](QUICKSTART.md) is the ordered on-ramp
> (tools, SSH keys, copying the inventory, the vault, first preflight); this
> page is the reference you fill values from at its step 3.

Every value the playbooks need lives in `inventory/example/group_vars/all.yml`
(the contract) plus per-node `host_vars/`. This page is the fill-out companion:
what each operator-supplied value is for, when it is required, and where it
comes from. The inline comments in the contract remain the authoritative
per-variable documentation; this page is the map.

**Validation is enforced, not promised.** `site.yml --tags preflight` runs a
one-pass inventory contract check that reports **every** unresolved value
together — leftover example placeholders, invalid `ceph.bootstrap_node`,
unstable OSD device names, missing registry credentials, bad `vm_catalog`
host references. Run it until it prints `Inventory contract clean`, then run
the stages.

## Fill-out map (by contract section)

| Value | Required when | Format / example | How to obtain / notes |
|---|---|---|---|
| `site_name`, `site_domain`, `site_timezone`, `site_dns_servers` | always | short id / DNS domain / IANA tz / IP list | site network plan |
| `deployment_mode` | always | `connected` \| `airgapped` | decides every package/image source below |
| `sources.*` | always | repo/registry URLs | connected: RHSM + `registry.redhat.io`; airgapped: your builder's mirror + registry (see the deployment guides) |
| `rhsm_activation_key`, `rhsm_org_id` | connected / satellite | strings | your RHSM or Satellite admin; keep credentials OUT of the inventory file (vault or extra-vars file) |
| `ceph.registry_credentials_file` | any registry that needs auth — **always for `registry.redhat.io`** | path on the **bootstrap node** to `{"url":…,"username":…,"password":…}` | **Terms-based registry service account** from access.redhat.com/terms-based-registry. A console.redhat.com **IAM service account will NOT authenticate** — it fails only at pull time. Pre-verify: `podman login registry.redhat.io` with those creds. |
| `vpac_nodes` | always | list of `{hostname, mgmt_ip, storage_ip, …, bmc_ip}` | site network plan. Hostnames here are the reference every other section must match — the contract check enforces it |
| per-node NIC names (`host_vars/`) | always | `ens1f0`, … | `ip -br link` + `ethtool -i` on each node; see the networking role README's mapping guide |
| `networks.*` (CIDRs, VLANs) | always | CIDR / VLAN ids | site network plan; heartbeat must be its own network (VLAN on the storage bond is supported — networking README "Heartbeat modes") |
| `time_sync.*` | always | `mode: ptp` for substations | PTP NIC must be dedicated (no bridge/bond/macvtap); grandmaster details from the site's timing engineer |
| `ceph.bootstrap_node` | 3+ nodes | ONE `vpac_nodes` hostname | **not a label** — every orchestrator command is delegated to this host |
| `ceph.osd_devices` | 3+ nodes | per-hostname list of `/dev/disk/by-id/...` | see the red box below |
| `ceph.pools` | 3+ nodes | list of `{name, type, pg_num}` | defaults fit the reference layout |
| `ceph.libvirt_secret_uuid` | never (leave `null`) | — | derived automatically from the cluster FSID; pin only to match a pre-existing secret, then never change |
| `pacemaker.*` / `stonith.*` | 3+ nodes | BMC IP/user/password per node | iDRAC/IPMI admin; **enable IPMI-over-LAN** on iDRACs (ships disabled) — preflight checks reachability |
| `rt_tuning.isolated_cpus` etc. | RT hosts | CPU list valid **for that node's topology** | `lscpu`; isolcpus **silently ignores** CPU numbers the machine doesn't have — recompute per hardware model, never copy between models |
| `vm_catalog` | **stage 80 only — never for the cluster itself** | list of VMs with `target_host` / `allowed_hosts`; **default `[]`** | Workloads are not a prerequisite: `[]` is the cluster-only path (stage 80 no-ops). When filled: hostnames must be `vpac_nodes` hostnames — preflight *warns*, stage 80 *enforces*. Vendor VM profiles (disk bus / NIC model) per the vm_templates README |

## Secrets — everything you owe the vault, in one place

All secrets live in an encrypted vault file, never in the inventory:

```bash
ansible-vault create inventory/<yoursite>/group_vars/vault.yml
# then run plays with --ask-vault-pass or --vault-password-file
```

`inventory/example/group_vars/vault.yml.example` enumerates **every** `vault_*`
name with format and where to obtain it — copy from it, never grep for what you
owe. Preflight verifies each secret **required for your deployment shape**
resolves non-empty and reports the *names* of anything missing (never values).

| Vault variable | Required when | What it is / where from |
|---|---|---|
| `vault_rhsm_activation_key` | `repo_source: rhsm` or `satellite` | activation key name — console.redhat.com (or Satellite org) → Activation keys |
| `vault_rhsm_org_id` | same | org ID, shown on the same page |
| `vault_redhat_registry_username` / `_password` | air-gapped builder workflow | **terms-based** registry service account (access.redhat.com/terms-based-registry); username shape `<org-id>\|<token>`; IAM service accounts do NOT work |
| `vault_bmc_password_node_a` / `_b` / `_c` | STONITH with `fence_ipmilan` (every production 3-node) | the dedicated STONITH user's password on each BMC; consumed at stage 75 — without the preflight check you'd only find out after a full Ceph build |
| `vault_hacluster_password` | every 3+ node cluster | site-generated strong random; set on all nodes, used once by `pcs host auth` (stage 70) |

Tasks that carry these credentials run with `no_log`; when one fails, the play
re-raises a redacted, actionable message (which value to check, which log on the
node) instead of a censored dead end.

## ⚠ OSD device names: use `/dev/disk/by-id/`, always

`ceph.osd_devices` entries are **wiped and turned into OSDs**. Kernel names
(`/dev/nvme0n1`, `/dev/sdb`) are assigned by **enumeration order and can swap
between boots, kernels, and reinstalls**. Field case: a node whose OS disk and
an OSD candidate sat one enumeration swap apart — under kernel naming, one
swapped boot and the wipe lands on the operating system.

Get stable paths on each node:

```bash
ls -l /dev/disk/by-id/ | grep -v part      # pick the nvme-<model>_<serial> links
```

and record those. The preflight contract check fails on unstable names unless
you explicitly set `preflight_allow_unstable_osd_device_names=true`.

## ⚠ Re-running stage 60 (Ceph) after OSDs exist

Two layers protect a re-run — know them both:

1. **Built-in guard (always on):** the pre-wipe skips any device already
   carrying Ceph LVM (a live OSD), and says so in the play output. A plain
   re-run after a mid-stage failure — the natural action — does **not**
   destroy existing OSDs.
2. **Belt-and-braces:** re-runs after OSDs exist should still pass
   `-e ceph_expand_wipe_osd_disks=false` — it skips the whole wipe phase and
   is the documented habit.

To **deliberately** destroy a previous cluster's OSDs (decommission,
version-bump redeploy): tear down with `cephadm rm-cluster --fsid <fsid>
--force --zap-osds`, verify `/etc/ceph/` is empty afterwards (a leftover
`ceph.conf` makes the next run silently skip bootstrap), or set
`ceph_expand_force_wipe_ceph_devices=true` for one run and remove it again.

## Running the stages

`site.yml` runs stages `00 → 90` in order; each is also its own playbook under
`playbooks/NN-*.yml` and its own tag.

**Two named deployment paths:**

- **Cluster-only (the default):** leave `vm_catalog: []`. Bare `site.yml` runs
  stages 00–75 to completion — networking, PTP, RT tuning, Ceph, Pacemaker,
  STONITH — stage 80 is an explicit no-op, and stage 90 validates the cluster.
  You get a usable, fenced, storage-backed vPAC cluster with no workloads.
  Building the cluster **never** requires a workload definition.
- **Cluster + workloads:** the same, plus a filled `vm_catalog`. Workload
  values are validated where they are consumed: preflight (stage 00) only
  *warns* about `vm_catalog` gaps; stage 80 *fails* on them, in one pass,
  before rendering anything. Typical field sequence: deploy cluster-only,
  then fill `vm_catalog` and run stage 80 + 90 when the workload images are
  in hand.

The working pattern:

1. `--tags preflight` until the contract check and host checks are green.
2. Run stages one at a time on first deploy (`playbooks/10-…` → `90-…`),
   checking the verify tasks at each stage tail; every stage is idempotent
   and safe to re-run **except** the stage-60 wipe caveat above.
3. Destructive-by-design steps: stage-60 OSD wipe (guarded, above) and
   stage-75 STONITH fence testing (drain the target first — see
   `op-stonith-fence-test.yml`).

Full step-by-step walkthroughs: `docs/DEPLOYMENT-CONNECTED.md` and
`docs/DEPLOYMENT-AIRGAPPED.md`. Field-derived per-stage commands, post-stage
validation checks, and a site-log template: `docs/DEPLOYMENT-RUNBOOK.md`.
