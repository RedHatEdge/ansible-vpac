#!/bin/bash
# collect-node-diag.sh — read-only diagnostic collection for ONE vPAC cluster node.
# Purpose: full node-state snapshot (RT tuning, networking, VMs, cluster, time)
#          plus a support-grade sos report. Run on EVERY node, as root or sudo.
# Output:  /var/tmp/vpac-diag-<host>-<date>.tar.xz  (+ the sos tarball path it prints)
# Safe:    read-only — no service changes, no config writes outside /var/tmp.
# Send to: the requesting engineer, via your usual file-transfer path.

set -u
TS=$(date +%Y%m%d-%H%M)
HOST=$(hostname -s)
OUT=/var/tmp/vpac-diag-${HOST}-${TS}
mkdir -p "$OUT"
log() { echo "== $*" ; }
run() { local f=$1; shift; log "$*"; { echo "### $*"; "$@" 2>&1; } > "$OUT/$f"; }

# --- identity / OS ---
run 00-hostnamectl.txt        hostnamectl
run 01-os-release.txt         cat /etc/os-release
run 02-uname.txt              uname -a
run 03-cmdline.txt            cat /proc/cmdline
run 04-uptime.txt             uptime

# --- real-time tuning state ---
run 10-tuned-active.txt       tuned-adm active
{ echo "### /etc/tuned recursive"; find /etc/tuned -type f -exec sh -c 'echo "--- $1"; cat "$1"' _ {} \; ; } > "$OUT/11-tuned-config.txt" 2>&1
run 12-isolated-cpus.txt      cat /sys/devices/system/cpu/isolated
run 13-rt-sysctls.txt         sysctl kernel.sched_rt_runtime_us kernel.nmi_watchdog
run 14-meminfo-huge.txt       grep -i huge /proc/meminfo
run 15-interrupts.txt         cat /proc/interrupts
{ echo "### effective affinity per IRQ"; for d in /proc/irq/[0-9]*; do printf '%s: ' "$d"; cat "$d/effective_affinity_list" 2>/dev/null || echo '-'; done; } > "$OUT/16-irq-affinity.txt" 2>&1
run 17-services-jitter.txt    systemctl status irqbalance ksm ksmtuned tuned-ppd --no-pager
run 18-cpufreq.txt            cpupower frequency-info

# --- networking ---
run 20-ip-addr.txt            ip -d addr
run 21-ip-link.txt            ip -d link
run 22-nmcli.txt              nmcli connection show
run 23-bridges.txt            bridge link show
{ echo "### per-NIC driver + timestamping"; for n in /sys/class/net/*; do i=$(basename "$n"); [ -d "$n/device" ] || continue; echo "--- $i"; ethtool -i "$i" 2>&1; ethtool -T "$i" 2>&1; done; } > "$OUT/24-ethtool.txt" 2>&1
run 25-ip-maddr.txt           ip maddr show

# --- time sync ---
run 30-chronyc-tracking.txt   chronyc tracking
run 31-chronyc-sources.txt    chronyc sources -v
run 32-timemaster.txt         systemctl status timemaster ptp4l phc2sys --no-pager
{ echo "### pmc port/parent data"; pmc -u -b 0 'GET PORT_DATA_SET' 2>&1; pmc -u -b 0 'GET PARENT_DATA_SET' 2>&1; pmc -u -b 0 'GET CURRENT_DATA_SET' 2>&1; } > "$OUT/33-pmc.txt" 2>&1

# --- virtualization ---
run 40-virsh-list.txt         virsh list --all
{ echo "### domain XML dumps (live)"; for d in $(virsh list --name --all 2>/dev/null); do echo "--- $d"; virsh dumpxml "$d" 2>&1; echo "--- $d vcpuinfo"; virsh vcpuinfo "$d" 2>&1; echo "--- $d emulatorpin"; virsh emulatorpin "$d" 2>&1; done; } > "$OUT/41-domains.txt" 2>&1
run 42-virsh-nets.txt         virsh net-list --all
run 43-libvirt-hooks.txt      sh -c 'ls -la /etc/libvirt/hooks/ 2>/dev/null; cat /etc/libvirt/hooks/qemu 2>/dev/null'

# --- cluster ---
run 50-pcs-status.txt         pcs status --full
run 51-pcs-config.txt         pcs config show --full
run 52-corosync-cfg.txt       corosync-cfgtool -s
run 53-corosync-conf.txt      cat /etc/corosync/corosync.conf
run 54-stonith.txt            pcs stonith status

# --- containers / packages ---
run 60-podman-ps.txt          podman ps -a
run 61-podman-images.txt      podman images
run 62-rpm-list.txt           sh -c 'rpm -qa | sort'
run 63-ceph-local.txt         sh -c 'rpm -qa | grep -iE "ceph|cephadm" ; cephadm version 2>&1'

# --- health snapshot ---
run 70-systemctl-failed.txt   systemctl --failed
run 71-journal-errs.txt       journalctl -p err --since "-7 days" --no-pager

# --- package it ---
tar -C /var/tmp -cJf "${OUT}.tar.xz" "$(basename "$OUT")" && rm -rf "$OUT"
log "Node snapshot: ${OUT}.tar.xz"

# --- sos report (support-grade, big — several hundred MB possible) ---
log "Starting sos report (this takes a while)..."
sos report --batch --all-logs --tmp-dir /var/tmp 2>&1 | tail -5
log "DONE. Send BOTH: ${OUT}.tar.xz AND the sosreport tarball printed above."
