# ansible-vpac

Ansible for deploying a Red Hat Edge **Virtual Protection Architecture Cluster (vPAC)** — a 3-node RHEL 9 cluster combining KVM virtualization, Ceph storage, Pacemaker HA, and PTP time synchronization, designed to host real-time utility protection workloads (IEC 61850 relays, RTAC/RTU applications, Windows engineering workstations with passthrough). The proven reference protection workload is the **ABB SSC600** VM — Red Hat's partnership with ABB is the validated end-to-end play for this pattern.

The architecture pattern this implements aligns with the [vPAC Alliance](https://vpacalliance.com/) software-defined substation vision and is documented at [github.com/RedHatEdge/virtual-protection](https://github.com/RedHatEdge/virtual-protection).

## What this deploys

A RHEL 9 cluster (3 nodes by default; single-node variant on the roadmap) with:

- **Libvirt/KVM** with isolated CPUs, hugepages, and per-VM RT tuning
- **Ceph** (cephadm) providing CephFS for shared VM storage
- **Pacemaker + Corosync** with STONITH fencing for VM HA across nodes
- **PTP** (IEEE 1588) time sync on a dedicated NIC, with RT-tuned chrony for relay VMs
- **Network segregation**: management, storage, station bus, PTP, and cluster heartbeat on separate interfaces/VLANs

## Two deployment paths

Both are first-class. Pick the one that matches your environment; the playbooks use the same inventory and the same `site.yml` for the cluster provisioning.

| Path | When to use | How |
|---|---|---|
| **Air-gapped** | Utility POCs, substations, any site without outbound internet | `00-mint-builder-iso.yml` → `01-build-builder.yml` → `00b-mint-cluster-isos.yml` → `site.yml`. Four playbooks run from your workstation, four boot-from-ISO events at the target hardware. Produces a builder that serves a local RPM mirror + container registry with Red Hat Ceph Storage images mirrored, plus per-node installer ISOs for the cluster. `site.yml` pulls everything from the builder — cluster nodes never reach outbound internet. |
| **Connected** | Lab, greenfield, any site with outbound internet | Install stock RHEL 9.7 on the nodes yourself (USB, PXE, Satellite, whatever). `site.yml` pulls from RHSM and `registry.redhat.io`. No builder host required. |

Which path the playbooks use is controlled by one inventory variable: `deployment_mode: airgapped | connected`.

Step-by-step for each:
- [`docs/DEPLOYMENT-AIRGAPPED.md`](docs/DEPLOYMENT-AIRGAPPED.md)
- [`docs/DEPLOYMENT-CONNECTED.md`](docs/DEPLOYMENT-CONNECTED.md)

## Requirements

**Cluster hardware** (both paths):

- 3 × RHEL 9.x hosts with virtualization-capable CPUs (Xeon Scalable or equivalent)
- BMCs (iDRAC, IPMI) reachable from the cluster network for STONITH
- Dedicated NIC per node for PTP (must not be in any bridge)
- Dedicated NIC/VLAN for Ceph storage traffic
- Dedicated NIC/VLAN for cluster heartbeat (separate from VM management bridge)
- SSH key access with `sudo` for the deploy user

**Your workstation** (both paths):

- Ansible 2.15+ with the collections in `requirements.yml`
- Python 3.9+

**Connected path extras:**

- Active RHEL subscription (RHSM or Satellite) reachable from the cluster nodes
- Red Hat Ceph Storage entitlement (the `rhceph-7-tools-for-rhel-9-x86_64-rpms` repo enabled via your subscription)

**Air-gapped path extras:**

- A builder machine (physical server, NUC, laptop, VM — anything with ~50 GB disk) that can reach outbound HTTPS for *one* run of `01-build-builder.yml`, then go offline
- A stock RHEL 9 DVD ISO downloaded from [access.redhat.com](https://access.redhat.com/downloads/content/rhel) (~13 GB)
- **RHSM activation key + org ID** for the cluster's entitlements — create at [access.redhat.com/management/activation_keys](https://access.redhat.com/management/activation_keys)
- **Red Hat registry service account** for pulling RHCS container images — create at [access.redhat.com/terms-based-registry](https://access.redhat.com/terms-based-registry/) (this is a different system from the IAM/API service accounts at `console.redhat.com/iam`, which don't authenticate to `registry.redhat.io`)
- `podman` or `docker` on your workstation (for the ISO-minting tooling container — works on Bazzite, Fedora, RHEL, macOS, Windows with Docker Desktop)

## Quick start — connected path

```bash
# 1. Clone and install collection deps
git clone https://github.com/RedHatEdge/ansible-vpac.git
cd ansible-vpac
ansible-galaxy collection install -r requirements.yml

# 2. Copy the example inventory and fill it in
cp -r inventory/example inventory/mysite
$EDITOR inventory/mysite/hosts.yml               # your 3 cluster node IPs
$EDITOR inventory/mysite/group_vars/all.yml      # set deployment_mode: connected, RHSM key, topology
ansible-vault create inventory/mysite/group_vars/vault.yml  # RHSM key, BMC passwords, hacluster pw

# 3. Install stock RHEL 9.7 on your 3 cluster nodes by any method you like.

# 4. Preflight, deploy, validate.
ansible-playbook -i inventory/mysite site.yml --tags preflight --ask-vault-pass
ansible-playbook -i inventory/mysite site.yml --ask-vault-pass
ansible-playbook -i inventory/mysite site.yml --tags validate --ask-vault-pass
```

Full walk-through: [`docs/DEPLOYMENT-CONNECTED.md`](docs/DEPLOYMENT-CONNECTED.md).

## Quick start — air-gapped path

```bash
# 1. Clone and install collection deps + fill in inventory (same as above,
#    but set deployment_mode: airgapped, fill in builder + cluster nodes,
#    and add redhat_registry creds to vault.yml)
git clone https://github.com/RedHatEdge/ansible-vpac.git
cd ansible-vpac
ansible-galaxy collection install -r requirements.yml
cp -r inventory/example inventory/mysite
$EDITOR inventory/mysite/hosts.yml inventory/mysite/group_vars/all.yml
ansible-vault create inventory/mysite/group_vars/vault.yml

# 2. Mint the builder installer ISO (runs on your workstation via a
#    podman/docker tooling container — works on Bazzite, Fedora, RHEL,
#    macOS, Windows with Docker Desktop).
ansible-playbook -i inventory/mysite playbooks/00-mint-builder-iso.yml \
    -e builder_iso_input=/path/to/rhel-9.7-x86_64-dvd.iso

# 3. Boot the builder from that ISO (USB flash, BMC virtual media, whatever
#    fits your hardware). Unattended install; SSH-reachable when done.

# 4. Bring up the builder: RHSM register + local RPM mirror + local
#    container registry with RHCS + monitoring images mirrored.
#    The ONLY step that needs outbound internet from the builder.
ansible-playbook -i inventory/mysite playbooks/01-build-builder.yml --ask-vault-pass

# (Disconnect the builder from the internet. It serves the cluster from here on.)

# 5. Mint one installer ISO per cluster node (each with its static IP +
#    hostname baked in; OSD disks protected from the installer).
ansible-playbook -i inventory/mysite playbooks/00b-mint-cluster-isos.yml \
    -e cluster_iso_input=/path/to/rhel-9.7-x86_64-dvd.iso

# 6. Boot each cluster node from its respective ISO (all 3 in parallel).
#    Unattended install; SSH-reachable when done.

# 7. Preflight, deploy, validate.
ansible-playbook -i inventory/mysite site.yml --tags preflight --ask-vault-pass
ansible-playbook -i inventory/mysite site.yml --ask-vault-pass
ansible-playbook -i inventory/mysite site.yml --tags validate --ask-vault-pass
```

Full walk-through: [`docs/DEPLOYMENT-AIRGAPPED.md`](docs/DEPLOYMENT-AIRGAPPED.md).

## Deployment stages

`site.yml` runs these in order. Each stage is also runnable independently via `--tags`.

| # | Stage | Tag | Status | What it does |
|---|---|---|---|---|
| 00 | Preflight | `preflight` | ✅ ready | Reachability, sudo, RHEL version, disk, BMC access, mode-aware sources probes, subnet uniqueness, hostname resolution, PTP HW-timestamp |
| 10 | Host baseline | `baseline` | ✅ ready | Subscription, SCA, optional Insights, repos, base packages, hostname, SELinux permissive, firewalld w/ HA + migration ports, chrony peer mesh, operator tools + Cockpit |
| 20 | Networking | `networking` | ✅ ready | Bonds, bridges, VLANs via nmstate; PTP NIC isolation; verify-time linkdown + subnet checks |
| 30 | Virtualization | `virt` | ✅ ready | libvirt, KVM modprobe drop-in, tuned cpu-partitioning, libvirt networks (mgmt + station-bus), qemu hook, qemu.conf overrides, sanlock host chain, virt-host-validate |
| 40 | PTP | `ptp` | ✅ ready | timemaster supervises ptp4l + phc2sys + embedded chronyd on hosts with a PTP NIC (system chronyd masked); NTP-follower path on hosts without one; Power Profile P2P/L2; multi-GM damping; `ptp_status` writer for virtiofs share to relay VMs; 4-sample GM-stability verify |
| 50 | RT tuning | `rt` | ✅ ready | kernel-rt + RT package set, versionlock pattern, RT cmdline knobs, `/etc/sysctl.d/vpac-rt.conf`, `realtime-virtual-host` tuned profile (variables file written **before** activation), `sys-fs-resctrl.mount` for Intel CAT, RT chrony overrides on relay hosts, cpufreq governor — runs on `rt_hosts` only; reboot required after first run |
| 60 | Ceph | `ceph` | ✅ ready | cephadm bootstrap with RHCS 7 (`--ssh-user`), post-bootstrap network/dashboard config, OSD wipe + restorecon; `ceph_expand` adds RBD pool, libvirt cephx secret with shared UUID, sanlock-on-RBD chain |
| 70 | Pacemaker | `pacemaker` | ✅ ready | pcs cluster on the heartbeat network, hacluster auth, `pcsd` web UI on :2224, resource defaults, operator recovery primitives (`pcs-safe-reboot`, `pcs-cluster-precheck`, `pcs-vm-move`, `pcs-vm-status`, `op-pacemaker-recover.yml`) |
| 75 | STONITH | `stonith` | ✅ ready | fence_ipmilan or fence_virsh per node (idempotent), location constraints (no node fences itself), atomic enable, interactive `op-stonith-fence-test.yml` playbook |
| 80 | VM deploy | `vm` | ✅ ready | render libvirt domain XML from `vm_catalog` (RT block, RBD disks, sanlock leases, virtiofs, Windows-11 UEFI/TPM/Hyper-V); Pacemaker-managed mode by default on ≥3-node clusters (`VirtualDomain` resources, location constraints, `op-vm-undefine.yml`); standalone fallback on single-node |
| 90 | Validate | `validate` | 🚧 stub | (will) cyclictest tail latency, `pcs status`, `ceph -s` parse, PTP offset, STONITH dry-run, subnet-uniqueness + linkdown + pending-fence checks distilled from `node-diag.sh` |

`site.yml` runs end-to-end through stage 80 today. The only remaining stub is stage 90 (`validate`); stub stages are no-ops that emit a `"not yet implemented"` debug line, so a current `site.yml` run delivers a fully working RHEL + KVM + RT + Ceph + Pacemaker + STONITH cluster with managed VMs.

A few activation steps are deliberately left to the operator and not automated by `site.yml`:

1. **After first `rt_tuning` run on each rt_host: reboot.** The role installs `kernel-rt`, sets it as the default boot, and schedules a reboot handler — but `rt_tuning_auto_reboot: false` by default so production operators schedule the reboot in their own maintenance window. The role's verify step **will fail** until the host has actually booted into `+rt`. Re-run `--tags rt-verify` after the reboot to confirm. Set `rt_tuning_auto_reboot: true` for unattended labs.
2. **After `ceph_expand` sanlock chain runs cleanly: flip `virtualization_lock_manager: "sanlock"` in inventory and re-run `--tags virt-qemu-conf`.** Default `"none"` keeps single-node labs working; the chain is dormant until the flip.
3. **After `vm_deploy` in managed mode: `pcs resource enable <vm>` per VM** once disk images are confirmed present (`vm_deploy_managed_initial_disabled: true` lands resources Stopped to mirror the standalone safety posture).
4. **First-deploy STONITH functional test** (real BMCs only): `ansible-playbook playbooks/op-stonith-fence-test.yml -e fence_target=<node> -e i_have_drained_vms=yes`. Will power-cycle the target.

Ceph (stage 60) **always** runs after host baseline, networking, and virtualization. STONITH (stage 75) **always** runs before VM deploy (stage 80) — enforced by `site.yml`'s stage ordering. Do not reorder.

## Directory layout

```
ansible-vpac/
├── ansible.cfg
├── requirements.yml
├── site.yml                              # 11-stage cluster deploy (connected + airgapped)
├── inventory/
│   └── example/                          # copy to inventory/<your-site>/ and edit
│       ├── hosts.yml                     # cluster nodes + builder host
│       ├── group_vars/
│       │   └── all.yml                   # site contract: sources, topology, networks, Ceph, VM catalog
│       └── host_vars/
│           ├── site1-node-a.yml
│           ├── site1-node-b.yml
│           └── site1-node-c.yml
├── playbooks/
│   ├── 00-mint-builder-iso.yml           # [air-gapped] mint builder installer ISO on workstation
│   ├── 00b-mint-cluster-isos.yml         # [air-gapped] mint per-node cluster installer ISOs
│   ├── 01-build-builder.yml              # [air-gapped] turn builder into local mirror + registry
│   ├── 00-preflight.yml                  # ↓ stages imported by site.yml
│   ├── 10-host-baseline.yml
│   ├── 20-networking.yml
│   ├── 30-virtualization.yml
│   ├── 40-ptp.yml
│   ├── 50-rt-tuning.yml
│   ├── 60-ceph.yml
│   ├── 70-pacemaker.yml
│   ├── 75-stonith.yml
│   ├── 80-vm-deploy.yml
│   ├── 90-validate.yml
│   ├── op-pacemaker-recover.yml          # ↓ operator helpers — invoked by hand, not by site.yml
│   ├── op-stonith-fence-test.yml
│   └── op-vm-undefine.yml
├── roles/
│   ├── builder_iso_mint/                 # mint builder installer ISO
│   ├── builder_rhsm/                     # register builder with RHSM, enable repos
│   ├── builder_mirror/                   # reposync RHSM repos to local httpd
│   ├── builder_registry/                 # run local registry:2, skopeo-copy RHCS images
│   ├── cluster_iso_mint/                 # mint per-node cluster installer ISOs
│   ├── common_tools/                     # vim/tmux/htop/sos/cockpit — included from host_baseline + builder track
│   ├── preflight/                        # ↓ roles invoked by site.yml
│   ├── host_baseline/
│   ├── networking/
│   ├── virtualization/
│   ├── ptp_isolation/
│   ├── ptp_timesync/
│   ├── rt_tuning/
│   ├── ceph_bootstrap/
│   ├── ceph_expand/
│   ├── pacemaker_base/
│   ├── stonith/
│   ├── vm_templates/
│   ├── vm_deploy/
│   └── validate/
├── tools/
│   └── iso-builder/                      # Containerfile + entrypoint for the shared
│                                         #   ISO-minting tooling container (Fedora + lorax
│                                         #   + xorriso). Built locally on the SA's
│                                         #   workstation; no pre-built image published.
├── build/                                # (ignored) minted ISOs land here by default
└── docs/
    ├── ARCHITECTURE.md
    ├── DEPLOYMENT-GUIDE.md
    ├── DEPLOYMENT-CONNECTED.md
    ├── DEPLOYMENT-AIRGAPPED.md
    ├── IMAGE-BUILDER.md
    ├── OPERATIONS.md
    ├── TROUBLESHOOTING.md
    └── HARDWARE-BOM.md
```

## Documentation

- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — what the cluster looks like, network layout, role of each component
- **[docs/DEPLOYMENT-GUIDE.md](docs/DEPLOYMENT-GUIDE.md)** — one-minute picker for choosing between connected and air-gapped
- **[docs/DEPLOYMENT-CONNECTED.md](docs/DEPLOYMENT-CONNECTED.md)** — step-by-step for internet-connected deployments
- **[docs/DEPLOYMENT-AIRGAPPED.md](docs/DEPLOYMENT-AIRGAPPED.md)** — step-by-step for air-gapped utility POCs
- **[docs/IMAGE-BUILDER.md](docs/IMAGE-BUILDER.md)** — how the ISO-minting tooling container works; both `builder_iso_mint` and `cluster_iso_mint` documented
- **[docs/OPERATIONS.md](docs/OPERATIONS.md)** — day-2 operations (planned reboot, node replacement, VM migration)
- **[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)** — common failure modes and recovery
- **[docs/HARDWARE-BOM.md](docs/HARDWARE-BOM.md)** — reference hardware and BIOS/iDRAC settings

## License

Apache-2.0. See `LICENSE`.

## Author

Stephen Smith &lt;stephesm@redhat.com&gt;
