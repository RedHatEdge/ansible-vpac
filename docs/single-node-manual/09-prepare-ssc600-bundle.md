# 09 — Prepare the SSC600SW bundle

This step unpacks the ABB bundle into a runnable disk image, installs the host-side files ABB ships with it, and configures them for this host. After it, the disk image is on local storage and the host is ready to define the domain.
You can obtain the bundle from the ABB Library.

## Extract the bundle

The bundle is a Microsoft `.cab`. It unpacks into a `virtual_products/` directory that holds a gzip-compressed raw disk image, ABB's host scripts and systemd units, a libvirt hook, a reference domain XML, and a checksum list:

```bash
# Work in a staging directory
mkdir -p ~/ssc600-stage && cd ~/ssc600-stage

# Unpack the cab. bsdtar (the bsdtar package from step 04) reads the Microsoft
# cab format via libarchive; the standalone cabextract tool is not in the RHEL
# repositories.
bsdtar -xf /path/to/SSC600_SW_KVM-<version>.cab

cd virtual_products               # if absent: find . -name checksums.sha256
sha256sum -c checksums.sha256     # every line: OK

# Decompress the raw disk image
gunzip ssc600_disk.img.gz
ls -lh ssc600_disk.img            # ~30 GiB raw image
```

The image is a raw KVM disk. No `qemu-img` conversion or OVA import is required; ABB delivers it KVM-ready.

## Place the disk image on local storage

Move it to the location libvirt will run it from. On single-node this is a local path; the reference uses the standard libvirt images directory:

```bash
sudo mv ssc600_disk.img /var/lib/libvirt/images/ssc600-01.img
sudo chown qemu:qemu /var/lib/libvirt/images/ssc600-01.img
selinuxenabled && sudo restorecon -v /var/lib/libvirt/images/ssc600-01.img   # SELinux label; skipped while SELinux is disabled
```

> Retain a pristine copy of the extracted image before booting and licensing the VM. Licensing and all in-VM configuration are written to this disk; restoring it to the factory image requires repeating license activation (step 11). A clean copy reduces recovery to a file copy rather than a re-extraction.

## Install ABB's host files

| File in `virtual_products/` | Installed to | What it does |
|---|---|---|
| `ptp_status.sh`, `ptp_status.service` | `/usr/sbin/`, `/etc/systemd/system/` | Writes the host's PTP state to a file the relay reads (below) |
| `ssc600-setup.sh`, `ssc600-startup.service` | `/usr/sbin/`, `/etc/systemd/system/` | Real-time host setup at boot: helper threads, L3 cache partition, process-bus NIC IRQs (below) |
| `qemu.hook` | `/etc/libvirt/hooks/qemu` | Places the relay's vhost-net and `kvm-pit` threads when the VM starts (step 10) |
| `ssc600-kvm.xml` | — | ABB's reference domain; step 10 defines this guide's version |

Install the scripts and units:

```bash
cd ~/ssc600-stage/virtual_products
chmod +x *.sh
sudo cp *.sh /usr/sbin/
sudo cp *.service /etc/systemd/system/
sudo systemctl daemon-reload
```

Keep ABB's files as shipped. Host-specific values go in systemd drop-ins (`systemctl edit`), which the units' own comments call for. The installed files then still match `checksums.sha256`, and a later bundle version is installed by copying its files again.

## The PTP-status share

The SSC600 reads the host's PTP sync state from a file named **`ptp_status`**, in a directory shared into the guest by virtiofs. ABB's `ptp_status.service` writes it once a second. The directory is the unit's `PTP_STATUS_FOLDER` setting, and the service creates it if it is missing:

```bash
grep -E 'PTP_STATUS_FOLDER|PTP4L_SOCKET_PATH' /etc/systemd/system/ptp_status.service
# SSC600 SW 1.5.1: PTP_STATUS_FOLDER=/var/lib/libvirt/images/ptp/
#                  PTP4L_SOCKET_PATH=/var/run/timemaster/ptp4l.<num>.socket
```

`PTP4L_SOCKET_PATH` points at timemaster's ptp4l socket, which matches Option A in step 07. On the Option B path (the `ptp4l` service), set the default ptp4l socket in a drop-in:

```bash
# Option B only:
sudo systemctl edit ptp_status.service
#   [Service]
#   Environment=PTP4L_SOCKET_PATH=/var/run/ptp4l
```

Start the service and confirm the file appears and refreshes:

```bash
sudo systemctl enable --now ptp_status.service
systemctl status ptp_status.service --no-pager
cat /var/lib/libvirt/images/ptp/ptp_status      # <PTP_STATUS_FOLDER>/ptp_status
```

> The shipped script reads ptp4l's state over its UNIX socket. With SELinux disabled (step 04) no policy work is required. If the host is returned to Enforcing and denials appear for that access (`ausearch -m AVC -ts recent`), build a local policy module from the actual denials (`audit2allow -M`).

