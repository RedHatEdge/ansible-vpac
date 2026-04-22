# builder_mirror

Second role in `01-build-builder.yml`. Reposyncs the RHSM repos enabled by `builder_rhsm` to a local directory on the builder and serves them over plain HTTP.

## What it does

1. Installs `httpd`, `dnf-utils` (for `reposync`), `createrepo_c`, `firewalld`, `rsync`.
2. Starts and enables `firewalld` + `httpd`; opens the `http` firewalld service.
3. Creates `{{ builder_mirror_root }}` (default `/var/www/html/mirror`) owned by apache.
4. `reposync`s every repo listed in `rhsm_repos` (same list `builder_rhsm` enabled) with `--newest-only --download-metadata --delete`.
5. Rebuilds the on-disk `repodata/` with `createrepo_c --update` to prevent clients from 404ing on intermediate package versions that the CDN's repomd referenced.
6. HEAD-probes the served `repomd.xml` for each repo to confirm it's reachable.
7. Prints the mirror URL to paste into the cluster inventory's `sources.local_mirror_url`.

## Variables

| Name | Default | Notes |
|---|---|---|
| `builder_mirror_root` | `/var/www/html/mirror` | filesystem path served by httpd |
| `builder_mirror_url_path` | `mirror` | URL path suffix (`http://<builder>/<path>`) |
| `builder_mirror_skip_reposync` | `false` | skip the reposync step on re-runs when you trust the existing mirror |

Reads `rhsm_repos` from `group_vars/all.yml`.

## Storage + time expectations

- First run pulls ~10–20 GB for the default 6-repo set (BaseOS + AppStream + HA + resilient-storage + NFV + rhceph-7-tools). Size grows with each additional repo.
- Duration depends on the builder's egress bandwidth. Plan 30–60 minutes for the initial sync; subsequent runs with no new packages upstream are fast.

## Dependencies

- `community.general`, `ansible.posix` collections
- `builder_rhsm` must run first (repos need to be enabled before `reposync` can see them)
