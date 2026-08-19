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

**Third check: is the heartbeat link's PHYSICAL layer dirty?** Field-diagnosed
cause with a deceptive signature: repeated corosync membership churn (dozens of
membership changes per hour, thousands of KNET link events, TOTEM retransmits),
`pacemaker-controld` crashes, and CIB operations landing minutes late (which
surfaces as `pcs resource create` timeouts on an apparently healthy cluster) —
while `ip -s link` on the HOSTS shows **clean counters**. The corruption shows
only on the **switch side**: check the switch port for CRC errors, fragments,
runts/undersize. Frames are being damaged host→switch, so the host never sees
its own bad transmissions.

Classic trigger: a media-rate mismatch on the heartbeat path — e.g. a 10G
copper SFP module in a NIC whose driver locks the cage at 10GBASE-CR, cabled
into a 1G switch port, so the module rate-adapts every frame. Note two traps
while diagnosing: some NIC drivers refuse `ethtool -s speed` on locked SFP
cages entirely, and 1000BASE-T switch ports may offer no fixed-speed option
at all (1000BASE-T mandates autonegotiation) — neither knob can fix a
transcoding module.

**Reading the switch counters — field-measured reference numbers:**

- **Only `fragments` discriminates.** `undersizes` and `jabbers` are noise —
  in the measured incident they were proportionally HIGHER on the known-clean
  control port. An operator chasing those counters will conclude the link is
  fine. Ignore them; watch `fragments`.
- Normalize per GB transmitted, not per time. Measured on a same-switch,
  same-NIC, same-firmware controlled pair differing only in module:
  **~2.6 fragments/GB (native-rate module) vs ~22 (rate-adapting module)** at
  light traffic — roughly 8× — and **~170–185 fragments/GB** on affected
  links under sustained multi-stream load. Even near-idle, the dirty link
  drifted at ~1.3 fragments/min.
- Second, blunter metric: **throughput collapse** — affected 1 Gb links
  sustained only ~130–145 Mbit/s in a multi-stream iperf3 ring. A genuine
  fix must lift this materially, not merely reduce fragments.
- The host-side clean-sheet is total, which is what makes the signature
  deceptive: `ethtool -S` shows zero error/discard/fault counters,
  `netstat -su` clean, zero ICMP loss including don't-fragment size sweeps,
  knet average latency in the tens of microseconds. **Everything a
  host-side investigation can reach looks perfect** — pull the switch port
  statistics or you will not find it.

**The fix is physical**: matched-rate media end to end (native 1G modules
into 1G ports, or 10G end to end). Corosync token tuning or a second knet
ring are *mitigations*, not fixes — especially when storage shares the same
physical link, because Ceph is eating the same corruption corosync is.

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
   If output shows `master <bridge>` or `macvtap@...` children, the NIC violates the dedicated-PTP-NIC requirement (a documented field regression involved exactly this sharing). Detach it (remove from the bridge, remove the macvtap VM interface).

2. Multiple PTP grandmasters on the domain. Run on two different nodes:
   ```bash
   pmc -u -b 0 'GET PARENT_DATA_SET'
   ```
   If the reported grandmaster identity differs between nodes, or shifts over time, there are multiple GMs competing. Coordinate with the network team to pick one.

3. NIC is a bond slave — PTP does not work reliably on bond slaves. Make the NIC standalone.

## PTP path delay reads `0.0` in P2P mode (red herring)

`pmc -u -b 0 'GET CURRENT_DATA_SET'` shows `meanPathDelay 0.0` even on a healthy,
locked P2P setup. This looks exactly like the classic "no transparent clock in
the path" symptom, but in **peer-to-peer (P2P) mode it is expected**:
`meanPathDelay` is the end-to-end (E2E) field and is unused under P2P. The real,
measured delay lives in `peerMeanPathDelay` in the **port** dataset:

```bash
pmc -u -b 0 'GET PORT_DATA_SET' | grep peerMeanPathDelay   # e.g. 6 (ns) — the real value
pmc -u -b 0 'GET CURRENT_DATA_SET' | grep -E 'offsetFromMaster|meanPathDelay'
```

