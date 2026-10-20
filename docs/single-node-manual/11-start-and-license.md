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

## Pin the relay's vhost-net threads

Now that the VM is running, its `vhost-net` kernel threads exist. They move the network traffic (including the GOOSE/Sampled-Value multicast) between the host and the guest. Left alone they run at `SCHED_OTHER` and wander onto whatever core is free — stealing jitter from the relay even though the vCPUs are pinned. Pin them to the emulator cores (the `<emulatorpin>` set from step 10) and raise them to a low real-time priority:

```bash
qpid=$(pgrep -f 'guest=ssc600-01,')
for tid in $(pgrep "vhost-$qpid"); do
  sudo taskset -pc <emulator-cores> "$tid"   # e.g. 10-11
  sudo chrt -rp 1 "$tid"                    # SCHED_RR 1 — the same policy the step-10 hook sets
done
```

Because these threads are recreated every time the VM boots, we drive this from a libvirt `qemu` hook on the `started` event (`/etc/libvirt/hooks/qemu`) — installed in step 10 — so it is reapplied automatically. That hook also re-asserts the NIC-IRQ affinity (by restarting `vpac-ssc600-setup.service`), since a VM start re-touches the NIC queues. Run the commands above manually only if you did not install the hook in step 10.

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
sudo virsh domiflist ssc600-01     # two interfaces: net-station network + macvtap
```

## Set the relay's station-bus address

The SSC600 starts with its station-bus port at its factory address, **192.168.2.10/24** (ABB SSC600 Engineering Manual 1.5, "Setting up IP addresses"); the default credentials are in ABB's documentation. Set the site's address through the relay's own web HMI, not from RHEL. The host does not configure the guest's IP.

1. Reach the relay at the factory address. With the temporary bridge address from step 05 in place, the host reaches it directly:

   ```bash
   ping -c3 192.168.2.10
   ```

   The web HMI is served over HTTPS only. From a workstation that can SSH to the host, forward a local port through the host, then browse to `https://localhost:8443`:

   ```bash
   ssh -L 8443:192.168.2.10:443 <user>@<host-management-ip>
   ```

   Alternatively, give the PCM600 workstation a second address in 192.168.2.0/24 on its station-bus NIC.
2. In the SSC600 **web HMI**, set the station-bus address for the site (and the service port's address, if it is used).
3. The chosen address must be in the **same subnet as the station-bus bridge** (step 05) but **distinct from the bridge's host address**; the host and the relay must not use the same IP (see step 13).
4. Remove the temporary bridge address from step 05:

   ```bash
   sudo nmcli connection modify br-station -ipv4.addresses 192.168.2.100/24
   sudo nmcli device reapply br-station
   ip -br addr show br-station      # the site address only
   ```

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
