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
    if f["mode"] == "connected":
        sub("registry_credentials_file", r'^  registry_credentials_file: null',
            '  registry_credentials_file: "/root/ceph-registry.json"')
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
             vault=dict(rhsm_activation_key=g("v_rhsm_key"), rhsm_org_id=g("v_rhsm_org"),
                        redhat_registry_username=g("v_reg_user"), redhat_registry_password=g("v_reg_pw"),
                        hacluster_password=g("v_hacluster")))
    return f

def validate(f):
    errs = []
    if not re.match(r"^[a-z0-9][a-z0-9_-]{1,30}$", f["site_name"] or ""):
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
    if f["cpu_count"]:
        try:
            top = max(int(x) for part in f["isolated_cpus"].split(",") for x in part.split("-"))
            if top >= int(f["cpu_count"]):
                errs.append("isolated_cpus references CPU %d but this machine reports only %s CPUs "
                            "(isolcpus would SILENTLY ignore the extras)" % (top, f["cpu_count"]))
        except ValueError:
            errs.append("isolated_cpus: use forms like 4-11 or 2,4-7")
    if len(f["vault_password"]) < 8: errs.append("vault password: 8+ characters")
    if not f["vault"]["hacluster_password"]: errs.append("hacluster password is required (3-node cluster)")
    if f["mode"] == "airgapped" and not (f["builder_host"] and f["builder_ip"] and f["mirror_url"]):
        errs.append("air-gapped mode needs builder hostname, IP and mirror URL")
    return errs

PAGE_HEAD = """<!doctype html><html><head><meta charset="utf-8"><title>vPAC site form</title><style>
body{font:15px/1.5 sans-serif;max-width:900px;margin:2em auto;padding:0 1em;color:#222}
fieldset{margin:1.2em 0;border:1px solid #bbb;border-radius:6px;padding:1em}
legend{font-weight:700} label{display:block;margin:.5em 0 .1em} input,select,textarea{width:100%%;
max-width:480px;padding:.35em;font:inherit} .hint{color:#555;font-size:.85em;margin:.1em 0 .4em}
.node{display:inline-block;vertical-align:top;width:31%%;margin-right:1%%}
.err{background:#fee;border:1px solid #c00;padding:1em;border-radius:6px}
.ok{background:#efe;border:1px solid #090;padding:1em;border-radius:6px}
code{background:#f4f4f4;padding:0 .3em} button{font:inherit;padding:.6em 2em;margin-top:1em}</style></head><body>
<h1>vPAC cluster — site form</h1>
<p>Fill this out top to bottom; it writes your whole site inventory, including the
encrypted vault. Nothing is written until you press the button at the end, and if
anything is wrong it refuses and tells you why. Every "how do I find this?" hint
is a command you run on the server itself.</p>"""

