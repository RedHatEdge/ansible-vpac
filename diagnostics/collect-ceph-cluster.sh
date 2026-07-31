#!/bin/bash
# collect-ceph-cluster.sh — read-only Ceph cluster-level diagnostic collection.
# Purpose: authoritative cluster state incl. the EXACT running versions
#          (`ceph versions` answers the product-vs-upstream question directly).
# Run:     ONCE, on the admin/bootstrap node, as root or sudo.
# Output:  /var/tmp/vpac-ceph-<date>.tar.xz
# Safe:    read-only — queries only.

set -u
TS=$(date +%Y%m%d-%H%M)
OUT=/var/tmp/vpac-ceph-${TS}
mkdir -p "$OUT"

# Use plain `ceph` if the client is installed; fall back to cephadm shell.
if command -v ceph >/dev/null 2>&1; then C="ceph"; R="rbd"; else C="cephadm shell -- ceph"; R="cephadm shell -- rbd"; fi
run() { local f=$1; shift; echo "== $*"; { echo "### $*"; eval "$@" 2>&1; } > "$OUT/$f"; }

run 00-status.txt         "$C -s"
run 01-health.txt         "$C health detail"
run 02-VERSIONS.txt       "$C versions"          # <-- the version-audit answer
run 03-orch-ls.txt        "$C orch ls"
run 04-orch-ps.txt        "$C orch ps"
run 05-orch-hosts.txt     "$C orch host ls"
run 06-config-dump.txt    "$C config dump"
run 07-report.json        "$C report"
run 08-osd-tree.txt       "$C osd tree"
run 09-osd-dump.txt       "$C osd dump"
run 10-osd-df.txt         "$C osd df tree"
run 11-df.txt             "$C df detail"
run 12-pools.txt          "$C osd pool ls detail"
run 13-fs-status.txt      "$C fs status"
run 14-mon-dump.txt       "$C mon dump"
run 15-crash-ls.txt       "$C crash ls"
run 16-device-ls.txt      "$C device ls"
{ echo "### rbd images per pool"; for p in $($C osd pool ls 2>/dev/null); do echo "--- pool: $p"; eval "$R ls -l $p" 2>&1; done; } > "$OUT/17-rbd-images.txt" 2>&1

tar -C /var/tmp -cJf "${OUT}.tar.xz" "$(basename "$OUT")" && rm -rf "$OUT"
echo "== DONE: ${OUT}.tar.xz"
