# 09 — Prepare the SSC600SW bundle

This step unpacks the ABB bundle into a runnable disk image, installs the host-side helper scripts the relay requires, and creates the PTP-status share. After it, the disk image is on local storage and the host is ready to define the domain.
You can obtain the bundle from the ABB Library.

## Extract the disk image

The bundle is a Microsoft `.cab` containing a gzip-compressed raw disk image. Extract, then decompress:

```bash
# Work in a staging directory
mkdir -p ~/ssc600-stage && cd ~/ssc600-stage

# Unpack the cab (yields, among other files, ssc600_disk.img.gz).
# bsdtar (the bsdtar package from step 04) reads the Microsoft cab format via
# libarchive; the standalone cabextract tool is not in the RHEL 9 repositories.
bsdtar -xf /path/to/SSC600_SW_KVM-<version>.cab

# Decompress the raw disk image
gunzip ssc600_disk.img.gz
ls -lh ssc600_disk.img       # ~30 GiB raw image
```

The image is a raw KVM disk. No `qemu-img` conversion or OVA import is required; ABB delivers it KVM-ready.

## Place the disk image on local storage

Move it to the location libvirt will run it from. On single-node this is a local path; the reference uses the standard libvirt images directory:

```bash
sudo mv ssc600_disk.img /var/lib/libvirt/images/ssc600-01.img
sudo chown qemu:qemu /var/lib/libvirt/images/ssc600-01.img
sudo restorecon -v /var/lib/libvirt/images/ssc600-01.img   # correct SELinux label
```

> Retain a pristine copy of the extracted image before booting and licensing the VM. Licensing and all in-VM configuration are written to this disk; restoring it to the factory image requires repeating license activation (step 11). A clean copy reduces recovery to a file copy rather than a re-extraction.

## The PTP-status share

The SSC600 reads the host's PTP sync state from a directory shared into the guest by virtiofs, from a file named **`ptp_status`** in a format the relay parses. **ABB ships the status writer with the bundle** — a `ptp_status.sh` script and a `ptp_status.service` unit in the extracted `virtual_products/` directory. Install the shipped files; do not substitute a custom writer (an earlier revision of this guide did, and the relay could not read it — wrong file name and untested format).

```bash
# The vendor scripts/units are in the extracted bundle (location within the
# bundle can vary by version — locate them if needed):
cd ~/ssc600-stage
find . -name 'ptp_status*'

# Install per the SSC600 engineering manual:
cd virtual_products/
chmod +x *.sh
sudo cp *.sh /usr/sbin/
sudo cp *.service /etc/systemd/system/
sudo systemctl daemon-reload

# The service writes into /var/lib/libvirt/images/ptp/ by default; the
# PTP_STATUS_FOLDER environment setting in the unit overrides it. Create the
# default location and start the service:
sudo mkdir -p /var/lib/libvirt/images/ptp
sudo systemctl enable --now ptp_status.service
systemctl status ptp_status.service --no-pager

# Confirm the status file appears and refreshes:
cat /var/lib/libvirt/images/ptp/ptp_status
```

> The shipped script reads ptp4l's state over its UNIX socket. If SELinux denials appear for that access (`ausearch -m AVC -ts recent`), build a local policy module from the actual denials (`audit2allow -M`) — do **not** switch SELinux to permissive or disabled on a protection host.

The virtiofs `<source dir>` in the domain XML (step 10) must point at the same directory this service writes to. This guide uses the vendor default, `/var/lib/libvirt/images/ptp`.

## The host real-time setup script (cache + IRQ affinity)

The SSC600 reference applies an L3 cache partition and IRQ affinity for the relay's cores at boot, using `pqos`. This is optional but recommended on Intel CAT-capable hardware; it prevents other host workloads from evicting the relay's cache lines.

```bash
sudo tee /usr/local/sbin/vpac-ssc600-setup.sh >/dev/null <<'EOF'
#!/usr/bin/env bash
# L3 cache partitioning + IRQ steering for the SSC600 relay cores.
# Requires /sys/fs/resctrl mounted (see step 08) and intel-cmt-cat installed.
set -euo pipefail

# The relay's protection cores — the subset of the VM's vCPU pins that gets
# the RT cache class. SSC600 vCPU 0 (host core 12 here) runs the relay's
# OS/WebHMI and is excluded; vCPUs 1-3 (host cores 13-15) run protection.
# Keep consistent with the <vcpupin> cores in the domain XML (step 10).
RT_CORES="13-15"

# Reset, then carve L3: give housekeeping cores one cache mask and the RT
# cores an exclusive mask. The masks are CPU-specific (the number of cache
# ways varies); these are illustrative — size them to your CPU's L3 ways.
pqos -R || true
pqos -e "llc:0=0x1ff;llc:1=0xe00"     # class 0 (non-RT) vs class 1 (RT)
pqos -a "llc:1=${RT_CORES}"           # assign RT cores to the RT cache class

NICS="ens2f0 ens2f1" # CHANGE FOR YOUR PTP & Process Bus NICs in use (blank space separated list)
# CPUMASK targets the core that services these NICs' IRQs. 0x200 = core 9,
# the top HOUSEKEEPING core in this example (isolated set is 10-15: emulator
# pin on 10-11, vCPUs on 12-15). Adjust to your topology.
CPUMASK="200"

# Process bus / networking
echo "Configuring network card interrupts and threads"
for nic in $NICS
do
	echo "Disabling NIC power management"
	ethtool --set-eee $nic eee off || echo "EEE off failed or not supported on $nic"
	ethtool --change $nic wol d || echo "WoL disable failed or not supported on $nic"
	echo on > /sys/class/net/$nic/power/control 2>/dev/null \
	  || echo "runtime PM control not available on $nic"
	IRQS=$(grep $nic /proc/interrupts | cut -d':' -f1 || true)
	for irq in $IRQS
	do
	echo $CPUMASK | tee /proc/irq/$irq/smp_affinity
	tasks=$(ps axo pid,command | grep -e "irq/$irq-" | grep -v grep | awk '{print $1}')
	for pid in $tasks
	do
	  taskset -p "0x$CPUMASK" $pid
	done
	done
done
EOF
sudo chmod +x /usr/local/sbin/vpac-ssc600-setup.sh

sudo tee /etc/systemd/system/vpac-ssc600-setup.service >/dev/null <<'EOF'
[Unit]
Description=SSC600 L3 cache partition + IRQ affinity
After=sys-fs-resctrl.mount network-online.target
Wants=network-online.target
Requires=sys-fs-resctrl.mount

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/vpac-ssc600-setup.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now vpac-ssc600-setup.service
sudo pqos -s         # show current allocation; confirm the RT class exists
```

> The cache way-masks (`0x1ff`, `0xe00`) depend on the number of L3 ways the CPU exposes. Run `pqos -d` and size the masks so the RT class receives an exclusive, non-overlapping slice. The values shown are examples, not universal settings. Refer to the [Troubleshooting Section](TROUBLESHOOTING.md) for more details on how to calculate the values for your specific hardware setup.

With the image in place and the host helpers running, define the VM. Continue to [10 — Define the SSC600 domain](10-define-ssc600-domain.md).