The virtiofs `<source dir>` in the domain XML (step 10) must be the same directory as `PTP_STATUS_FOLDER`. The value can differ between bundle versions; the unit file is authoritative.

## Configure ABB's host setup service

> **Every core number and mask below comes from the layout you worked out in
> [step 06](06-virtualization.md#plan-your-core-layout).** The values shown are for the
> guide's 16-core example. Substitute yours — a different CPU produces different numbers.

`ssc600-startup.service` runs `ssc600-setup.sh` once at boot. The script:

- moves the kernel's helper threads and workqueues to the housekeeping cores (`NON_RT_CORES`);
- turns off transparent hugepages, KSM and timer migration, and sets the `performance` governor;
- partitions the L3 cache with `pqos`: the relay's cores (`RT_CORES`) get their own ways (`RT_CACHE`), everything else gets the rest (`NON_RT_CACHE`);
- pins the interrupts of each process-bus NIC (`NICS`) and their IRQ threads to the process-bus IRQ core(s) (`CPUMASK`).

The values in ABB's unit are for ABB's reference host. Set this host's values in a drop-in:

| Variable | Meaning | Format | 16-core example | From |
|---|---|---|---|---|
| `NON_RT_CORES` | Housekeeping cores | Hex CPU mask, no `0x` | `3ff` (cores 0–9) | Step 06 |
| `RT_CORES` | Cores in the relay's cache class | Core list | `13-15` | Step 06 |
| `RT_CACHE` | The relay's L3 ways | Hex way mask | `0x700` (22 MiB / 11-way) | Step 06 cache table |
| `NON_RT_CACHE` | L3 ways for everything else | Hex way mask | `0x0ff` (22 MiB / 11-way) | The ways `RT_CACHE` leaves |
| `CPUMASK` | Process-bus IRQ core(s) | Hex CPU mask, no `0x` | `200` (core 9) | Step 06 |
| `NICS` | Process-bus interfaces | Space-separated names | `ens2f0` | Step 05 |

Both cache masks must fit inside `/sys/fs/resctrl/info/L3/cbm_mask` (`7ff` on an 11-way L3; on a 12-way L3, `fff`, for example `0x3ff` and `0xc00`). The script applies one `CPUMASK` to every interface in `NICS`; with two process-bus NICs (PRP), give it both cores from step 06 (for example `300` for cores 8 and 9).

```bash
sudo systemctl edit ssc600-startup.service
```

Enter this between the comment lines the editor shows, with your values, and save:

```ini
[Unit]
# pqos needs the resctrl mount (step 08).
After=sys-fs-resctrl.mount
Requires=sys-fs-resctrl.mount

[Service]
Environment=NON_RT_CORES=3ff
Environment=RT_CORES=13-15
Environment=RT_CACHE=0x700
Environment=NON_RT_CACHE=0x0ff
Environment=CPUMASK=200
Environment="NICS=ens2f0"
```

Enable it, run it now, and read what it did:

```bash
sudo systemctl enable --now ssc600-startup.service
systemctl status ssc600-startup.service --no-pager     # active (exited)
journalctl -u ssc600-startup.service -b --no-pager | tail -40
sudo pqos -s                                            # RT_CORES in their own class with RT_CACHE
# The script selects a NIC's IRQs by its name in /proc/interrupts; check the same set:
for irq in $(grep -E 'ens2f0' /proc/interrupts | cut -d: -f1); do   # your NICS, e.g. 'ens2f0|ens2f1'
  echo "irq$irq -> $(cat /proc/irq/$irq/effective_affinity_list)"
done                                                    # every line: a CPUMASK core
```

The script logs every command to the journal. Reading that log:

- **Early lines can report a missing file. These are harmless; the script carries on past them.** Two examples: `transparent_hugepage` does not exist on the real-time kernel, and `intel_pstate/no_turbo` does not exist when the tuned profile disables `intel_pstate` (step 08).
- **From the cache partition on, the first error stops the script.** A cache mask that does not fit the CPU, or a CPU without L3 cache allocation (step 08 checks it), therefore leaves the process-bus IRQs unplaced and the unit `failed`. Fix the value in the drop-in and run `sudo systemctl restart ssc600-startup.service`.
- **Turbo.** ABB's script turns turbo off through `intel_pstate`. With `intel_pstate` disabled by the tuned profile, that has no effect, so turbo stays a BIOS setting (step 01).
- **KSM.** The script writes `0` to `/sys/kernel/mm/ksm/run`, where the tuned profile writes `2`. Both values mean KSM is stopped.
- **Re-applying the placement.** The script runs once per boot. A later queue or ring change on a process-bus NIC (`ethtool -L`/`-G`, a link reset) recreates its interrupts and can spread them across all CPUs. Re-apply the placement afterwards with `sudo systemctl restart ssc600-startup.service`.

With the image in place and the host services running, define the VM. Continue to [10 — Define the SSC600 domain](10-define-ssc600-domain.md).
