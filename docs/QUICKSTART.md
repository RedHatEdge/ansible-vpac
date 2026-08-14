# Quickstart — zero to your first clean preflight

This page assumes **no Ansible knowledge**. It is written for the protection or
substation engineer who has three servers racked, a laptop, and this repo — and
takes you, in order, to the point where the tooling itself confirms your site
configuration is complete and correct. Everything after that has its own guide;
nothing before that is assumed.

Time: roughly an hour, most of it filling in your site's values.

## Words this guide can't avoid (one line each)

| Term | What it means here |
|---|---|
| **control node** | Your ordinary laptop or workstation. You run all commands from it; it talks to the servers over SSH. Nothing is installed on the servers by hand. It is NOT one of the three servers. |
| **inventory** | A folder of text files describing YOUR site — hostnames, addresses, disks, settings. Think of it as the datasheet the automation reads. |
| **playbook** | A runnable procedure (like an ordered test plan) that configures the servers to match the inventory. |
| **role** | A pre-built chapter of a playbook (networking, storage, time sync…). You never edit these. |
| **tag** | A label that runs just one part of the procedure, e.g. `--tags preflight` runs only the checks. |
| **vault** | One encrypted file holding all passwords/keys. You unlock it with a single password when you run a playbook. |
| **fact** | Something the automation reads from a server (NIC names, CPU count) to make decisions. You don't manage facts. |

## Step 0 — getting RHEL onto three servers (the part every guide skips)

**This guide documents the CONNECTED path**: your servers can reach the
internet, and everything installs from Red Hat's own services. If your site
has no outbound internet (most substations), you want the air-gapped path —
[`DEPLOYMENT-AIRGAPPED.md`](DEPLOYMENT-AIRGAPPED.md) — where a "builder"
machine is loaded once and the servers install from it. If you are unsure:
labs and proofs-of-concept are usually connected; production substations are
usually air-gapped. Everything below Step 3 is the same for both.

