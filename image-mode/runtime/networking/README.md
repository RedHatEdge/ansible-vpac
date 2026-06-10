# Single-node networking (image-mode, applied at deploy)

This is the **per-site** networking layer for a single-node vPAC host. The
`vpac-node` bootc image is generic and identical for every site; this play,
driven by a per-site variable file, makes a node site-specific at deploy time.
Nothing here is baked into the image — only the *tools* (`nmstate`,
NetworkManager) are baked.

## The layout it configures

Matches `docs/single-node-manual/05-networking.md`:

| Role | Configured as | Logical net |
|---|---|---|
| Management | a **bridge** (`br-mgmt`) the host IP lives on, so VMs attach and get addresses on the mgmt segment (like the cluster role). `manage: false` leaves it untouched (no VM attachment). | `mgmt` (bridge) |
| Station bus | a **linux bridge** the relay attaches to; the host holds the segment address (not the relay's) | `station-bus` (bridge) |
| Process bus | **reserved, UP, no host IP** — the relay takes it by **macvtap** | `process-bus` (macvtap) |
| PTP | a **dedicated NIC** with an IP, used only by `ptp4l` | — (host-only) |

> Setting `mgmt.manage: true` **reconfigures the management interface into a
> bridge** — the host IP moves from the NIC to `br-mgmt`. Do this at
> provisioning. nmstate rolls back if it can't confirm the apply.

A libvirt **logical network** is defined wherever a VM attaches, so the relay XML
references the network by name (`station-bus`, `process-bus`) and stays decoupled
from the site's physical interface names. The PTP NIC has none — nothing attaches
to it; it is host-only. Optional VLAN tags are supported on the station bus and
PTP NIC.

## Files

```
apply-networking.yml                 the play (run at deploy)
templates/nmstate-singlenode.yml.j2  renders the nmstate document
site-vars.example.yml                the per-site var file — copy and fill in
```

## Use

1. On the node, list interfaces: `ip -br link`.
2. Copy `site-vars.example.yml` to `site-vars.yml` and set the real interface
   names + addresses for the site.
3. Apply:

   ```bash
   ansible-playbook -i '<node-ip>,' apply-networking.yml \
       -e @site-vars.yml -u ansible --become
   ```

`nmstatectl apply` runs with `--timeout` and **rolls back automatically** if it
isn't confirmed — so a mistake on the management interface won't strand the
node. Management is left alone by default for the same reason.

## How this scales to a 3-node cluster

The same generic image is used on every node. For a cluster, the networking
contract grows to the bond-based `networking` role (mgmt/storage/station/
heartbeat/ptp with bonds + VLANs) driven by the cluster inventory — the same
"generic image + per-site vars" pattern, just a fuller variable set. This
single-node play is the minimal end of that spectrum.
