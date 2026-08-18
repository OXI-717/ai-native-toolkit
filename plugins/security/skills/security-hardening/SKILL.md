---
name: security-hardening
description: "Comprehensive security audit and hardening checklist for VPS servers, WordPress sites, and Next.js applications. Covers SSH hardening, firewall configuration, fail2ban, CrowdSec, kernel sysctl, Docker security, SSL/TLS, WAF, OWASP Top 10, headers, secrets management, backups, and incident response. Use this skill whenever the user mentions security, hardening, securing a server, защита сервера, обезопасить VPS, security audit, security checklist, or asks about SSH, firewall, fail2ban, ports, brute force, DDoS, WAF, SSL certificates, security headers, or any topic related to protecting servers, websites, or web applications from attacks — even if they don't explicitly say 'security'."
---

# Security Hardening

Interactive security audit and hardening skill for VPS, WordPress, and Next.js.

**Default language: English.** Use `REPORT_LANGUAGE` if set. Otherwise honor an explicit language request from the user. If neither is present, provide the analysis, findings, score, and recommendations in English. Russian output is only for an explicit Russian request or `REPORT_LANGUAGE=ru`.

**Default mode: audit and propose first.** Start by assessing the current state, then present prioritized findings and recommended changes. Do not jump straight to applying hardening changes. When moving from analysis to fixes, do it step by step with pre-checks, verification, and a rollback path for each change.

## RULE ZERO: DO NOT LOCK YOURSELF OUT

**Preserving admin access to the server is more important than any hardening measure.** Every SSH, firewall, or network change must follow this sequence:

1. **Pre-check** — verify current state before changing anything
2. **Change** — apply one change at a time
3. **Verify** — confirm the change works (from a separate session if SSH/firewall)
4. **Rollback path** — know how to undo it before you apply it

Never recommend "apply all at once" sequences for SSH/firewall/network changes. If the user doesn't have provider console or rescue mode access, tell them to set that up first.

---

## Workflow

When triggered, act as an interactive security auditor. Don't dump everything at once — guide the user step by step.

### Phase 1: Detect the Stack

Ask or detect what the user is running:

```bash
# Auto-detect
uname -a                           # OS
cat /etc/os-release                # Distro
systemctl list-units --type=service --state=running  # Services
docker ps 2>/dev/null              # Containers
ss -tlnp                           # Listening ports
```

Based on what's found:
- **VPS/Linux server** → use the VPS checklist below + read `${CLAUDE_PLUGIN_ROOT}/skills/security-hardening/references/vps.md` for deep detail
- **WordPress** → read `${CLAUDE_PLUGIN_ROOT}/skills/security-hardening/references/wordpress.md`
- **Next.js / Node.js** → read `${CLAUDE_PLUGIN_ROOT}/skills/security-hardening/references/nextjs.md`
- **General web app** → read `${CLAUDE_PLUGIN_ROOT}/skills/security-hardening/references/general-web.md`

### Phase 2: Preflight Safety

Before touching anything, run through this checklist with the user:

- [ ] **Provider console access** — confirm web console / rescue mode / KVM exists and is tested. Without this, SSH lockout = bricked server.
- [ ] **Snapshot/backup** — create a server snapshot before risky changes
- [ ] **Second session** — keep a separate SSH session open while testing changes
- [ ] **Inventory exposure** — run `ss -tlnp` and map what's public vs localhost-only
- [ ] **SSH key access confirmed** — verify at least one public key login works before disabling passwords
- [ ] **Provider firewall** — check if there's an external firewall/security group layer (AWS SG, Hetzner Firewall, etc.)

### Phase 3: Audit and Report

Run diagnostic commands and categorize findings into:

| Priority | Description | Examples |
|----------|-------------|---------|
| **Critical now** | Actively exploitable or data at risk | Root login enabled, no firewall, default passwords, exposed DB port |
| **Safe baseline** | Low-risk, high-impact improvements | SSH key-only auth, fail2ban, UFW, unattended-upgrades |
| **Advanced hardening** | Significant improvement, some breakage risk | sysctl hardening, CIS items, auditd, AIDE |
| **Defense-in-depth** | Optional layers, diminishing returns | CrowdSec, non-standard SSH port, kernel module restrictions |