**Get RHEL:** download the RHEL 9 DVD ISO from
[access.redhat.com/downloads](https://access.redhat.com/downloads/content/rhel)
— this needs a Red Hat account login (creating one is free). Use any current
RHEL 9 minor release, 9.6 or newer — this project is field-validated on 9.7
and 9.8; preflight checks the version for you. Install it on each of the
three servers with the **Server (no GUI)** base environment, create the same
admin user on each, and note each server's management IP.

**Subscriptions — what you actually need (ask for this list by name):**
a subscription covering **RHEL 9 + High Availability + Resilient Storage +
NFV (real-time kernel) + Red Hat Ceph Storage**. Asking for "RHEL" alone
gets you a subscription that fails mid-deployment when the HA or real-time
repositories turn out to be missing. Where to get it:

- **Lab / evaluation:** the free [Red Hat Developer subscription]
  (https://developers.redhat.com/register) covers individual
  development/testing use — enough to build this cluster in a lab.
- **Production:** give your Red Hat account team the entitlement list above.

Then create an **activation key** (console.redhat.com → Inventory → System
Configuration → Activation keys) and note the **org ID** shown on the same
page — the form/vault asks for both later.

## What else you need

- The servers' BMC (iDRAC/IPMI) addresses and a STONITH user on each — with
  **IPMI-over-LAN enabled** (iDRACs ship with it off)
- (Air-gapped only) a registry service account —
  [`OPERATOR-VALUES.md`](OPERATOR-VALUES.md) says how
- A laptop with Linux or macOS (this guide shows RHEL/Fedora commands)

## Step 1 — Put the tools on your laptop

```bash
sudo dnf install -y ansible-core git python3-pip
git clone https://github.com/RedHatEdge/ansible-vpac.git
cd ansible-vpac
ansible-galaxy collection install -r requirements.yml
pip install --user -r requirements.txt
```

## Step 2 — Give your laptop key-based SSH access to every node

Ansible logs into the servers the same way you would — over SSH — but it must
do so **without password prompts**. That takes two one-time setups per node:
an SSH key, and passwordless sudo for your admin user.

```bash
# 2a. Create a key on the laptop, once (accept the defaults; a passphrase is
#     fine if you use ssh-agent, but simplest is none for a dedicated key):
ssh-keygen -t ed25519

# 2b. Copy the key to EACH node (repeat per node; enter the admin password
#     one last time when asked):
ssh-copy-id admin@10.0.0.11
ssh-copy-id admin@10.0.0.12
ssh-copy-id admin@10.0.0.13

# 2c. On EACH node, allow the admin user to sudo without a password:
ssh admin@10.0.0.11
echo 'admin ALL=(ALL) NOPASSWD: ALL' | sudo tee /etc/sudoers.d/90-vpac-admin
sudo chmod 440 /etc/sudoers.d/90-vpac-admin
exit

# 2d. THE TEST — this exact command must complete with NO password prompt
#     of any kind, for every node:
ssh admin@10.0.0.11 sudo -n true && echo OK
```

If 2d prints `OK` silently for all three nodes, this step is done forever.

**How Ansible finds the key:** the example inventory sets
`ansible_ssh_private_key_file: ~/.ssh/id_ed25519` in `hosts.yml` — if you made
your key anywhere else, change that one line. (The alternative,
`--private-key <path>` on every command, works but is easy to forget.)

## Step 3 — Make the inventory yours

**The easy way — use the form.** The repo ships a local form that asks for
every value in plain language and writes all of these files for you,
including the encrypted vault (steps 3 and 4 collapse into one):

```bash
python3 tools/site-form.py     # then open http://127.0.0.1:8765
```

Fill it top to bottom; on success it prints the exact preflight command —
skip straight to Step 5. The rest of this step is the **by-hand path**: the
fallback, and the reference for exactly what the form writes.

```bash
cp -r inventory/example inventory/mysite

# Rename the per-node files to match YOUR hostnames — the automation finds
# each node's file BY ITS FILENAME, so this rename is required, not cosmetic:
cd inventory/mysite/host_vars
mv site1-node-a.yml your-node-1.yml
mv site1-node-b.yml your-node-2.yml
mv site1-node-c.yml your-node-3.yml
cd -
```

Then edit, in this order:

1. `inventory/mysite/hosts.yml` — replace every `site1-node-*` with your real
   hostnames, set each `ansible_host:` to the node's management IP and
   `ansible_user:` to your admin user (the one from step 2). **Also: delete
   the `builder` group and its `site1-builder` host entirely unless you are
   on the air-gapped path** — connected deployments have no builder, and a
   leftover placeholder host lingers forever otherwise (preflight will
   remind you).
2. `inventory/mysite/group_vars/all/main.yml` — the big one. Work top to bottom;
   every value is commented, and
   [`OPERATOR-VALUES.md`](OPERATOR-VALUES.md) is the companion table
   (what's required, what format, where to get it). Leave `vm_catalog: []`
   alone for now — a cluster with no workloads is the normal starting point.
3. `inventory/mysite/host_vars/<each node>.yml` — per-node NIC names and disk
   paths (the file comments say how to look them up on the node).

## Step 4 — Put the secrets in a vault

```bash
# The example enumerates every secret you owe, with where to get each:
less inventory/mysite/group_vars/all/vault.yml.example

# Create the encrypted real one (pick a vault password; you'll type it on
# every run) and paste/edit the contents of the example inside:
ansible-vault create inventory/mysite/group_vars/all/vault.yml
```

## Step 5 — Run preflight and read the verdict

```bash
ansible-playbook -i inventory/mysite site.yml --tags preflight --ask-vault-pass
```

The first task block validates your whole datasheet in one pass and reports
**everything** still wrong, together. A first run typically looks like this
(sample, abbreviated):

```
TASK [preflight : Contract: report every unresolved operator value together]
fatal: [your-node-1]: FAILED! => The inventory contract has 4 unresolved operator value(s):
1. example placeholder hostnames ('site1-node-…') are still present in: ceph — replace ...
2. ceph.osd_devices uses unstable kernel device names: /dev/nvme0n1 ... Use /dev/disk/by-id/...
3. rhsm_activation_key is empty — set vault_rhsm_activation_key in your vault.yml ...
4. bmc_password resolves empty for node(s): your-node-3 — set the vault_bmc_password_* ...
Fix all of the above, then re-run: ...
```

That is the tool working as designed: fix every numbered line, re-run, repeat.
No value you type is ever printed back — secrets are reported by name only.
Success looks exactly like this:

```
TASK [preflight : Contract: clean]
ok: [your-node-1] => msg: Inventory contract clean (cluster-forming values + required
secrets): no placeholders, ceph.bootstrap_node valid, osd_devices stable-path and
host-matched, registry auth consistent. vm_catalog empty — cluster-only path; stage 80
will be a no-op.
```

…followed by the rest of preflight's live checks against the nodes
(reachability, subscriptions, NIC and PTP hardware checks) and a green recap
(`failed=0`). When you see that, your datasheet is complete and the servers
are reachable and sane.

## Step 6 — Deploy, one stage at a time

From here follow [`DEPLOYMENT-RUNBOOK.md`](DEPLOYMENT-RUNBOOK.md) Part B: the
stage ladder (baseline → networking → virtualization → PTP → RT → storage →
cluster → fencing → validate), one command per stage, with what to check after
each. One heads-up so it doesn't surprise you there: the RT stage (50)
**stages** its changes and nothing takes effect until each node reboots — the
runbook ships a safe one-node-at-a-time reboot playbook for exactly that; the
servers are never rebooted behind your back. Path-specific detail: [`DEPLOYMENT-CONNECTED.md`](DEPLOYMENT-CONNECTED.md)
or [`DEPLOYMENT-AIRGAPPED.md`](DEPLOYMENT-AIRGAPPED.md).

If anything fails with a message that does not tell you what to do next,
that is a bug in this repo — please file it.
