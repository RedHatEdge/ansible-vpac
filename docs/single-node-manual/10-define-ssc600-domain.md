# 10 — Define the SSC600SW domain

> **The `<vcpupin>` and `<emulatorpin>` cores below must match the layout from
> [step 06](06-virtualization.md#plan-your-core-layout)** — the same cores you isolated,
> and the same ones step 09 assigns the cache class to. The XML shown uses the guide's
> 16-core example; substitute your own numbers.

This step defines the libvirt domain XML that applies the host tuning to the relay VM. Each element is documented in the comments.

This is the **single-node** domain: a **local file-backed disk** (no Ceph RBD), **no sanlock lease** (no cluster), and **virtio** disk and NICs (the SSC600 guest kernel includes virtio drivers). The three-node automated path adds RBD, leases, and Pacemaker to this same structure.

## Adjust these to the host before defining

| Placeholder in the XML | Replace with | Must match |
|---|---|---|
| vCPU pins `12`,`13`,`14`,`15` | The VM's isolated cores | Step 06 `isolated_cores` / step 09 `RT_CORES` |
| emulator pin `10-11` | Two isolated housekeeping-adjacent cores | Inside the `isolated_cores` set |
| `memory` `8` | Guest RAM in GiB | A whole number of 1 GiB hugepages reserved in step 06/08 |
| disk `source file` | Where the image was placed (step 09) | The actual path |
| `source network='net-station'` | The libvirt network name | Step 05 |
| `source dev='ens2f0'` | The process-bus NIC's **interface name**, as `ip -br link` shows it — not the NetworkManager connection name (`eth-process-…`) from step 05. Interface names are at most 15 characters; a longer name fails at `virsh start` with `invalid interface name`. | Step 05 |
| `source dir` (virtiofs) | Host PTP-status dir (`PTP_STATUS_FOLDER`) | Step 09 |

## The XML

Save as `~/ssc600-01.xml`:

```xml
<!-- Single-node ABB SSC600 protection relay -->
<domain type='kvm'>
  <name>ssc600-01</name>

  <!-- Guest RAM, backed entirely by 1 GiB hugepages so it never pages.
       locked        = memory cannot be swapped (determinism).
       nosharepages  = exclude from KSM page-merging (KSM scanning is jitter).
       access shared = required so virtiofs (the PTP-status share below) can
                       map guest memory. -->
  <memory unit='GiB'>8</memory>
  <currentMemory unit='GiB'>8</currentMemory>
  <memoryBacking>
    <hugepages>
      <page size='1' unit='GiB'/>
    </hugepages>
    <locked/>
    <nosharepages/>
    <access mode='shared'/>
  </memoryBacking>

  <vcpu placement='static'>4</vcpu>

  <!-- CPU pinning and real-time scheduling. -->
  <cputune>
    <!-- Each vCPU pinned 1:1 to an isolated physical core. These four cores
         are part of the isolated set from step 06 (applied to the cmdline by
         tuned) and are assigned the RT cache class in step 09. -->
    <vcpupin vcpu='0' cpuset='12'/>
    <vcpupin vcpu='1' cpuset='13'/>
    <vcpupin vcpu='2' cpuset='14'/>
    <vcpupin vcpu='3' cpuset='15'/>

    <!-- QEMU's emulator threads run on separate isolated cores so they never
         steal cycles from a vCPU. -->
    <emulatorpin cpuset='10-11'/>

    <!-- The vCPUs run SCHED_FIFO at priority 50 — the SSC600 reference RT
         priority. (The NovaTech Orion/VPR relay uses 40.) The emulator runs
         FIFO 1: high enough to take precedence over ordinary host work (sshd,
         chrony) but below the vCPU priority so it never preempts a vCPU. -->
    <vcpusched vcpus='0' scheduler='fifo' priority='50'/>
    <vcpusched vcpus='1' scheduler='fifo' priority='50'/>
    <vcpusched vcpus='2' scheduler='fifo' priority='50'/>
    <vcpusched vcpus='3' scheduler='fifo' priority='50'/>
    <emulatorsched scheduler='fifo' priority='1'/>
  </cputune>

  <!-- Strict NUMA pinning: hugepages must come from the node the cores live
       on, never silently from a remote node. Single-socket reference is
       node 0; set to the node the isolated cores belong to (lscpu shows it). -->
  <numatune>
    <memory mode='strict' nodeset='0'/>
  </numatune>

  <!-- q35 machine, BIOS boot (the SSC600 image is a BIOS guest, not UEFI). -->
  <os>
    <type arch='x86_64' machine='q35'>hvm</type>
    <boot dev='hd'/>
  </os>

  <!-- Real-time feature block.
       apic eoi=on   : Intel APIC end-of-interrupt optimization.
       hint-dedicated: tell the guest its vCPUs are dedicated (it can spin
                       instead of yielding).
       poll-control off: disable adaptive halt-polling (a jitter source).
       pv-ipi on     : paravirtual inter-processor interrupts (lower latency).
       vmport off    : disable the legacy VMware backdoor port (jitter).
       pmu off       : no guest perfmon counters — prevents PMI storms on the
                       isolated cores. -->
  <features>
    <acpi/>
    <apic eoi='on'/>
    <kvm>
      <hint-dedicated state='on'/>
      <poll-control state='off'/>
      <pv-ipi state='on'/>
    </kvm>
    <vmport state='off'/>
    <pmu state='off'/>
  </features>

  <!-- host-passthrough exposes every host CPU feature the relay software may
       need. invtsc advertises an invariant TSC so the guest can pin its clock
       to the TSC. Explicit single-thread topology so the guest sees 4 plain
       cores (no in-guest hyperthread split), matching whole-core isolation. -->
  <cpu mode='host-passthrough' check='none' migratable='on'>
    <cache mode='passthrough'/>
    <topology sockets='1' cores='4' threads='1'/>
    <feature policy='require' name='invtsc'/>
  </cpu>

  <!-- Clock: UTC, no HPET (HPET reads are ~3.6 µs each and dominate jitter),
       kvmclock + native TSC so the guest reads time without trapping into the
       hypervisor. -->
  <clock offset='utc'>
    <timer name='rtc' tickpolicy='catchup'/>
    <timer name='pit' tickpolicy='delay'/>
    <timer name='hpet' present='no'/>
    <timer name='kvmclock' present='yes'/>
    <timer name='tsc' present='yes' mode='native'/>
  </clock>

  <!-- No guest-initiated suspend — a relay must not suspend. -->
  <pm>
    <suspend-to-mem enabled='no'/>
    <suspend-to-disk enabled='no'/>
  </pm>

  <on_poweroff>destroy</on_poweroff>
  <on_reboot>restart</on_reboot>
  <on_crash>restart</on_crash>

  <devices>
    <emulator>/usr/libexec/qemu-kvm</emulator>

    <!-- The SSC600 disk: the raw image extracted in step 09, local file.
         cache=none bypasses the host page cache (predictable, no double
         buffering); io=threads uses a worker pool for I/O. virtio bus —
         the SSC600 guest kernel includes virtio drivers. -->
    <disk type='file' device='disk'>
      <driver name='qemu' type='raw' cache='none' io='threads'/>
      <source file='/var/lib/libvirt/images/ssc600-01.img'/>
      <target dev='vda' bus='virtio'/>
    </disk>

    <!-- NIC 1 — station bus. Attached to the libvirt 'net-station' network
         (the bridge from step 05). Carries MMS, engineering tools, and the
         relay's web HMI. virtio model. -->
    <interface type='network'>
      <source network='net-station'/>
      <model type='virtio'/>
    </interface>

    <!-- NIC 2 — process bus. macvtap direct passthrough on the reserved
         process-bus NIC, bridge mode: Bridge mode drops a switch-reflection dependency and lets the host macvtap forward directly. This NIC has no host IP (step 05). -->
    <interface type='direct' trustGuestRxFilters='yes'>
      <source dev='ens2f0' mode='bridge'/>
      <model type='virtio'/>
    </interface>

    <!-- PRP variant (see steps 01/05): add a THIRD interface — one macvtap
         per PRP LAN, each on its own physical NIC. The relay performs the
         PRP duplicate-discard across the two; assign LAN A / LAN B inside
         the relay per ABB's PRP configuration.

    <interface type='direct' trustGuestRxFilters='yes'>
      <source dev='ens2f1' mode='bridge'/>
      <model type='virtio'/>
    </interface>
    -->

    <!-- No memory ballooning — a relay's memory footprint is fixed; balloon
         inflate/deflate is not used. -->
    <memballoon model='none'/>

    <!-- q35 auto-injects an ITCO watchdog if nothing is emitted. An ITCO
         watchdog with action='reset' can cause false-positive reboots under
         load, so it is emitted explicitly with action='none': present
         (required by the q35 schema) but inactive. -->
    <watchdog model='itco' action='none'/>

    <serial type='pty'>
      <target type='isa-serial' port='0'>
        <model name='isa-serial'/>
      </target>
    </serial>
    <console type='pty'>
      <target type='serial' port='0'/>
    </console>

    <!-- qemu-guest-agent channel: lets `virsh shutdown` signal a graceful
         stop instead of a hard kill. -->
    <channel type='unix'>
      <source mode='bind'/>
      <target type='virtio' name='org.qemu.guest_agent.0'/>
    </channel>

    <!-- virtio RNG so the guest does not block on entropy right after boot. -->
    <rng model='virtio'>
      <backend model='random'>/dev/urandom</backend>
    </rng>

    <!-- virtiofs PTP-status share. ABB's ptp_status service (step 09)
         writes the relay's status file into its PTP_STATUS_FOLDER
         (/var/lib/libvirt/images/ptp in SSC600 SW 1.5.1); the guest mounts
         it via the 'ptp' tag and reads host time status.
         Requires the <access mode='shared'/> set in memoryBacking above. -->
    <filesystem type='mount' accessmode='passthrough'>
      <driver type='virtiofs'/>
      <source dir='/var/lib/libvirt/images/ptp'/>
      <target dir='ptp'/>
    </filesystem>

    <!-- Local console for the relay's first-boot network setup. -->
    <graphics type='vnc' port='-1' autoport='yes' listen='127.0.0.1'/>
  </devices>
</domain>
```

## If the process-bus interfaces show RX errors or drops

Sampled Values is high-rate L2 multicast (≈4000–4800 frames/s per stream). **Before acting on a
counter, establish whether it is still moving.** A macvtap's `rx_dropped` accumulates several
million frames at every guest boot — the relay discards Sampled Values for roughly the first 40 s,
until its driver starts consuming — and then stops. A large but static count is that boot window,
not ongoing loss, and tuning against it changes nothing. Sample the counter twice under load:

```bash
DEV=$(sudo virsh domiflist ssc600-01 | awk '/direct/{print $1}' | head -1)   # the macvtap device
ip -s link show "$DEV" | awk '/RX:/{getline; print $4}'; sleep 60
ip -s link show "$DEV" | awk '/RX:/{getline; print $4}'
```

Equal numbers mean the drops are the boot window; nothing below applies. If the count is climbing,
diagnose which counter first — it points at the cause — then work down the mitigations:

1. **Use `mode='bridge'`, not `mode='vepa'`.** VEPA forces all guest frames out to the adjacent switch and depends on it doing reflective relay (802.1Qbg hairpin); most switches don't, which drops frames. The XML above already specifies bridge mode.
2. **Keep NIC IRQs and vhost-net threads off the isolated cores.** If either lands on an isolated core you get RX drops and RT jitter from the same cause — verify per step 12.
3. **Raise the NIC ring buffers** on the host: `sudo ethtool -G <nic> rx 4096`. (A ring/queue change re-spreads the NIC's IRQs — re-apply the placement afterwards with `sudo systemctl restart ssc600-startup.service`; see step 09.)
4. **Virtio multiqueue** — `<driver name='vhost' queues='N'/>` on the interface (N up to the vCPU count) — only if the guest supports it: inside the VM, `ethtool -l` on the process-bus interface must show a `Combined` maximum above 1, or the host-side change does nothing. Applying it costs a relay restart, so confirm the guest's capability first. Each extra queue adds a vhost thread that must fit on the emulator cores (step 11).

Diagnose which counter is incrementing first — it points at the cause:

```bash
ip -s -s link show <nic>                       # errors vs dropped vs fifo/overrun
ethtool -S <nic> | grep -iE 'drop|err|fifo|miss|over'
ethtool -g <nic>                               # current vs max ring
```

- `rx_fifo` / `overrun` / `missed` → rate / ring-buffer / IRQ placement.
- `rx_dropped` at low rate → multicast filtering or a VEPA-mode reflection dependency.

## Define the domain

This registers the domain with libvirt; it does not start it:

```bash
sudo virsh define ~/ssc600-01.xml
sudo virsh list --all      # ssc600-01 should appear as "shut off"
```

If `virsh define` rejects the XML, review the error. Common causes are a pin to a nonexistent core, a hugepage size with no matching reservation, or a referenced network or NIC that is not present. Correct the value — which will trace back to an earlier step — and define again.

**Keep the domain's identity from here on.** The XML above has no `<uuid>` and no `<mac address>`; libvirt generates them at this first define. The relay's license (step 11) is requested from the running guest, and ABB's documentation does not list which VM identifiers it binds to. Keep every identifier the guest can see unchanged once the license is requested:
- Make later changes (pinning, scheduling, memory) with `sudo virsh edit ssc600-01`, which keeps the UUID and MAC addresses.
- Do not undefine the domain and define it again from `~/ssc600-01.xml`: a fresh define generates a new UUID and new MAC addresses.

Save the defined XML, with its generated identifiers, as the reference copy:

```bash
sudo virsh dumpxml ssc600-01 > ~/ssc600-01.defined.xml
grep -E '<uuid>|<mac address' ~/ssc600-01.defined.xml
```


## Install ABB's qemu hook

ABB's `qemu.hook` (step 09) runs when the relay VM starts. It acts only on domains whose name contains `ssc600`. It waits for the VM's QEMU process, then moves the VM's vhost-net threads and its `kvm-pit` thread onto the cores of the QEMU process (the `<emulatorpin>` set) at real-time priority 1.

libvirt executes only the single script at `/etc/libvirt/hooks/qemu`, so installing this file replaces
anything already at that path. If the host already carries a hook supplied with another workload, that
hook may set the CPU affinity or scheduling policy its guest depends on. Check before overwriting.

First, detect any hook already at that path and preserve it. Run this on its own before continuing.

```bash
# create hooks directory if it does not exist yet
sudo mkdir -p /etc/libvirt/hooks/

# if a hook is already present, show what it does and keep a timestamped copy
if [ -f /etc/libvirt/hooks/qemu ]; then
    grep -nE 'chrt|taskset|numactl|renice|cgroup|sched' /etc/libvirt/hooks/qemu
    sudo cp -a /etc/libvirt/hooks/qemu \
        "/etc/libvirt/hooks/qemu.bak.$(date +%Y-%m-%d@%H:%M:%S)"
fi
```

If that printed nothing, no hook is present, or the one present does no scheduling or affinity work.
Continue.

If it printed any lines, a hook is already installed and it sets CPU affinity or scheduling policy
for a guest. Merge that behaviour into the installed hook before continuing — libvirt runs only this
one file. There are two exceptions: a hook carrying the `#libvirt qemu hook script for ssc600`
header is ABB's hook from an earlier run of this step, and one carrying `# Managed by ansible-vpac`
was written by the `virtualization` role. Replacing either is expected.

Install ABB's hook with one change, `pidof -w`:

```bash
sudo cp ~/ssc600-stage/virtual_products/qemu.hook /etc/libvirt/hooks/qemu
sudo chmod +x /etc/libvirt/hooks/qemu

# Find the vhost threads with pidof -w:
sudo sed -i 's/pidof vhost-/pidof -w vhost-/' /etc/libvirt/hooks/qemu
grep -n 'pidof' /etc/libvirt/hooks/qemu       # pidof -w vhost-$qemu_pid

# libvirt detects a newly added hook script when its daemon starts:
sudo systemctl restart virtqemud
```

> **Why `-w`.** vhost-net worker threads have no command line, and `pidof` skips such processes
> unless given `-w`. Without it the hook finds no vhost threads, exits with an error after about
> 10 seconds, and libvirt aborts the start: `Hook script execution failed ... unexpected exit
> status 1`, with `no vhost-<pid> processes found` in the hook's output. On a running VM,
> `pidof vhost-<qemu-pid>` prints nothing and `pidof -w vhost-<qemu-pid>` prints the threads.
> This is the only change this guide makes to ABB's files.
>
> `chrt -p 1` with no policy flag sets **SCHED_RR** at priority 1 — `chrt`'s default policy — so the
> vhost and `kvm-pit` threads run RR 1, not FIFO. Step 11's manual fallback and step 12's check
> match that. (The `ansible-vpac` `virtualization` role sets SCHED_FIFO 1 for the same threads; the
> two paths differ in policy, not priority.)

Continue to [11 — Start and license](11-start-and-license.md).
