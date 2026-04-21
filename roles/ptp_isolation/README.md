# ptp_isolation

Post-apply verification that the PTP NIC is in the exact state the `ptp_timesync` role expects: link UP, no IP, not a bridge member, not a bond slave, no macvtap children. Runs at the tail of stage 20 (networking) to confirm we did not accidentally enslave the PTP NIC while configuring the rest of the network.

The same checks run inside the `preflight` role before any changes. This role re-runs them after networking changes — belt and suspenders against regressions in the networking role template.

## Why this exists as its own role

The field deployment March 2026 incident: `ptp4l` was bound to a NIC that was also the macvtap target for guest VMs. Guests stole PTP frames and produced `SYNCHRONIZATION_FAULT` every ~10 seconds for multiple days. Making this a dedicated, re-runnable role keeps the check visible and tag-addressable — any change to networking gets this gate for free.

## Tags

- `ptp-isolation` — run the checks

## Dependencies

Reads `networking_defaults.ptp_nic`. No handlers. Read-only.
