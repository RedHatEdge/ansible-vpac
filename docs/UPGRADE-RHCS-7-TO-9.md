# Upgrading Ceph in place: RHCS 7 → RHCS 9

This is the procedure for upgrading a deployed cluster's Red Hat Ceph Storage from RHCS 7 (Ceph 18.2 "Reef") to RHCS 9 (Ceph 20.2 "Tentacle") **in place** — the cluster stays formed, the OSDs keep their data, and no redeploy happens. RHCS 7 reaches end of support on 2026-12-12, so every RHCS 7 cluster deployed from this project needs this eventually. The 7 → 9 jump skips RHCS 8 and is within Ceph's supported upgrade span.

Everything in this document was validated on real hardware, twice:

- A reference-scale run: **169 GiB stored**, in-place upgrade under continuous client I/O, **669/669 file checksums intact** afterwards, worst single client stall **2.02 s**.
- A byte-identity run: a 200-file sha256 manifest written under 18.2.1, verified after the upgrade to 20.2.1 — **200 OK, 0 FAILED**. Byte-identical across the major version.

Where a step below is something we *added by design* rather than something the validated runs did, it is labelled as exactly that.

## The goal, and what to do with your workloads

The goal of a major storage upgrade is that **the data on the cluster stays good**. It is not keeping VMs running through the upgrade window. This is a planned maintenance action — treat it like one:

**Shut down all VMs before starting.** Not just as caution — there is a specific mechanism:

> **`cephadm` deadlocks against isolated CPUs running a pinned VM.** During upgrades cephadm gathers host facts, which runs `sysctl -a`; reading `vm.stat_refresh` makes the kernel schedule work on **every** CPU (`schedule_on_each_cpu`). On an RT host, a vCPU pinned to an isolated CPU never yields, so that call blocks in uninterruptible D-state forever — wedging cephadm on that node. Field-observed: the upgrade froze at 1/28 daemons for ~52 minutes, only on the node running a pinned guest, and unblocked the moment the guest was suspended. With guests down, the same upgrade completed without incident.

Shut VMs down through Pacemaker if they are managed (`pcs resource disable <vm>` per VM), or `virsh shutdown` for standalone ones. Verify with `virsh list` on every node: no running domains.

## Preconditions

Check every one of these before starting. `playbooks/op-ceph-upgrade.yml` (below) checks them for you.

1. **`ceph health` is `HEALTH_OK`.** Never start an upgrade on a degraded cluster.
2. **Every daemon is on the same source version** — `ceph versions` shows exactly one version overall.
3. **All nodes are reachable.** An unreachable node auto-pauses the upgrade (see below).
4. **No running VMs** (mechanism above).
5. **The target image is reachable.** Connected sites: `registry.redhat.io/rhceph/rhceph-9-rhel9` (needs the registry login from deployment). Air-gapped sites: the RHCS 9 image must already be in the local registry under the same name the `container_images` contract uses.
6. **A recent backup/snapshot posture you are comfortable with.** Downgrade does not exist (see "Rollback posture").

## The procedure

All commands run **on the bootstrap node** (the first node in `vpac_nodes`). They work either directly (`ceph ...` — the nodes have `ceph-common`) or wrapped as `cephadm shell -- ceph ...`; the validated runs used the wrapped form.

**1. Baseline — record what you have:**

```bash
ceph -s
ceph versions        # exactly ONE version listed under "overall"
ceph df              # note your pools and stored bytes
```

**2. Start the upgrade:**

```bash
ceph orch upgrade start --image registry.redhat.io/rhceph/rhceph-9-rhel9:latest
ceph orch upgrade status
```

That is the command the validated runs used, `:latest` included. **Design improvement we recommend (not what the validated run did): pin the tag** (for example `:20.2.1`) instead of `:latest`, so every node pulls the identical image even if the registry moves under you mid-upgrade. Air-gapped sites pin implicitly by mirroring one image.

**3. Watch it.** The validated cadence was a manual poll roughly every 30–60 seconds:

```bash
ceph orch upgrade status    # "in_progress": true, progress counts up
ceph -s                     # health + a progress bar during the upgrade
```

