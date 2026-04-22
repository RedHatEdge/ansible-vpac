# builder_rhsm

First role in `01-build-builder.yml`. Registers the builder host with Red Hat Subscription Management and enables the repos it needs to reposync. Runs once against `hosts[builder]`.

## What it does

1. Asserts RHSM credentials are set (activation key + org, OR username + password).
2. Registers the host with RHSM, preferring activation key over username/password.
3. Enables every repo in `rhsm_repos`.

Idempotent — a second run against a registered host with repos already enabled is a no-op.

## Variables

| Name | Source | Notes |
|---|---|---|
| `rhsm_activation_key` | `group_vars/vault.yml` | preferred auth path |
| `rhsm_org_id` | `group_vars/vault.yml` | required with activation key |
| `rhsm_username` | `group_vars/vault.yml` | fallback auth path |
| `rhsm_password` | `group_vars/vault.yml` | required with username |
| `rhsm_server_url` | `group_vars/all.yml` | defaults to `subscription.rhsm.redhat.com`; override for Satellite |
| `rhsm_repos` | `group_vars/all.yml` | list of repo IDs to enable; include `rhceph-7-tools-for-rhel-9-x86_64-rpms` to source RHCS packages |
| `builder_rhsm_skip_register` | role default `false` | flip true if the host is already registered (Satellite-managed, re-run, etc.) |

## When to use

Production (air-gapped): run once on the new builder while it still has outbound HTTPS to Red Hat CDN or Satellite. After the subsequent mirror + registry roles finish, the builder can be disconnected.

Lab: run on the builder VM with activation key creds loaded from `~/.vpac-lab-rhsm.yml`.

## Dependencies

- `community.general` collection (for `redhat_subscription` and `rhsm_repository` modules)
- Builder host must reach `subscription.rhsm.redhat.com` (or your Satellite) at run time