Also assign a **security score from 0 to 10** for the VPS (or the relevant stack) and explain it in plain language.

Score guidance:

| Score | Meaning |
|-------|---------|
| **0-2** | Severely exposed. Basic protections missing; compromise risk is high. |
| **3-4** | Weak protection. Some controls exist, but important attack paths remain open. |
| **5-6** | Acceptable baseline. Not terrible, but still missing several important protections. |
| **7-8** | Well protected. Good baseline plus several stronger hardening measures. |
| **9-10** | Very strong. Access control, exposure reduction, monitoring, recovery, and hardening are all handled well. |

When giving the score:
- Base it on actual findings, not vibes
- Penalize heavily for exposed admin services, weak SSH posture, missing firewall, missing backups/snapshots, or no recovery path
- Do not give `9-10` unless both prevention and recovery are strong
- If evidence is incomplete, say the score is preliminary

### Scoring Formula (0-10)

Use this simple scoring model so the result is consistent:

1. Start at `10`
2. Subtract penalties for weaknesses
3. Add small bonuses for strong measures already in place
4. Clamp the final result to the `0-10` range

**Major penalties**:
- `-3` public root SSH login or password-only SSH
- `-3` no firewall or clearly exposed unnecessary public services
- `-3` no provider console/rescue path and no clear recovery path
- `-2` admin panels / databases / Redis / Prometheus exposed to the public internet
- `-2` no tested SSH key access before disabling passwords
- `-2` no backups / no snapshot habit before risky changes
- `-2` outdated or unsupported OS / major unpatched security updates

**Medium penalties**:
- `-1` fail2ban missing on a public SSH endpoint
- `-1` weak SSH policy (`PermitRootLogin`, too many auth attempts, broad `AllowUsers`, etc.)
- `-1` Docker publishes ports publicly without deliberate review
- `-1` no automatic security updates or no patching routine
- `-1` no log visibility / monitoring for auth and service failures
- `-1` no rollback notes for SSH/firewall changes

**Small bonuses**:
- `+0.5` SSH key-only auth is correctly enforced and verified
- `+0.5` provider firewall + OS firewall are both configured correctly
- `+0.5` management access is limited to Tailscale/VPN or trusted IP ranges
- `+0.5` recovery path is tested (console/rescue/snapshot)
- `+0.5` strong exposure reduction: admin services bound to localhost/VPN only
- `+0.5` monitoring/integrity layers exist (`auditd`, AIDE, alerting, log review)

**Hard caps**:
- Maximum `4/10` if the server can likely lock the admin out and there is no proven recovery path
- Maximum `5/10` if SSH is still weak or public admin/database services are exposed
- Maximum `8/10` if basic hardening is good but recovery/monitoring is still weak
- `9-10` only if access control, exposure reduction, patching, monitoring, and recovery are all meaningfully covered

If exact facts are missing, do not pretend precision. Say `preliminary score` and explain what evidence is still needed.

Use this output structure in the final audit summary:
1. `Overall score: X/10`
2. `Brief summary` — 1-3 sentences in the selected report language
3. `Critical issues`
4. `What is already good`
5. `Next steps by priority`

### Phase 4: Fix

Walk through fixes in the safe rollout order below. For each fix, show the pre-check, the change, and how to verify it worked.

---

## VPS Hardening Quick Checklist

This is the core checklist — always available in context. For full configs and commands, read `${CLAUDE_PLUGIN_ROOT}/skills/security-hardening/references/vps.md`.

### Safe Rollout Order

Apply changes in this exact sequence to avoid lockout:

1. **Establish SSH key access** → test it in a fresh session
2. **Harden SSH config** → reload (not restart), test in new session, keep old session alive
3. **Enable firewall** → allow SSH first, then add other rules incrementally
4. **Install fail2ban** → useful, but only after allowlisting your own admin IP / VPN range
5. **Enable automatic updates** → low risk
6. **Kernel sysctl baseline** → safe subset only
7. **Advanced hardening** → CIS items, auditd, AIDE, CrowdSec — one at a time

