# 05 — Networking

Configure the four interfaces from step 01, each to its single purpose. Correct operation of a Virtual Protection host depends on keeping these separated.

Substitute the real interface names (from `ip -br link`) for the examples:

| Role | Example | This step configures it as |
|---|---|---|
| Management | `ens1f0` | Already up (step 02); leave it |
| Station bus | `ens1f1` | A bridge the VM attaches to |
| Process bus | `ens2f0` | **Reserved, no IP** — the VM takes it by macvtap |
| PTP (dedicated) | `ens2f1` | A plain IP interface, used only by `ptp4l` |

NetworkManager (`nmcli`) is used throughout — it is the default on RHEL 9 and 10 and persists across reboots.

Connection profile names in this guide follow `<type>-<role>-<NIC>`: the profile type (`eth-` for Ethernet, `br-` for a bridge), its role, and the physical NIC it binds to, for example `eth-ptp-ens2f1`. A bridge's port profile is named `br-<role>-port-<NIC>`. Virtual devices carry the type prefix in the device name as well: the station-bus bridge device is `br-station`, and the libvirt network on it is `net-station`. The output of `nmcli connection show` then identifies each profile's type, role and NIC by its name.

Each `nmcli` command below is a single line. Enter them one at a time. **Every `nmcli connection add` for a physical NIC must include `ifname <NIC>`.** A profile added without it has no interface binding, and NetworkManager can activate it on whichever NIC is available. Each `add` below is followed by a check that the profile is bound to the intended NIC. To correct a profile, delete it and re-run its whole block (`add`, `modify`, `up`): a profile re-added without its `modify` line falls back to automatic addressing (DHCP and IPv6 link-local).

## Remove auto-created connection profiles

A fresh installation leaves connection profiles on every port, in one or both of two forms:

- **Profiles named after the device** (for example `ens1f1`): the installer creates one for each NIC it detects.
- **`Wired connection 1`, `Wired connection 2`, and so on**: NetworkManager creates a DHCP profile with autoconnect enabled for each port that has no profile.

On the station-bus, process-bus and PTP NICs these compete with the profiles this step creates: after a reboot, NetworkManager can bring up a leftover DHCP profile instead of the intended one. Delete every profile except `lo` and the management profile before configuring the interfaces.

The installer creates the management profile with the management interface's name (for example `ens1f0`). **Do not delete it**: deleting it drops the SSH session. Rename it instead, after the deletions below; renaming does not interrupt the connection.

```bash
# List the profiles and the device each one is active on
nmcli connection show

# Delete every profile except lo and the management profile, one command per profile,
# whether it is named "Wired connection N" or after a device
sudo nmcli connection delete "Wired connection 1"
sudo nmcli connection delete "Wired connection 2"
sudo nmcli connection delete ens1f1
sudo nmcli connection delete ens2f0

# Rename the management profile to the naming scheme
sudo nmcli connection modify ens1f0 connection.id eth-mgmt-ens1f0
```

Prevent NetworkManager from creating new default profiles for unconfigured ports. Create the configuration file with an editor:

```bash
sudo vi /etc/NetworkManager/conf.d/10-no-auto-default.conf
```

Enter this content and save:

```ini
[main]
no-auto-default=*
```

Reload NetworkManager and list the profiles again:

```bash
sudo systemctl reload NetworkManager

# Only eth-mgmt-ens1f0 remains, plus lo where listed
# (and virbr0 if libvirt is already running; it is removed below)
nmcli connection show
```

## Station bus — a bridge for the VM

The SSC600's station-bus NIC is a virtio interface attached to a libvirt network backed by a host bridge. Create the bridge and add the physical station-bus NIC to it as a port:

```bash
# Create the bridge: profile and device are both br-station
sudo nmcli connection add type bridge con-name br-station ifname br-station

# Add the physical station-bus NIC as the bridge's port
sudo nmcli connection add type ethernet con-name br-station-port-ens1f1 ifname ens1f1 master br-station

# Confirm the port profile is bound to the station-bus NIC
nmcli -g connection.interface-name connection show br-station-port-ens1f1    # ens1f1
```

> **The port profile must carry `ifname`.** A port profile created without it has no interface binding (`connection.interface-name` shows `--`), and NetworkManager can attach it to any available NIC, including the PTP or process-bus NIC. If the check above prints nothing, delete the profile and add it again with `ifname`.

Give the bridge its host address on the station-bus segment and bring it up:

```bash
# IMPORTANT: this is the HOST's address on the segment — it must NOT be the
# address the SSC600SW appliance uses. The relay ships with its own station-bus
# IP (set later via the web HMI / PCM600). Pick a host address in the same
# subnet but distinct from the relay's.
sudo nmcli connection modify br-station ipv4.addresses 10.1.0.1/24 ipv4.method manual
sudo nmcli connection up br-station
```

The relay starts with its station-bus (rear) port at its factory address, **192.168.2.10/24** (ABB SSC600 Engineering Manual 1.5, "Setting up IP addresses"). Give the bridge a temporary second address in that subnet now, so the host can reach the relay for its first re-addressing in step 11. Step 11 removes it afterwards:

```bash
sudo nmcli connection modify br-station +ipv4.addresses 192.168.2.100/24
sudo nmcli device reapply br-station
ip -br addr show br-station      # the site address and 192.168.2.100/24
```

If the site's station-bus subnet is itself 192.168.2.0/24, skip this: the bridge's host address already reaches the relay.

> A bridge is used here rather than macvtap because the station bus carries MMS and the relay's web HMI, which the host and PCM600 must reach through the host network. A bridge allows the host, the VM, and the rest of the segment to communicate. The process bus uses a different mechanism (below).

