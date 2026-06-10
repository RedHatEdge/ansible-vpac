#!/usr/bin/env bash
#
# Build the single-node vPAC bootc image and, optionally, installable media.
#
# Usage:
#   ./build.sh connected   [IMAGE_REF]   # pull base + push to quay.io / a registry
#   ./build.sh airgapped   [IMAGE_REF]   # pull base + push to a local builder registry
#
# IMAGE_REF defaults to localhost/vpac-node:9.7. For connected/air-gapped use,
# pass the fully-qualified destination (e.g. quay.io/yourorg/vpac-node:9.7 or
# registry.example.internal/vpac-node:9.7).
#
# Prerequisites:
#   - podman, and (for media) the bootc-image-builder image
#   - registry.redhat.io login with a terms-based registry service account
#   - for air-gapped: the base image mirrored into the local registry first

set -euo pipefail

MODE="${1:-}"
IMAGE_REF="${2:-localhost/vpac-node:9.7}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

BUILD_ARGS=()
case "$MODE" in
  connected)
    # dnf uses the host's subscription via the entitlement mount. Build rootful.
    ;;
  airgapped)
    # Point the build at a local reposync mirror. Set MIRROR_URL in the
    # environment, e.g. MIRROR_URL=http://mirror.example/mirror ./build.sh ...
    if [ -z "${MIRROR_URL:-}" ]; then
      echo "airgapped mode needs MIRROR_URL set (e.g. http://mirror.example/mirror)" >&2
      exit 2
    fi
    BUILD_ARGS+=(--build-arg "REPO_BASEURL=${MIRROR_URL}")
    ;;
  *) echo "usage: $0 {connected|airgapped} [IMAGE_REF]" >&2; exit 2 ;;
esac

echo ">> Building bootc image: ${IMAGE_REF}  (mode: ${MODE})"
podman build "${BUILD_ARGS[@]}" -t "${IMAGE_REF}" -f "${HERE}/Containerfile" "${HERE}"

echo ">> Pushing ${IMAGE_REF}"
podman push "${IMAGE_REF}"

cat <<EOF

>> Image built and pushed: ${IMAGE_REF}

To produce installable media (ISO / qcow2 / raw) with bootc-image-builder:

  sudo podman run --rm -it --privileged --pull=newer \\
    --security-opt label=type:unconfined_t \\
    -v ${HERE}/bib/config.toml:/config.toml:ro \\
    -v ./output:/output \\
    -v /var/lib/containers/storage:/var/lib/containers/storage \\
    registry.redhat.io/rhel9/bootc-image-builder:latest \\
    --type iso \\
    --config /config.toml \\
    ${IMAGE_REF}

Then boot a node from output/, and apply runtime config (see runtime/README.md):
networking identity, hostname, per-host isolated-core kargs, and the relay VM.
EOF