Confirm health from: `portState SLAVE`, `delayMechanism 2` (P2P), a non-zero
`peerMeanPathDelay`, a small `offsetFromMaster`, and `grandmasterIdentity`
matching your grandmaster's MAC (e.g. `aabbcc.fffe.ddeeff` for MAC
`aa:bb:cc:dd:ee:ff`). `chronyc sources` should show the PTP refclock with
`reach` climbing to `377`.

## Storage / heartbeat 1 GbE fibre links flap constantly

**Symptom:** a 1000BASE-X fibre link flaps continuously — `ip -s link show
<nic>` shows `carrier_changes` climbing at ~1–4 **per minute** — and
corosync/Ceph traffic on that path is unstable. The flap persists on an
**idle** link (no traffic, no PTP), so it is pure link-layer.

**Check the switch firmware FIRST.** In the confirmed field case the cause was
**switch firmware, not the NIC**: Advantech **EKI-8528-4XFL running firmware
1.00.04** flapped 1000BASE-X links from multiple unrelated NIC families, and
upgrading to **1.00.06 (r610) stopped the flap completely**. This was proven by
a controlled A/B — one host, two links from the same NIC card and the same
optic model/batch, one link into each of two switches: the 1.00.04 side kept
flapping while the upgraded side recorded zero carrier changes, and after
upgrading both switches both links went silent and PTP announce-timeout events
stopped. Notes for the upgrade:

- The fix is **not enumerated in the vendor release notes** (only 1.00.06 has a
  documented bug-fix section at all) — absence of a documented fix is not
  evidence the firmware is fine.
- Config **is preserved** across the 1.00.04 → 1.00.06 upgrade (verified;
  expect ~80–105 s of switch downtime). The previous image remains in the
  backup slot for rollback via the switch's image-select menu.

**On the NIC attribution:** this flap was earlier attributed to the Intel
E823-C (`ice`) driver, based on a comparison NIC that later turned out to sit
behind a switch already running the fixed firmware — a confounded control. A
direct retest then settled it: a 1000BASE-X link on the **same blamed E823-C
NIC**, never moved off 1 GbE, went from continuous flapping (~2.4/min sustained
over 78 h) to **zero carrier changes** with the switch firmware upgrade as the
only variable — NIC, optic, cabling, and port unchanged. The E823-C is
**exonerated** for this symptom. The `ethtool` quirk on E823-C (Supported shows
`1000baseT` while Advertised shows `1000baseX`) remains observable but is
demonstrated **non-causal** — the link is fully stable with the mismatch
present:
```bash
ethtool <nic> | grep -E 'Supported link modes|Advertised link modes|Speed'
```

**Fixes, in order:**
1. **Upgrade the switch firmware** (EKI-8528-4XFL: ≥ 1.00.06) and re-measure
   `carrier_changes` over 15+ minutes. This is the fix, not a mitigation.
2. If the flap persists on fixed firmware (a different fabric/NIC pair than
   the documented case), pin the port explicitly and disable autoneg:
   `ethtool -s <nic> speed 1000 duplex full autoneg off` (persist via a
   NetworkManager `ethtool` setting), and only then pursue NIC driver/firmware
   avenues with the switch excluded.
3. Moving cluster links to 10 GbE is a fine choice for **bandwidth**, but it
   is a workaround, not a fix, for this symptom — 1 GbE fibre is stable on
   this NIC once the switch firmware is current.

Keep cluster-critical traffic (corosync heartbeat, Ceph) on the stable links.

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
2. **Governor**: `cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor` — should be `performance` (if CPU throtteling via the OS is not completely disabled on BIOS level).
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

## Removing a Ceph cluster leaves residue on non-bootstrap nodes

`cephadm rm-cluster --fsid <fsid> --force --zap-osds` only cleans the node it runs on — `cephadm` is typically installed on the bootstrap node alone, so the other nodes keep their OSD containers, device-mapper mappings, and disk signatures. After any cluster removal, verify **on every node** (not just where the command ran):

```bash
ls -la /etc/ceph/ /var/lib/ceph/ 2>/dev/null   # must be empty/absent — a leftover
                                               # ceph.conf makes a re-deploy silently
                                               # SKIP bootstrap
podman ps -a                                   # no ceph containers
dmsetup ls | grep ceph                         # no ceph--* mappings
lsblk                                          # OSD devices bare
```

