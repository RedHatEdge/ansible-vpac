# builder_registry

Third role in `01-build-builder.yml`. Runs a local container registry on the builder and mirrors Red Hat container images (RHCS, and whatever else the deployment needs) into it via `skopeo copy`.

After this role finishes, the builder is fully ready to serve an air-gapped cluster: RPMs over HTTP from the mirror, container images over HTTP from the local registry.

## What it does

1. Installs `podman` + `skopeo`.
2. Opens `{{ builder_registry_port }}/tcp` (default `5000`) in firewalld.
3. Creates `{{ builder_registry_data_dir }}` for persistent registry blobs.
4. Starts a `registry:2` container (default image `docker.io/library/registry:2`) as a managed podman container with a persistent volume and `restart_policy: always`.
5. Waits for the registry to accept connections.
6. For each entry in `builder_registry_images`, runs `skopeo copy` from `{{ redhat_registry_url }}/{path}` to `localhost:{{ builder_registry_port }}/{path}`, authenticating the pull with `redhat_registry_username` / `redhat_registry_password`.
7. Inspects each image in the local registry to confirm it's mirrored.
8. Prints the registry URL to paste into the cluster inventory's `sources.container_registry`.

## Variables

| Name | Default | Notes |
|---|---|---|
| `builder_registry_port` | `5000` | TCP port the registry listens on |
| `builder_registry_data_dir` | `/var/lib/vpac-registry` | persistent blob storage |
| `builder_registry_container_name` | `vpac-registry` | podman container + systemd unit name |
| `builder_registry_image` | `docker.io/library/registry:2` | the registry image itself |
| `redhat_registry_url` | `registry.redhat.io` | source registry for the mirror copy |
| `redhat_registry_username` | *(required, vault)* | service account token user |
| `redhat_registry_password` | *(required, vault)* | service account token value |
| `builder_registry_images` | `["rhceph/rhceph-7-rhel9:latest"]` | images to mirror (extend as needed) |

## Getting a Red Hat registry service account token

Go to <https://access.redhat.com/terms-based-registry/>, click **New Service Account**, pick a name. Red Hat generates a username shaped like `<org-id>|<token-name>` plus a long random password. Paste those into `group_vars/vault.yml`:

```yaml
redhat_registry_username: "12345678|my-sa-token"
redhat_registry_password: "<long-random-password>"
```

This is the *container registry* service account system, not the IAM/API service accounts at `console.redhat.com/iam/service-accounts` — those tokens won't authenticate to `registry.redhat.io`.

## Dependencies

- `containers.podman`, `ansible.posix` collections
- `builder_rhsm` + `builder_mirror` typically run first; not technically required for the registry itself, but the full `01-build-builder.yml` imports all three in order.

## Limitations

- The registry runs unauthenticated over plain HTTP on the builder. Cluster nodes get an insecure trust drop-in (`/etc/containers/registries.conf.d/99-vpac-local.conf`) from `ceph_bootstrap` and `ceph_expand` when `sources.container_registry_insecure: true`. This is appropriate for a site-local registry behind a physical airgap; it is NOT suitable for a registry exposed on any untrusted network.
- No garbage collection is scheduled. If the builder churns through many image updates, the operator should periodically run `podman exec vpac-registry bin/registry garbage-collect /etc/docker/registry/config.yml`.
