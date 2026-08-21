#!/usr/bin/env python3
"""vPAC site form — local web form that writes inventory/<site>/ for you.

Run from anywhere inside the ansible-vpac checkout:

    python3 tools/site-form.py
    # then open the printed http://127.0.0.1:8765 in any browser

Stdlib only. Binds to localhost only. Writes the whole inventory tree —
hosts.yml (groups derived, bootstrap group == bootstrap variable, builder
group only when air-gapped), group_vars/all/main.yml (template-substituted
over the shipped example so comments/defaults survive; every substitution
is ASSERTED and the write aborts loudly if any anchor is missing),
group_vars/all/vault.yml (encrypted via ansible-vault), host_vars/ (empty
when nodes are homogeneous; per-node only when they differ).

`python3 tools/site-form.py --selfcheck` runs the section-coverage assert
and a dry-run substitution pass with dummy values, writing nothing.
"""
import http.server, json, os, re, shutil, subprocess, sys, tempfile, urllib.parse
try:
    from zoneinfo import available_timezones
    TZS = sorted(available_timezones())
except Exception:
    TZS = ["UTC"]

def tz_select_html():
    groups = {}
    for z in TZS:
        if "/" not in z: continue
        region, _, city = z.partition("/")
        groups.setdefault(region, []).append((z, city.replace("_", " ")))
    h = '<select name="site_timezone" id="tzsel">'
    for region in sorted(groups):
        h += '<optgroup label="%s">' % region
        for z, city in groups[region]:
            h += '<option value="%s">%s</option>' % (z, city)
        h += '</optgroup>'
    return h + '</select>'

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
EXAMPLE = os.path.join(REPO, "inventory", "example", "group_vars", "all", "main.yml")
PORT = 8765

# ---------------------------------------------------------------- coverage
# Section coverage is DERIVED from the contract, not hand-listed: every
# `# N. Title` header must map to a form step or an explicit exclusion.
COVERED = {"1": "step 1 Site", "2": "step 2 Mode", "3": "step 2 Mode (sources)",
           "4": "step 6 Secrets", "5": "step 3 Nodes", "6": "step 4 Networks",
           "6b": "derived by the contract itself (not operator input)",
           "7": "step 4b Timing", "8": "step 5b Real-time", "9": "steps 3+5 (bootstrap, disks)",
           "10": "vault (hacluster) + defaults", "11": "step 3 (BMC) + vault",
           "14": "step 7 Thresholds"}
EXCLUDED = {"12": "vm_catalog — cluster-only default; the form writes []",
            "13": "builder ISO minting — air-gapped guide, hand-configured for now"}

SITE_RX = re.compile(r"^[a-z0-9][a-z0-9_-]{1,30}$")
def valid_site(name):
    return bool(SITE_RX.match(name or ""))

def contract_text():
    if not os.path.exists(EXAMPLE):
        sys.exit("FATAL: run from an ansible-vpac checkout (missing %s)" % EXAMPLE)
    return open(EXAMPLE).read()

def assert_coverage(text):
    headers = re.findall(r"^# (\d+b?)\. (.+)$", text, re.M)
    missing = [(n, t) for n, t in headers if n not in COVERED and n not in EXCLUDED]
    if missing:
        sys.exit("FATAL: contract sections with NO form step and NO declared "
                 "exclusion (add a step or an exclusion before shipping): %s" % missing)
    return headers

# ------------------------------------------------------------ substitution
class Sub:
    def __init__(self, label, pattern, repl, flags=re.M):
        self.label, self.rx, self.repl = label, re.compile(pattern, flags), repl

def scoped(text, block_rx, inner_rx, repl, misses, label):
    """Replace inner_rx once inside the block matched by block_rx."""
    m = re.search(block_rx, text, re.M)
    if not m:
        misses.append(label + " (block anchor)"); return text
    seg, n = re.subn(inner_rx, repl, m.group(0), count=1, flags=re.M)
    if n == 0:
        misses.append(label); return text
    return text[:m.start()] + seg + text[m.end():]

def build_main_yml(f, base):
    """f = form dict. Returns (content, misses list)."""
    misses = []
    t = base
    def sub(label, pattern, repl):
        nonlocal t
        t, n = re.subn(pattern, repl, t, count=1, flags=re.M)
        if n == 0:
            misses.append(label)
    e = re.escape
    sub("site_name", r'^site_name: ".*?"', 'site_name: "%s"' % f["site_name"])
    sub("site_domain", r'^site_domain: ".*?"', 'site_domain: "%s"' % f["site_domain"])
    sub("site_timezone", r'^site_timezone: ".*?"', 'site_timezone: "%s"' % f["site_timezone"])
    dns = "\n".join("  - %s" % d for d in f["dns"]) or "  - 8.8.8.8"
    sub("site_dns_servers", r'^site_dns_servers:\n(?:  - .*\n)+', "site_dns_servers:\n%s\n" % dns)
    sub("deployment_mode", r'^deployment_mode: ".*?"', 'deployment_mode: "%s"' % f["mode"])
    if f["mode"] == "connected":
        sub("registry_credentials_file", r'^  registry_credentials_file: .*$',
            '  registry_credentials_file: "%s"' % f["reg_creds_path"])
    if f["mode"] == "airgapped":
        sub("repo_source", r'^  repo_source: ".*?"', '  repo_source: "local_mirror"')
        sub("local_mirror_url", r'^  local_mirror_url: ".*?"', '  local_mirror_url: "%s"' % f["mirror_url"])
        sub("container_registry", r'^  container_registry: ".*?"', '  container_registry: "%s"' % f["registry"])
        sub("registry_insecure", r'^  container_registry_insecure: \S+', '  container_registry_insecure: true')
    # nodes block: full generated replacement (one asserted sub)
    nodes_yaml = "vpac_nodes:\n"
    for n in f["nodes"]:
        nodes_yaml += ('  - hostname: "%(host)s"\n    mgmt_ip: "%(mgmt)s"\n'
                       '    storage_ip: "%(storage)s"\n    station_ip: "%(station)s"\n'
                       '    heartbeat_ip: "%(hb)s"\n    bmc_ip: "%(bmc)s"\n'
                       '    bmc_type: "%(bmc_type)s"\n    bmc_user: "%(bmc_user)s"\n'
                       '    bmc_password: "{{ vault_bmc_password_%(letter)s | default(\'\') }}"\n') % n
    sub("vpac_nodes block", r'^vpac_nodes:\n(?:  .*\n|\n)+?(?=^# -)', nodes_yaml + "\n")
    # networks cidr/vlan per subnet
    for net in ("mgmt", "storage", "station", "heartbeat", "bmc"):
        blk = r'^  %s:\n(?:    .*\n)+' % net
        t = scoped(t, blk, r'^(    cidr: ").*?(")', r'\g<1>%s\g<2>' % f["net"][net]["cidr"], misses, "networks.%s.cidr" % net)
        v = f["net"][net]["vlan"]
        t = scoped(t, blk, r'^(    vlan: )\S+', r'\g<1>%s' % (v if v else "null"), misses, "networks.%s.vlan" % net)
    # timing (4b)
    t = scoped(t, r'^time_sync:\n(?:  .*\n|\n)+?(?=^# )', r'^(  mode: ").*?(")', r'\g<1>%s\g<2>' % f["ts_mode"], misses, "time_sync.mode")
    for key, val, quote in (("domain", f["ptp_domain"], False), ("transport", f["ptp_transport"], True),
                            ("delay_mechanism", f["ptp_delay"], True), ("profile", f["ptp_profile"], True)):
        pat = r'^(    %s: ").*?(")' % key if quote else r'^(    %s: )\S+' % key
        rep = (r'\g<1>%s\g<2>' if quote else r'\g<1>%s') % val
        t = scoped(t, r'^  ptp:\n(?:    .*\n)+', pat, rep, misses, "time_sync.ptp.%s" % key)
    sub("ptp_nic", r'^  ptp_nic: ".*?"', '  ptp_nic: "%s"' % f["ptp_nic"])
    # NIC-to-role bond mapping (from the operator's typed NIC lists — the
    # collected names are now CONSUMED, not discarded). Homogeneous default:
    # one mapping for all nodes; differing nodes stay a host_vars task.
    for _mk, _bn in (("map_mgmt", "mgmt_bond"), ("map_storage", "storage_bond"),
                     ("map_station", "station_bond")):
        _members = "[%s]" % ", ".join('"%s"' % m for m in f[_mk])
        _blk = r'^  %s:\n(?:    .*\n)+' % _bn
        t = scoped(t, _blk, r'^(    members: )\[.*\]$', r'\g<1>%s' % _members,
                   misses, "networking_defaults.%s.members" % _bn)
        if _bn in ("mgmt_bond", "station_bond") and f[_mk]:
            t = scoped(t, _blk, r'^(      primary: ").*(")', r'\g<1>%s\g<2>' % f[_mk][0],
                       misses, "networking_defaults.%s.primary" % _bn)
    # Heartbeat mode — BOTH keys written from the one explicit answer, so
    # the VLAN and the mode selector can never disagree (HF-5).
    if f["hb_mode"] == "shared":
        # `    shared_bond:` at 4-space indent is unique in the contract —
        # a plain anchored sub is sufficient and survives comment churn.
        sub("networks.heartbeat.shared_bond", r'^    shared_bond: \S+',
            '    shared_bond: "%s"' % f["hb_bond"])
        sub("heartbeat_nic", r'^  heartbeat_nic: ".*?"', '  heartbeat_nic: ""')
    else:
        sub("heartbeat_nic", r'^  heartbeat_nic: ".*?"', '  heartbeat_nic: "%s"' % f["hb_nic"])
    # real-time (5b)
    sub("isolated_cpus", r'^  isolated_cpus: ".*?"', '  isolated_cpus: "%s"' % f["isolated_cpus"])
    sub("hugepage_size", r'^  hugepage_size: "\S+"', '  hugepage_size: "%s"' % f["hugepage_size"])
    # ceph
    sub("ceph.public_network", r'^  public_network: ".*?"', '  public_network: "%s"' % f["net"]["storage"]["cidr"])
    sub("ceph.cluster_network", r'^  cluster_network: ".*?"', '  cluster_network: "%s"' % f["net"]["storage"]["cidr"])
    sub("ceph.bootstrap_node", r'^  bootstrap_node: ".*?"', '  bootstrap_node: "%s"' % f["bootstrap"])
    osd = "  osd_devices:\n"
    for n in f["nodes"]:
        osd += '    %s:\n' % n["host"] + "".join('      - "%s"\n' % d for d in n["disks"])
    sub("osd_devices block", r'^  osd_devices:\n(?:    .*\n)+', osd)
    # (registry_credentials_file is substituted earlier, from the operator's
    # reg_creds_path field — previously hardcoded here.)
    # stonith
    sub("fence_agent", r'^  fence_agent: "\S+"', '  fence_agent: "%s"' % f["fence_agent"])
    # thresholds (step 7)
    sub("storage_nic_min_mbps", r'^  storage_nic_min_mbps: \d+', '  storage_nic_min_mbps: %s' % f["storage_mbps"])
    sub("cyclictest_max_latency_us", r'^  cyclictest_max_latency_us: \d+', '  cyclictest_max_latency_us: %s' % f["cyclictest_us"])
    return t, misses

