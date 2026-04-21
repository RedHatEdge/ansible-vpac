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

Both are first-class. Pick the one that matches your environment; the playbooks use the same inventory and the same `site.yml`.

| Path | When to use | How |
|---|---|---|
| **Air-gapped** | Utility POCs, substations, any site without outbound internet | `build-installer.yml` on a builder host produces a custom RHEL 9.7 installer ISO with packages pre-baked. Boot nodes from the ISO via iDRAC/IPMI virtual media. `site.yml` pulls from a local Satellite / mirror / registry. |
| **Connected** | Lab, greenfield, any site with outbound internet | Install stock RHEL 9.7 on the nodes yourself (USB, PXE, Satellite, whatever). `site.yml` pulls from RHSM and `quay.io`. |

Which path the playbooks use is controlled by one inventory variable: `deployment_mode: airgapped | connected`.

Step-by-step for each:
- [`docs/DEPLOYMENT-AIRGAPPED.md`](docs/DEPLOYMENT-AIRGAPPED.md)
- [`docs/DEPLOYMENT-CONNECTED.md`](docs/DEPLOYMENT-CONNECTED.md)

## Requirements

- 3 × RHEL 9.x hosts with virtualization-capable CPUs (Xeon Scalable or equivalent)
- BMCs (iDRAC, IPMI) reachable from the cluster network for STONITH
- Dedicated NIC per node for PTP (must not be in any bridge)
- Dedicated NIC/VLAN for Ceph storage traffic
- Dedicated NIC/VLAN for cluster heartbeat (separate from VM management bridge)
- SSH key access with `sudo` for the deploy user
- **Connected path:** active RHEL subscription (RHSM or Satellite)
- **Air-gapped path:** a builder host (NUC, laptop, VM, or Node A before the cluster is provisioned) with enough disk for the composed ISO; a reachable local RPM mirror or Satellite; a reachable local container registry for Ceph images
- Ansible 2.15+ on the control node with the collections in `requirements.yml`

## Quick start

```bash
# 1. Clone and install collection deps
git clone https://github.com/RedHatEdge/ansible-vpac.git
cd ansible-vpac
ansible-galaxy collection install -r requirements.yml

# 2. Copy the example inventory and fill it in
cp -r inventory/example inventory/mysite
$EDITOR inventory/mysite/hosts.yml
$EDITOR inventory/mysite/group_vars/all.yml   # set deployment_mode, sources, topology

# 3. (Air-gapped path only) Build the installer ISO and boot nodes from it
ansible-playbook -i inventory/mysite build-installer.yml

# 4. Preflight — confirms mode-specific reachability, subscriptions, hardware, networks
ansible-playbook -i inventory/mysite site.yml --tags preflight

# 5. Full deploy
ansible-playbook -i inventory/mysite site.yml

# 6. Validate
ansible-playbook -i inventory/mysite site.yml --tags validate
```

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
├── site.yml
├── inventory/
│   └── example/
│       ├── hosts.yml
│       ├── group_vars/
│       │   └── all.yml
│       └── host_vars/
│           ├── node-a.yml
│           ├── node-b.yml
│           └── node-c.yml
├── playbooks/
│   ├── 00-preflight.yml
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
│   ├── preflight/
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
├── files/
│   └── (static files referenced by roles)
├── diagnostics/
│   └── (read-only scripts for gathering data from existing clusters)
└── docs/
    ├── ARCHITECTURE.md
    ├── DEPLOYMENT-GUIDE.md
    ├── OPERATIONS.md
    ├── TROUBLESHOOTING.md
    ├── HARDWARE-BOM.md
    └── IMAGE-BUILDER.md
```

## Documentation

- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — what the cluster looks like, network layout, role of each component
- **[docs/DEPLOYMENT-CONNECTED.md](docs/DEPLOYMENT-CONNECTED.md)** — step-by-step for internet-connected deployments
- **[docs/DEPLOYMENT-AIRGAPPED.md](docs/DEPLOYMENT-AIRGAPPED.md)** — step-by-step for air-gapped utility POCs
- **[docs/OPERATIONS.md](docs/OPERATIONS.md)** — day-2 operations (planned reboot, node replacement, VM migration)
- **[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)** — common failure modes and recovery
- **[docs/HARDWARE-BOM.md](docs/HARDWARE-BOM.md)** — reference hardware and BIOS/iDRAC settings
- **[docs/IMAGE-BUILDER.md](docs/IMAGE-BUILDER.md)** — builder architecture for the air-gapped path

## License

Apache-2.0. See `LICENSE`.

## Author

Stephen Smith &lt;stephesm@redhat.com&gt;