Define a **libvirt network** on the bridge. The VM XML references the network by name (`net-station`), which decouples it from the bridge device name. Create the network definition with an editor:

```bash
vi /tmp/net-station.xml
```

Enter this content and save:

```xml
<network>
  <name>net-station</name>
  <forward mode='bridge'/>
  <bridge name='br-station'/>
</network>
```

Define, start, and autostart the network:

```bash
sudo virsh net-define /tmp/net-station.xml
sudo virsh net-start net-station
sudo virsh net-autostart net-station
```

(libvirt must be running for this. If `virsh` errors, do step 06 first, then return for these three commands and the default-network removal below. The bridge itself does not depend on libvirt.)

### Remove the libvirt default network

libvirt defines a `default` NAT network (`virbr0`, 192.168.122.0/24) and starts it with the libvirt network service. While a NAT network runs, libvirt enables IPv4 forwarding on the host. A protection host does not route or NAT for its guests, so remove the network:

```bash
sudo virsh net-destroy default
sudo virsh net-autostart default --disable
sudo virsh net-undefine default
sudo virsh net-list --all          # net-station only
```

Forwarding that libvirt enabled can remain on until the next reboot. Step 08 verifies that it is off.

## Process bus — reserved, no IP

The process bus carries GOOSE and Sampled Values: Layer-2 multicast protection signaling at sub-millisecond cadence. The relay receives it by **macvtap passthrough** — frames go from the wire directly into the VM, bypassing the host bridge to minimize latency.

The host must not assign an IP to this NIC or bridge it. Bring it up with no addressing:

```bash
sudo nmcli connection add type ethernet con-name eth-process-ens2f0 ifname ens2f0
sudo nmcli connection modify eth-process-ens2f0 ipv4.method disabled ipv6.method disabled connection.autoconnect yes
sudo nmcli connection up eth-process-ens2f0

# Confirm the binding and that addressing is off (prints ens2f0, disabled, disabled)
nmcli -g connection.interface-name,ipv4.method,ipv6.method connection show eth-process-ens2f0
```

The VM's XML (step 10) attaches to this NIC by name with a macvtap interface in bridge mode. Once the VM is running, the host hands this NIC to the guest.

> **PRP variant (two process-bus LANs — see step 01)**
> Reserve a **second** process-bus NIC the same way. Name both profiles with their LAN:
>
> ```bash
> # Process bus A: rename the existing profile
> sudo nmcli connection modify eth-process-ens2f0 connection.id eth-process-a-ens2f0
>
> # Process bus B: reserved, no addressing, same as process bus A
> sudo nmcli connection add type ethernet con-name eth-process-b-ens2f1 ifname ens2f1
> sudo nmcli connection modify eth-process-b-ens2f1 ipv4.method disabled ipv6.method disabled connection.autoconnect yes
> sudo nmcli connection up eth-process-b-ens2f1
> nmcli -g connection.interface-name,ipv4.method,ipv6.method connection show eth-process-b-ens2f1    # ens2f1, disabled, disabled
> ```
>
> In the 5-NIC PRP layout the port this guide used for PTP (`ens2f1`) becomes process bus B, and **PTP moves to a fifth port** (e.g. `ens3f0`, profile `eth-ptp-ens3f0`) — substitute accordingly below and in step 07. Each PRP LAN must be its own physical port on its own switch.

## PTP NIC — dedicated, nothing else

The PTP NIC runs **only** `ptp4l`: no bridge, no macvtap, no other traffic. If another process consumes its inbound frames, the host clock will not lock.

Assign it an address on the PTP/timing segment:

```bash
sudo nmcli connection add type ethernet con-name eth-ptp-ens2f1 ifname ens2f1
sudo nmcli connection modify eth-ptp-ens2f1 ipv4.addresses 10.2.0.10/24 ipv4.method manual
sudo nmcli connection up eth-ptp-ens2f1

# Confirm the binding and the static address (prints ens2f1, manual)
nmcli -g connection.interface-name,ipv4.method connection show eth-ptp-ens2f1
```

Do not bridge it or attach a VM macvtap to it. Step 07 directs `ptp4l` to this interface.

## Verify the layout

```bash
ip -br addr
# Expect:
#   ens1f0                    — management IP
#   br-station (bridge)       — 10.1.0.1/24
#   ens1f1                    — UP, no IP of its own (bridge port)
#   ens2f0                    — UP, NO IP (process bus)
#   ens2f1                    — 10.2.0.10/24 (PTP)

nmcli -f DEVICE,STATE,CONNECTION device
# Each NIC must carry its own profile: ens1f1 -> br-station-port-ens1f1,
# ens2f0 -> eth-process-ens2f0, ens2f1 -> eth-ptp-ens2f1. A profile whose name
# ends in one NIC but is shown against another was added without ifname:
# delete it and re-run its whole block (add, modify, up) with ifname.

nmcli -f NAME,TYPE,DEVICE,AUTOCONNECT connection show
# Expect only: eth-mgmt-ens1f0, br-station, br-station-port-ens1f1,
# eth-process-ens2f0, eth-ptp-ens2f1, plus lo where listed
# (PRP variant: eth-process-a-ens2f0, eth-process-b-ens2f1, eth-ptp-ens3f0).
# No "Wired connection N" and no virbr0.

bridge link show
# Only ens1f1 may show "master br-station". Some NICs (for example Intel X710)
# list every port with "hwmode VEPA" even when it is in no bridge; that line
# alone is not bridge membership.

sudo virsh net-list --all # net-station only; no default
```

If the process-bus NIC shows an IP, or the PTP NIC is enslaved to a bridge, correct it before proceeding. These conditions break protection signaling and time synchronization.

Continue to [06 — Virtualization](06-virtualization.md).
