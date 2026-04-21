# RHEL installer image builder

The builder produces a custom RHEL 9.7 installer ISO with cluster packages pre-baked in and a kickstart injected, plus one cloud-init seed ISO per node for unattended first-boot config. It is the first step of the **air-gapped** deployment path — see [`DEPLOYMENT-AIRGAPPED.md`](DEPLOYMENT-AIRGAPPED.md) for the operator walkthrough. On the **connected** path it is not used.

The builder is a separate workflow from `site.yml`. It has its own top-level playbook:

```bash
ansible-playbook -i inventory/<site> build-installer.yml
```

## Where the builder runs

Set `imagebuilder.location` in `group_vars/all.yml`:

### Mode 1: Builder on Node A (before the cluster is provisioned)

Simpler for single-site deployments. The role installs `osbuild-composer` on Node A, composes the ISO, then Node A is reformatted by the same ISO along with the other two nodes.

```yaml
imagebuilder:
  location: "node_a"
```

Pros: no extra hardware.
Cons: the builder is one-shot — if you need to rebuild the ISO later (e.g. to add packages), Node A is already in production and you need to bring up a separate builder.

### Mode 2: Builder on an external host (NUC, laptop, VM)

Appropriate for multi-site deployments where you build one ISO and deploy it to many substations, or for any site where you want the builder persistent.

```yaml
imagebuilder:
  location: "external_host"
```

Add the builder to inventory:

```yaml
imagebuilder_host:
  hosts:
    mysite-builder:
      ansible_host: 10.0.0.50
      ansible_user: admin
```

Pros: reusable, decoupled from the cluster lifecycle.
Cons: one more box to maintain.

## What gets baked into the ISO

The `compose_packages` list in the inventory controls this. The default set covers everything `site.yml` needs so air-gapped nodes never reach out during the cluster bootstrap:

- `ansible-core`, the required collections (as a tarball)
- `kernel-rt`, `tuned-profiles-nfv`, `tuned-profiles-realtime`
- `pacemaker`, `corosync`, `pcs`, `fence-agents-ipmilan`
- `qemu-kvm`, `libvirt`, `virt-install`, `swtpm`, `edk2-ovmf`
- `cloud-init`, `chrony`, `linuxptp`
- `cockpit`, `firewalld`
- `ceph-common`

Extend `compose_packages` for site-specific tools (monitoring agents, custom RPMs, etc.).

## Kickstart

`roles/imagebuilder/templates/ks.cfg.j2` uses `--device=link` for network configuration — it selects the first NIC with a cable. Hardcoding specific NIC names in kickstart is brittle across hardware revisions; the post-install `networking` role handles final interface mapping against `networking_defaults`.

Customize the template for site-specific partitioning, language, keyboard, etc.

## Cloud-init seed ISOs

One per entry in `vpac_nodes`. Sets hostname, admin user, SSH public key, and any per-node first-boot config. Generated as raw ISOs attached to each node as a secondary virtual media alongside the installer ISO.

## Package and image mirror pre-reqs

The builder itself needs connectivity — either directly (build in a connected environment, ship ISO physically to the site) or via your local Satellite/mirror/registry.

During `site.yml`, the nodes pull from the sources configured in `group_vars/all.yml`:

```yaml
sources:
  repo_source: "local_mirror"            # or satellite
  local_mirror_url: "http://builder.example.local/mirror"
  container_registry: "registry.example.local:5000"
  ansible_collections_source: "local_tarball"
  ansible_collections_local_path: "/opt/ansible-collections.tar.gz"
```

The `DEPLOYMENT-AIRGAPPED.md` guide covers the mirror/registry setup details.
