# validate

**Status: stub — not yet implemented.**

Stage 90 of the vPAC site deployment.

## Planned behavior

- `cyclictest` on each `rt_hosts` member for `validate.cyclictest_duration_s` seconds; fail if max latency > `validate.cyclictest_max_latency_us`
- `pcs status` — all nodes Online, no failed actions, STONITH devices registered + Started
- `ceph -s` — HEALTH_OK, all OSDs up, no stuck PGs (required when `validate.ceph_require_health_ok: true`)
- PTP offset — `pmc -u -b 0 'GET CURRENT_DATA_SET'` on each node; assert offset < `validate.ptp_max_offset_ns`
- Corosync links — `corosync-cfgtool -s` shows all rings connected, no failed links
- STONITH dry-run — `pcs node standby <node>`, `pcs stonith fence <node> --dry-run`, confirm device responds; `pcs node unstandby <node>` (opt-in via `--tags stonith-dryrun`)
- Emit a summary report to `/var/log/vpac-validate-<timestamp>.txt` on the control node

## Dependencies

- All prior stages (preflight through vm_deploy)

## Tags

- `validate` — full role (currently a no-op stub)
- `stonith-dryrun` — optional real fence test (disabled by default in production)
