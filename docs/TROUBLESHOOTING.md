# Troubleshooting

Common failure modes and recovery paths.

## Split-brain / partitioned cluster

**Symptom:** `pcs status` on different nodes shows different views of online membership. Pacemaker logs `KNET link down` followed by a token timeout.

**First check:** is STONITH enabled?

```bash
pcs property show stonith-enabled
```

If `false`, that is the bug. Fence resources may exist but are inactive. Enable with:

```bash
pcs property set stonith-enabled=true
```

**Second check:** did bridge churn starve corosync? Look for a large RX dropped counter on the bridge that carries corosync:

```bash
cat /proc/net/dev | grep br-
ethtool -S <bridge>
```

If RX drops are high (tens of thousands+) and you see a VM restart/thrash loop in `journalctl -u libvirtd`, the root cause is corosync sharing a bridge with VM management traffic. Long-term fix: move corosync to a dedicated heartbeat NIC (the `heartbeat_nic` variable). This is the `ARCHITECTURE.md` correct topology and the playbooks enforce it on new deployments.

**Recovery from an active split-brain:**

1. Identify which partition has quorum: `pcs status` shows `partition with quorum` vs `partition WITHOUT quorum`
2. On the non-quorum side: `pcs cluster stop` (yes, here it is correct — you are not planning to reboot, you are removing these nodes from the cluster state)
3. On the quorum side: confirm VMs are running in only one place. Check each cluster-managed VM with `virsh list` on every node.
4. If a VM is running on two nodes: destroy the instance on the non-quorum side (`virsh destroy <vm>`), then let Pacemaker reconcile
5. Once the quorum side is stable, `pcs cluster start` on the nodes that were stopped
6. `pcs resource cleanup`
7. Address the root cause (STONITH? bridge separation?) before the next incident

## Node refuses to rejoin after reboot

**Symptom:** node boots, `systemctl status pacemaker` shows `inactive (dead)`, journal shows `Shutting down controller after unexpected shutdown request` and `Inhibiting respawn`.

**Cause:** `pcs cluster stop` was run before the reboot. The shutdown attribute in the CIB persists, and the node honors it on rejoin.

**Fix:**

```bash
sudo pcs cluster start
sudo pcs resource cleanup
```

Confirm `pcs status` shows the node Online. Going forward, use `pcs node standby` + `systemctl reboot` instead (see `OPERATIONS.md`).

## OSD crash loop

**Symptom:** `ceph -s` shows OSDs flapping (down/up/down). `ceph orch ps` shows OSD containers restarting.

**Investigate:**

```bash
# On the affected node:
journalctl -u ceph-<fsid>@osd.<id>.service -n 200
# Also check the container log:
cephadm logs --fsid <fsid> --name osd.<id>
```

Common causes:

- Disk bus error (check `dmesg` for NVMe/SATA errors) — replace the device per `OPERATIONS.md#replacing-a-failed-osd`
- Out of memory on the node — `cephadm` OSDs need ~4 GB each; if the node is also hosting VMs with locked memory, total can exceed physical RAM
- Clock skew — `chrony sources` should show all nodes within a few hundred microseconds. If not, re-check PTP.

## PTP `SYNCHRONIZATION_FAULT` every few seconds

**Cause (order of likelihood):**

1. PTP NIC is attached to a bridge or is a macvtap target. Check:
   ```bash
   ip -d link show <ptp-nic>
   ```
   If output shows `master <bridge>` or `macvtap@...` children, the NIC is stealing PTP frames. Detach it (remove from the bridge, remove the macvtap VM interface).

2. Multiple PTP grandmasters on the domain. Run on two different nodes:
   ```bash
   pmc -u -b 0 'GET PARENT_DATA_SET'
   ```
   If the reported grandmaster identity differs between nodes, or shifts over time, there are multiple GMs competing. Coordinate with the network team to pick one.

3. NIC is a bond slave — PTP does not work reliably on bond slaves. Make the NIC standalone.

## Windows VM won't start on target node

**Symptom:** `pcs status` shows the Windows VM resource `Stopped (blocked)`.

**Likely cause:** PCI passthrough device missing or in use. Confirm:

```bash
virsh nodedev-list | grep <pci-address>
lspci -k -s <pci-address>   # Driver should be vfio-pci
```

If the driver is not `vfio-pci`, the host grabbed the device. Re-apply the `virtualization` role (which configures `vfio-pci` via kernel cmdline or `/etc/modprobe.d/`) and reboot the node.

## cyclictest tail latency above target

**Investigate in order:**

1. **CPU isolation**: `cat /sys/devices/system/cpu/isolated` — should match `rt_tuning.isolated_cpus`. If not, kernel cmdline didn't apply; re-run stage 30/50, reboot.
2. **Governor**: `cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor` — should be `performance`.
3. **RT throttling**: `cat /proc/sys/kernel/sched_rt_runtime_us` — should be `-1`.
4. **Other RT tasks**: `ps -eLo pid,tid,class,rtprio,ni,pri,psr,comm | awk '$4 > 0'` on the isolated CPUs. Unexpected RT tasks starve the VM.
5. **Power Profile / BIOS**: on Dell hardware, iDRAC should have Power Profile set to "Performance per Watt Optimized (DAPC)" or similar RT-friendly profile, and C-states disabled. Vendor-specific; see `HARDWARE-BOM.md`.

## Ceph health degraded after a node reboot

Normal after a short outage — OSDs on the rebooting node come back and catch up. `ceph -s` should return to HEALTH_OK within minutes.

If HEALTH_WARN persists:

```bash
ceph health detail   # read the specific warning
```

Common cases:
- `PG_DEGRADED` but actively backfilling — wait it out
- `OSD_DOWN` — check `journalctl` for the OSD that didn't come back; likely a disk issue
- `MON_CLOCK_SKEW` — PTP/chrony issue; see PTP troubleshooting above

## Deployment halts in stage 60 (Ceph)

Most common causes in order:

1. Storage network not up on all nodes — the Ceph bootstrap tries to reach peer storage IPs before they are configured. Re-run stage 20 and confirm.
2. Hostname resolution failing between nodes — `cephadm` expects to be able to resolve all OSD hostnames. `host_baseline` writes `/etc/hosts` entries; confirm they match reality.
3. OSD devices not empty — `cephadm` refuses to create OSDs on devices with existing data. `wipefs -a` any stale disks and re-run.
4. Podman/container registry unreachable — air-gapped sites must populate `ceph.registry` with a local mirror.