def build_hosts_yml(f):
    hosts = [n["host"] for n in f["nodes"]]
    def grp(names): return "".join("        %s:\n" % h for h in names)
    out = ("---\n# Generated by tools/site-form.py — groups are DERIVED from your\n"
           "# answers; the ceph_bootstrap_node group always equals ceph.bootstrap_node.\n"
           "all:\n  vars:\n    ansible_ssh_private_key_file: %s\n  children:\n" % f["ssh_key"])
    if f["mode"] == "airgapped":
        out += "    builder:\n      hosts:\n        %s:\n" % f["builder_host"]
    for g in ("vpac_cluster", "rt_hosts", "ceph_nodes", "pacemaker_cluster"):
        out += "    %s:\n      hosts:\n%s" % (g, grp(hosts))
    out += "    ceph_bootstrap_node:\n      hosts:\n        %s:\n" % f["bootstrap"]
    out += "  hosts:\n"
    if f["mode"] == "airgapped":
        out += "    %s:\n      ansible_host: %s\n      ansible_user: %s\n" % (f["builder_host"], f["builder_ip"], f["ssh_user"])
    for n in f["nodes"]:
        out += "    %s:\n      ansible_host: %s\n      ansible_user: %s\n" % (n["host"], n["mgmt"], f["ssh_user"])
    return out

def build_vault(f):
    v = "---\n"
    for k in ("rhsm_activation_key", "rhsm_org_id", "redhat_registry_username",
              "redhat_registry_password", "hacluster_password"):
        v += 'vault_%s: "%s"\n' % (k, f["vault"][k])
    for n in f["nodes"]:
        v += 'vault_bmc_password_%s: "%s"\n' % (n["letter"], n["bmc_pw"])
    return v

def write_site(f):
    """Returns (ok, message, files)."""
    base = contract_text()
    main_yml, misses = build_main_yml(f, base)
    if misses:
        return False, ("REFUSING TO WRITE — %d substitution anchor(s) did not match the "
                       "shipped contract (a renamed key must abort, never silently ship "
                       "the example default): %s" % (len(misses), ", ".join(misses))), []
    site_dir = os.path.join(REPO, "inventory", f["site_name"])
    if os.path.exists(site_dir):
        return False, "inventory/%s already exists — this form writes NEW sites only; move or remove it first." % f["site_name"], []
    stage = site_dir + ".staging"
    shutil.rmtree(stage, ignore_errors=True)
    try:
        os.makedirs(os.path.join(stage, "group_vars", "all"))
        os.makedirs(os.path.join(stage, "host_vars"))
        files = []
        def put(rel, content):
            p = os.path.join(stage, rel); open(p, "w").write(content); files.append("inventory/%s/%s" % (f["site_name"], rel))
        put("hosts.yml", build_hosts_yml(f))
        put(os.path.join("group_vars", "all", "main.yml"), main_yml)
        # host_vars: homogeneous => empty commented files; else per-node NICs
        homog = len({tuple(n["nics"]) for n in f["nodes"]}) == 1
        for n in f["nodes"]:
            if homog:
                put(os.path.join("host_vars", "%s.yml" % n["host"]),
                    "---\n# Intentionally empty: nodes are homogeneous; group-scope\n"
                    "# networking_defaults covers this node. Add per-node overrides here\n"
                    "# only if this node's hardware diverges.\n")
            else:
                put(os.path.join("host_vars", "%s.yml" % n["host"]),
                    "---\n# Per-node NIC names (nodes differ across the cluster).\n"
                    "# Map each to its role in networking_defaults-style overrides —\n"
                    "# see the networking role README.\nnode_nics:  # informational list from the form\n"
                    + "".join('  - "%s"\n' % x for x in n["nics"]))
        # vault (encrypted)
        vpath = os.path.join(stage, "group_vars", "all", "vault.yml")
        open(vpath, "w").write(build_vault(f))
        with tempfile.NamedTemporaryFile("w", delete=False) as pw:
            pw.write(f["vault_password"]); pwfile = pw.name
        try:
            r = subprocess.run(["ansible-vault", "encrypt", "--vault-password-file", pwfile, vpath],
                               capture_output=True, text=True)
        finally:
            os.unlink(pwfile)
        if r.returncode != 0:
            raise RuntimeError("ansible-vault encrypt failed: %s" % (r.stderr.strip() or r.stdout.strip()))
        files.append("inventory/%s/group_vars/all/vault.yml (ENCRYPTED)" % f["site_name"])
        os.rename(stage, site_dir)
        return True, "written", files
    except Exception as ex:
        shutil.rmtree(stage, ignore_errors=True)
        return False, "REFUSED / rolled back — nothing was written: %s" % ex, []

