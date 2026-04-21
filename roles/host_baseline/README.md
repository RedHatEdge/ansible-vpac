# host_baseline

Stage 10. Brings each cluster node to a known, minimal state before any networking, virtualization, or cluster work. Idempotent — safe to re-run.

## What it does

1. **SELinux** — assert enabled (does not change mode)
2. **Timezone** — set from `site_timezone`
3. **Hostname** — set from the node's `vpac_nodes` entry
4. **/etc/hosts** — write mgmt + storage entries for every node from `vpac_nodes`. Ceph bootstrap and Pacemaker both need hostnames that resolve *without* DNS, since storage networks are L2-only at many sites.
5. **Repo source** — branches on `sources.repo_source`:
   - `rhsm` → `subscription-manager register` with activation key, enable `rhsm_repos`
   - `satellite` → register against the Satellite URL with the same key
   - `local_mirror` → write dnf repo files pointing at `sources.local_mirror_url`; no subscription
6. **Packages** — install the baseline toolset (networking, tracing, cluster-adjacent utilities)
7. **Firewalld** — start + enable; allow SSH. Other services open their own ports in their own roles.
8. **Journald** — persistent storage under `/var/log/journal`, sized to `journald_max_use_mb`

## Variables (with defaults)

| Name | Default | Notes |
|---|---|---|
| `baseline_packages` | see `defaults/main.yml` | extendable by appending in group_vars |
| `baseline_extra_packages` | `[]` | site-specific additions |
| `journald_max_use_mb` | `2048` | persistent journal cap |
| `local_mirror_gpgcheck` | `false` | flip to `true` + set `local_mirror_gpgkey_url` for prod |
| `local_mirror_gpgkey_url` | `""` | required when `local_mirror_gpgcheck: true` |
| `firewalld_default_zone` | `"public"` | |

Reads from `group_vars/all.yml`: `deployment_mode`, `sources.*`, `rhsm_*`, `vpac_nodes`, `site_domain`, `site_timezone`.

## Tags

- `baseline` — everything
- `baseline-selinux`, `baseline-timezone`, `baseline-hostname`, `baseline-hosts`, `baseline-repo`, `baseline-packages`, `baseline-firewall`, `baseline-journald` — individual groups

## Handlers

- `restart journald` — triggered when `/etc/systemd/journald.conf` changes

## Dependencies

None at the role level. Expects `preflight` to have run (but does not enforce it — `site.yml` does).
