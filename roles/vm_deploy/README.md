# vm_deploy

Stage 80 (second half). Defines each VM in `vm_catalog` as a standalone libvirt domain on its `target_host`, using the XMLs that `vm_templates` rendered.

## What it does (v1)

Loops over `vm_catalog`; for each entry where `target_host == inventory_hostname`:

1. Reads `{{ vm_deploy_xml_dir }}/<name>.xml` (produced by `vm_templates`).
2. `community.libvirt.virt: command=define` — idempotent. First run defines the domain; subsequent runs with the same XML are a no-op; subsequent runs with different XML update the persistent definition.
3. Optionally marks the domain autostart-on-boot (`vm_deploy_autostart_on_boot: true`).
4. Optionally starts the domain (`vm_deploy_autostart: true`). **Defaults to false in v1** — see safety posture below.

After this role runs, `sudo virsh list --all` on each cluster node shows the VMs it owns. `sudo virsh dumpxml <name>` shows the definition libvirt persisted.

## Safety posture (why defaults are conservative)

- **Does NOT auto-start VMs by default.** Operator runs `virsh start <name>` manually after confirming the backing qcow2 exists and the domain definition accepted cleanly. This avoids a fleet of VMs failing to boot on first run because disks weren't provisioned yet.
- **Does NOT undefine VMs that aren't in `vm_catalog`.** Removing a VM is an explicit, local operator decision. Silent deletion is how playbooks destroy production by accident.

Flip `vm_deploy_autostart: true` once the site is ready to bring workloads up.

## Variables

| Name | Default | Notes |
|---|---|---|
| `vm_deploy_xml_dir` | `/etc/libvirt/qemu-vpac` | must match `vm_templates_xml_dir` |
| `vm_deploy_autostart` | `false` | start VMs after defining |
| `vm_deploy_autostart_on_boot` | `false` | run `virsh autostart <name>` per VM |

Reads from `group_vars/all.yml`: `vm_catalog`.

## Non-clustered vs clustered

**v1 (today): non-clustered.** Each VM is a standalone libvirt domain. If the host goes down, the VM goes with it — no automatic failover. Appropriate for single-node deployments or for bringing up a cluster where Pacemaker isn't yet in scope.

**v2 (follow-up): Pacemaker-managed.** When the `stonith` role lands, `vm_deploy` will be extended (or split) to create each VM as an `ocf:heartbeat:VirtualDomain` Pacemaker resource with location constraints from `vm_catalog[].target_host` + `allowed_hosts`. At that point VMs auto-failover on node failure and `vm_deploy_autostart` becomes irrelevant (Pacemaker owns the start/stop decision).

## Dependencies

- `vm_templates` (same stage) — XMLs must exist under `vm_deploy_xml_dir`
- `virtualization` (stage 30) — libvirt up and running
- `ceph_expand` (stage 60) — `/vms/` mount exists for CephFS-backed disks
- `community.libvirt` collection (already in `requirements.yml`)

## Tags

- `vm`, `vm-deploy` — this role

## Operator workflow first time through

```bash
# 1. Run stage 80 (templates + deploy). Creates XMLs + defines domains, does NOT start.
ansible-playbook -i inventory/mysite site.yml --tags vm --ask-vault-pass

# 2. On each cluster node, confirm the XMLs landed and libvirt accepted them.
sudo ls /etc/libvirt/qemu-vpac/
sudo virsh list --all

# 3. Provision each VM's qcow2 (or copy in an existing image, or import an OVA).
sudo qemu-img create -f qcow2 /vms/ssc600-01.qcow2 40G

# 4. Start one VM by hand, confirm it boots.
sudo virsh start ssc600-01
sudo virsh console ssc600-01    # Ctrl+] to exit

# 5. Once satisfied, flip vm_deploy_autostart to true in inventory and re-run
#    stage 80 — the rest of the VMs start.
```
