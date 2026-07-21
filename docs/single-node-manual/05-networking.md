# 05 — Networking

Configure the four interfaces from step 01, each to its single purpose. Correct operation of a Virtual Protection host depends on keeping these separated.

Substitute the real interface names (from `ip -br link`) for the examples:

| Role | Example | This step configures it as |
|---|---|---|
| Management | `ens1f0` | Already up (step 02); leave it |
| Station bus | `ens1f1` | A bridge the VM attaches to |
| Process bus | `ens2f0` | **Reserved, no IP** — the VM takes it by macvtap |
| PTP (dedicated) | `ens2f1` | A plain IP interface, used only by `ptp4l` |

NetworkManager (`nmcli`) is used throughout — it is the default on RHEL 9 and persists across reboots.

## Station bus — a bridge for the VM

The SSC600's station-bus NIC is a virtio interface attached to a libvirt network backed by a host bridge. Create the bridge:

```bash
# Create the bridge
sudo nmcli connection add type bridge con-name station-bus ifname station-bus

# Enslave the physical station-bus NIC to it
sudo nmcli connection add type ethernet con-name station-bus-port \
  ifname ens1f1 master station-bus

# Give the bridge the station-bus gateway address for this segment.
# IMPORTANT: this is the HOST's address on the segment — it must NOT be the
# address the SSC600SW appliance uses. The relay ships with its own station-bus
# IP (set later via the web HMI / PCM600). Pick a host address in the same
# subnet but distinct from the relay's.
sudo nmcli connection modify station-bus ipv4.addresses 10.1.0.1/24 ipv4.method manual

sudo nmcli connection up station-bus
```

> A bridge is used here rather than macvtap because the station bus carries MMS and the relay's web HMI, which the host and PCM600 must reach through the host network. A bridge allows the host, the VM, and the rest of the segment to communicate. The process bus uses a different mechanism (below).

Define a **libvirt network** pointing at the bridge. The VM XML references the network by name (`station-bus`), which decouples it from the bridge's interface name:

```bash
cat > /tmp/net-station-bus.xml <<'EOF'
<network>
  <name>station-bus</name>
  <forward mode='bridge'/>
  <bridge name='station-bus'/>
</network>
EOF

sudo virsh net-define /tmp/net-station-bus.xml
sudo virsh net-start station-bus
sudo virsh net-autostart station-bus
```

(libvirt must be running for this. If `virsh` errors, do step 06 first, then return for these three commands. The bridge itself does not depend on libvirt.)

## Process bus — reserved, no IP

The process bus carries GOOSE and Sampled Values: Layer-2 multicast protection signaling at sub-millisecond cadence. The relay receives it by **macvtap passthrough** — frames go from the wire directly into the VM, bypassing the host bridge to minimize latency.

The host must not assign an IP to this NIC or bridge it. Bring it up with no addressing:

```bash
sudo nmcli connection add type ethernet con-name process-bus ifname ens2f0
sudo nmcli connection modify process-bus \
  ipv4.method disabled ipv6.method disabled \
  connection.autoconnect yes
sudo nmcli connection up process-bus
```

The VM's XML (step 10) attaches to this NIC by name with a macvtap interface in bridge mode. Once the VM is running, the host hands this NIC to the guest.

> **PRP variant (two process-bus LANs — see step 01)**
> Reserve a **second** process-bus NIC the same way, and rename for clarity:
>
> ```bash
> # Rename the existing connection to process-bus-a (optional but clearer):
> sudo nmcli connection modify process-bus connection.id process-bus-a
>
> # Process bus B — reserved, no addressing, same as process bus A:
> sudo nmcli connection add type ethernet con-name process-bus-b ifname ens2f1
> sudo nmcli connection modify process-bus-b \
>   ipv4.method disabled ipv6.method disabled \
>   connection.autoconnect yes
> sudo nmcli connection up process-bus-b
> ```
>
> In the 5-NIC PRP layout the port this guide used for PTP (`ens2f1`) becomes process bus B, and **PTP moves to a fifth port** (e.g. `ens3f0`) — substitute accordingly in step 07. Each PRP LAN must be its own physical port on its own switch.

## PTP NIC — dedicated, nothing else

The PTP NIC runs **only** `ptp4l`: no bridge, no macvtap, no other traffic. If another process consumes its inbound frames, the host clock will not lock.

Assign it an address on the PTP/timing segment:

```bash
sudo nmcli connection add type ethernet con-name ptp ifname ens2f1
sudo nmcli connection modify ptp ipv4.addresses 10.2.0.10/24 ipv4.method manual
sudo nmcli connection up ptp
```

Do not bridge it or attach a VM macvtap to it. Step 07 directs `ptp4l` to this interface.

## Verify the layout

```bash
ip -br addr
# Expect:
#   ens1f0 / public           — management IP
#   station-bus (bridge)      — 10.1.0.1/24
#   ens1f1                    — enslaved to station-bus, no IP of its own
#   ens2f0 / process-bus      — UP, NO IP
#   ens2f1 / ptp              — 10.2.0.10/24

nmcli connection show
bridge link show          # ens1f1 should appear under the station-bus bridge
```

If the process-bus NIC shows an IP, or the PTP NIC is enslaved to a bridge, correct it before proceeding. These conditions break protection signaling and time synchronization.

Continue to [06 — Virtualization](06-virtualization.md).
