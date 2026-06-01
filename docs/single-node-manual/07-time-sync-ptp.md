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

# ptp4l options for the host clock: slave-only — discipline from the
# grandmaster on the segment, never act as a master.
[ptp4l.conf]
slaveOnly 1

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

With this option, remove NTP sources from chrony so it does not pull against `phc2sys`. Either stop chronyd entirely, or comment out every `server`/`pool` line in `/etc/chrony.conf` and leave chrony only as a local clock holder.

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

The SSC600 reads the host's PTP status from inside the guest via a shared directory (a virtiofs mount configured in step 09). The host writes its current PTP state into that directory for the relay. The directory and status writer are created in step 09. The relay depends on the host's PTP synchronization, so it must remain stable.

Continue to [08 — Real-time tuning](08-rt-tuning.md).