def form_page(errors=None, notice=None):
    h = PAGE_HEAD
    if errors:
        h += '<div class="err"><b>Not written — fix these and resubmit:</b><ul>%s</ul></div>' % "".join("<li>%s</li>" % e for e in errors)
    if notice: h += notice
    node_block = ""
    for i, label in ((1, "Node A"), (2, "Node B"), (3, "Node C")):
        node_block += ('<div class="node"><h4>%s</h4>'
          '<label>Hostname (FQDN)</label><input name="n%d_host" required>'
          '<label>Management IP</label><input name="n%d_mgmt" required>'
          '<label>Storage IP</label><input name="n%d_storage" required>'
          '<label>Station IP</label><input name="n%d_station" required>'
          '<label>Heartbeat IP</label><input name="n%d_hb" required>'
          '<label>BMC (iDRAC/IPMI) IP</label><input name="n%d_bmc" required>'
          '<label>BMC type</label><select name="n%d_bmc_type"><option>idrac9</option><option>idrac8</option>'
          '<option>supermicro_ipmi</option><option>generic_ipmilan</option></select>'
          '<label>BMC username</label><input name="n%d_bmc_user" value="admin">'
          '<label>BMC password (goes to vault)</label><input type="password" name="n%d_bmc_pw">'
          '<label>NIC names (comma-sep)</label><input name="n%d_nics" placeholder="eno1,eno2,eno3,eno4">'
          '<div class="hint">on the node: <code>ip -br link</code></div>'
          '<label>OSD disks — one /dev/disk/by-id/ path per line</label><textarea rows="4" name="n%d_disks"></textarea>'
          '<div class="hint">on the node: <code>ls -l /dev/disk/by-id/ | grep -v part</code> — these disks are WIPED</div>'
          '</div>') % ((label,) + (i,) * 11)
    nets = ""
    defaults = dict(mgmt=("10.0.0.0/24", ""), storage=("10.0.10.0/24", "10"), station=("10.0.20.0/24", "20"),
                    heartbeat=("10.0.30.0/24", "30"), bmc=("10.0.100.0/24", ""))
    for k in ("mgmt", "storage", "station", "heartbeat", "bmc"):
        c, v = defaults[k]
        nets += ('<label>%s CIDR</label><input name="net_%s_cidr" value="%s">'
                 '<label>%s VLAN (blank = untagged)</label><input name="net_%s_vlan" value="%s">') % (k, k, c, k, k, v)
    h += """<form method="POST" action="/write">
<fieldset><legend>1. Site</legend>
<label>Site name (becomes the folder name)</label><input name="site_name" required pattern="[a-z0-9][a-z0-9_-]+">
<label>DNS domain</label><input name="site_domain" required placeholder="ops.utility.example">
<label>Timezone (IANA)</label><input name="site_timezone" value="UTC">
<label>DNS servers (comma-sep)</label><input name="dns" placeholder="10.0.0.1,10.0.0.2">
<label>Admin username on the nodes</label><input name="ssh_user" value="admin">
<label>SSH private key path on THIS laptop</label><input name="ssh_key" value="~/.ssh/id_ed25519">
<div class="hint">the key you ssh-copy-id'd to each node (QUICKSTART step 2)</div></fieldset>
<fieldset><legend>2. Mode</legend>
<label>Deployment mode</label><select name="mode"><option>connected</option><option>airgapped</option></select>
<label>Air-gapped only — builder hostname</label><input name="builder_host">
<label>Air-gapped only — builder mgmt IP</label><input name="builder_ip">
<label>Air-gapped only — mirror URL</label><input name="mirror_url" placeholder="http://builder.example:80/mirror">
<label>Air-gapped only — local registry host:port</label><input name="registry" placeholder="builder.example:5000"></fieldset>
<fieldset><legend>3. Nodes</legend>%s
<label>Which node bootstraps Ceph?</label><select name="bootstrap" id="bootstrap"></select>
<div class="hint">the form derives BOTH the variable and the inventory group from this — they can never disagree</div></fieldset>
<fieldset><legend>4. Networks</legend>%s</fieldset>
<fieldset><legend>4b. Timing — your domain, not a default</legend>
<label>Time mode</label><select name="ts_mode"><option value="ptp">ptp — pure PTP, no NTP below the grandmaster (substation standard)</option>
<option value="ptp_with_ntp">ptp_with_ntp — PTP with NTP fallback</option><option value="ntp">ntp — NTP only (labs)</option></select>
<label>PTP domain</label><input name="ptp_domain" value="0">
<label>Transport</label><select name="ptp_transport"><option>L2</option><option>UDPv4</option></select>
<label>Delay mechanism</label><select name="ptp_delay"><option>P2P</option><option>E2E</option></select>
<label>Profile</label><input name="ptp_profile" value="default">
<label>Dedicated PTP NIC name (same on all nodes)</label><input name="ptp_nic" placeholder="eno4">
<div class="hint">must be its own NIC — never bridged, bonded or shared. Check hardware timestamping: <code>ethtool -T &lt;nic&gt;</code></div></fieldset>
<fieldset><legend>5b. Real-time</legend>
<label>Total logical CPUs per node (from lscpu "CPU(s):")</label><input name="cpu_count" placeholder="16">
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
<label>hacluster password (invent a strong one)</label><input type="password" name="v_hacluster" required></fieldset>
<fieldset><legend>7. Thresholds — set from YOUR hardware</legend>
<label>Storage network speed</label><select name="storage_mbps"><option value="10000">10 Gb (10000)</option><option value="1000">1 Gb (1000)</option></select>
<div class="hint">answer honestly — the wrong value fails validation at the very end of deployment</div>
<label>cyclictest max latency (µs)</label><input name="cyclictest_us" value="120">
<label>Fence agent</label><select name="fence_agent"><option>fence_ipmilan</option><option>fence_virsh</option></select></fieldset>
<button>Review nothing — WRITE my site inventory now</button>
<div class="hint">On success you get the exact next command to run. On any problem NOTHING is written.</div>
</form>
<script>
const b=document.getElementById('bootstrap');
function syncBoot(){const hs=[1,2,3].map(i=>document.querySelector(`[name=n${i}_host]`).value.trim()).filter(x=>x);
b.innerHTML=hs.map(h=>`<option>${h}</option>`).join('');}
[1,2,3].forEach(i=>document.querySelector(`[name=n${i}_host]`).addEventListener('input',syncBoot));syncBoot();
</script></body></html>""" % (node_block, nets)
    return h

