# common_tools

Installs operator-expected RHEL tooling (vim, tmux, htop, sos, audit, cloud-init, podman, …) and the Cockpit web console on every vPAC host. Included from both `host_baseline` (cluster nodes) and `playbooks/01-build-builder.yml` (builder) so both kinds of host get the same operator experience.

Every package the role installs is in RHEL 9 BaseOS or AppStream — no EPEL, no third-party repos.

## Why this role exists separately

An SA landing on any vPAC-deployed host (cluster node OR builder) should find the same `vim`, `tmux`, `htop`, Cockpit dashboard, `sos` report tooling, etc. Keeping the list + install logic in one role avoids duplication between `host_baseline` and the three `builder_*` roles, and means "add a package to the standard set" is a single-file edit.

## What it installs

### Always

Organized in `defaults/main.yml` under `common_tools_packages`:

- Shell + package management: `bash-completion`, `bind-utils`, `curl`, `dnf-plugins-core`, `dnf-utils`, `git`, `iputils`, `less`, `net-tools`, `nmap-ncat`, `rsync`, `tar`, `unzip`, `wget`
- Editors / multiplexer: `tmux`, `vim-enhanced`
- Ops visibility: `iotop`, `iperf3`, `jq`, `lsof`, `strace`, `sysstat`, `tcpdump` (`htop` isn't in RHEL 9 — EPEL only; add to `common_tools_extra_packages` on EPEL-enabled sites)
- Compression: `bzip2`, `zstd`
- System support: `audit`, `ca-certificates`, `cloud-init`, `cloud-utils-growpart`, `irqbalance`, `linux-firmware`, `sos`, `tuned`
- Container runtime: `podman`

### Conditional (Cockpit, on by default)

When `common_tools_install_cockpit: true` (the default):

- `cockpit`, `cockpit-system`, `cockpit-machines`, `cockpit-networkmanager`, `cockpit-podman`, `cockpit-selinux`, `cockpit-sosreport`, `cockpit-storaged`, `cockpit-packagekit`
- Enables + starts `cockpit.socket`
- Opens the `cockpit` firewalld service in the default zone

After this role runs on a host, `https://<host>:9090` answers with a Cockpit login. On cluster nodes you'll see the Virtual Machines tab with `virsh` domains listed; on the builder you'll see the Podman Containers tab with the `registry:2` container.

### Site extensions

Append to `common_tools_extra_packages` in inventory `group_vars/all.yml` to pull in customer-specific tools without editing the default list.

## Variables

| Name | Default | Notes |
|---|---|---|
| `common_tools_packages` | *(curated list, see above)* | always-installed packages |
| `common_tools_install_cockpit` | `true` | flip `false` to skip Cockpit entirely |
| `common_tools_cockpit_packages` | *(9 cockpit modules)* | override to narrow the Cockpit stack |
| `common_tools_extra_packages` | `[]` | site extras |
| `common_tools_firewalld_zone` | `public` | which zone to open cockpit in |

## Dependencies

- firewalld must be running when this role executes. Both host_baseline and builder_mirror start it first, so in the normal flow this is automatic. Running common_tools standalone on a host without firewalld active will fail at the firewall rule step.
- `ansible.posix` collection for the firewalld module.

## When it runs

- **Cluster nodes**: host_baseline's `tasks/main.yml` calls `include_role: common_tools` as its final step, after firewalld + chrony are configured.
- **Builder**: `playbooks/01-build-builder.yml` lists `common_tools` as its last role, after `builder_rhsm` + `builder_mirror` + `builder_registry`.

## Tags

- `common-tools` — this role
