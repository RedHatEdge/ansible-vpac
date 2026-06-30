# validate

Stage 90 of the vPAC site deployment. Read-only, end-to-end cluster
health checks. Designed to run both at the tail of `site.yml` and
ad-hoc post-incident — every check is tag-addressable.

## Behavior

| Tag | What it asserts |
|---|---|
| `validate-preflight` | sudo elevates, RHEL 9.x running |
| `validate-rt-kernel` | `+rt` kernel, `realtime-virtual-host` tuned profile, `isolcpus` / `nohz_full` / `rcu_nocbs` / `idle=poll` / `default_hugepagesz` on cmdline, `/sys/.../{isolated,nohz_full}` matches inventory, NMI watchdog off |
| `validate-rt-scheduling` | `sched_rt_runtime_us` matches inventory, `irqbalance` inactive, `system.slice` cpuset excludes isolated CPUs, KSM disabled |
| `validate-cyclictest` | `cyclictest -m -p 95 -t1 -i200 -D <duration>` max latency under `validate.cyclictest_max_latency_us` (skipped on non-RT hosts) |
| `validate-memory` | `HugePages_Total` matches `rt_tuning_nr_hugepages`, `MemAvailable` ≥ floor, no swap activity |
| `validate-ceph` | `ceph health detail` HEALTH_OK (with operator-provided allowlist), full mon quorum, all OSDs up+in, every PG `active+clean`, MDS active, mon clock skew under threshold |
| `validate-network` | no `linkdown` routes, storage NIC up + not bridge-enslaved + ≥ 10 Gbps, configured bridges present |
| `validate-cluster` | every node `Online`, no failed actions / fencing, `corosync` quorate with no faulty rings, `stonith-enabled=true`, no leftover `cli-prefer-` / `cli-ban-` move constraints |
| `validate-ptp` | `timemaster` (PTP role) or `chronyd` (NTP-follower) active, ptp4l `offsetFromMaster` under threshold, chrony `Last offset` under threshold and `Leap status: Normal`, at least one selected source |
| `validate-vms` | running set matches `vm_catalog` placement, **iothread only on virtio disks** (libvirt rejects iothread on SATA/IDE/USB), per RT VM: hugepages backing + locked + nosharepages + `pmu` off + `vcpusched fifo` + watchdog neutered, RBD disks: `cache='none'` + `<auth>` element present |
| `validate-system` | dmesg clean of RCU stalls / hung tasks / OOM / I/O errors, `journalctl --disk-usage` under `validate.journal_max_disk_gb`, SELinux Enforcing or Permissive (never Disabled) |
| `validate-summary` | aggregate every host's findings into `~/vpac-validate-reports/vpac-validate-<UTC>.txt` on the control node (override via `validate.summary_dir`) |

## Variables

All thresholds live under the `validate.*` block in inventory
`group_vars/all.yml` (section 14 of the example contract). Every key has
a default in `roles/validate/defaults/main.yml`; only override what your
site needs.

Soft-fail toggle for ad-hoc full-cluster sweeps:

```yaml
validate_warn_only: true   # convert each fail into a warning; summary
                           # captures the full finding set in one pass
```

## Out of scope

- **STONITH dry-run.** Validate stays read-only. Use the operator
  helper `op-stonith-fence-test.yml` to drain a node and trigger a
  real fence on a controlled basis.
- **Active reconciliation.** Validate never modifies state — if a check
  fails, fix at the appropriate stage and re-run that stage.

## Dependencies

All prior stages (preflight through vm_deploy).
