# 03 — Register and enable repositories

The host requires four package repositories. Single-node deployments do not require the High Availability or Ceph repositories, as they include neither Pacemaker nor Ceph. They require `kernel-rt` (from the NFV repository) and several CodeReady Builder packages.

| Repository | Provides |
|---|---|
| `rhel-9-for-x86_64-baseos-rpms` | The base OS |
| `rhel-9-for-x86_64-appstream-rpms` | libvirt, qemu-kvm, tooling |
| `rhel-9-for-x86_64-nfv-rpms` | `kernel-rt` — the real-time kernel |
| `codeready-builder-for-rhel-9-x86_64-rpms` | Supporting build/runtime packages |

## Before you begin (connected sites)

Registering the host to Red Hat Subscription Manager requires an account, a subscription, and an activation key. If these are already in place, skip to the next section.

1. **Red Hat account and subscription.** The host must be covered by a RHEL subscription that includes the RT/NFV entitlement (the source of `kernel-rt`). Accounts and subscriptions are managed at [access.redhat.com](https://access.redhat.com). Registration concepts and methods are described in [Getting Started with RHEL System Registration](https://docs.redhat.com/en/documentation/subscription_central/1-latest/html-single/getting_started_with_rhel_system_registration/index).

2. **Activation key and organization ID.** An activation key (combined with the numeric organization ID) is the recommended way to register — it avoids embedding a username and password and supports automation. Create a key and read the organization ID on the **Activation Keys** page of the Red Hat Hybrid Cloud Console at [console.redhat.com](https://console.redhat.com) (Services → Activation Keys). The organization ID is a numeric identifier, separate from the account number. See [Getting started with activation keys on the Hybrid Cloud Console](https://docs.redhat.com/en/documentation/subscription_central/1-latest/html/getting_started_with_activation_keys_on_the_hybrid_cloud_console/index).

3. **Simple Content Access (SCA).** Red Hat accounts use [Simple Content Access](https://access.redhat.com/articles/simple-content-access) by default. Under SCA there is no subscription-attach step: registering grants content access, and repositories are enabled directly. `subscription-manager status` intentionally reports `Overall Status: Disabled` under SCA; this is expected and not an error.

## Connected path — register with RHSM

Register the host with the activation key and organization ID:

```bash
sudo subscription-manager register --org=<your-org-id> --activationkey=<your-key>
```

No `subscription-manager attach` step is required — SCA grants content access on registration.

Two alternatives:

- **Username/password** (interactive): `sudo subscription-manager register` and supply credentials when prompted.
- **`rhc connect`** registers and connects the host to Red Hat Insights / remote management in one step: `sudo rhc connect --organization=<your-org-id> --activation-key=<your-key>`.

Enable the four repositories:

```bash
sudo subscription-manager repos \
  --enable=rhel-9-for-x86_64-baseos-rpms \
  --enable=rhel-9-for-x86_64-appstream-rpms \
  --enable=rhel-9-for-x86_64-nfv-rpms \
  --enable=codeready-builder-for-rhel-9-x86_64-rpms
```

Verify the registration and the enabled repositories:

```bash
sudo subscription-manager identity                # confirms the system is registered
sudo subscription-manager repos --list-enabled | grep -E 'baseos|appstream|nfv|codeready'
dnf clean all && dnf repolist
```

The RHEL 9 registration procedure is documented in full in [Configuring basic system settings, Chapter 2 — Registering the system and managing subscriptions](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/9/html/configuring_basic_system_settings/assembly_registering-the-system-and-managing-subscriptions_configuring-basic-system-settings).

> **Air-gapped variant**
> The host cannot reach `subscription.rhsm.redhat.com` or the CDN. Two common options:
>
> **A. Red Hat Satellite.** Register to Satellite instead of the public portal:
> ```bash
> sudo rpm -Uvh http://<satellite-fqdn>/pub/katello-ca-consumer-latest.noarch.rpm
> sudo subscription-manager register --org=<org> --activationkey=<key>
> ```
> Enable the same four repository labels — Satellite serves them from its synced content.
>
> **B. A plain reposync mirror.** With BaseOS/AppStream/NFV/CRB mirrored to an internal web server, skip `subscription-manager` and add a repo file:
> ```ini
> # /etc/yum.repos.d/vpac-local.repo
> [baseos]
> name=RHEL 9 BaseOS (local mirror)
> baseurl=http://<mirror-host>/rhel9/baseos/
> enabled=1
> gpgcheck=1
> gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-redhat-release
>
> [appstream]
> name=RHEL 9 AppStream (local mirror)
> baseurl=http://<mirror-host>/rhel9/appstream/
> enabled=1
> gpgcheck=1
> gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-redhat-release
>
> [nfv]
> name=RHEL 9 NFV (local mirror)
> baseurl=http://<mirror-host>/rhel9/nfv/
> enabled=1
> gpgcheck=1
> gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-redhat-release
>
> [crb]
> name=RHEL 9 CodeReady Builder (local mirror)
> baseurl=http://<mirror-host>/rhel9/crb/
> enabled=1
> gpgcheck=1
> gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-redhat-release
> ```
> Ensure the mirror includes the **NFV** content, which provides `kernel-rt`; it is commonly omitted from partial mirrors.

## Update the base system

Update the host before installing additional packages, so the RT kernel and libvirt install onto a consistent base:

```bash
sudo dnf -y update
```

If the kernel was updated, rebooting can be deferred: step 04 installs `kernel-rt` and step 08 reboots into it, covering both. Rebooting now to pick up the latest stock kernel first is also acceptable.

Continue to [04 — Host baseline](04-host-baseline.md).
