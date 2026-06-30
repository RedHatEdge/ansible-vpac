#!/bin/bash
# Managed by ansible-vpac / rt_tuning role — do not edit by hand.
# Regenerate: ansible-playbook site.yml --tags rt-irq
#
# Pin the device IRQs of the listed interfaces onto the housekeeping
# (non-isolated) CPU set.
#
# Why this exists: isolate_managed_irq=Y only constrains kernel-managed IRQ
# placement at device PROBE time. A runtime queue/ring change — tuned's
# netdev_queue_count, `ethtool -L/-G`, or an interface down/up — tears the
# queues down and recreates them, re-spreading the managed IRQs across ALL
# online CPUs. That can drop a busy NIC's RX queue (and its softirq load)
# onto an isolated RT core, which is exactly what must never happen on a
# process-bus NIC carrying GOOSE/Sampled-Value multicast (observed on a
# field host: a process-bus RX queue serving an isolated CPU under SV load).
# This service re-pins after the network and the tuned profile have settled.

set -u

CONF=/etc/vpac/rt-irq-interfaces
[[ -r $CONF ]] || { echo "no $CONF — nothing to pin"; exit 0; }

isol=$(cat /sys/devices/system/cpu/isolated 2>/dev/null)
[[ -n $isol ]] || { echo "no isolated CPUs — nothing to do"; exit 0; }

# housekeeping = all present CPUs minus the isolated set
declare -A ISO
for seg in ${isol//,/ }; do
  if [[ $seg == *-* ]]; then
    for c in $(seq "${seg%-*}" "${seg#*-}"); do ISO[$c]=1; done
  else
    ISO[$seg]=1
  fi
done
hk=""
for c in $(seq 0 "$(($(nproc --all) - 1))"); do
  [[ ${ISO[$c]:-} == 1 ]] || hk+="${hk:+,}$c"
done
[[ -n $hk ]] || { echo "no housekeeping CPUs — aborting"; exit 0; }

rc=0
while read -r nic; do
  nic="${nic%%#*}"; nic="${nic//[[:space:]]/}"
  [[ -n $nic ]] || continue
  if [[ ! -e /sys/class/net/$nic ]]; then echo "skip $nic (absent)"; continue; fi
  irqs=$(ls "/sys/class/net/$nic/device/msi_irqs" 2>/dev/null)
  [[ -n $irqs ]] || { echo "skip $nic (no msi_irqs)"; continue; }
  for irq in $irqs; do
    if echo "$hk" > "/proc/irq/$irq/smp_affinity_list" 2>/dev/null; then
      echo "pinned $nic irq $irq -> $hk"
    else
      echo "WARN $nic irq $irq: could not set affinity (fully managed?)"; rc=1
    fi
  done
done < "$CONF"
exit $rc