# ------------------------------------------------------------------- form
def parse(qs):
    d = urllib.parse.parse_qs(qs, keep_blank_values=True)
    g = lambda k, dflt="": d.get(k, [dflt])[0].strip()
    letters = ("a", "b", "c")
    nodes = []
    for i, L in enumerate(letters, 1):
        nics = [x.strip() for x in g("n%d_nics" % i).split(",") if x.strip()]
        disks = [x.strip() for x in g("n%d_disks" % i).splitlines() if x.strip()]
        nodes.append(dict(host=g("n%d_host" % i), mgmt=g("n%d_mgmt" % i), storage=g("n%d_storage" % i),
                          station=g("n%d_station" % i), hb=g("n%d_hb" % i), bmc=g("n%d_bmc" % i),
                          bmc_type=g("n%d_bmc_type" % i, "idrac9"), bmc_user=g("n%d_bmc_user" % i, "admin"),
                          bmc_pw=g("n%d_bmc_pw" % i), nics=nics, disks=disks, letter=L))
    net = {}
    for k in ("mgmt", "storage", "station", "heartbeat", "bmc"):
        net[k] = dict(cidr=g("net_%s_cidr" % k), vlan=g("net_%s_vlan" % k))
    f = dict(site_name=g("site_name"), site_domain=g("site_domain"), site_timezone=g("site_timezone", "UTC"),
             dns=[x.strip() for x in g("dns").split(",") if x.strip()], mode=g("mode", "connected"),
             mirror_url=g("mirror_url"), registry=g("registry"), builder_host=g("builder_host"),
             builder_ip=g("builder_ip"), nodes=nodes, net=net, bootstrap=g("bootstrap"),
             ts_mode=g("ts_mode", "ptp"), ptp_domain=g("ptp_domain", "0"), ptp_transport=g("ptp_transport", "L2"),
             ptp_delay=g("ptp_delay", "P2P"), ptp_profile=g("ptp_profile", "default"), ptp_nic=g("ptp_nic"),
             isolated_cpus=g("isolated_cpus"), cpu_count=g("cpu_count"), hugepage_size=g("hugepage_size", "1G"),
             fence_agent=g("fence_agent", "fence_ipmilan"), storage_mbps=g("storage_mbps", "10000"),
             cyclictest_us=g("cyclictest_us", "120"), ssh_user=g("ssh_user", "admin"),
             ssh_key=g("ssh_key", "~/.ssh/id_ed25519"), vault_password=g("vault_password"),
             reg_creds_path=g("reg_creds_path", "/root/ceph-registry.json"),
             map_mgmt=[x.strip() for x in g("map_mgmt").split(",") if x.strip()],
             map_storage=[x.strip() for x in g("map_storage").split(",") if x.strip()],
             map_station=[x.strip() for x in g("map_station").split(",") if x.strip()],
             hb_mode=g("hb_mode"), hb_bond=g("hb_bond", "storage"), hb_nic=g("hb_nic"),
             vault=dict(rhsm_activation_key=g("v_rhsm_key"), rhsm_org_id=g("v_rhsm_org"),
                        redhat_registry_username=g("v_reg_user"), redhat_registry_password=g("v_reg_pw"),
                        hacluster_password=g("v_hacluster")))
    return f

def validate(f):
    errs = []
    if not valid_site(f["site_name"]):
        errs.append("site name: lowercase letters/digits/dash, becomes the directory name")
    hosts = [n["host"] for n in f["nodes"]]
    if len(set(hosts)) != 3 or "" in hosts: errs.append("three distinct node hostnames are required")
    if f["bootstrap"] not in hosts: errs.append("bootstrap node must be one of the three hostnames")
    for n in f["nodes"]:
        for d in n["disks"]:
            if not d.startswith("/dev/disk/by-"):
                errs.append("%s: OSD device '%s' is not a stable /dev/disk/by-id/ path" % (n["host"], d))
        if not n["disks"]: errs.append("%s: at least one OSD by-id device required" % n["host"])
        if not n["bmc_pw"] and f["fence_agent"] == "fence_ipmilan":
            errs.append("%s: BMC password required for fence_ipmilan" % n["host"])
        if "." in n["host"]:
            errs.append("%s: hostname must be the SHORT name — the DNS domain '%s' is appended "
                        "automatically; an FQDN here doubles the domain in every /etc/hosts entry"
                        % (n["host"], f["site_domain"]))
    # NIC role mapping must AGREE with the typed per-node NIC lists (the
    # agreement-pattern family: disagreement is rejected here as UX, and
    # preflight re-asserts it as the sole enforcer).
    _rolemap = {"mgmt bond": f["map_mgmt"], "storage bond": f["map_storage"],
                "station bond": f["map_station"]}
    _assigned = []
    for _role, _members in _rolemap.items():
        if not _members:
            errs.append("%s members: at least one NIC required (single-member bonds are fine)" % _role)
        for _m in _members:
            for n in f["nodes"]:
                if n["nics"] and _m not in n["nics"]:
                    errs.append("%s member '%s' is not in %s's NIC list (%s) — every member must "
                                "exist on every node" % (_role, _m, n["host"], ", ".join(n["nics"])))
            if _m in _assigned:
                errs.append("NIC '%s' is assigned to two roles — one role per NIC" % _m)
            _assigned.append(_m)
    if f["ptp_nic"] and f["ptp_nic"] in _assigned:
        errs.append("PTP NIC '%s' is also a bond member — the PTP NIC must stay out of all bonds" % f["ptp_nic"])
    # Heartbeat mode: an explicit choice, both keys written from it.
    if f["hb_mode"] not in ("shared", "dedicated"):
        errs.append("Heartbeat 'runs on' choice is required — shared (VLAN on a bond) or dedicated NIC")
    elif f["hb_mode"] == "shared":
        _hbv = f["net"]["heartbeat"]["vlan"]
        if not _hbv:
            errs.append("shared heartbeat mode needs the heartbeat VLAN (Networks section)")
        elif _hbv == f["net"]["storage"]["vlan"]:
            errs.append("heartbeat VLAN must differ from the storage VLAN — same VLAN = one broadcast "
                        "domain = no ring separation")
    else:
        if not f["hb_nic"]:
            errs.append("dedicated heartbeat mode needs the heartbeat NIC name")
        else:
            if f["hb_nic"] in _assigned:
                errs.append("heartbeat NIC '%s' is also a bond member — dedicated mode needs its own port" % f["hb_nic"])
            if f["hb_nic"] == f["ptp_nic"]:
                errs.append("heartbeat NIC and PTP NIC cannot be the same port")
            for n in f["nodes"]:
                if n["nics"] and f["hb_nic"] not in n["nics"]:
                    errs.append("heartbeat NIC '%s' is not in %s's NIC list" % (f["hb_nic"], n["host"]))
    if f["cpu_count"]:
        if not f["isolated_cpus"].strip():
            errs.append("cpu_count is set but isolated_cpus is blank — the two fields are "
                        "validated TOGETHER: either fill the isolated CPUs (e.g. 4-11) or "
                        "clear cpu_count")
        try:
            top = max(int(x) for part in f["isolated_cpus"].split(",") for x in part.split("-"))
            if top >= int(f["cpu_count"]):
                errs.append("isolated_cpus references CPU %d but this machine reports only %s CPUs "
                            "(isolcpus would SILENTLY ignore the extras)" % (top, f["cpu_count"]))
        except ValueError:
            errs.append("isolated_cpus: use forms like 4-11 or 2,4-7")
    if f["site_timezone"] not in TZS:
        errs.append("timezone '%s' is not a valid zone — pick from the dropdown" % f["site_timezone"])
    kp = os.path.expanduser(f["ssh_key"])
    if not os.path.exists(kp):
        errs.append("SSH key '%s' does not exist on this laptop — use 'Create a dedicated key' below, "
                    "or point at a real key file" % f["ssh_key"])
    if len(f["vault_password"]) < 8: errs.append("vault password: 8+ characters")
    if not f["vault"]["hacluster_password"]: errs.append("hacluster password is required (3-node cluster)")
    if f["mode"] == "airgapped" and not (f["builder_host"] and f["builder_ip"] and f["mirror_url"]):
        errs.append("air-gapped mode needs builder hostname, IP and mirror URL")
    return errs

