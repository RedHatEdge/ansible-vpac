#!/bin/bash
#
# Tooling container entrypoint. Wraps `mkksiso` with argument validation +
# friendly errors so the Ansible role (and human operators) can invoke
# with env vars instead of argparse.
#
# Expected env:
#   INPUT_ISO   — path (in-container) to the source RHEL 9 DVD ISO
#   KICKSTART   — path (in-container) to the kickstart file to inject
#   OUTPUT_ISO  — path (in-container) where the minted ISO should land
#
# Typical invocation from the role:
#   podman run --rm \
#     -v /path/to/rhel-9.7.iso:/in/rhel.iso:ro,Z \
#     -v /path/to/ks.cfg:/in/ks.cfg:ro,Z \
#     -v /path/to/output-dir:/out:rw,Z \
#     -e INPUT_ISO=/in/rhel.iso \
#     -e KICKSTART=/in/ks.cfg \
#     -e OUTPUT_ISO=/out/vpac-builder-installer.iso \
#     localhost/vpac-iso-builder:latest

set -euo pipefail

die() { echo "mint-iso: $*" >&2; exit 1; }

: "${INPUT_ISO:?INPUT_ISO must be set (path in-container to source DVD ISO)}"
: "${KICKSTART:?KICKSTART must be set (path in-container to kickstart file)}"
: "${OUTPUT_ISO:?OUTPUT_ISO must be set (path in-container for minted ISO)}"

test -f "$INPUT_ISO"  || die "INPUT_ISO does not exist: $INPUT_ISO"
test -f "$KICKSTART"  || die "KICKSTART does not exist: $KICKSTART"
test -d "$(dirname "$OUTPUT_ISO")" || die "OUTPUT_ISO parent dir missing: $(dirname "$OUTPUT_ISO")"

echo "mint-iso: source   = $INPUT_ISO ($(stat -c '%s' "$INPUT_ISO") bytes)"
echo "mint-iso: kickstart = $KICKSTART"
echo "mint-iso: output   = $OUTPUT_ISO"

# Remove any previous run's output so mkksiso doesn't refuse.
rm -f "$OUTPUT_ISO"

# mkksiso does the heavy lifting: extracts the source ISO, drops the
# kickstart in at /ks.cfg, edits isolinux.cfg + EFI/BOOT/grub.cfg to add
# `inst.ks=cdrom:/ks.cfg` to the default boot entry, and rebuilds a
# hybrid BIOS+UEFI bootable ISO. No manual xorriso gymnastics needed.
mkksiso --ks "$KICKSTART" "$INPUT_ISO" "$OUTPUT_ISO"

echo "mint-iso: wrote $(ls -lh "$OUTPUT_ISO" | awk '{print $5}') → $OUTPUT_ISO"
