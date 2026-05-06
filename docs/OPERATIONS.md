# Operations

Day-2 operations for a deployed vPAC cluster.

## Operator helper playbooks + scripts

The `pacemaker_base`, `stonith`, and `vm_deploy` roles install three things you can use during day-2:

**On-node helper scripts** (under `/usr/local/sbin/`):

| Script | What it does |
|---|---|
| `pcs-safe-reboot` | Standby → reboot pattern, refuses to run if `pcs cluster stop` is requested. The CIB-shutdown trap protector. |
| `pcs-cluster-precheck` | Quick health summary: corosync ring, quorum, stonith state, pcs property show. |
| `pcs-stonith-confirm-helper` | Walks pending fencing actions in the CIB and clears them safely. Used after a recovered node returns. |
| `pcs-vm-move <vm> <target>` | Wrapper around `pcs resource move` that auto-runs `pcs resource clear` after placement. |
| `pcs-vm-status` | Per-VM rollup: location, lifetime constraints, recent failcounts. |

**Operator playbooks** (under `playbooks/op-*.yml` — invoked by hand, NOT by `site.yml`):

| Playbook | Purpose | When to run |
|---|---|---|
| `op-pacemaker-recover.yml` | Documented cold-start recovery: stop stack on every node, clear stale CIB shutdown attributes, start in order, clean up failcounts | When a cluster hangs in a partial state and `pcs cluster start --all` won't fix it |
| `op-stonith-fence-test.yml` | Confirms each fence device works by actually firing it. **Power-cycles the target VM.** Requires `-e fence_target=<node> -e i_have_drained_vms=yes` | First-deploy validation against real BMCs; never on a node hosting live workloads |
| `op-vm-undefine.yml` | Cleanly removes a VM Pacemaker resource AND undefines the libvirt domain on every node | Decommissioning a VM permanently |

```bash
# Examples:
ansible-playbook -i inventory/<site> playbooks/op-pacemaker-recover.yml
ansible-playbook -i inventory/<site> playbooks/op-stonith-fence-test.yml \
    -e fence_target=site1-node-c -e i_have_drained_vms=yes
ansible-playbook -i inventory/<site> playbooks/op-vm-undefine.yml \
    -e vm_to_remove=ssc600-01
```

## Operator activation steps after first `site.yml` run

`site.yml` deliberately leaves four things to the operator. Running them in order takes a freshly-deployed cluster from "infrastructure ready" to "production posture".

### 1. Reboot rt_hosts to land on the +rt kernel

`rt_tuning` schedules a notify on first install but `rt_tuning_auto_reboot: false` by default. Reboot rt_hosts on a maintenance window, then re-run verify:

```bash
# On each rt_host (one at a time, drain first):
pcs node standby <rt-host>
pcs status                              # wait for resources to drain
ssh <rt-host> 'sudo systemctl reboot'
# Wait for reboot...
pcs node unstandby <rt-host>

# After all rt_hosts are on +rt, confirm:
ansible-playbook -i inventory/<site> site.yml --tags rt-verify
```

Set `rt_tuning_auto_reboot: true` in inventory if you want the role to handle the reboot itself (lab/unattended scenarios).

### 2. Activate the sanlock-on-RBD chain

`ceph_expand` provisions the lockspace image and templates `/etc/libvirt/qemu-sanlock.conf` on every node, but leaves `virtualization_lock_manager: "none"` in `/etc/libvirt/qemu.conf` so single-node labs stay working. Flip after `ceph_expand` reports HEALTH_OK:

```bash
# In inventory/<site>/group_vars/all.yml:
#   virtualization_lock_manager: "sanlock"

ansible-playbook -i inventory/<site> site.yml --tags virt-qemu-conf
```

The `--tags virt-qemu-conf` re-run rewrites only `/etc/libvirt/qemu.conf` and reloads libvirtd. Existing VMs keep running; new VM starts now acquire a sanlock lease before opening their disk image.

### 3. Enable each VirtualDomain resource

In managed mode (default on 3+ node clusters), `vm_deploy` lands every Pacemaker resource `Stopped` so a misconfigured catalog doesn't autostart broken VMs. Enable each one once you've confirmed disk images are present:

```bash
# Confirm the disk image / RBD volume exists, then:
pcs resource enable ssc600-01
pcs status
```

If you'd rather have resources land Started by default, set `vm_deploy_managed_initial_disabled: false` in inventory before running stage 80.

### 4. STONITH functional test

On real BMCs, fire each fence device once to confirm the IPMI path actually works end-to-end. **This power-cycles the target node.** Drain it first.