### 1. SSH Hardening

```bash
# Check current config
sudo sshd -T | grep -E 'port |permitrootlogin|passwordauthentication|pubkeyauthentication|maxauthtries'

# Generate ed25519 key (on client machine)
ssh-keygen -t ed25519 -C "user@machine"

# Copy key to server
ssh-copy-id -i ~/.ssh/id_ed25519.pub user@server
```

Create `/etc/ssh/sshd_config.d/99-hardening.conf`:
```
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
MaxAuthTries 3
MaxStartups 10:30:60
AllowUsers your_username
X11Forwarding no
```

```bash
sudo sshd -t                     # Verify syntax — must be silent
sudo systemctl reload ssh        # Reload, don't restart
# TEST FROM A NEW SESSION before closing the current one
```

**Non-standard port** — optional noise reduction only, not a primary security control. Reduces log spam from automated scanners but doesn't protect against targeted attacks. If you change it:
- Add new port to firewall BEFORE removing old port
- Test new port BEFORE removing old SSH config
- Update fail2ban port config

### 2. Firewall (UFW)

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow from YOUR_IP to any port 22    # Or use: sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status verbose
```

**Docker bypasses UFW** — Docker manipulates iptables directly, so UFW rules don't apply to container-published ports. Mitigate by binding containers to localhost: `127.0.0.1:8080:8080` and proxying through nginx.

### 3. Fail2ban

```bash
sudo apt install fail2ban -y
```

Create `/etc/fail2ban/jail.local`:
```ini
[DEFAULT]
bantime = 3600
findtime = 600
maxretry = 5
# Anti-lockout: never forget to allowlist your own admin IP / VPN range first
# Example:
# ignoreip = 127.0.0.1/8 YOUR_PUBLIC_IP TAILSCALE_SUBNET

[sshd]
enabled = true
port = ssh
maxretry = 3

[recidive]
enabled = true
logpath = /var/log/fail2ban.log
banaction = %(banaction_allports)s
bantime = 604800
findtime = 86400
maxretry = 3
```

The recidive jail watches fail2ban's own log — if an IP gets banned 3 times in 24 hours, it gets banned for a week across all ports.

Before enabling fail2ban, set `ignoreip` for your own admin IP or VPN/Tailscale range. Otherwise the server can ban you during testing or after repeated reconnects.

```bash
sudo systemctl enable --now fail2ban
sudo fail2ban-client status sshd
```

### 4. Automatic Updates

```bash
sudo apt install unattended-upgrades -y
sudo dpkg-reconfigure -plow unattended-upgrades
```

Auto-reboot: only enable with explicit awareness of downtime. Check `/etc/apt/apt.conf.d/50unattended-upgrades` and set `Unattended-Upgrade::Automatic-Reboot` only if you have health checks and the service recovers automatically.

### 5. Kernel Sysctl (Safe Baseline)

Create `/etc/sysctl.d/99-security.conf`:
```ini
# Network hardening — safe, no breakage risk
net.ipv4.tcp_syncookies = 1
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv6.conf.all.accept_redirects = 0
net.ipv4.icmp_echo_ignore_broadcasts = 1
net.ipv4.conf.all.log_martians = 1
net.ipv4.tcp_fin_timeout = 15
net.ipv4.tcp_max_syn_backlog = 4096
```

```bash
sudo sysctl --system    # Apply
```

For advanced params (kptr_restrict, dmesg_restrict, ptrace_scope, sysrq, BPF restrictions) — see `${CLAUDE_PLUGIN_ROOT}/skills/security-hardening/references/vps.md` section "Advanced Sysctl". These can break debugging tools and monitoring agents; apply with caution.

### 6. Tailscale VPN (Recommended)

Hide management ports from the public internet entirely:
```bash
# After Tailscale is running:
sudo ufw allow in on tailscale0 to any port 22
sudo ufw delete allow 22/tcp          # Remove public SSH
```

Now SSH is only accessible via Tailscale network. Provider console remains as break-glass.

### 7. Secrets Management

Don't store API keys and tokens in plaintext config files. Options from simplest to most robust:

- **`pass` + GPG** — encrypted files on disk, `$(pass path/to/secret)` in scripts
- **`age`** — modern encryption, simpler than GPG: `age -e -R key.pub secrets.env`
- **SOPS** — encrypts values in YAML/JSON, keys stay readable, works with git
- **HashiCorp Vault** — full secrets manager with rotation and audit (overkill for single VPS)

---

## Verification Checklist

After hardening, verify each layer:

```bash
# SSH — effective config
sudo sshd -T | grep -E 'permitrootlogin|passwordauthentication|maxauthtries'

