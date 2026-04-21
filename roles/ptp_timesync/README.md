# ptp_timesync

**Status: stub — not yet implemented.**

Stage 40 of the vPAC site deployment.

## Planned behavior

- Install `linuxptp` (ptp4l, phc2sys) and `chrony`
- Configure `ptp4l` to run on `networking_defaults.ptp_nic` only — never on a bridged/bonded/macvtap NIC (see `ptp_isolation` role)
- Use `ptp_domain`, `ptp_transport`, `ptp_delay_mechanism`, `ptp_profile` from inventory
- When `ptp_is_authoritative: true`, render a chrony config with **no** `server`/`pool` NTP lines so chrony trusts PTP as the only source
- When `ptp_is_authoritative: false`, keep NTP fallback servers from `site_dns_servers` or a vendor list
- Enable `timemaster.service` instead of running `ptp4l`/`chronyd` separately — single daemon to supervise
- Preflight-style regression guard: re-run `ptp_isolation` checks before arming ptp4l

## Dependencies

- `networking` (stage 20) — PTP NIC must be configured up-with-no-IP before this role
- `ptp_isolation` role must pass — the PTP NIC cannot be in a bridge or bond

## Tags

- `ptp` — full role (currently a no-op stub)
