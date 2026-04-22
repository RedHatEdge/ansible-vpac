# Deployment guide — pick your path

This page is a one-minute picker. Once you know which path you're on, follow its detailed walkthrough.

## Which path?

| Situation | Path | Walkthrough |
|---|---|---|
| Cluster nodes have outbound internet (lab, greenfield, internet-connected DC) | **Connected** | [`DEPLOYMENT-CONNECTED.md`](DEPLOYMENT-CONNECTED.md) |
| Cluster nodes have no outbound internet (utility POC, substation, isolated DC) | **Air-gapped** | [`DEPLOYMENT-AIRGAPPED.md`](DEPLOYMENT-AIRGAPPED.md) |

The choice is controlled by a single inventory variable: `deployment_mode: connected | airgapped`.

## What differs between the paths

| Concern | Connected | Air-gapped |
|---|---|---|
| RHEL install | Operator handles (any method) | `00-mint-builder-iso.yml` + `00b-mint-cluster-isos.yml` produce unattended installer ISOs |
| RPM source | RHSM CDN or Satellite | Local mirror on the builder host (populated by `01-build-builder.yml`) |
| Container images (RHCS + monitoring) | `registry.redhat.io` | Local registry on the builder (populated by `01-build-builder.yml` via `skopeo copy`) |
| Ansible collections | `ansible-galaxy` from internet | Local tarball on the builder |
| Needs a builder host? | No | Yes — typically a NUC, laptop, or VM with ~50 GB disk and one-time internet access |
| Number of playbook runs end-to-end | 1 (`site.yml`, optionally tagged) | 4 (`00-mint-builder-iso.yml` → `01-build-builder.yml` → `00b-mint-cluster-isos.yml` → `site.yml`) |

Both paths share the same inventory shape, the same cluster roles, and the same `site.yml`. The only thing that changes downstream of `deployment_mode` is where the package/image payloads come from.

## Common shape of both walkthroughs

Both guides follow the same basic structure so you can skim them side-by-side:

1. Clone the repo and install Ansible collections
2. Copy `inventory/example/` → `inventory/<your-site>/`, edit `hosts.yml` + `group_vars/all.yml`
3. Create `group_vars/vault.yml` with the secrets (BMC passwords, RHSM key, registry creds where applicable)
4. (Air-gapped only) Mint builder ISO → boot builder → bootstrap builder → mint cluster ISOs → boot cluster nodes
5. (Connected only) Install stock RHEL 9.7 on the cluster nodes yourself
6. Preflight, deploy (`site.yml`), validate

## Related docs

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — what the cluster looks like, network layout, why each piece is there
- [`IMAGE-BUILDER.md`](IMAGE-BUILDER.md) — the ISO-minting tooling container (used by the air-gapped path)
- [`HARDWARE-BOM.md`](HARDWARE-BOM.md) — reference hardware and BIOS / iDRAC settings
- [`OPERATIONS.md`](OPERATIONS.md) — day-2 operations once the cluster is deployed
- [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) — common failures and recovery paths
