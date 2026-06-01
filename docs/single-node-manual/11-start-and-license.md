# 11 — Start and license

The domain is defined. This step boots it, enables persistence, sets its station-bus identity, and activates its ABB license. The first three are RHEL/libvirt operations; license activation is an ABB workflow, summarized here.

## Start the VM

```bash
sudo virsh start ssc600-01
sudo virsh list                 # ssc600-01 should be "running"
```

Enable automatic start when the host boots, so the relay restarts after a power event:

```bash
sudo virsh autostart ssc600-01
```

## Watch it boot

Attach to the serial console (detach with `Ctrl+]`):

```bash
sudo virsh console ssc600-01
```

or open the graphical console over the local VNC (from the host's desktop, or via a tunnel). First find the VNC port libvirt assigned — autoport starts at 5900:

```bash
# On the host — prints e.g. 127.0.0.1:0  (display :0 = TCP port 5900)
sudo virsh vncdisplay ssc600-01

# From a workstation, tunnel that port (5900 + display number) to the host:
ssh -L 5900:127.0.0.1:5900 <admin>@<host-mgmt-ip>
# then point a VNC viewer at localhost:5900
```

Confirm the guest sees its devices as the XML intended:

```bash
# From the host:
sudo virsh dominfo ssc600-01
sudo virsh vcpuinfo ssc600-01      # each vCPU pinned to its core
sudo virsh domiflist ssc600-01     # two interfaces: station-bus network + macvtap
```

## Set the relay's station-bus address

The SSC600 ships with a default station-bus IP. Set the site's address through the relay's own interface, via its web HMI, not from RHEL. The host does not configure the guest's IP.

1. From the PCM600 workstation (or a browser) on the station bus, reach the relay at its current address. The SSC600 ships with a factory-default station-bus address and default credentials, documented in ABB's SSC600 documentation — consult it for the default values. To reach the relay the first time, temporarily place the PCM600 workstation (or the station-bus bridge address) in the relay's default subnet.
2. In the SSC600 **web HMI**, set the station-bus (and any service/rear-port) addresses for the site.
3. The chosen address must be in the **same subnet as the station-bus bridge** (step 05) but **distinct from the bridge's host address**; the host and the relay must not use the same IP (see step 13).

Confirm reachability from the host once set:

```bash
ping -c3 <relay-station-bus-ip>
```

## Activate the ABB license

SSC600 licensing is **per-VM** and is activated **after** the VM is running, using ABB's tools:

1. Open **PCM600** on the Windows workstation and connect to the running SSC600 over the station bus.
2. Activate the license for this instance per ABB's procedure (the relay's serial/instance identity is what the license binds to).
3. Complete the protection configuration — settings, SCADA bindings, GOOSE/SV datasets — in PCM600 and the web HMI. This engineering is ABB-specific and customer-specific, and is out of scope for this host-build guide.

> The license is stored on the disk. Activation and all in-VM configuration are written into `ssc600-01.img`. Restoring the pristine factory image from step 09 requires repeating activation and configuration. Keep a post-licensing backup of the disk once the relay is configured:
> ```bash
> sudo virsh shutdown ssc600-01           # graceful stop (qemu-guest-agent)
> # wait for it to power off, then:
> sudo cp --sparse=always /var/lib/libvirt/images/ssc600-01.img \
>                         /var/lib/libvirt/images/ssc600-01.licensed.img
> sudo virsh start ssc600-01
> ```

With the relay running, reachable, and licensed, verify the host. Continue to [12 — Validate](12-validate.md).