# SSH — test login from new session
ssh -v user@server

# Firewall — effective rules
sudo ufw status verbose

# Listening ports — what's actually public
sudo ss -tlnp

# Fail2ban — jails active
sudo fail2ban-client status

# Updates — timer active
systemctl status unattended-upgrades

# Sysctl — applied
sysctl net.ipv4.tcp_syncookies   # Should be 1
```

---

## Recovery / Break-Glass

### SSH Lockout
1. Access server via **provider web console / serial console** (Hetzner Console, AWS EC2 Serial Console, DigitalOcean Console)
2. Or boot into **rescue/recovery mode** from provider dashboard
3. Fix `/etc/ssh/sshd_config.d/99-hardening.conf` — revert the problematic setting
4. Restart SSH: `systemctl restart ssh`

### Firewall Lockout
1. Provider console → `sudo ufw disable` or `sudo ufw allow 22/tcp`
2. Or: provider-level firewall (AWS Security Group, Hetzner Firewall) — add SSH rule there

### Bad Sysctl
1. Boot into recovery → remove or comment out the problematic line in `/etc/sysctl.d/99-security.conf`
2. `sysctl --system` to re-apply clean config

### Failed Reboot
1. Provider console → check `journalctl -xb` for boot errors
2. Common causes: bad fstab entry, kernel module disabled that's needed, broken initramfs
3. Boot previous kernel from GRUB if available

---

## Stack-Specific References

Read the relevant reference file for your stack. Each is self-contained with a table of contents.

| Stack | Reference | When to Read |
|-------|-----------|-------------|
| VPS / Linux Server | `${CLAUDE_PLUGIN_ROOT}/skills/security-hardening/references/vps.md` | Full SSH/firewall/sysctl configs, CrowdSec, auditd, CIS benchmarks, recovery playbooks |
| WordPress | `${CLAUDE_PLUGIN_ROOT}/skills/security-hardening/references/wordpress.md` | wp-config hardening, PHP security, plugin vetting, WAF, malware scanning, backups |
| Next.js / React / Node.js | `${CLAUDE_PLUGIN_ROOT}/skills/security-hardening/references/nextjs.md` | Server Components, API security, headers/CSP, auth, Docker, CI/CD |
| Any Web App | `${CLAUDE_PLUGIN_ROOT}/skills/security-hardening/references/general-web.md` | OWASP Top 10, DNS, TLS, cookies, CORS, rate limiting, compliance, incident response |

---

## Quick Diagnostic Commands

Run these to get a fast picture of current security posture:

```bash
# Who's trying to break in?
sudo journalctl -u ssh --since "24 hours ago" | grep -c "Failed password"
sudo fail2ban-client status sshd 2>/dev/null

# What's exposed?
sudo ss -tlnp | grep -v 127.0.0.1

# Are updates current?
apt list --upgradable 2>/dev/null | tail -n +2 | wc -l

# Any suspicious processes?
ps aux --sort=-%mem | head -20

# Disk usage (full disk = broken logging = blind spot)
df -h /

# Last logins
last -n 20

# Cron jobs (check for unexpected entries)
for user in $(cut -f1 -d: /etc/passwd); do crontab -l -u $user 2>/dev/null | grep -v '^#' | grep -v '^$' && echo "--- $user ---"; done
```
