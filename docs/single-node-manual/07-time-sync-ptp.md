# 07 — Time synchronization (PTP)

Protection relays require precise, traceable time; IEC 61850 sampled-value alignment depends on it. The host runs **PTP (IEEE 1588)** on its dedicated NIC, disciplines the system clock from the PTP hardware clock, and exposes its sync state to the relay VM.

Two requirements:

1. **PTP runs on the dedicated NIC only** (`ens2f1` from step 05). No other traffic uses that NIC.
2. **When PTP is the authoritative source, NTP must not compete with it.** Either configure chrony to discipline from the PHC and remove its internet/LAN NTP servers, or run PTP standalone. Two time authorities disciplining the clock simultaneously produce unstable timekeeping.

## Confirm the NIC has a PTP hardware clock

```bash
sudo ethtool -T ens2f1
```

Look for `PTP Hardware Clock:` with a non-negative index and `hardware-transmit`/`hardware-receive` timestamping capabilities. If the NIC supports only software timestamping, PTP will run with reduced accuracy; a NIC with a hardware PHC is preferred for the PTP role.

## Option A — timemaster (recommended)

`timemaster` (from `linuxptp`) runs `ptp4l` and feeds the result to chrony, providing PTP precision with chrony as the single disciplining authority. This satisfies the second requirement above.

Edit `/etc/timemaster.conf`. Point the PTP domain at the dedicated NIC and define no NTP servers, so PTP is the only time source:

```ini
# /etc/timemaster.conf  (key sections; leave the rest at package defaults)

# PTP source on the dedicated NIC. timemaster runs ptp4l here and feeds the
# result to chrony.
[ptp_domain 0]
interfaces ens2f1

[timemaster]
ntp_program chronyd

# ptp4l options for the host clock.
# clientOnly       — discipline from the grandmaster, never act as a server.
# network_transport / delay_mechanism / step_threshold — the IEC 61850-9-3
#   power-utility profile: PTP over Ethernet (L2) with peer-to-peer delay.
#   This matches the SSC600 vendor documentation and most substation
#   grandmasters. The transport is SITE-DEPENDENT: if the grandmaster runs
#   PTP over UDPv4 with end-to-end delay, set network_transport UDPv4 and
#   delay_mechanism E2E instead — confirm against the grandmaster's config.
[ptp4l.conf]
clientOnly 1
network_transport L2
delay_mechanism P2P
step_threshold 0.1

# Do NOT add any [ntp_server <address>] sections. Their absence is what makes
# PTP the only time source; add one only if site policy requires a fallback.
```

timemaster generates chrony's configuration from these sections, using the PTP domain as the reference clock. With no `[ntp_server …]` section defined, chrony has no NTP servers and PTP is the sole time source.

Disable standalone chronyd and ptp4l (timemaster manages them), then start timemaster:

```bash
sudo systemctl disable --now chronyd
sudo systemctl enable --now timemaster
systemctl status timemaster --no-pager
```

## Option B — ptp4l + phc2sys directly

To run the daemons individually, drive `ptp4l` on the PTP NIC and use `phc2sys` to copy the PHC to the system clock.

```bash
# /etc/sysconfig/ptp4l — drives the ptp4l service on the dedicated NIC.
# -s = slave-only (this host disciplines from the grandmaster, never masters).
sudo tee /etc/sysconfig/ptp4l >/dev/null <<'EOF'
OPTIONS="-f /etc/ptp4l.conf -i ens2f1 -s"
EOF

sudo systemctl enable --now ptp4l

# phc2sys: discipline the system clock from the PTP NIC's PHC
sudo tee /etc/sysconfig/phc2sys >/dev/null <<'EOF'
OPTIONS="-s ens2f1 -c CLOCK_REALTIME -w -O 0"
EOF

sudo systemctl enable --now phc2sys
```

With this option, put the same profile settings in the `[global]` section of `/etc/ptp4l.conf`: `network_transport L2`, `delay_mechanism P2P`, `step_threshold 0.1` — or the UDPv4/E2E branch, per the grandmaster (see Option A).

Remove NTP sources from chrony so it does not pull against `phc2sys`. Either stop chronyd entirely, or comment out every `server`/`pool` line in `/etc/chrony.conf` and leave chrony only as a local clock holder.

## Discipline the process-bus NIC's hardware clock

In this guide's four-NIC layout the process bus and PTP are separate ports. The vendor's engineering manual calls for the process-bus NIC's PHC to be disciplined from the PTP NIC's PHC in that case, so the port carrying Sampled Values agrees with the grandmaster's time. (Running ptp4l on the process-bus port directly is not an option here — its inbound frames belong to the relay's macvtap.)

```bash
sudo tee /etc/systemd/system/phc2sys-procbus.service >/dev/null <<'EOF'
[Unit]
Description=Sync process-bus NIC PHC from the PTP NIC PHC
After=timemaster.service ptp4l.service

[Service]
# -c = clock being disciplined (process-bus NIC), -s = source (PTP NIC)
ExecStart=/usr/sbin/phc2sys -c ens2f0 -s ens2f1 -O 0
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now phc2sys-procbus.service
journalctl -u phc2sys-procbus -n 5 --no-pager    # offset should settle near 0
```

This applies to both Option A and Option B. With two process-bus ports (the PRP variant), run one instance per port.

## Verify lock

Wait approximately one minute, then check the offset to the grandmaster:

```bash
# With timemaster/ptp4l running:
sudo pmc -u -b 0 'GET CURRENT_DATA_SET' -i ens2f1
# Look at offsetFromMaster — should converge toward 0 (nanoseconds)

# System clock discipline:
chronyc tracking      # Option A — Last offset should be small, Leap: Normal
# or
journalctl -u phc2sys -f   # Option B — offset should settle near 0
```

A correctly synchronized host shows the PTP port in `SLAVE` state with `offsetFromMaster` below one microsecond. If the port alternates between `SLAVE` and `UNCALIBRATED`, the common cause is another process consuming PTP frames on that NIC; confirm no VM has a macvtap on the PTP interface and that it is not bridged (see step 13).

## The relay needs to see PTP health

The SSC600SW reads the host's PTP status from inside the guest via a shared directory (a virtiofs mount configured in step 09). The status writer is **shipped by ABB in the bundle** — step 09 installs the vendor's `ptp_status` service, which writes the file the relay parses into that directory. The relay depends on the host's PTP synchronization, so it must remain stable.

Continue to [08 — Real-time tuning](08-rt-tuning.md).
