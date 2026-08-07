# Tests

Lightweight, infrastructure-free regression tests that exercise real role logic
against controlled inputs. Run with a plain Ansible install — no libvirt, no
cluster, no molecule driver.

## Running

```bash
ansible-playbook tests/regression/<test>.yml
```

A test passes when the final play summary shows `failed=0` and its closing
assert reports success. (Cases that verify an assertion *should* fire will print
`fatal: FAILED!` mid-run — that failure is caught by a `rescue` and counted as a
pass; only the final summary line is authoritative.)

## Tests

### `regression/test_linkdown_scope.yml`

Locks the stage-20 "linkdown route" assertion to the interfaces the networking
role manages. Regression guarded:

> The original check grepped the whole route table and flagged libvirt's NAT
> bridge `virbr0`, which connected-mode deployments keep by design (for the
> Windows `virt-install --network default` install path). Every stage-20 **re-run
> after stage 30** — which creates `virbr0` — then hard-failed a healthy cluster.

It drives `roles/networking/tasks/assert_linkdown_scope.yml` (the same file
production uses) across five cases: `virbr0` alone (must pass), a managed bridge
down (must fail), no linkdown (must pass), `virbr0` + storage bond down (flag
only the bond), and a shared-VLAN heartbeat `bond1.23` down (must fail).

**Integration-ordering caveat.** The bug only manifests when stage 20 runs
*after* stage 30 has created `virbr0`. A full `20 -> 30 -> 20` integration run
needs real NICs + libvirt and belongs in the lab, not here. This unit test locks
the assertion *logic*; if you add an integration/molecule scenario later, it MUST
run networking a second time after virtualization or it passes vacuously.