PAGE_HEAD = """<!doctype html><html><head><meta charset="utf-8"><title>vPAC Cluster Builder</title><style>
/* PatternFly-derived tokens (inline; no CDN — offline/air-gap safe). */
:root{--rh-red:#EE0000;--rh-red-dark:#B1380B;--bg:#FFFFFF;--surface:#F2F2F2;
--text:#151515;--muted:#6A6E73;--border:#D2D2D2;--ok-bg:#F3FAF2;--ok-bd:#3E8635;
--err-bg:#FAEAE8;--err-bd:#C9190B}
/* Logo wrappers size/switch the mark externally; the SVGs themselves are the
   official assets, unmodified (see header comment). Base rules must stay ABOVE
   the dark media query or its equal-specificity override loses the cascade. */
.rh-logo{display:flex;align-items:center}
.rh-logo svg{height:34px;width:auto}
.rh-logo-dark{display:none}
@media (prefers-color-scheme: dark){:root{--bg:#151515;--surface:#212427;
--text:#F0F0F0;--muted:#B8BBBE;--border:#444548;--ok-bg:#1e2b1c;--err-bg:#3b1f1b}
.rh-logo-light{display:none}.rh-logo-dark{display:flex}}
body{font:15px/1.5 "RedHatText","Red Hat Text",system-ui,-apple-system,"Segoe UI",sans-serif;
max-width:920px;margin:2em auto;padding:0 1em;color:var(--text);background:var(--bg)}
h1,h4,legend{font-family:"RedHatDisplay","Red Hat Display",system-ui,sans-serif}
h1{border-bottom:3px solid var(--rh-red);padding-bottom:.3em}
fieldset{margin:1.2em 0;border:1px solid var(--border);border-radius:3px;
padding:1em 1.2em;background:var(--surface)}
legend{font-weight:700;padding:0 .5em;background:var(--bg);border:1px solid var(--border);border-radius:3px}
label{display:block;margin:.6em 0 .15em;font-weight:500}
input,select,textarea{width:100%;max-width:480px;padding:.4em .5em;font:inherit;
color:var(--text);background:var(--bg);border:1px solid var(--border);border-radius:3px}
input:focus,select:focus,textarea:focus{outline:2px solid var(--rh-red);outline-offset:0}
.hint{color:var(--muted);font-size:.85em;margin:.15em 0 .45em}
.node{display:inline-block;vertical-align:top;width:31%;margin-right:1%}
.err{background:var(--err-bg);border:1px solid var(--err-bd);padding:1em;border-radius:3px}
.ok{background:var(--ok-bg);border:1px solid var(--ok-bd);padding:1em;border-radius:3px}
code{background:var(--surface);border:1px solid var(--border);padding:0 .3em;border-radius:2px}
button{font:inherit;font-weight:600;padding:.55em 1.6em;margin-top:.8em;cursor:pointer;
color:#fff;background:var(--rh-red);border:none;border-radius:3px}
button:hover{background:var(--rh-red-dark)} button:disabled{opacity:.55;cursor:wait}
</style></head><body>
<div id="errbanner" style="display:none;background:var(--err-bg);border:2px solid var(--err-bd);padding:.8em;border-radius:3px;position:sticky;top:0;z-index:9"></div>
<div style="display:flex;align-items:center;gap:1em;border-bottom:3px solid var(--rh-red);padding-bottom:.5em;margin-bottom:.5em">
<!-- Official Red Hat lockups, inlined verbatim from Red Hat's brand-assets
     CDN (static.redhat.com/libs/redhat/brand-assets/2/corp/). Do NOT edit,
     recolour, or restyle the SVGs themselves; size/theme-switching lives on
     the wrapper spans only. Light shows the standard mark, dark the reverse. -->
<span class="rh-logo rh-logo-light" role="img" aria-label="Red Hat"><svg id="b6bdd2b4-52ab-488a-9a30-1e6d1d7dd2d4" data-name="Layer 1" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 611.8 144"><defs><style>.a737459c-e8c7-4afa-8008-f6cfd15ccda2{fill:#e00;}</style></defs><path d="M579.3,92.3c0,11.9,7.2,17.7,20.2,17.7a53.39,53.39,0,0,0,11.9-1.7V94.5a25.27,25.27,0,0,1-7.7,1.2c-5.4,0-7.4-1.7-7.4-6.7V67.8h15.6V53.6H596.3v-18l-17,3.7V53.6H568V67.8h11.2l.1,24.5Zm-53,.3c0-3.7,3.7-5.5,9.3-5.5a38.35,38.35,0,0,1,10.1,1.3v7.2a20.82,20.82,0,0,1-10.6,2.6c-5.5,0-8.8-2.1-8.8-5.6m5.2,17.6a26.69,26.69,0,0,0,15.4-4.3v3.4h16.8V73.6c0-13.6-9.1-21-24.4-21-8.5,0-16.9,2-26,6.1l6.1,12.5c6.5-2.7,12-4.4,16.8-4.4,7,0,10.6,2.7,10.6,8.3v2.7a48.92,48.92,0,0,0-12.6-1.6c-14.3,0-22.9,6-22.9,16.7,0,9.8,7.8,17.3,20.2,17.3m-92.4-.9h18.1V80.4h30.3v28.8h18.1V35.6H487.5V63.9H457.2V35.6H439.1ZM370.2,81.4c0-8,6.3-14.1,14.6-14.1a17.72,17.72,0,0,1,11.8,4.3V91.1a16.62,16.62,0,0,1-11.8,4.5c-8.2-.1-14.6-6.2-14.6-14.2m26.6,27.9h16.8V31.9l-17,3.7V56.5a28.14,28.14,0,0,0-14.2-3.7c-16.2,0-28.9,12.5-28.9,28.5a28.25,28.25,0,0,0,27.9,28.6h.5a25.46,25.46,0,0,0,14.9-4.8ZM319.6,66.5c5.4,0,9.9,3.5,11.7,8.8H308.1a11.56,11.56,0,0,1,11.5-8.8m-28.7,15c0,16.2,13.2,28.8,30.3,28.8,9.4,0,16.2-2.5,23.2-8.4l-11.3-10c-2.6,2.7-6.5,4.2-11.1,4.2a14.37,14.37,0,0,1-13.7-8.8h39.6V83.1c0-17.7-11.9-30.4-28.1-30.4a28.58,28.58,0,0,0-29,28.1,1.48,1.48,0,0,1,.1.7M261.6,51.1c6,0,9.4,3.8,9.4,8.3s-3.4,8.3-9.4,8.3H243.7V51.1Zm-36,58.1h18.1V82.4h13.8l13.9,26.8h20.2L275.4,79.7A22.32,22.32,0,0,0,289.3,59c0-13.2-10.4-23.5-26-23.5H225.6v73.7Z" transform="translate(-0.1)"/><path class="a737459c-e8c7-4afa-8008-f6cfd15ccda2" d="M127.1,83c12.5,0,30.6-2.6,30.6-17.5a19.53,19.53,0,0,0-.3-3.4L150,29.7c-1.7-7.1-3.2-10.4-15.7-16.6C124.6,8.1,103.5,0,97.2,0c-5.9,0-7.6,7.5-14.5,7.5C76,7.5,71.1,1.9,64.8,1.9c-6,0-9.9,4.1-12.9,12.5,0,0-8.4,23.7-9.5,27.2a6.15,6.15,0,0,0-.2,1.9c-.1,9.2,36.2,39.4,84.9,39.5m32.5-11.4c1.7,8.2,1.7,9.1,1.7,10.1,0,14-15.7,21.8-36.4,21.8-46.8,0-87.7-27.4-87.7-45.5a18.35,18.35,0,0,1,1.5-7.3C21.9,51.5.1,54.5.1,73.7.1,105.2,74.7,144,133.7,144c45.3,0,56.7-20.5,56.7-36.7,0-12.7-11-27.1-30.8-35.7" transform="translate(-0.1)"/><path d="M159.6,71.6c1.7,8.2,1.7,9.1,1.7,10.1,0,14-15.7,21.8-36.4,21.8-46.8,0-87.7-27.4-87.7-45.5a18.35,18.35,0,0,1,1.5-7.3l3.7-9.1a6.15,6.15,0,0,0-.2,1.9c0,9.2,36.3,39.4,84.9,39.4,12.5,0,30.6-2.6,30.6-17.5a19.53,19.53,0,0,0-.3-3.4Z" transform="translate(-0.1)"/></svg></span>
<span class="rh-logo rh-logo-dark" role="img" aria-label="Red Hat"><svg id="Layer_1" data-name="Layer 1" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 613 145"><defs><style>.cls-1{fill:#e00;}.cls-2{fill:#fff;}</style></defs><title>RedHat-Logo-A-Reverse</title><path class="cls-1" d="M127.47,83.49c12.51,0,30.61-2.58,30.61-17.46a14,14,0,0,0-.31-3.42l-7.45-32.36c-1.72-7.12-3.23-10.35-15.73-16.6C124.89,8.69,103.76.5,97.51.5,91.69.5,90,8,83.06,8c-6.68,0-11.64-5.6-17.89-5.6-6,0-9.91,4.09-12.93,12.5,0,0-8.41,23.72-9.49,27.16A6.43,6.43,0,0,0,42.53,44c0,9.22,36.3,39.45,84.94,39.45M160,72.07c1.73,8.19,1.73,9.05,1.73,10.13,0,14-15.74,21.77-36.43,21.77C78.54,104,37.58,76.6,37.58,58.49a18.45,18.45,0,0,1,1.51-7.33C22.27,52,.5,55,.5,74.22c0,31.48,74.59,70.28,133.65,70.28,45.28,0,56.7-20.48,56.7-36.65,0-12.72-11-27.16-30.83-35.78"/><path d="M160,72.07c1.73,8.19,1.73,9.05,1.73,10.13,0,14-15.74,21.77-36.43,21.77C78.54,104,37.58,76.6,37.58,58.49a18.45,18.45,0,0,1,1.51-7.33l3.66-9.06A6.43,6.43,0,0,0,42.53,44c0,9.22,36.3,39.45,84.94,39.45,12.51,0,30.61-2.58,30.61-17.46a14,14,0,0,0-.31-3.42Z"/><path class="cls-2" d="M579.74,92.8c0,11.89,7.15,17.67,20.19,17.67a52.11,52.11,0,0,0,11.89-1.68V95a24.84,24.84,0,0,1-7.68,1.16c-5.37,0-7.36-1.68-7.36-6.73V68.3h15.56V54.1H596.78v-18l-17,3.68V54.1H568.49V68.3h11.25Zm-53,.32c0-3.68,3.69-5.47,9.26-5.47a43.12,43.12,0,0,1,10.1,1.26v7.15a21.51,21.51,0,0,1-10.63,2.63c-5.46,0-8.73-2.1-8.73-5.57m5.2,17.56c6,0,10.84-1.26,15.36-4.31v3.37h16.82V74.08c0-13.56-9.14-21-24.39-21-8.52,0-16.94,2-26,6.1l6.1,12.52c6.52-2.74,12-4.42,16.83-4.42,7,0,10.62,2.73,10.62,8.31v2.73a49.53,49.53,0,0,0-12.62-1.58c-14.31,0-22.93,6-22.93,16.73,0,9.78,7.78,17.24,20.19,17.24m-92.44-.94h18.09V80.92h30.29v28.82H506V36.12H487.93V64.41H457.64V36.12H439.55ZM370.62,81.87c0-8,6.31-14.1,14.62-14.1A17.22,17.22,0,0,1,397,72.09V91.54A16.36,16.36,0,0,1,385.24,96c-8.2,0-14.62-6.1-14.62-14.09m26.61,27.87h16.83V32.44l-17,3.68V57.05a28.3,28.3,0,0,0-14.2-3.68c-16.19,0-28.92,12.51-28.92,28.5a28.25,28.25,0,0,0,28.4,28.6,25.12,25.12,0,0,0,14.93-4.83ZM320,67c5.36,0,9.88,3.47,11.67,8.83H308.47C310.15,70.3,314.36,67,320,67M291.33,82c0,16.2,13.25,28.82,30.28,28.82,9.36,0,16.2-2.53,23.25-8.42l-11.26-10c-2.63,2.74-6.52,4.21-11.14,4.21a14.39,14.39,0,0,1-13.68-8.83h39.65V83.55c0-17.67-11.88-30.39-28.08-30.39a28.57,28.57,0,0,0-29,28.81M262,51.58c6,0,9.36,3.78,9.36,8.31S268,68.2,262,68.2H244.11V51.58Zm-36,58.16h18.09V82.92h13.77l13.89,26.82H292l-16.2-29.45a22.27,22.27,0,0,0,13.88-20.72c0-13.25-10.41-23.45-26-23.45H226Z"/></svg></span>
<h1 style="border:none;margin:0;padding:0;font-size:24px;line-height:1">vPAC Cluster Builder</h1></div>
<p>Fill this out top to bottom; it writes your whole site inventory, including the
encrypted vault. Nothing is written until you press the button at the end, and if
anything is wrong it refuses and tells you why. Every "how do I find this?" hint
is a command you run on the server itself.</p>"""

