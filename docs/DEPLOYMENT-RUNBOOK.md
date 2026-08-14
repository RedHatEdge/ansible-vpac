# Deployment runbook — define, run, log

A field-derived companion to the deployment guides, built from real hardware
validation runs of this repo. Three parts: **A** — define *your* environment,
**B** — the exact commands per stage with post-stage validation, **C** — a
site log template you append to as you run. Keep your filled copy with your
site inventory (not in this repo).

Reference workload: ABB SSC600 (IEC 61850 vPAC relay) — but note the
**cluster-only path is the default**: an empty `vm_catalog` builds the full
cluster with stage 80 as a no-op. Workloads are never a prerequisite.

---

## 0. Prerequisites (control node)

**First deployment, or new to Ansible? Start with
[`QUICKSTART.md`](QUICKSTART.md)** — it walks the whole chain below from zero
(installing Ansible, creating and distributing the SSH key, passwordless sudo,
copying and renaming the inventory, the vault) with nothing assumed.

- Ansible core ≥ 2.15, `git`, and the repo's collections:
  `ansible-galaxy collection install -r requirements.yml` (plus
  `pip install --user -r requirements.txt`)
- SSH key access to every node as an admin user with passwordless `sudo`
  (`ansible.cfg` sets `become=True`). Setup + the no-prompt test:
  QUICKSTART.md step 2. The key path is set once in `hosts.yml`
  (`ansible_ssh_private_key_file`).
- RHEL 9.7 installed on the nodes (connected mode); for the air-gapped path
  the builder/ISO workflow installs them — see `docs/DEPLOYMENT-AIRGAPPED.md`.
- Connected mode: RHSM activation key + org, and `registry.redhat.io` pull
  credentials (**terms-based registry service account** — see
  `docs/OPERATOR-VALUES.md`). Keep secrets in Ansible Vault as the `vault_*`
  variables the contract references.
- `ansible.cfg` defaults `inventory = inventory/example` — **always pass
  `-i inventory/<yoursite>` explicitly** so you never run against the template.
- **Eject BMC virtual media after installing the nodes** (iDRAC: Virtual Media →
  Disconnect; equivalent on other BMCs). Leftover virtual CD/DVD and floppy
  devices from the install linger as `sr0`/`sda` block devices, then surface as
  rejected devices in `ceph orch device ls` and confuse storage audits.
  Field-observed on two of three nodes after ISO-based installs.

## Part A — Define YOUR environment

```bash
cp -r inventory/example inventory/<yoursite>
```

Edit three things: `hosts.yml`, `group_vars/all/main.yml`, `host_vars/*.yml`.
`docs/OPERATOR-VALUES.md` is the per-variable companion; the notes below are
the field-learned emphasis.

### A.1 `hosts.yml` — groups & node list

Keep the group shape, swap in your FQDNs: `vpac_cluster` (every node),
`rt_hosts` / `ceph_nodes` / `pacemaker_cluster` (normally identical to
`vpac_cluster`), `ceph_bootstrap_node` (the ONE node that runs
`cephadm bootstrap`), `builder` (air-gapped only; `site.yml` does not target
it). Per-node: `ansible_host` (mgmt IP) and `ansible_user`.

The design is **any VM runs on any node** — do not encode per-node capability
differences as groups; VM placement lives in `vm_catalog`
(`target_host` / `allowed_hosts`).

### A.2 `group_vars/all/main.yml` — field-learned emphasis per section

| Key | Field-learned notes |
|---|---|
| `site_timezone` | host_baseline applies it to every node — set it here, never by hand |
| `deployment_mode` | `connected` pulls RHSM + registry.redhat.io, no builder host needed |
| secrets (`rhsm_*`, `redhat_registry_*`) | Vault only; registry auth is a **terms-based** SA, not IAM |
| `vpac_nodes` | length ≥ 3 gates Pacemaker/Ceph; hostnames here are the reference everything else must match |
| `time_sync` | **Pure PTP for substations**: `mode: ptp` (no NTP fallback below the grandmaster — NTP belongs only at the GM), `delay_mechanism: P2P`, `transport: L2`, domain per IEC 61850-9-3. The per-host PTP NIC goes in `host_vars`. |
| `rt_tuning` | `isolated_cpus` MUST be recomputed per CPU topology — `isolcpus` silently ignores CPU numbers the machine doesn't have. `rt_chrony` keeps `lock_all` / `sched_priority 60` / `combinelimit 0` (vendor relay guidance). |
| `ceph.osd_devices` | **`/dev/disk/by-id/…` stable paths, never `/dev/nvmeXn1`** — kernel names reorder across boots; a swapped name landing on the OS disk wipes the OS. Field near-miss caught only by a manual pre-check; preflight now enforces this. Per-node lists live in `host_vars`. |
| `pacemaker` / `stonith` | corosync ring on the heartbeat network; STONITH via BMC (iDRAC/IPMI) per node — **enable IPMI-over-LAN** (iDRACs ship with it disabled) |
| `vm_catalog` | **leave `[]` for cluster-only** (default); fill and re-run stage 80 when workload images are in hand; give each RBD-backed VM a unique `lease_offset` |

### A.3 `host_vars/<node>.yml`

Per-node NIC names, the **dedicated PTP NIC**, and the node's **by-id OSD
device list**. Get NIC identities from `ip -br link` + `ethtool -i` (mapping
guide: networking role README); get disk by-id paths from
`ls -l /dev/disk/by-id/ | grep -v part`.

### A.4 Secrets

`ansible-vault create inventory/<yoursite>/group_vars/all/vault.yml`, define the
`vault_*` keys, run with `--ask-vault-pass` or `--vault-password-file`.

## Part B — Run it (exact commands)