```bash
# Pick a node that's not currently hosting workloads:
pcs node standby site1-node-c
pcs status                   # confirm resources moved off

ansible-playbook -i inventory/<site> playbooks/op-stonith-fence-test.yml \
    -e fence_target=site1-node-c \
    -e i_have_drained_vms=yes

# Node hard-reboots. Wait for it to come back, then:
pcs node unstandby site1-node-c
pcs resource cleanup
```

Repeat for each cluster node. On `fence_virsh` lab clusters this can be exercised freely (it just kills the VM); on real hardware run it once per fence device, not more.

## Planned reboot of a node

**Do not run `pcs cluster stop` before rebooting.** It sets a `shutdown` node attribute in the CIB that persists across the reboot. When the node boots and pacemaker starts, it reads the CIB, sees its own pending shutdown request, and honors it with respawn inhibited. The node will stay OFFLINE until someone manually runs `pcs cluster start`.

Correct sequence:

```bash
# From any other cluster node:
pcs node standby <target-node>

# Wait for resources to drain — confirm with:
pcs status

# On the target node (or via iDRAC):
systemctl reboot

# After it comes back:
pcs node unstandby <target-node>

# Confirm:
pcs status
ceph -s
```

If you did run `pcs cluster stop` and the node won't rejoin, run `pcs cluster start` on the stuck node manually. Then `pcs resource cleanup <resource>` on any resources with failcounts.

## Migrating a VM between nodes

```bash
pcs resource move <vm-name> <target-node>

# Confirm placement:
pcs status

# Release the move constraint so the VM can float again:
pcs resource clear <vm-name>
```

`pcs resource move` creates an infinite location constraint. `pcs resource clear` removes it so normal placement rules resume.

## Replacing a failed OSD

```bash
# Identify the failed OSD:
ceph osd tree down

# Remove with replacement reservation:
ceph orch osd rm <osd-id> --replace

# Wait for rebalance to finish:
ceph -s

# Physically replace the NVMe/SSD.

# Add the new device (matches the slot):
ceph orch daemon add osd <hostname>:<device-path>
```

## Adding a node

1. Rack + cable the new node on all five networks
2. Add to `inventory/<site>/hosts.yml` under `vpac_cluster`, `ceph_nodes`, `pacemaker_cluster`
3. Add its entry to `vpac_nodes` in `group_vars/all.yml`
4. Run preflight against the new node only: `ansible-playbook -i inventory/<site> site.yml --tags preflight --limit new-node`
5. Run full deploy limited to the new node: `ansible-playbook -i inventory/<site> site.yml --limit new-node`
6. Validate cluster-wide: `ansible-playbook -i inventory/<site> site.yml --tags validate`

## Fencing a stuck node

```bash
pcs stonith fence <node-name>
```

This is a full power cycle via IPMI. Use only when the node is unresponsive or believed to be in an inconsistent state. After the node comes back:

```bash
pcs node unstandby <node-name>   # if it was in standby
pcs resource cleanup
```

## Checking PTP health

```bash
# On any node:
pmc -u -b 0 'GET PORT_DATA_SET'
journalctl -u timemaster -u ptp4l --since "1 hour ago" | grep -i 'fault\|offset'

# Expected: port state SLAVE, offset well under 1 microsecond, no SYNCHRONIZATION_FAULT.
```

If you see `SYNCHRONIZATION_FAULT`, the most likely causes (in order) are:

1. The PTP NIC has been attached to a bridge or macvtap — check `ip -d link show <ptp-nic>` for `master <something>`. Detach it.
2. Multiple PTP grandmasters on the domain — run `pmc -u -b 0 'GET GRANDMASTER_SETTINGS_NP'` on multiple nodes and compare.
3. The NIC is a bond slave — the PTP NIC must be a standalone interface.

## Draining a node for maintenance

`pcs node standby` is the general-purpose drain. It stops all resources on the node and marks it as unavailable for new placements. VMs with `allow-migrate=true` will live-migrate if configured; others will restart on another node.

```bash
pcs node standby <node>
pcs status                   # wait until resources are off the node
# ... maintenance ...
pcs node unstandby <node>
```

## Backup / disaster recovery

The cluster configuration lives in:

- `/etc/corosync/corosync.conf` on each node (same on all)
- Pacemaker CIB — back up with `pcs cluster cib backup <file>`
- Ceph monitors — hold the authoritative cluster map; lose all three and the cluster is unrecoverable without a backup

VM disk images live on CephFS at `/vms/`. Back up with your preferred CephFS snapshot or external backup tool. VM XML definitions are regenerated by the playbook from `vm_catalog`, so that inventory file is the authoritative source.

Re-running `site.yml` against a partially-lost cluster is a recovery path for node failures (not for complete Ceph loss).