import hashlib as _hashlib
BUILD_STAMP = _hashlib.sha256(open(os.path.abspath(__file__), "rb").read()).hexdigest()[:10]

def form_page(errors=None, notice=None):
    h = PAGE_HEAD
    if errors:
        h += '<div class="err"><b>Not written — fix these and resubmit:</b><ul>%s</ul></div>' % "".join("<li>%s</li>" % e for e in errors)
    if notice: h += notice
    node_block = ""
    for i, label in ((1, "Node A"), (2, "Node B"), (3, "Node C")):
        node_block += ('<div class="node"><h4>%s</h4>'
          '<label>Hostname (SHORT name — the DNS domain from step 1 is appended automatically)</label><input name="n%d_host" required>'
          '<div class="hint">on the node: <code>hostname -s</code>. NO dots — an FQDN here would double the domain in /etc/hosts</div>'
          '<label>Management IP</label><input name="n%d_mgmt" required>'
          '<div class="hint">the address you SSH to today</div>'
          '<label>Storage IP</label><input name="n%d_storage" required>'
          '<label>Station IP</label><input name="n%d_station" required>'
          '<label>Heartbeat IP</label><input name="n%d_hb" required>'
          '<div class="hint">these three come from your NETWORK PLAN (QUICKSTART: what you need) — the interfaces do not exist on the node yet; the automation creates them</div>'
          '<label>BMC (iDRAC/IPMI) IP</label><input name="n%d_bmc" required>'
          '<div class="hint">from the BMC settings screen (iDRAC: Settings → Network)</div>'
          '<label>BMC type</label><select name="n%d_bmc_type"><option>idrac9</option><option>idrac8</option>'
          '<option>supermicro_ipmi</option><option>generic_ipmilan</option></select>'
          '<label>BMC username</label><input name="n%d_bmc_user" value="root">'
          '<label>BMC password (goes to vault)</label><input type="password" name="n%d_bmc_pw">'
          '<label>NIC names (comma-sep)</label><input name="n%d_nics" placeholder="eno1,eno2,eno3,eno4">'
          '<div class="hint">on the node: <code>ip -br link</code></div>'
          '<label>OSD disks — one /dev/disk/by-id/ path per line</label><textarea rows="4" name="n%d_disks"></textarea>'
          '<div class="hint">by-id ONLY — kernel names like /dev/sdb or /dev/nvme0n1 reorder across boots, the storage stage WIPES what it is handed, and a swapped name can destroy the OS disk. List them: <code>ls -l /dev/disk/by-id/</code></div>'
          '<div class="hint">on the node: <code>ls -l /dev/disk/by-id/ | grep -v part</code> — these disks are WIPED</div>'
          '</div>') % ((label,) + (i,) * 11)
    nets = ""
    defaults = dict(mgmt=("10.0.0.0/24", ""), storage=("10.0.10.0/24", "10"), station=("10.0.20.0/24", "20"),
                    heartbeat=("10.0.30.0/24", "30"), bmc=("10.0.100.0/24", ""))
    for k in ("mgmt", "storage", "station", "heartbeat", "bmc"):
        c, v = defaults[k]
        nets += ('<label>%s CIDR</label><input name="net_%s_cidr" value="%s">'
                 '<label>%s VLAN (blank = untagged)</label><input name="net_%s_vlan" value="%s">') % (k, k, c, k, k, v)
    h = h + ("""<form method="POST" action="/write">
<fieldset><legend>1. Site</legend>
<label>Site name (becomes the folder name)</label><input name="site_name" required pattern="[a-z0-9][a-z0-9_-]+">
<label>DNS domain</label><input name="site_domain" required placeholder="ops.utility.example">
<label>Where is this site? (sets every node's clock)</label>%%TZSEL%%
<div class="hint">pre-selected from this laptop's own timezone — change it if the site is elsewhere</div>
<label>DNS servers (comma-sep)</label><input name="dns" placeholder="10.0.0.1,10.0.0.2">
<label>Admin username on the nodes</label><input name="ssh_user" value="admin">
<label>SSH key for the automation</label>
<div class="hint"><b>Recommended: a NEW dedicated key</b> — not your personal one. It has no
passphrase (unattended automation breaks on one), it can be rotated without touching anyone's
identity, and it can be handed to a colleague.</div>
<button type="button" onclick="guard(this,'keymsg',()=>makeKey())">Create a dedicated key for this site</button>
<span id="keymsg" class="hint"></span>
<label>Key path (filled by the button, or point at an existing key)</label>
<input name="ssh_key" id="sshkey" value="">
<div id="copyid" class="hint"></div>
<button type="button" onclick="guard(this,'sshtest',()=>testSsh())">Test connectivity to all three nodes</button>
<div id="sshtest" class="hint"></div></fieldset>
<fieldset><legend>2. Mode</legend>
<label>Deployment mode</label><select name="mode" id="modesel"><option>connected</option><option>airgapped</option></select>
<div id="agfields">
<label>Air-gapped only — builder hostname</label><input name="builder_host">
<label>Air-gapped only — builder mgmt IP</label><input name="builder_ip">
<label>Air-gapped only — mirror URL</label><input name="mirror_url" placeholder="http://builder.example:80/mirror">
<label>Air-gapped only — local registry host:port</label><input name="registry" placeholder="builder.example:5000">
</div><div class="hint" id="modenote"></div></fieldset>
<fieldset><legend>3. Nodes</legend>%s
<label>Which node bootstraps Ceph?</label><select name="bootstrap" id="bootstrap"></select>
<div class="hint">the form derives BOTH the variable and the inventory group from this — they can never disagree</div></fieldset>
<fieldset><legend>4. Networks</legend>
<div class="hint">Everything here comes from your NETWORK PLAN, not from the nodes — see QUICKSTART "What else you need". Four networks, addresses per node already entered above.</div>
<label>Mgmt bond members (comma-separated, from the NIC names typed above)</label><input name="map_mgmt" required placeholder="eno1,eno2">
<label>Storage bond members</label><input name="map_storage" required placeholder="ens1f0,ens1f1">
<label>Station-bus bond members</label><input name="map_station" required placeholder="ens2f0,ens2f1">
<div class="hint">every name must appear in EVERY node's NIC list (homogeneous hardware is the default; per-node differences go to host_vars by hand). One role per NIC; the PTP NIC stays out of all bonds; single-member bonds are fine.</div>
<label>Heartbeat runs on</label><select name="hb_mode" id="hbmode" required><option value="">— choose —</option>
<option value="shared">a VLAN on an existing bond (typical for 4-NIC hardware; shares physical fate with that bond)</option>
<option value="dedicated">its own dedicated NIC (needs a spare port)</option></select>
<span id="hbshared"><label>Which bond carries the heartbeat VLAN</label><select name="hb_bond"><option>storage</option><option>mgmt</option><option>station</option></select>
<div class="hint">uses the heartbeat VLAN below — it MUST differ from the storage VLAN (ring separation; preflight enforces it)</div></span>
<span id="hbdedicated"><label>Dedicated heartbeat NIC</label><input name="hb_nic" placeholder="ens3f0">
<div class="hint">must exist on every node and belong to no bond</div></span>
%s</fieldset>
<fieldset><legend>4b. Timing — your domain, not a default</legend>
<label>Time mode</label><select name="ts_mode"><option value="ptp">ptp — pure PTP, no NTP below the grandmaster (substation standard)</option>
<option value="ptp_with_ntp">ptp_with_ntp — PTP with NTP fallback</option><option value="ntp">ntp — NTP only (labs)</option></select>
<label>PTP domain</label><input name="ptp_domain" value="0">
<label>Transport</label><select name="ptp_transport"><option>L2</option><option>UDPv4</option></select>
<label>Delay mechanism</label><select name="ptp_delay"><option>P2P</option><option>E2E</option></select>
<div class="hint">P2P requires the SWITCH carrying PTP to be a P2P transparent clock. A plain bridge cannot forward peer-delay frames (they are link-local): nodes receive sync but never compute a path delay, and NO node-side setting can fix it. Verify the switch configuration before install day.</div>
<label>Profile</label><select name="ptp_profile"><option>default</option><option>G.8275.1</option><option>G.8275.2</option></select>
<label>Dedicated PTP NIC name (same on all nodes)</label><input name="ptp_nic" placeholder="eno4">
<div class="hint">must be its own NIC — never bridged, bonded or shared. Check hardware timestamping: <code>ethtool -T &lt;nic&gt;</code></div></fieldset>
<fieldset><legend>5b. Real-time</legend>
<label>Paste the output of <code>lscpu</code> from one node (auto-fills the count)</label>
<textarea rows="3" id="lscpu" placeholder="paste here — only the CPU(s): line is read"></textarea>
<label>Total logical CPUs per node</label><input name="cpu_count" id="cpucount" placeholder="16">
<label>Isolated CPUs for VMs</label><input name="isolated_cpus" placeholder="4-11">
<div class="hint">on a node: <code>lscpu</code>. The form refuses CPU numbers your machine doesn't have — isolcpus would silently ignore them.</div>
<label>Hugepage size</label><select name="hugepage_size"><option>1G</option><option>2M</option></select></fieldset>
<fieldset><legend>6. Secrets (encrypted into the vault — never stored in plain text)</legend>
<label>Vault password (you'll type this on every playbook run)</label><input type="password" name="vault_password" required>
<label>RHSM activation key</label><input type="password" name="v_rhsm_key">
<label>RHSM org ID</label><input name="v_rhsm_org">
<label>registry.redhat.io service-account username</label><input name="v_reg_user" placeholder="1234567|token-name">
<div class="hint">TERMS-BASED account from access.redhat.com/terms-based-registry — an IAM account will NOT work</div>
<label>registry.redhat.io service-account password</label><input type="password" name="v_reg_pw">
<label>Registry credentials file path ON the bootstrap node</label><input name="reg_creds_path" value="/root/ceph-registry.json">
<div class="hint">Connected mode: BEFORE the storage stage you must create this file on the bootstrap node — JSON with "url", "username", "password" of the account above, then <code>chmod 0600</code> it. The success page prints the exact commands. Preflight verifies it exists. Air-gapped sites with an anonymous local registry: ignore, it is not written.</div>
<div class="hint">Plan for the download: the first deploy pulls a ~1.3 GB ceph image PER NODE from the registry — minutes per node on a 1 Gb link. The roles pre-pull it deliberately so later steps are not timed out by the download; size your maintenance window for it.</div>
<label>hacluster password (invent a strong one)</label><input type="password" name="v_hacluster" required></fieldset>
<fieldset><legend>7. Thresholds — set from YOUR hardware</legend>
<label>Storage network speed</label><select name="storage_mbps"><option value="10000">10 Gb (10000)</option><option value="1000">1 Gb (1000)</option></select>
<div class="hint">answer honestly — the wrong value fails validation at the very end of deployment. Do NOT read this from ethtool or /sys: a media-converter module reports its HOST-side rate (10000) even when the wire is 1 Gb. Verify with a throughput test or the switch port state.</div>
<label>cyclictest max latency (µs)</label><input name="cyclictest_us" value="120">
<label>Fence agent</label><select name="fence_agent"><option>fence_ipmilan</option><option>fence_virsh</option></select></fieldset>
<button>Review nothing — WRITE my site inventory now</button>
<div class="hint">On success you get the exact next command to run. On any problem NOTHING is written.</div>
</form>
<p class="hint">form build %s — verify this matches <code>sha256sum tools/site-form.py | cut -c1-10</code>; a mismatch means a STALE server process is answering (kill it and restart)</p>
<script>
function showErr(m){const b=document.getElementById('errbanner');b.style.display='block';
b.textContent='The form hit a problem in this browser: '+m+' — nothing was changed on disk. Report this exact text.';}
window.onerror=function(m,src,l){showErr(m+' (line '+l+')');};
window.addEventListener('unhandledrejection',e=>showErr(e.reason&&e.reason.message||e.reason));
async function guard(btn,statusId,fn){const el=document.getElementById(statusId);
let t;if(btn){btn.disabled=true;t=btn.textContent;btn.textContent='working…';}
try{await fn();}catch(e){if(el)el.textContent='FAILED: '+(e.message||e);showErr(e.message||e);}
finally{if(btn){btn.disabled=false;btn.textContent=t;}}}
async function jfetch(url,body){const r=await fetch(url,{method:'POST',body});
if(!r.ok)throw new Error(url+' returned HTTP '+r.status);return r.json();}
try{const mytz=Intl.DateTimeFormat().resolvedOptions().timeZone;
const t=document.getElementById('tzsel');if(t&&[...t.options].some(o=>o.value===mytz))t.value=mytz;}catch(e){}
const b=document.getElementById('bootstrap');
function syncBoot(){const hs=[1,2,3].map(i=>document.querySelector(`[name=n${i}_host]`).value.trim()).filter(x=>x);
b.innerHTML=hs.map(h=>`<option>${h}</option>`).join('');}
[1,2,3].forEach(i=>document.querySelector(`[name=n${i}_host]`).addEventListener('input',syncBoot));syncBoot();
function toggleMode(){const ag=document.getElementById('modesel').value==='airgapped';
document.getElementById('agfields').style.display=ag?'':'none';
document.getElementById('modenote').textContent=ag?'':'Connected mode: no builder exists — the builder questions are hidden because they do not apply.';}
document.getElementById('modesel').addEventListener('change',toggleMode);toggleMode();
function hbToggle(){const v=document.getElementById('hbmode').value;
document.getElementById('hbshared').style.display=(v==='shared')?'':'none';
document.getElementById('hbdedicated').style.display=(v==='dedicated')?'':'none';}
document.getElementById('hbmode').addEventListener('change',hbToggle);hbToggle();
document.getElementById('lscpu').addEventListener('input',e=>{const m=e.target.value.match(/^CPU\\(s\\):\\s*(\\d+)/m);
if(m)document.getElementById('cpucount').value=m[1];});
function fd(){const o=new URLSearchParams();['site_name','ssh_user'].forEach(k=>o.set(k,document.querySelector(`[name=${k}]`).value));
[1,2,3].forEach(i=>o.set('ip'+i,document.querySelector(`[name=n${i}_mgmt]`).value));
o.set('key',document.getElementById('sshkey').value);return o;}
async function makeKey(forceNew){const o=fd();if(forceNew)o.set('force_new','1');
const j=await jfetch('/makekey',o);
if(j.exists){document.getElementById('keymsg').innerHTML=
 `A key named <code>${j.exists}</code> already exists (created ${j.created}). `+
 `<button type="button" onclick="reuseKey('${j.exists}')">REUSE it</button> — right if this is the same `+
 `cluster and you already copied it to the nodes — or `+
 `<button type="button" onclick="guard(this,'keymsg',()=>makeKey(true))">CREATE A NEW ONE (${j.next.split('/').pop()})</button>`;
 return;}
document.getElementById('keymsg').textContent=j.msg||'';
if(j.path){document.getElementById('sshkey').value=j.path;
const u=document.querySelector('[name=ssh_user]').value||'admin';
const ips=[1,2,3].map(i=>document.querySelector(`[name=n${i}_mgmt]`).value).filter(x=>x);
document.getElementById('copyid').innerHTML='<b>Now copy it to each node (each asks for the admin password once):</b><br>'+
 ips.map(ip=>`<code>ssh-copy-id -i ${j.path}.pub ${u}@${ip}</code>`).join('<br>');}}
function reuseKey(p){document.getElementById('sshkey').value=p;
document.getElementById('keymsg').textContent='reusing '+p+' — make sure this key was already copied (ssh-copy-id) to THESE nodes';}
async function testSsh(){const el=document.getElementById('sshtest');el.textContent='testing…';
const j=await jfetch('/testssh',fd());
el.innerHTML=j.results.map(x=>`${x.ip}: <b style="color:${x.ok?'#080':'#c00'}">${x.ok?'OK — key + passwordless sudo work':'FAILED — '+x.err}</b>`).join('<br>');}
</script></body></html>""" % (node_block, nets, BUILD_STAMP)).replace("%TZSEL%", tz_select_html())
    return h