def success_page(f, files):
    return PAGE_HEAD + """<div class="ok"><h2>Site inventory written</h2><p>Files created:</p><ul>%s</ul>
<h3>Your next command (from the repo root):</h3>
<pre>ansible-playbook -i inventory/%s site.yml --tags preflight --ask-vault-pass</pre>
<p>It will either print a numbered list of everything still to fix (fix all, re-run), or:</p>
<pre>Inventory contract clean (cluster-forming values + required secrets): ...
vm_catalog empty — cluster-only path; stage 80 will be a no-op.</pre>
<p>From there, follow <code>docs/DEPLOYMENT-RUNBOOK.md</code> Part B, one stage at a time.</p>
<p><b>Left at example defaults for hand review</b> (deliberate v1 scope):
NIC-to-role mapping beyond the PTP NIC (contract's networking_defaults),
Ceph pool layout, and — air-gapped sites — the builder ISO section (contract §13).</p></div></body></html>""" % (
        "".join("<li><code>%s</code></li>" % x for x in files), f["site_name"])

class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, html, code=200):
        b = html.encode(); self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self): self._send(form_page())
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        f = parse(body)
        errs = validate(f)
        if errs: return self._send(form_page(errors=errs), 400)
        ok, msg, files = write_site(f)
        if not ok: return self._send(form_page(errors=[msg]), 400)
        self._send(success_page(f, files))

def selfcheck():
    text = contract_text()
    assert_coverage(text)
    dummy_nodes = [dict(host="h%d" % i, mgmt="1.1.1.%d" % i, storage="2.2.2.%d" % i, station="3.3.3.%d" % i,
                        hb="4.4.4.%d" % i, bmc="5.5.5.%d" % i, bmc_type="idrac9", bmc_user="admin",
                        bmc_pw="x", nics=["eno1"], disks=["/dev/disk/by-id/x%d" % i], letter=L)
                   for i, L in ((1, "a"), (2, "b"), (3, "c"))]
    f = dict(site_name="selfcheck", site_domain="d", site_timezone="UTC", dns=["1.1.1.1"], mode="connected",
             mirror_url="", registry="", builder_host="", builder_ip="", nodes=dummy_nodes,
             net={k: dict(cidr="9.9.9.0/24", vlan="7") for k in ("mgmt", "storage", "station", "heartbeat", "bmc")},
             bootstrap="h1", ts_mode="ptp", ptp_domain="0", ptp_transport="L2", ptp_delay="P2P",
             ptp_profile="default", ptp_nic="eno4", isolated_cpus="4-11", cpu_count="16", hugepage_size="1G",
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
