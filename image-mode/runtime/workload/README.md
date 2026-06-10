# Relay workload (image-mode, single-node)

Deploys the protection-relay VM(s) onto a single-node image-mode host. This is
**reuse** of the project's existing `vm_templates` (renders the real-time
libvirt domain XML) and `vm_deploy` (defines/starts the domain) roles — the
single-node image-mode path differs only in being **standalone** (no Pacemaker)
with a **local file-backed disk** (no Ceph RBD, no sanlock lease).

## What it produces

The `ssc600` profile renders the same domain the manual guide builds by hand
(`docs/single-node-manual/10-define-ssc600-domain.md`): RT vCPU pinning to the
host's isolated cores with `SCHED_FIFO` priority, 1 GiB hugepage-backed locked
memory, host-passthrough CPU with RT features/timers, neutered ITCO watchdog, no
memballoon, a virtiofs PTP-status share, and NICs attached to the libvirt
logical networks.

## Files

```
deploy-relay.yml            the workload play (reuses vm_templates + vm_deploy)
relay-catalog.example.yml   the per-site VM catalog — copy and fill in
```

## Use

1. Place the vendor relay disk on the node (e.g.
   `/var/lib/libvirt/images/ssc600-01.img`). These roles do **not** create disk
   images — the vendor supplies them.
2. Copy `relay-catalog.example.yml` to `relay-catalog.yml`; set the cores,
   memory, disk path, and which logical networks the relay attaches to.
3. From the repo root (so `ansible.cfg` resolves roles + collections):

   ```bash
   ansible-playbook -i '<node-ip>,' \
       image-mode/runtime/workload/deploy-relay.yml \
       -e @image-mode/runtime/workload/relay-catalog.yml -u ansible --become
   ```

   The play defines the domain; set `vm_deploy_autostart: true` (or run
   `virsh start <name>`) once the disk is confirmed.

## Profiles

`profile: ssc600` (ABB SSC600 — RT, **virtio**, BIOS) is the reference. For a
**non-virtio** vendor guest such as the NovaTech Orion (Photon OS, no virtio
drivers), use `profile: vpr` with `bus: sata` and a NIC `model: e1000`. Both
profiles ship under `roles/vm_templates/vars/profiles/`.

## Networks

NICs reference libvirt **logical networks** (`station-bus`, `process-bus`, and
optionally `mgmt`) created by the networking step, so the catalog is decoupled
from the host's physical interface names. `process-bus` is a macvtap network for
GOOSE/Sampled-Value traffic; `station-bus` is the MMS/HMI bridge.

## Scaling to a cluster

On a 3-node cluster the *same* catalog grows `target_host`/`allowed_hosts`,
switches disks to Ceph RBD, adds a sanlock `lease_offset`, and lets
`vm_deploy` auto-select **managed** (Pacemaker) mode — all already supported by
these roles. Single-node is the standalone end of that spectrum.