def success_page(f, files):
    note = ""
    if f["mode"] == "connected" and any((f["builder_host"], f["builder_ip"], f["mirror_url"], f["registry"])):
        note = ('<p><b>Note:</b> builder/mirror values were entered but this is a CONNECTED '
                'site — they do not apply and were intentionally NOT written.</p>')
    reg_note = ""
    if f["mode"] == "connected":
        reg_note = (
            '<p><b>One manual step before the storage stage</b> — on the bootstrap '
            'node (<code>' + f["bootstrap"] + '</code>), create the registry credentials file '
            '(use the account you entered in the form; the password is never shown here):</p>'
            '<pre>sudo tee ' + f["reg_creds_path"] + " &lt;&lt;'EOF'\n"
            '{"url":"registry.redhat.io","username":"YOUR-TERMS-BASED-USERNAME","password":"YOUR-TOKEN"}\n'
            "EOF\n"
            'sudo chmod 0600 ' + f["reg_creds_path"] + '</pre>'
            '<p>Preflight checks that the file exists; the deploy fails without it.</p>')
    return PAGE_HEAD + note + reg_note + """<div class="ok"><h2>Site inventory written</h2><p>Files created:</p><ul>%s</ul>
<h3>Your next command (from the repo root):</h3>
<pre>ansible-playbook -i inventory/%s site.yml --tags preflight --ask-vault-pass</pre>
<p>It will either print a numbered list of everything still to fix (fix all, re-run), or:</p>
<pre>Inventory contract clean (cluster-forming values + required secrets): ...
vm_catalog empty — cluster-only path; stage 80 will be a no-op.</pre>
<p>From there, follow <code>docs/DEPLOYMENT-RUNBOOK.md</code> Part B, one stage at a time.</p>
<p><b>Left at example defaults for hand review</b> (deliberate v1 scope):
Ceph pool layout, bond modes/options (members and heartbeat mode are now
written from your answers; the mode/miimon/LACP options stay at the
example's defaults), and — air-gapped sites — the builder ISO section
(contract §13).</p></div></body></html>""" % (
        "".join("<li><code>%s</code></li>" % x for x in files), f["site_name"])

