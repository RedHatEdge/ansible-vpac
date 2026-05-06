# vm_deploy

Stage 80 (second half). Defines each VM in `vm_catalog`. Two modes:

- **standalone** — plain libvirt domain on `target_host`. No failover. Use for single-node deployments or bring-up before `pacemaker_base` + `stonith` land.
- **managed** — each VM is an `ocf:heartbeat:VirtualDomain` Pacemaker resource with location constraints from `vm_catalog[].target_host` and `allowed_hosts`. Pacemaker auto-failovers on node loss.

`vm_deploy_managed_mode` defaults to `True` when `len(vpac_nodes) >= 3` AND `stonith.enabled`. The role's runtime precheck additionally verifies `stonith-enabled=true` on the cluster — fails fast if pacemaker_base ran but stonith hasn't.

Per-VM override: `vm_catalog[].pacemaker_managed: true | false` takes precedence over the cluster-wide default. Useful for one-off debugging — never have an entire site half in each mode.

## Standalone mode

Loops over `vm_catalog`; for each entry where `target_host == inventory_hostname`:

1. Reads `{{ vm_deploy_xml_dir }}/<name>.xml` (produced by `vm_templates`).
2. `community.libvirt.virt: command=define` — idempotent.
3. Optionally marks the domain autostart-on-boot (`vm_deploy_autostart_on_boot: true`).
4. Optionally starts the domain (`vm_deploy_autostart: true`).

Defaults to `vm_deploy_autostart: false` so the operator can verify qcow2 disks exist before fleet startup. **Does NOT undefine VMs that aren't in `vm_catalog`.**

## Managed mode

Once-per-run setup:

1. **Precheck** — confirms `pcs property show stonith-enabled` returns `true` on the cluster. Refuses to proceed otherwise.
2. **Helpers** — drops `/usr/local/sbin/pcs-vm-move` and `pcs-vm-status` operator helpers.

Per-VM:

3. **Shared XML** — copies the per-host XML at `{{ vm_deploy_xml_dir }}/<name>.xml` to `{{ vm_deploy_managed_xml_dir }}/<name>.xml` (defaults to the CephFS mountpoint). Pacemaker reads it from there on whichever node it starts the VM.
4. **Standalone undefine** — sweeps every cluster node: if a standalone libvirt domain by this VM's name exists, `virsh shutdown` (best-effort) and `virsh undefine`. UEFI VMs (XML contains `<loader>`) get `--keep-nvram` so SecureBoot state is preserved across the handoff. Necessary because Pacemaker creates its own libvirt domain at start time; leaving a persistent definition is the documented split-brain footgun.
5. **`pcs resource create`** — `ocf:heartbeat:VirtualDomain` with `config=<shared-path>`, `hypervisor=qemu:///system`, op timeouts from the cluster defaults set by `pacemaker_base`. PCI-passthrough VMs (`vm_catalog[].pci_passthrough` non-empty) auto-derive `meta allow-migrate=false` and drop the `migrate_to`/`migrate_from` ops. Idempotent: `pcs resource config <name>` gates the create. Resources land `--disabled` by default so the operator runs `pcs resource enable <name>` after confirming the backing disk exists (matches the standalone safety posture; toggle via `vm_deploy_managed_initial_disabled: false`).
6. **Constraints** — three families per VM, all idempotent (gated on `pcs constraint location <vm>` / `pcs constraint colocation` greps):
   - `prefers <target_host>=<score>` (score from `vm_catalog[].location_score | default(100)`)
   - `avoids <node>=INFINITY` for every node not in `allowed_hosts`
   - `colocation <vm> with <other> -INFINITY` when `vm_catalog[].anti_affinity_with` is set; only the alphabetically-first VM emits to avoid duplicates
7. **Verify** — `pcs resource config <name>` confirms the resource registered. When `vm_deploy_managed_initial_disabled: false`, additionally polls `pcs resource status <name>` until `Started`.

## Variables

| Name | Default | Mode | Notes |
|---|---|---|---|
| `vm_deploy_xml_dir` | `/etc/libvirt/qemu-vpac` | both | per-host XML output from vm_templates |
| `vm_deploy_managed_xml_dir` | `{{ ceph.cephfs_mountpoint }}` | managed | shared dir Pacemaker reads from |
| `vm_deploy_managed_mode` | auto: `len(vpac_nodes) >= 3 and stonith.enabled` | both | cluster-wide toggle |
| `vm_deploy_managed_initial_disabled` | `true` | managed | create resources `--disabled` |
| `vm_deploy_autostart` | `false` | standalone | start VMs after defining |
| `vm_deploy_autostart_on_boot` | `false` | standalone | `virsh autostart <name>` per VM |

Catalog fields (per-VM):

- `target_host` — preferred node (managed: `prefers <target_host>`)
- `allowed_hosts` — list of nodes that may run the VM (managed: `avoids` for everything else)
- `pacemaker_managed` *(optional)* — per-VM override of `vm_deploy_managed_mode`
- `location_score` *(optional, default 100)* — score for the `prefers` constraint; set to `INFINITY` for hard pin (PCI-passthrough VMs)
- `anti_affinity_with` *(optional)* — name of another VM that must NOT run on the same node
- `pci_passthrough` — non-empty disables migration auto-derived

## Operator helpers (managed mode)

Installed under `/usr/local/sbin/`:

- **`pcs-vm-move <vm> <target>`** — wraps `pcs resource move <vm> <target> && pcs resource clear <vm>`. Forgetting the clear leaves a permanent INFINITY constraint that disables HA failover until manually removed (the most-repeated documented operational mistake).
- **`pcs-vm-status <vm>`** — diagnostic: per-VM Pacemaker config + state + location constraints + per-node `virsh domstate`. Useful when the Pacemaker view doesn't match what virsh sees.

## Companion operational playbook

`playbooks/op-vm-undefine.yml` — retire a VM end-to-end (disable resource → wait Stopped → `pcs resource delete` → `virsh undefine` on every node). Double-guarded:

```bash
ansible-playbook -i inventory/<site> playbooks/op-vm-undefine.yml \
  -e vm_name=<vm> \
  -e i_want_to_destroy=yes
```

Backing disk image is preserved.

## Intentional v1 omissions (for managed mode)

- **VIPaddr2 + colocation pairs** — documented field deployment did not use these for production VMs; defer.
- **`pcs resource group`** — unused in documented field deployment; defer.
- **`priority` per-resource** — unused; defer.
- **Order constraints with Ceph** — Ceph isn't modelled as a Pacemaker resource; the cluster treats CephFS as external infra.
- **Empty disk provisioning** — operator provides qcow2 / RBD images out-of-band. A future `vm_provision` role or opt-in `create_empty_disk` mode would close this.

## Dependencies

- `vm_templates` (same stage) — XMLs at `vm_deploy_xml_dir` on every host
- `virtualization` (stage 30) — libvirt running
- `ceph_expand` (stage 60) — `vm_deploy_managed_xml_dir` mountpoint exists; libvirt cephx secret + sanlock chain in place for RBD VMs
- `pacemaker_base` (stage 70) — cluster up, resource defaults applied
- `stonith` (stage 75) — `stonith-enabled=true`

## Tags

- `vm`, `vm-deploy` — everything
- `vm-deploy-managed-precheck`, `vm-deploy-managed-helpers` — once-per-run managed setup
