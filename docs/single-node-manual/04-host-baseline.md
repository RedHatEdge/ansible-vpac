# 04 — Host baseline

This step configures host identity, the package set, the firewall, and temporary time synchronization. PTP replaces the time synchronization in step 07.

## Hostname

Set a fully-qualified hostname. A stable hostname is required by the relay tooling:

```bash
sudo hostnamectl set-hostname site1-node-a.example.com
```

Ensure it resolves — via DNS or a `/etc/hosts` entry for the management IP:

```bash
# /etc/hosts
10.0.0.10   site1-node-a.example.com site1-node-a
```

## Packages

Install the full single-node package set: virtualization, the real-time kernel, PTP, the cache controller, and the bundle-extraction tool.

```bash
sudo dnf -y install \
  qemu-kvm libvirt libvirt-client virt-install \
  edk2-ovmf swtpm swtpm-tools \
  virtiofsd \
  tuned tuned-profiles-realtime tuned-profiles-nfv \
  kernel-rt kernel-rt-core \
  linuxptp \
  chrony \
  intel-cmt-cat \
  bsdtar \
  realtime-tests \
  cockpit cockpit-machines
```

Purpose of the less obvious packages:

- **`tuned-profiles-realtime` / `tuned-profiles-nfv`** — provide the `realtime-virtual-host` tuned profile used in step 06 (from the NFV repo enabled in step 03).
- **`kernel-rt` / `kernel-rt-core`** — the `PREEMPT_RT` kernel. Installed now, booted in step 08.
- **`linuxptp`** — `ptp4l`, `phc2sys`, `timemaster`, `pmc` for step 07.
- **`intel-cmt-cat`** — the `pqos` tool for L3 cache partitioning in step 08. (It can be installed on non-Intel hardware but is unused there.)
- **`virtiofsd`** — the host daemon backing the virtiofs PTP-status share the SSC600 consumes.
- **`bsdtar`** — extracts the ABB `.cab` bundle in step 09 (libarchive reads the cab format; the standalone `cabextract` tool is not in the RHEL 9 repositories).
- **`realtime-tests`** — provides `cyclictest` for validation in step 12 (in AppStream; this is the RHEL 9/10 successor to the older `rt-tests` package, which no longer exists).
- **`cockpit` / `cockpit-machines`** — provides the RHEL Web Console incl. VM management

## Firewall baseline

Keep the firewall enabled. Open only the services the host requires, per interface zone. At minimum, allow SSH on the management interface, then reload so the permanent changes take effect:

```bash
sudo systemctl enable --now firewalld

# Put the management NIC in a zone that allows SSH
sudo firewall-cmd --permanent --zone=public --change-interface=ens1f0
sudo firewall-cmd --permanent --zone=public --add-service=ssh

# Activate the permanent changes
sudo firewall-cmd --reload
```

The management interface is the one that needs host firewall rules. The other three interfaces are configured in step 05; how they are zoned depends on site policy:

- **Process bus** — carries Layer-2 GOOSE/SV that does not transit the host firewall at all (it is delivered directly to the VM by macvtap), so no host rules apply.
- **PTP NIC** — carries PTP event/general traffic (UDP 319/320). Place it in a zone that permits that traffic, or a trusted zone.
- **Station bus** — the bridge carries the relay's MMS and web-HMI traffic to the VM; firewall its host address per site policy.

> A protection host is a high-value target on an OT network. Open ports deliberately, document each one, and default to closed. This baseline is a minimum, not a complete hardening pass.

## Temporary time sync

PTP becomes the authoritative clock in step 07. Until then, configure chrony so the host maintains an accurate wall clock for package operations, logs, and certificates:

```bash
sudo systemctl enable --now chronyd
chronyc tracking
```

Do not tune chrony now; step 07 reconfigures it (or transfers timekeeping entirely to PTP and removes NTP sources). This step only establishes an approximately correct clock during the build.


## Configure RHEL Web Console

```bash
# Enable the service:
sudo systemctl enable --now cockpit.socket

# Open firewall (management zone only):
sudo firewall-cmd --permanent --zone=public --add-service=cockpit
sudo firewall-cmd --reload
```

Log in at `https://<management-ip>:9090` with the sudo admin user. Root web
login is disallowed by default (`/etc/cockpit/disallowed-users`) — leave it
that way on a protection host; the admin user can escalate privileges inside
the session where needed.

Two operational caveats:

- Expose the web console on the **management network only** — never toward the
  station or process bus.
- Close web console sessions before running latency validation (step 12); an
  open session polls the host and adds avoidable noise to the measurement.

Continue to [05 — Networking](05-networking.md).