class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, html, code=200):
        b = html.encode(); self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self): self._send(form_page())
    def _json(self, obj):
        b = json.dumps(obj).encode(); self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        if self.path == "/makekey":
            d = urllib.parse.parse_qs(body); site = d.get("site_name", [""])[0].strip()
            if not valid_site(site):
                return self._json(dict(path="", msg="fix the site name first (lowercase letters/"
                                       "digits/dash) — the key is named after it"))
            path = os.path.expanduser("~/.ssh/vpac-%s" % site)
            if os.path.exists(path) and not d.get("force_new"):
                import datetime
                created = datetime.date.fromtimestamp(os.path.getmtime(path)).isoformat()
                n = 2
                while os.path.exists("%s-%d" % (path, n)): n += 1
                return self._json(dict(path="", exists=path, created=created,
                                       next="%s-%d" % (path, n)))
            if d.get("force_new"):
                n = 2
                while os.path.exists("%s-%d" % (path, n)): n += 1
                path = "%s-%d" % (path, n)
            r = subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-q",
                                "-C", "vpac-%s" % site, "-f", path], capture_output=True, text=True)
            if r.returncode != 0:
                return self._json(dict(path="", msg="ssh-keygen failed: %s" % r.stderr.strip()))
            return self._json(dict(path=path, msg="created %s (ed25519, no passphrase — dedicated to this site)" % path))
        if self.path == "/testssh":
            d = urllib.parse.parse_qs(body)
            key = os.path.expanduser(d.get("key", [""])[0]); user = d.get("ssh_user", ["admin"])[0] or "admin"
            out = []
            for i in (1, 2, 3):
                ip = d.get("ip%d" % i, [""])[0].strip()
                if not ip: continue
                r = subprocess.run(["ssh", "-i", key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                                    "-o", "StrictHostKeyChecking=accept-new",
                                    "%s@%s" % (user, ip), "sudo", "-n", "true"],
                                   capture_output=True, text=True)
                out.append(dict(ip=ip, ok=r.returncode == 0,
                                err=(r.stderr.strip().splitlines() or ["no route / auth failed"])[-1] if r.returncode else ""))
            return self._json(dict(results=out))
        f = parse(body)
        errs = validate(f)
        if errs: return self._send(form_page(errors=errs), 400)
        ok, msg, files = write_site(f)
        if not ok: return self._send(form_page(errors=[msg]), 400)
        self._send(success_page(f, files))