cephadm upgrades one daemon at a time in a safe order (mgr daemons first, then mons, then OSDs and the rest). Measured twice, on independent runs a day apart: **28 daemons in 10m05s and 10m06s** — same shape both times, steady daemon-by-daemon progress from the start, on a 1 GbE replication fabric. The first pull per node is ~1.3 GB (the image size); a **per-node pre-pull** before starting (another labelled design improvement — both validated runs went in cold with no measured pull penalty) takes that pull off the critical path on links where it would matter.

**4. Confirm completion:**

```bash
ceph orch upgrade status    # "in_progress": false
ceph versions               # exactly ONE version again — the target
ceph health                 # HEALTH_OK
```

The run was a single unrestricted `upgrade start`. Staged upgrades (`--daemon-types`) exist upstream but are unexplored by us — neither recommended nor warned against here.

## Events you may see, and what they mean

**The upgrade auto-pauses when a node goes unreachable.** `ceph orch upgrade status` shows `"is_paused": true` and health shows `UPGRADE_OFFLINE_HOST`. This is correct behavior, exercised in our validation under a real fault: fix the node's reachability, then

```bash
ceph orch upgrade resume
```

**The orchestrator can go dead after a mgr failover mid-upgrade — while looking enabled.** Mixed-version mgr cache is not backward-readable: if the active mgr fails over to a not-yet-upgraded mgr, cephadm can crash reading state the newer mgr wrote (`DaemonDescription: __init__() got an unexpected keyword argument ...`). The trap: `ceph mgr module ls` still shows cephadm **"on"** while `ceph orch status` returns **"Module not found"** — enabled but not loaded, which reads as healthy if you only check the first. Remedy, field-proven:

```bash
ceph mgr fail    # promotes the standby (already-upgraded) mgr; orchestrator returns immediately
```

**The upgrade freezes at 1/N daemons on one node.** That is the pinned-VM deadlock from the top of this document — a guest is still running on that node. Shut it down or suspend it; the upgrade unblocks on its own.

## After the upgrade: update your inventory — nothing does this for you

Your contract still declares RHCS 7 in three places, and **no check compares the contract against the running cluster version** — preflight's Ceph agreement assert verifies the three pins agree *with each other*, not with reality. A stale contract bites later in surprising ways (a replacement node would get RHCS 7 repos and tooling against a RHCS 9 cluster). Update all three pins together, now:

1. `ceph.release` → `"9"`
2. The tools repo in `rhsm_repos` → `rhceph-9-tools-for-rhel-9-x86_64-rpms`
3. `container_images.ceph` → `"rhceph/rhceph-9-rhel9:latest"` (air-gapped sites: mirror the RHCS 9 images to the builder registry too)

Then run preflight — it should pass clean at 9.

## Rollback posture — stated honestly

- **Downgrade is not a thing.** Ceph does not support downgrading to an older release. This is upstream-documented behavior, not our finding. The moment daemons run the new version, forward is the only direction.
- **Pause/resume: exercised under fault** in our validation (the auto-pause + `resume` path above). `ceph orch upgrade pause` by hand follows the same mechanics.
- **`ceph orch upgrade stop` (abandon and hold mixed versions): untested by us.** We label it rather than guess.

Your realistic fallback is the one this project already gives you: the cluster's data was proven to survive the upgrade, and a full redeploy from the contract plus restore is always available as the nuclear option.

## Limits of the validation, carried with the numbers

- Our layout: 3 nodes, 12 OSDs, ~22–28 daemons. Larger clusters take proportionally longer.
- Synthetic file data under checksum manifests — not a live protection workload.
- 1 GbE replication fabric — both ~10-minute measurements come from that fabric; faster networks are unmeasured.
- Guests were **down** during the validated upgrade window (by design, per the goal).
- Single unrestricted start; staged ordering unexplored.

## The automated version

`playbooks/op-ceph-upgrade.yml` encodes this procedure — consent-gated, refuses unless every precondition above holds, starts the upgrade, polls to completion, and verifies. It was **written from the validated runs' command transcript but has not itself driven an upgrade yet** — that label comes off the first time it does. Until then the manual procedure above is the validated path and the playbook is its faithful transcription with the gates added.

```bash
ansible-playbook -i inventory/<site> playbooks/op-ceph-upgrade.yml \
  -e i_want_a_ceph_upgrade=yes \
  -e ceph_upgrade_target_image=registry.redhat.io/rhceph/rhceph-9-rhel9:latest
```