`site.yml` chains all stages; each is tag-gated and has its own playbook.
**Recommended: run stage-by-stage, validating between** — especially around
the destructive ones.

```bash
# Full build (00→90):
ansible-playbook -i inventory/<yoursite> site.yml

# One stage at a time (preferred while validating):
ansible-playbook -i inventory/<yoursite> site.yml --tags <tag>
ansible-playbook -i inventory/<yoursite> playbooks/<NN>-<name>.yml

# Scope: --limit <node>   Preview: --check --diff   Verbose: -v / -vvv
```

| Stage | Tag | Playbook | Does | Post-stage validation |
|---|---|---|---|---|
| 00 | `preflight` | `00-preflight.yml` | inventory contract (one-pass), reachability, entitlement, PTP-NIC HW timestamping | recap green + `Inventory contract clean` |
| 10 | `baseline` | `10-host-baseline.yml` | tz, hostname/hosts, repos, packages, firewalld | `timedatectl`, `subscription-manager status` |
| 20 | `networking` | `20-networking.yml` | nmstate bonds/bridges/VLANs incl. heartbeat | `nmstatectl show`, heartbeat mesh ping |
| 30 | `virt` | `30-virtualization.yml` | KVM/libvirt, tuned, qemu hook | `virsh list`, `systemctl status libvirtd` |
| 40 | `ptp` | `40-ptp.yml` | timemaster (ptp4l+phc2sys+chrony), masks system chronyd, cephadm time-sync alias | **portState SLAVE + bounded, live offset** (see gotcha below) |
| 50 | `rt` | `50-rt-tuning.yml` | **stages** kernel-rt, isolcpus, RT cmdline (RT kernels only), tuned — **takes effect only after the rolling reboot below** | staged: `grubby --default-kernel` shows the rt kernel. After the reboot: `cat /sys/devices/system/cpu/isolated` equals your declared list, cyclictest |
| 60 | `ceph` | `60-ceph.yml` | cephadm bootstrap → hosts → OSDs → CephFS/RBD → monitoring | `ceph -s` HEALTH_OK, `ceph osd tree`. **Destructive on osd_devices** (guarded — see OPERATOR-VALUES.md re-run section) |
| 70 | `pacemaker` | `70-pacemaker.yml` | corosync/pacemaker on the heartbeat ring | `pcs status` |
| 75 | `stonith` | `75-stonith.yml` | fencing (fence_ipmilan / fence_virsh) | `pcs stonith`, then `op-stonith-fence-test.yml` on a **drained** node |
| 80 | `vm` | `80-vm-deploy.yml` | render + define VMs from `vm_catalog` (no-op when empty) | `virsh list`, VM boots |
| 90 | `validate` | `90-validate.yml` | end-to-end assertions | recap green |

Air-gapped only, before all of the above: mint the builder ISO
(`00-mint-builder-iso.yml`), build the builder (`01-build-builder.yml`), mint
cluster ISOs (`00b-mint-cluster-isos.yml`), install the nodes from them, then
run `site.yml` against the local mirror/registry.

Ops playbooks: `op-rolling-reboot.yml`, `op-pacemaker-recover.yml`,
`op-stonith-fence-test.yml`, `op-vm-undefine.yml`.

> **Stage 50 requires a reboot, and the reboot is YOUR move — here is the
> safe way.** The role deliberately does not reboot
> (`rt_tuning_auto_reboot` defaults false): substations reboot in controlled
> windows, and three nodes rebooting together loses Ceph mon quorum. Nothing
> RT takes effect — `/sys/devices/system/cpu/isolated` stays EMPTY — until
> each node reboots. Use the shipped playbook, which encodes the whole
> procedure:
>
> ```bash
> ansible-playbook -i inventory/<yoursite> playbooks/op-rolling-reboot.yml \
>     -e i_want_a_rolling_reboot=yes
> ```
>
> One node at a time; Ceph (if deployed) is told to expect the bounce
> (`noout`/`norebalance`, set before and cleared after); readiness is
> verified by SSH + boot-time + the RT kernel actually running + PTP
> re-locked; Ceph health is polled **via a surviving node**. What to expect:
> **~5 minutes per node**; the RT kernel's **first boot performs an extra
> warm reset** (looks like a second reboot — normal); PTP may show
> FAULTY/LISTENING for up to a minute after boot and self-recovers to SLAVE.
>
> If you script this yourself instead, two field-proven traps: **ping is not
> readiness** (a node answers ping seconds into boot, right before the warm
> reset takes it down again — gate on SSH + boot time + `uname -r`), and
> **never query Ceph health through the node you are rebooting** — always
> ask a surviving node.

> **Stage-40 gotcha (hardware-observed):** a node can show a *stable
> grandmaster identity* and healthy Pdelay yet never synchronize — stuck
> UNCALIBRATED with a frozen `offsetFromMaster`, because the switch fabric is
> not delivering the two-step `Follow_Up`. The stage-40 verify gate catches
> this as a clean failure. Diagnose on the wire (`tcpdump ether proto
> 0x88f7`: zero Follow_Up = fabric problem) and fix the **fabric, not the
> node** — re-initialize the transparent clock's PTP engine, verify the GM is
> disciplined, and check the switch firmware level. Full diagnostic recipe
> (including the required `pmc -s` socket form): ptp_timesync role README,
> "PTP fabric requirement".

## Part C — your site's command log (template)

Copy this table into your site's copy of the runbook and append verbatim as
stages run — the log of what was actually executed, by whom, and what it took
is the part no template can write for you, and it is what turns your next
deployment (or your next recovery) from archaeology into a lookup.

| Date | Stage | Command | Result / notes |
|---|---|---|---|
| | | | |

Record deviations from the stock template in a "site deviations" list at the
bottom — every one of them is either a bug report or a docs improvement for
this repo. File them.