# CONVENTION (finding V): never put prose containing apostrophes inside
# JS string literals — a rendered SyntaxError voids the ENTIRE script
# block and every button dies silently. Keep prose apostrophe-free or in
# HTML. The gate below enforces it.
def js_gate():
    page = form_page()
    m = re.search(r"<script>(.*?)</script>", page, re.S)
    assert m, "js_gate: no script block found"
    js = m.group(1)
    node = shutil.which("node") or shutil.which("deno")
    if node:
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as tf:
            tf.write(js); jf = tf.name
        try:
            r = subprocess.run([node, "--check", jf], capture_output=True, text=True)
        finally:
            os.unlink(jf)
        if r.returncode != 0:
            sys.exit("JS GATE FAIL (%s --check):\n%s" % (node, r.stderr))
        return "js gate: parsed by " + node
    bad = []
    for i, line in enumerate(js.splitlines(), 1):
        stripped = re.sub(r"`[^`]*`", "", line)
        stripped = re.sub(r'"[^"]*"', "", stripped)
        if stripped.replace("\\'", "").count("'") % 2:
            bad.append("script line %d: odd unescaped single-quote count: %s" % (i, line.strip()[:90]))
    if bad:
        sys.exit("JS GATE FAIL (no JS engine; apostrophe lint):\n" + "\n".join(bad))
    return "js gate: apostrophe lint clean (no JS engine on this machine)"

def selfcheck():
    text = contract_text()
    assert_coverage(text)
    print(js_gate())
    dummy_nodes = [dict(host="h%d" % i, mgmt="1.1.1.%d" % i, storage="2.2.2.%d" % i, station="3.3.3.%d" % i,
                        hb="4.4.4.%d" % i, bmc="5.5.5.%d" % i, bmc_type="idrac9", bmc_user="admin",
                        bmc_pw="x", nics=["eno1", "eno2", "ens1f0", "ens1f1", "ens2f0", "ens2f1", "ens3f0", "eno4"],
                        disks=["/dev/disk/by-id/x%d" % i], letter=L)
                   for i, L in ((1, "a"), (2, "b"), (3, "c"))]
    f = dict(site_name="selfcheck", site_domain="d", site_timezone="UTC", dns=["1.1.1.1"], mode="connected",
             mirror_url="", registry="", builder_host="", builder_ip="", nodes=dummy_nodes,
             net={k: dict(cidr="9.9.9.0/24", vlan="7") for k in ("mgmt", "storage", "station", "heartbeat", "bmc")},
             bootstrap="h1", ts_mode="ptp", ptp_domain="0", ptp_transport="L2", ptp_delay="P2P",
             ptp_profile="default", ptp_nic="eno4", reg_creds_path="/root/ceph-registry.json",
             map_mgmt=["eno1", "eno2"], map_storage=["ens1f0", "ens1f1"], map_station=["ens2f0", "ens2f1"],
             hb_mode="shared", hb_bond="storage", hb_nic="", isolated_cpus="4-11", cpu_count="16", hugepage_size="1G",
             fence_agent="fence_ipmilan", storage_mbps="1000", cyclictest_us="120", ssh_user="admin",
             ssh_key="~/.ssh/k", vault_password="testtest",
             vault=dict(rhsm_activation_key="k", rhsm_org_id="o", redhat_registry_username="u",
                        redhat_registry_password="p", hacluster_password="h"))
    _, misses = build_main_yml(f, text)
    if misses:
        sys.exit("SELFCHECK FAIL — unmatched substitution anchors: %s" % misses)
    build_hosts_yml(f); build_vault(f)
    print("selfcheck OK: coverage asserted, all %s substitution anchors match, generators run" % "…")

if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        selfcheck(); sys.exit(0)
    assert_coverage(contract_text())
    print("vPAC site form — open  http://127.0.0.1:%d  in your browser (Ctrl-C to stop)" % PORT)
    http.server.HTTPServer(("127.0.0.1", PORT), H).serve_forever()