Clean each non-bootstrap node in this order: `systemctl stop 'ceph-*'` → `podman rm -fa` → `dmsetup remove` each `ceph--*` mapping → `wipefs -a` + `sgdisk --zap-all` each OSD device (by-id paths — double-check none is the OS disk).

**⚠ ORDER MATTERS: remove client-side artifacts BEFORE destroying the cluster.** Kernel RBD mappings (`rbd showmapped`, or `ls /sys/bus/rbd/devices`) and CephFS mounts are CLIENT state that outlives the cluster — and once the cluster's OSDs are gone they become **reboot-only**: `rbd unmap` (force included) hangs against a dead cluster, any read of the device parks in uninterruptible D-state (`kill -9` cannot touch it), and everything that enumerates block devices — including `ceph-volume activate` on the NEXT deployment — blocks on it forever. Field-diagnosed: one stale lockspace mapping from a destroyed cluster silently broke every OSD activation of the replacement cluster on all three nodes, and only a rolling reboot cleared it. The teardown order is therefore: **`rbd unmap` every mapping and clear its `/etc/ceph/rbdmap` entry and disable `rbdmap.service` (the sanlock chain installs boot-persistent mappings there; leaving the entries plus the enabled service resurrects the dead mapping on the next boot) → `virsh secret-undefine` the cluster's libvirt cephx secret on every node (libvirt enforces uniqueness on the secret's USAGE NAME, so a stale secret blocks the next cluster's define — and the roles' FSID-derived UUID means the replacement can never win a name squat) → `umount` CephFS + remove fstab lines → THEN destroy the cluster.** Reversing that order converts removable state into a reboot.

**If the cluster being removed is WEDGED (orchestrator stuck, deploys abandoned): do not lead with `rm-cluster` at all.** `cephadm rm-cluster` depends on the same podman/ceph-volume path that is already jammed — field-measured, it ran 32 minutes against a wedged cluster with zero state change, while the same command had removed a healthy cluster in ~2. The first step of any teardown-after-failure is to kill orphaned `ceph-volume`, `podman run`, and `conmon` processes left by the failed deploy (they hold device locks and can hang even `podman ps`), then go straight to the manual per-node path above, which depends on nothing being healthy.

**A CephFS mount and its fstab entry survive cluster removal.** Neither `rm-cluster` nor the manual path unmounts a CephFS mount or removes its `_netdev` fstab line — a mount pointing at a destroyed cluster's fsid persists on every node, collides with a redeploy that mounts the same path (the roles now refuse loudly when they detect it), and is a boot-time hazard. On each node: `umount -l <mountpoint>` (lazy — a dead-cluster cephfs mount can block a normal umount), then remove the matching `/etc/fstab` line (back the file up first). The tell is the fsid in the mount source not matching the current cluster's.

Two systemd traps seen in the field while doing this:
- Phantom `ceph-<fsid>.target` units that survive `systemctl reset-failed` — it does **not** clear not-found *inactive* units. Delete the dangling symlinks under `/etc/systemd/system/{ceph.target.wants,multi-user.target.wants,ceph-<fsid>.target.wants}/`, then `systemctl daemon-reload`.
- `pgrep -f`/`pkill -f` patterns that match their own shell wrapper — bracket the first character of the pattern (`pgrep -af '[c]eph'`) and verify results by observed state, not exit code.

## Deployment halts in stage 60 (Ceph)

Most common causes in order:

1. Storage network not up on all nodes — the Ceph bootstrap tries to reach peer storage IPs before they are configured. Re-run stage 20 and confirm.
2. Hostname resolution failing between nodes — `cephadm` expects to be able to resolve all OSD hostnames. `host_baseline` writes `/etc/hosts` entries; confirm they match reality.
3. OSD devices not empty — `cephadm` refuses to create OSDs on devices with existing data. `wipefs -a` any stale disks and re-run.
4. Podman/container registry unreachable — for air-gapped sites, `sources.container_registry` must point at the builder's local registry (default `<builder>:5000`) and `sources.container_registry_insecure: true` for the plain-HTTP registry the builder serves. Confirm with `curl -v http://<builder>:5000/v2/_catalog` from a cluster node; should return a JSON list that includes `rhceph/rhceph-7-rhel9`.
