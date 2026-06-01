# 10 — Define the SSC600 domain

This step defines the libvirt domain XML that applies the host tuning to the relay VM. Each element is documented in the comments.

This is the **single-node** domain: a **local file-backed disk** (no Ceph RBD), **no sanlock lease** (no cluster), and **virtio** disk and NICs (the SSC600 guest kernel includes virtio drivers). The three-node automated path adds RBD, leases, and Pacemaker to this same structure.

## Adjust these to the host before defining

| Placeholder in the XML | Replace with | Must match |
|---|---|---|
| vCPU pins `12`,`13`,`14`,`15` | The VM's isolated cores | Step 06 `isolated_cores` / step 09 `RT_CORES` |
| emulator pin `10-11` | Two isolated housekeeping-adjacent cores | Inside the `isolated_cores` set |
| `memory` `8` | Guest RAM in GiB | A whole number of 1 GiB hugepages reserved in step 06/08 |
| disk `source file` | Where the image was placed (step 09) | The actual path |
| `source network='station-bus'` | The libvirt network name | Step 05 |
| `source dev='ens2f0'` | The process-bus NIC | Step 05 |
| `source dir` (virtiofs) | Host PTP-status dir | Step 09 |

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

    <!-- NIC 1 — station bus. Attached to the libvirt 'station-bus' network
         (the bridge from step 05). Carries MMS, engineering tools, and the
         relay's web HMI. virtio model. -->
    <interface type='network'>
      <source network='station-bus'/>
      <model type='virtio'/>
    </interface>

    <!-- NIC 2 — process bus. macvtap direct passthrough on the reserved
         process-bus NIC, VEPA mode: GOOSE/Sampled-Value frames go directly
         from the wire into the guest at sub-millisecond cadence, bypassing
         the host bridge. This NIC has no host IP (step 05). -->
    <interface type='direct' trustGuestRxFilters='yes'>
      <source dev='ens2f0' mode='vepa'/>
      <model type='virtio'/>
    </interface>

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

    <!-- virtiofs PTP-status share. The host writes its PTP sync state into
         /var/lib/libvirt/ptp-status (step 09); the guest mounts it via the
         'ptp' tag and reads host time status. Requires the
         <access mode='shared'/> set in memoryBacking above. -->
    <filesystem type='mount' accessmode='passthrough'>
      <driver type='virtiofs'/>
      <source dir='/var/lib/libvirt/ptp-status'/>
      <target dir='ptp'/>
    </filesystem>

    <!-- Local console for the relay's first-boot network setup. -->
    <graphics type='vnc' port='-1' autoport='yes' listen='127.0.0.1'/>
  </devices>
</domain>
```

## Define the domain

This registers the domain with libvirt; it does not start it:

```bash
sudo virsh define ~/ssc600-01.xml
sudo virsh list --all      # ssc600-01 should appear as "shut off"
```

If `virsh define` rejects the XML, review the error. Common causes are a pin to a nonexistent core, a hugepage size with no matching reservation, or a referenced network or NIC that is not present. Correct the value — which will trace back to an earlier step — and define again.

Continue to [11 — Start and license](11-start-and-license.md).
