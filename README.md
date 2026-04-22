# ansible-vpac

Ansible for deploying a Red Hat Edge **Virtual Protection Architecture Cluster (vPAC)** — a RHEL 9 cluster combining KVM virtualization, Ceph storage, Pacemaker HA, and PTP time synchronization, designed to host real-time utility protection workloads (IEC 61850 relays, RTAC/RTU applications, Windows engineering workstations with passthrough).

The architecture pattern this implements is documented at [github.com/RedHatEdge/virtual-protection](https://github.com/RedHatEdge/virtual-protection).

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

| # | Stage | Tag | What it does |
|---|---|---|---|
| 00 | Preflight | `preflight` | Reachability, sudo, RHEL version, disk, BMC access |
| 10 | Host baseline | `baseline` | Subscription, repos, base packages, hostname, firewall, journald |
| 20 | Networking | `networking` | Bonds, bridges, VLANs via nmstate; verifies PTP NIC isolation |
| 30 | Virtualization | `virt` | libvirt, KVM, tuned, hugepages, isolated CPUs, kernel cmdline |
| 40 | PTP | `ptp` | timemaster/ptp4l on dedicated NIC; chrony with NTP stripped when PTP-authoritative |
| 50 | RT tuning | `rt` | `sched_rt_runtime_us`, cpufreq governor, RT chrony overrides |
| 60 | Ceph | `ceph` | cephadm bootstrap, expand to 3 nodes, add OSDs, create CephFS |
| 70 | Pacemaker | `pacemaker` | pcs, corosync on dedicated cluster network, cluster auth |
| 75 | STONITH | `stonith` | fence_ipmilan per node, location constraints, `stonith-enabled=true` |
| 80 | VM deploy | `vm` | render libvirt XML, define VMs, create as Pacemaker resources |
| 90 | Validate | `validate` | cyclictest, `pcs status`, `ceph -s`, PTP offset, STONITH dry-run |

Ceph (stage 60) **always** runs after host baseline, networking, and virtualization. STONITH (stage 75) **always** runs before VM deploy (stage 80). Do not reorder.

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
│           ├── node-a.yml
│           ├── node-b.yml
│           └── node-c.yml
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
│   └── 90-validate.yml
├── roles/
│   ├── builder_iso_mint/                 # mint builder installer ISO
│   ├── builder_rhsm/                     # register builder with RHSM, enable repos
│   ├── builder_mirror/                   # reposync RHSM repos to local httpd
│   ├── builder_registry/                 # run local registry:2, skopeo-copy RHCS images
│   ├── cluster_iso_mint/                 # mint per-node cluster installer ISOs
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
├── files/
│   └── (static files referenced by roles)
├── diagnostics/
│   └── (read-only scripts for gathering data from existing clusters)
└── docs/
    ├── ARCHITECTURE.md
    ├── DEPLOYMENT-CONNECTED.md
    ├── DEPLOYMENT-AIRGAPPED.md
    ├── OPERATIONS.md
    ├── TROUBLESHOOTING.md
    ├── HARDWARE-BOM.md
    └── IMAGE-BUILDER.md
```

## Documentation

- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — what the cluster looks like, network layout, role of each component
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
