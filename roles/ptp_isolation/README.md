# ptp_isolation

Post-apply verification that the PTP NIC is in the exact state the `ptp_timesync` role expects: link UP, no IP, not a bridge member, not a bond slave, no macvtap children. Invoked from THREE places for defense-in-depth:

1. The `preflight` role (stage 00) runs the checks before any changes.
2. The `networking` role (stage 20) imports `ptp_isolation` at the tail of its play to confirm we did not accidentally enslave the PTP NIC while configuring the rest of the network.
3. `ptp_timesync` (stage 40) re-runs `ptp_isolation` immediately before arming `ptp4l` — by then the host has been touched by host_baseline, virtualization, and rt_tuning; this last gate catches anything that might have re-attached the NIC since stage 20.

Running it three times is cheap (read-only assertions) and the cost of a regression slipping through is days of `SYNCHRONIZATION_FAULT` debugging.

## Why this exists as its own role

Documented field incident: `ptp4l` was bound to a NIC that was also the macvtap target for guest VMs. Guests stole PTP frames and produced `SYNCHRONIZATION_FAULT` every ~10 seconds for multiple days. Making this a dedicated, re-runnable role keeps the check visible and tag-addressable — any change to networking gets this gate for free.

## Tags

- `ptp-isolation` — run the checks

## Dependencies

Reads `networking_defaults.ptp_nic`. No handlers. Read-only.
