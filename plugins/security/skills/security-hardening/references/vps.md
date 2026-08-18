# VPS / Linux Server Hardening Reference

> Actionable hardening guide for Ubuntu/Debian VPS instances.
> Every section explains **why** the control matters and gives copy-paste commands.
> Tested against Ubuntu 22.04 LTS and 24.04 LTS; adapt paths for RHEL/Fedora.

---

## Table of Contents

1. [Anti-Lockout Operating Principle](#1-anti-lockout-operating-principle)
2. [Preflight Safety](#2-preflight-safety)
3. [Exposure Mapping](#3-exposure-mapping)
4. [SSH Hardening](#4-ssh-hardening)
5. [Firewall (UFW / nftables)](#5-firewall-ufw--nftables)
6. [Fail2ban](#6-fail2ban)
7. [CrowdSec IPS](#7-crowdsec-ips)
8. [Kernel Sysctl Hardening](#8-kernel-sysctl-hardening)
9. [Tailscale VPN Isolation](#9-tailscale-vpn-isolation)
10. [User Management](#10-user-management)
11. [Automatic Updates](#11-automatic-updates)
12. [auditd](#12-auditd)
13. [AIDE File Integrity](#13-aide-file-integrity)
14. [Trivy Scanning](#14-trivy-scanning)
15. [Log Monitoring](#15-log-monitoring)
16. [Recovery Playbooks](#16-recovery-playbooks)
17. [CIS Benchmark Items](#17-cis-benchmark-items)

---

## 1. Anti-Lockout Operating Principle

**Rule: preserving remote access is priority number one.** A perfectly hardened server
you cannot reach is a brick. Every change to SSH, firewall, or PAM must be validated
before the old session is closed.

### The Two-Session Rule

Always keep a root/sudo shell open in a second terminal while making access-affecting
changes. Only close it after you have confirmed login works from a *third* connection.

### Safe SSH/Firewall Sequencing

The correct order when tightening SSH and firewall together:

```
1.  Open two SSH sessions (A and B).
2.  In session A: edit sshd_config, run `sshd -T` to syntax-check.
3.  In session A: `systemctl reload ssh` (or `sshd`, distro-dependent; on Ubuntu usually `ssh`) (reload, NOT restart).
4.  In session B: open a NEW session C to verify login works.
5.  Only after C succeeds: apply firewall rules in session A.
6.  Test firewall from session C by opening session D.
7.  Only after D succeeds: close sessions A and B.
```

If you reverse steps 2-3 and 5 (firewall first, then SSH), a typo in sshd_config
can lock you out because the firewall already blocks your fallback port.

### Why `reload` not `restart`

`systemctl reload ssh` (or `sshd`, distro-dependent) tells the running daemon to re-read its config without
dropping existing connections. `restart` tears down all connections, including yours.
If the new config is broken, reload lets you fix it in-place; restart may kill the
session before you can react.

---

## 2. Preflight Safety

Before touching anything, guarantee you can recover.

### 2.1 Take a Snapshot

Every major provider supports point-in-time snapshots:

```bash
# Hetzner
hcloud server create-image --type snapshot --description "pre-hardening" <server-id>

# DigitalOcean
doctl compute droplet-action snapshot <droplet-id> --snapshot-name "pre-hardening"

# AWS
aws ec2 create-snapshot --volume-id vol-xxx --description "pre-hardening"
```

If the CLI is unavailable, use the provider web console. Snapshots are cheap insurance.

### 2.2 Verify Provider Console / Rescue Mode

Before locking down SSH, confirm you can reach the server through an out-of-band path:

- **Hetzner**: Robot console or rescue mode (boots a temporary Linux from network)
- **DigitalOcean**: Droplet Console (browser-based VNC)
- **AWS**: EC2 Serial Console (must be enabled per-account) or detach root volume and
  mount it on a rescue instance
- **Vultr / Linode**: Web console (noVNC)

Test the console **now** while SSH still works. If you have never used it, you will
fumble it during an emergency.

### 2.3 Second Session

Open a second SSH session and leave it idle. If your changes break authentication or
firewall rules, this session remains connected and lets you revert.

```bash
# In a separate terminal:
ssh user@server
sudo -i   # Escalate now so you don't need PAM later
```

### 2.4 Rollback Workflow

If a change breaks access:

```
1.  Try the existing second session first.
2.  If that is gone, use provider console / rescue mode.
3.  In rescue mode, mount the root filesystem:
      mount /dev/sda1 /mnt
4.  Revert the offending config:
      cp /mnt/etc/ssh/sshd_config.bak /mnt/etc/ssh/sshd_config
5.  Reboot into normal mode.
6.  If all else fails, restore the snapshot.
```

---

## 3. Exposure Mapping

Before hardening, understand what is currently exposed.

### 3.1 List Listening Services

```bash
# Show all TCP listeners with the owning process
ss -tlnp

# Alternative with file descriptor detail
sudo lsof -iTCP -sTCP:LISTEN -nP
```

Examine every line. If a service listens on `0.0.0.0` or `::`, it is reachable from
the public internet (unless a firewall blocks it). Services that only need local
access should bind to `127.0.0.1` or the Tailscale IP.

### 3.2 Public vs. Private Bind Strategy

| Service          | Should bind to         | Why                                      |
|------------------|------------------------|------------------------------------------|
| SSH              | `0.0.0.0` (or Tailscale only) | Needs remote access               |
| PostgreSQL       | `127.0.0.1`           | App connects locally                     |
| Redis            | `127.0.0.1`           | No reason to expose                      |
| Prometheus       | `127.0.0.1` or Tailscale IP | Scrape locally or over VPN        |
| Nginx/Caddy      | `0.0.0.0`             | Serves public traffic                    |
| Admin panels     | Tailscale IP or `127.0.0.1` | Never expose to public internet   |

Edit each service's config to set the bind address. For example, PostgreSQL:

```
# /etc/postgresql/16/main/postgresql.conf
listen_addresses = '127.0.0.1'
```

### 3.3 Provider Firewall / Security Groups

Most providers offer a network-level firewall that sits *outside* the VPS. Use it as
your first layer. It cannot be bypassed by anything running on the server (unlike
iptables, which Docker can modify).

Set the provider firewall to allow only:
- TCP 22 (SSH) from your IP or VPN range
- TCP 80, 443 (HTTP/S) from anywhere (if serving web traffic)
- Drop everything else

Then use the OS-level firewall (UFW/nftables) as the second layer.

---

## 4. SSH Hardening

SSH is the single most attacked service on any public server. Harden it aggressively.

### 4.1 Generate ed25519 Keys

ed25519 is faster, shorter, and has no known weaknesses compared to RSA. Generate
on your *local machine*, never on the server:

```bash
ssh-keygen -t ed25519 -C "yourname@machine" -f ~/.ssh/id_ed25519_vps
```

Copy the public key to the server:

```bash
ssh-copy-id -i ~/.ssh/id_ed25519_vps.pub user@server
```

### 4.2 sshd_config Hardening

Edit `/etc/ssh/sshd_config`. On Ubuntu 22.04+, drop-in files in
`/etc/ssh/sshd_config.d/` are included *before* the main file, so settings there
take precedence. Check with `sshd -T` to see the effective config.

Create `/etc/ssh/sshd_config.d/00-hardening.conf`:

```
# ── Authentication ───────────────────────────────────────────
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
AuthenticationMethods publickey
MaxAuthTries 3

# ── Access Control ───────────────────────────────────────────
AllowUsers deploy monitoring
# Or use group-based: AllowGroups ssh-users

# ── Connection Limits ────────────────────────────────────────
# start:rate:full — after 10 unauthenticated connections, randomly drop
# 30% of new ones, hard-refuse at 60 concurrent.
MaxStartups 10:30:60
LoginGraceTime 20
ClientAliveInterval 300
ClientAliveCountMax 2

# ── Cryptography Whitelist ───────────────────────────────────
# Only modern, audited algorithms. Drops anything CBC, SHA-1, or DH <3072.
KexAlgorithms sntrup761x25519-sha512@openssh.com,curve25519-sha256,curve25519-sha256@libssh.org
HostKeyAlgorithms ssh-ed25519,ssh-ed25519-cert-v01@openssh.com,rsa-sha2-512,rsa-sha2-256
Ciphers chacha20-poly1305@openssh.com,aes256-gcm@openssh.com,aes128-gcm@openssh.com
MACs hmac-sha2-512-etm@openssh.com,hmac-sha2-256-etm@openssh.com

# ── Misc ─────────────────────────────────────────────────────
X11Forwarding no
AllowTcpForwarding no
AllowAgentForwarding no
PermitTunnel no
Banner none
DebugLevel VERBOSE
```

### 4.3 Non-Standard Port (Optional Noise Reduction)

Changing the SSH port from 22 to something else (e.g., 2222) is **not a security
control**. Any port scanner finds it in seconds. It only reduces log noise from
automated bots that only try port 22.

If you do it, remember to update firewall rules *before* reloading sshd:

```bash
sudo ufw allow 2222/tcp comment 'SSH alt port'
sudo ufw reload
# THEN change Port in sshd_config and reload sshd
# THEN test login on new port
# ONLY THEN remove the old rule:
sudo ufw delete allow 22/tcp
```

### 4.4 SFTP Logging with Match Block

By default, SFTP operations are not logged. Add a Match block to capture them:

```
# Append to /etc/ssh/sshd_config.d/00-hardening.conf

Subsystem sftp internal-sftp -l INFO

Match Group sftponly
    ChrootDirectory /srv/sftp/%u
    ForceCommand internal-sftp -l INFO
    AllowTcpForwarding no
    X11Forwarding no
    PermitTunnel no
```

SFTP log lines go to AUTH log facility. Check with:

```bash
journalctl -u ssh -g "sftp"
```

### 4.5 Verification

Always validate the effective configuration before and after changes:

```bash
# Syntax check + dump effective config (catches typos, ordering issues)
sudo sshd -T

# Check a specific user's effective config
sudo sshd -T -C user=deploy,host=0.0.0.0,addr=0.0.0.0

# Dry-run test (does not affect running daemon)
sudo sshd -t
```

If `sshd -T` exits with an error, do NOT reload. Fix the config first.

---

## 5. Firewall (UFW / nftables)

### 5.1 UFW Basics

UFW is a frontend to nftables/iptables. It is simple and sufficient for most VPS use
cases.

```bash
sudo apt install ufw

# Set defaults: deny inbound, allow outbound
sudo ufw default deny incoming
sudo ufw default allow outgoing

# Allow SSH FIRST (anti-lockout)
sudo ufw allow 22/tcp comment 'SSH'

# Allow web traffic
sudo ufw allow 80/tcp comment 'HTTP'
sudo ufw allow 443/tcp comment 'HTTPS'

# Enable (this activates the firewall immediately)
sudo ufw enable

# Verify
sudo ufw status verbose
```

### 5.2 Rate Limiting

UFW has built-in connection rate limiting. It drops connections from an IP that
initiates more than 6 connections in 30 seconds.

```bash
# Replace the plain allow with a rate-limited rule for SSH
sudo ufw limit 22/tcp comment 'SSH rate-limited'
```

This is a blunt instrument — it does not distinguish successful from failed auth.
Use fail2ban for smarter banning.

### 5.3 Application Profiles

Create reusable profiles in `/etc/ufw/applications.d/`. Example for a custom app:

```ini
# /etc/ufw/applications.d/myapp
[MyApp]
title=My Application
description=Custom web application
ports=8080/tcp
```

Then use it:

```bash
sudo ufw allow MyApp
```

### 5.4 Provider Firewall Layer

Use the provider's firewall (Hetzner firewall, AWS Security Groups, DO Firewall) as
the outer layer. Benefits:

- Cannot be bypassed by anything on the server (including Docker)
- Filters traffic before it reaches the VPS, saving CPU
- Survives OS reinstall

Rule of thumb: provider firewall = coarse allow-list; OS firewall = fine-grained
per-service rules.

### 5.5 WARNING: Docker Bypasses UFW

**This is the single most common firewall misconfiguration on Linux servers.**

Docker manipulates iptables directly. When you `docker run -p 8080:80`, Docker
inserts PREROUTING and FORWARD rules that bypass UFW entirely. A service you thought
was firewalled is now exposed to the internet.

#### Diagnosis

```bash
# Show Docker's iptables chains
sudo iptables -L DOCKER -n -v
sudo iptables -t nat -L DOCKER -n -v
```

If you see ACCEPT rules for ports you did not whitelist in UFW, Docker has bypassed
your firewall.

#### Mitigation Option 1: Disable Docker iptables Management

```json
// /etc/docker/daemon.json
{
  "iptables": false
}
```

```bash
sudo systemctl restart docker
```

**Trade-off:** You must manually manage port forwarding and container networking.
Container-to-container communication via Docker networks still works, but published
ports (`-p`) will not be automatically forwarded.

#### Mitigation Option 2: Bind to Localhost Only

Instead of `-p 8080:80`, use `-p 127.0.0.1:8080:80`. The port is only reachable
from the server itself (or via a reverse proxy).

```yaml
# docker-compose.yml
services:
  app:
    ports:
      - "127.0.0.1:8080:80"
```

#### Mitigation Option 3: ufw-docker Utility

The `ufw-docker` project patches UFW to handle Docker correctly:

```bash
sudo wget -O /usr/local/bin/ufw-docker \
  https://github.com/chaifeng/ufw-docker/raw/master/ufw-docker
sudo chmod +x /usr/local/bin/ufw-docker
sudo ufw-docker install
sudo systemctl restart ufw
```

Then manage Docker port access through UFW:

```bash
sudo ufw-docker allow mycontainer 80/tcp
```

---

## 6. Fail2ban

Fail2ban watches log files for repeated failures and temporarily bans offending IPs
via firewall rules. It dramatically reduces brute-force noise.

### 6.1 Install and Enable

```bash
sudo apt install fail2ban
sudo systemctl enable --now fail2ban
```

### 6.2 SSH Jail

Create `/etc/fail2ban/jail.local` (never edit `jail.conf` directly — it gets
overwritten on upgrade):

```ini
[DEFAULT]
bantime  = 1h
findtime = 10m
maxretry = 3
banaction = ufw
ignoreip = 127.0.0.1/8 YOUR_PUBLIC_IP YOUR_VPN_OR_TAILSCALE_RANGE
# Send alerts (optional)
# destemail = you@example.com
# action = %(action_mwl)s

[sshd]
enabled  = true
port     = ssh
filter   = sshd
logpath  = /var/log/auth.log
maxretry = 3
bantime  = 1h
```

`ignoreip` should include your current admin IP and any trusted VPN/Tailscale range.
Without it, fail2ban can ban you during testing, repeated reconnects, or noisy auth
changes.

### 6.3 Recidive Jail (Escalating Bans)

The recidive jail watches fail2ban's own log. If an IP gets banned multiple times
across any jail, recidive escalates the ban to 1 week. This catches persistent
attackers who rotate their attack vector.

```ini
# Append to /etc/fail2ban/jail.local

[recidive]
enabled  = true
filter   = recidive
logpath  = /var/log/fail2ban.log
bantime  = 1w
findtime = 1d
maxretry = 3
# Use a separate action so recidive bans don't interfere with per-jail bans
banaction = ufw
```

### 6.4 WordPress / Nginx Jails

```ini
[nginx-http-auth]
enabled  = true
port     = http,https
filter   = nginx-http-auth
logpath  = /var/log/nginx/error.log
maxretry = 3
bantime  = 1h

[wordpress-login]
enabled  = true
port     = http,https
filter   = wordpress-login
logpath  = /var/log/nginx/access.log
maxretry = 5
bantime  = 1h
```

### 6.5 Custom Filter Creation

Create a filter for any log pattern. Example — ban IPs that hit a honeypot URL:

```ini
# /etc/fail2ban/filter.d/nginx-honeypot.conf
[Definition]
failregex = ^<HOST> .* "(GET|POST) /wp-login\.php
            ^<HOST> .* "(GET|POST) /xmlrpc\.php
            ^<HOST> .* "(GET|POST) /\.env
ignoreregex =
```

```ini
# In jail.local
[nginx-honeypot]
enabled  = true
port     = http,https
filter   = nginx-honeypot
logpath  = /var/log/nginx/access.log
maxretry = 1
bantime  = 1d
```

### 6.6 Useful Commands

```bash
# Check jail status
sudo fail2ban-client status sshd

# Unban an IP
sudo fail2ban-client set sshd unbanip 1.2.3.4

# Test a filter against a log file
sudo fail2ban-regex /var/log/auth.log /etc/fail2ban/filter.d/sshd.conf
```

---

## 7. CrowdSec IPS

CrowdSec is a collaborative intrusion prevention system. It parses logs like
fail2ban but also shares threat intelligence with a community blocklist. Use it
when you want:

- Shared IP reputation (community-sourced blocklists)
- More sophisticated detection (scenarios, not just regex)
- A dashboard for visibility

**When to skip it:** If you are running a single low-traffic VPS and fail2ban is
sufficient, CrowdSec adds complexity without proportional benefit. It also phones
home to the CrowdSec API (the community blocklist), which may not be acceptable in
air-gapped or privacy-sensitive environments.

### 7.1 Install

```bash
curl -s https://install.crowdsec.net | sudo bash
sudo apt install crowdsec crowdsec-firewall-bouncer-iptables
```

### 7.2 Collections

Collections bundle parsers and scenarios for a service. Install the ones you need:

```bash
# Linux system logs (auth, syslog)
sudo cscli collections install crowdsecurity/linux

# Nginx access/error logs
sudo cscli collections install crowdsecurity/nginx

# SSH brute force (more nuanced than fail2ban's regex)
sudo cscli collections install crowdsecurity/sshd
```

### 7.3 Firewall Bouncer

The bouncer is what actually blocks IPs. It inserts iptables/nftables rules.

```bash
# Check bouncer status
sudo cscli bouncers list

# Check current bans
sudo cscli decisions list
```

### 7.4 Alerts and Monitoring

```bash
# Real-time alerts
sudo cscli alerts list

# Metrics dashboard
sudo cscli metrics
```

### 7.5 Tradeoff vs. Fail2ban

| Aspect         | Fail2ban                    | CrowdSec                        |
|----------------|-----------------------------|----------------------------------|
| Complexity     | Low, single config file     | Medium, collections + bouncers   |
| Community data | None                        | Shared blocklists                |
| Resource use   | Minimal                     | ~50-80 MB RAM for the agent      |
| Privacy        | Fully local                 | Sends/receives IP reputation     |
| Maturity       | 20+ years, battle-tested    | Newer, rapidly evolving          |

You can run both simultaneously, but treat that as an advanced setup. Verify ban
behavior carefully and make sure trusted admin IPs or VPN ranges are allowlisted
before enabling both layers.

---

## 8. Kernel Sysctl Hardening

Sysctl parameters tune kernel behavior. Some are safe defaults that should be on
every server. Others restrict debugging and introspection features and can break
monitoring tools, container runtimes, or development workflows.

### 8.1 Safe Baseline

These settings are universally safe and should be applied to every server. They
defend against well-known network attacks with zero compatibility risk.

Create `/etc/sysctl.d/90-hardening.conf`:

```ini
# ── TCP SYN Flood Protection ────────────────────────────────
# SYN cookies prevent the SYN queue from being exhausted during
# a SYN flood. No legitimate connections are dropped.
net.ipv4.tcp_syncookies = 1

# ── Reverse Path Filtering ──────────────────────────────────
# Drop packets with source addresses that would not be routable
# back through the interface they arrived on. Prevents IP spoofing.
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1

# ── Disable ICMP Redirects ──────────────────────────────────
# ICMP redirects can be used to reroute traffic through an
# attacker's machine. A server should never accept them.
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv6.conf.all.accept_redirects = 0
net.ipv6.conf.default.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.default.send_redirects = 0

# ── Ignore Broadcast Pings (Smurf Attack) ───────────────────
# Smurf attacks send ICMP echo requests to broadcast addresses.
# The server should not participate in amplification.
net.ipv4.icmp_echo_ignore_broadcasts = 1

# ── Log Martian Packets ─────────────────────────────────────
# Packets with impossible source addresses (RFC 1918 on public
# interfaces, etc.) are logged. Useful for detecting spoofing
# and misconfigured networks.
net.ipv4.conf.all.log_martians = 1
net.ipv4.conf.default.log_martians = 1

# ── Disable Source Routing ───────────────────────────────────
# Source-routed packets let the sender dictate the path. No
# legitimate use on a server.
net.ipv4.conf.all.accept_source_route = 0
net.ipv4.conf.default.accept_source_route = 0
net.ipv6.conf.all.accept_source_route = 0
net.ipv6.conf.default.accept_source_route = 0

# ── Disable IPv6 Router Advertisements ───────────────────────
# A server should not auto-configure from router advertisements
# unless you are intentionally using SLAAC.
net.ipv6.conf.all.accept_ra = 0
net.ipv6.conf.default.accept_ra = 0
```

Apply immediately:

```bash
sudo sysctl --system
```

### 8.2 Advanced / High-Breakage Settings

These settings restrict kernel introspection and debugging interfaces. They improve
security but can break legitimate tools. **Test each one individually and verify
your monitoring/container stack still works before moving to the next.**

Create `/etc/sysctl.d/91-hardening-advanced.conf`:

```ini
# ── Restrict Kernel Pointer Exposure ────────────────────────
# Hides kernel addresses from /proc/kallsyms and similar.
# WARNING: Breaks perf, SystemTap, some profilers, and any tool
# that resolves kernel symbols. Safe for production servers that
# do not run kernel debugging.
kernel.kptr_restrict = 2

# ── Restrict dmesg Access ───────────────────────────────────
# Only root can read kernel ring buffer.
# WARNING: Breaks non-root monitoring agents that read dmesg.
# Most production setups can enable this safely.
kernel.dmesg_restrict = 1

# ── Restrict ptrace ─────────────────────────────────────────
# Only a parent process can ptrace its children (no cross-
# process attachment). Prevents many exploit techniques.
# WARNING: Breaks strace/gdb on already-running processes for
# non-root users. Debuggers still work for processes they launch.
# Set to 2 to restrict even root; 3 to disable ptrace entirely.
kernel.yama.ptrace_scope = 1

# ── Disable Magic SysRq ─────────────────────────────────────
# SysRq key combinations can reboot, kill processes, etc. from
# keyboard. On a headless VPS, there is no keyboard, so this is
# safe to disable. On a machine with physical access for
# recovery, keep it enabled (or set to a bitmask like 176).
kernel.sysrq = 0

# ── BPF Restrictions ────────────────────────────────────────
# Unprivileged BPF is used by some container runtimes and
# observability tools (Cilium, bpftrace, Falco).
# WARNING: Setting to 1 requires CAP_BPF for all BPF operations.
# Breaks unprivileged Cilium setups and user-space BPF tools.
kernel.unprivileged_bpf_disabled = 1

# ── Perf Event Restrictions ─────────────────────────────────
# Controls access to performance counters.
# 0 = unrestricted, 1 = restricted to privileged, 2 = no access
# WARNING: Setting to 2 breaks perf entirely for non-root.
# 1 is a good balance.
kernel.perf_event_paranoid = 2

# ── Protect FIFOs and Regular Files ─────────────────────────
# Prevents following FIFOs/regular files in world-writable sticky
# directories (like /tmp) unless the owner matches. Mitigates
# symlink/TOCTOU attacks.
# Generally safe. Rarely breaks anything.
fs.protected_fifos = 2
fs.protected_regular = 2
```

#### Settings to Apply Manually (Not in a Sysctl File)

```bash
# ── Disable Kernel Module Loading ────────────────────────────
# Once set to 1, no new kernel modules can be loaded until reboot.
# Apply AFTER all services have started (systemd is done loading
# modules at that point). Run manually or via a oneshot service.
#
# WARNING: HIGH BREAKAGE. Prevents loading of:
#   - USB drivers plugged in later
#   - Filesystem modules on first mount
#   - Docker overlay modules if not already loaded
#   - WireGuard/Tailscale if the module is not already loaded
#
# Only use on servers with a fully static, known workload.
# sudo sysctl -w kernel.modules_disabled=1

# ── Disable Unprivileged User Namespaces ─────────────────────
# User namespaces are the attack surface for many container escape
# and privilege escalation exploits.
# WARNING: BREAKS rootless Docker, rootless Podman, Flatpak,
# Chrome/Chromium sandbox, and some snap packages.
# On servers running only root-ful containers, this is safe.
# sudo sysctl -w kernel.unprivileged_userns_clone=0
```

---

## 9. Tailscale VPN Isolation

Tailscale creates a WireGuard-based mesh VPN. Use it to remove management ports
(SSH, databases, admin panels) from the public internet entirely.

### 9.1 Install

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --ssh  # Enable Tailscale SSH (optional)
```

### 9.2 Move SSH Behind Tailscale

The pattern: allow SSH on the Tailscale interface only, then remove the public rule.

```bash
# Step 1: Allow SSH on the Tailscale interface
sudo ufw allow in on tailscale0 to any port 22 proto tcp comment 'SSH via Tailscale'

# Step 2: Verify you can SSH via Tailscale IP
ssh user@100.x.y.z    # Your server's Tailscale IP

# Step 3: Only after step 2 succeeds, remove public SSH
sudo ufw delete allow 22/tcp
# or: sudo ufw delete allow 2222/tcp (if using alt port)
```

### 9.3 Bind Admin Services to Tailscale IP

Find your Tailscale IP:

```bash
tailscale ip -4
# Example: 100.100.12.34
```

Configure services to listen only on that IP:

```ini
# PostgreSQL: /etc/postgresql/16/main/postgresql.conf
listen_addresses = '127.0.0.1,100.100.12.34'

# Prometheus: /etc/prometheus/prometheus.yml
# (in the web config)
# --web.listen-address=100.100.12.34:9090

# Grafana: /etc/grafana/grafana.ini
[server]
http_addr = 100.100.12.34
```

Binding to the Tailscale IP means the service is only reachable by authenticated
Tailscale peers. Even if the firewall has a gap, the service does not listen on
the public interface.

### 9.4 Tailscale SSH (Optional)

Tailscale can handle SSH authentication itself, using your identity provider. This
eliminates SSH keys entirely for human operators while keeping key-based auth for
automation.

```bash
sudo tailscale up --ssh
```

Tailscale SSH logs are in `journalctl -u tailscaled`. Access is controlled via
Tailscale ACLs, not sshd_config.

---

## 10. User Management

### 10.1 No Root Login

Root login should be disabled at both the SSH level (Section 4) and the PAM level:

```bash
# Lock the root password (prevents console login too)
sudo passwd -l root
```

If you need root for recovery via provider console, keep a strong password but
disable SSH root login via `PermitRootLogin no`.

### 10.2 Least-Privilege Sudo

Do not add users to the `sudo` group by default. Create a purpose-specific sudoers
drop-in:

```bash
# /etc/sudoers.d/deploy
deploy ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart myapp, \
                            /usr/bin/systemctl status myapp, \
                            /usr/bin/journalctl -u myapp
```

Validate with:

```bash
sudo visudo -c -f /etc/sudoers.d/deploy
```

### 10.3 Sudo Logging

Log every sudo invocation to a dedicated file:

```bash
# /etc/sudoers.d/logging
Defaults logfile="/var/log/sudo.log"
Defaults log_input, log_output
Defaults iolog_dir="/var/log/sudo-io/%{user}"
```

This captures not just *what* was run but the full terminal I/O, which is invaluable
for auditing and incident response.

### 10.4 Password Complexity (libpam-pwquality)

Even with key-only SSH, local passwords matter for sudo and console access.

```bash
sudo apt install libpam-pwquality
```

Edit `/etc/security/pwquality.conf`:

```ini
minlen = 14
dcredit = -1      # At least 1 digit
ucredit = -1      # At least 1 uppercase
lcredit = -1      # At least 1 lowercase
ocredit = -1      # At least 1 special character
maxrepeat = 3     # No more than 3 consecutive identical chars
dictcheck = 1     # Reject dictionary words
```

### 10.5 PAM SHA-512 Password Hashing

Ensure passwords are stored with SHA-512 (not MD5 or SHA-256):

```bash
# Check current setting
grep "pam_unix.so" /etc/pam.d/common-password
```

The line should include `sha512`. If not:

```
# /etc/pam.d/common-password
password [success=1 default=ignore] pam_unix.so obscure use_authtok try_first_pass sha512 rounds=65536
```

The `rounds=65536` parameter increases the iteration count, making brute-force
cracking slower.

---

## 11. Automatic Updates

Unpatched software is the leading cause of server compromise. Automate security
updates.

### 11.1 unattended-upgrades

```bash
sudo apt install unattended-upgrades apt-listchanges
sudo dpkg-reconfigure -plow unattended-upgrades
```

Edit `/etc/apt/apt.conf.d/50unattended-upgrades`:

```
Unattended-Upgrade::Allowed-Origins {
    "${distro_id}:${distro_codename}-security";
    "${distro_id}ESMApps:${distro_codename}-apps-security";
};

// Remove unused kernels and dependencies
Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";
Unattended-Upgrade::Remove-Unused-Dependencies "true";

// Email notification
Unattended-Upgrade::Mail "you@example.com";
Unattended-Upgrade::MailReport "on-change";
```

### 11.2 needrestart

`needrestart` detects services that need restarting after a library update (e.g.,
libc, openssl). Without it, a patched library is not active until the service
restarts.

```bash
sudo apt install needrestart
```

Configure `/etc/needrestart/needrestart.conf`:

```perl
# Automatic restart mode:
# 'a' = auto (restarts without asking)
# 'i' = interactive
# 'l' = list only
$nrconf{restart} = 'a';
```

### 11.3 Auto-Reboot

Some updates (kernel, systemd) require a full reboot. **Only enable auto-reboot if
you have health checks, a load balancer, or a maintenance window.** An unmonitored
reboot can cause extended downtime if the server does not come back cleanly.

```
# /etc/apt/apt.conf.d/50unattended-upgrades
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "04:00";
Unattended-Upgrade::Automatic-Reboot-WithUsers "false";
```

If `Automatic-Reboot-WithUsers` is false, the reboot only happens if no users are
logged in. This prevents rebooting while an admin is mid-session.

**Verify it works:**

```bash
sudo unattended-upgrade --dry-run --debug
```

---

## 12. auditd

The Linux audit framework provides kernel-level logging of security-relevant events.
Unlike application logs, auditd cannot be bypassed by a compromised process (short of
a kernel exploit).

### 12.1 Install

```bash
sudo apt install auditd audispd-plugins
sudo systemctl enable --now auditd
```

### 12.2 Audit Rules

Create `/etc/audit/rules.d/hardening.rules`:

```
# ── Identity File Changes ────────────────────────────────────
# Detect modification of user/group/password databases.
-w /etc/passwd -p wa -k identity
-w /etc/shadow -p wa -k identity
-w /etc/group -p wa -k identity
-w /etc/gshadow -p wa -k identity
-w /etc/sudoers -p wa -k identity
-w /etc/sudoers.d/ -p wa -k identity

# ── SSH Configuration ───────────────────────────────────────
-w /etc/ssh/sshd_config -p wa -k sshd_config
-w /etc/ssh/sshd_config.d/ -p wa -k sshd_config

# ── Sudo Usage ───────────────────────────────────────────────
-w /usr/bin/sudo -p x -k sudo_usage
-w /usr/bin/su -p x -k su_usage

# ── Process Execution Tracking ───────────────────────────────
# Log every execve call. WARNING: This is verbose on busy servers.
# Consider limiting to specific users or paths in production.
-a always,exit -F arch=b64 -S execve -k exec_log
-a always,exit -F arch=b32 -S execve -k exec_log

# ── Kernel Module Loading ───────────────────────────────────
-w /sbin/insmod -p x -k modules
-w /sbin/modprobe -p x -k modules
-w /sbin/rmmod -p x -k modules
-a always,exit -F arch=b64 -S init_module,finit_module,delete_module -k modules

# ── Network Configuration Changes ───────────────────────────
-w /etc/hosts -p wa -k network_config
-w /etc/network/ -p wa -k network_config
-w /etc/netplan/ -p wa -k network_config

# ── Time Changes ────────────────────────────────────────────
-a always,exit -F arch=b64 -S adjtimex,settimeofday,clock_settime -k time_change
-w /etc/localtime -p wa -k time_change

# ── Make rules immutable (requires reboot to change) ────────
-e 2
```

Load the rules:

```bash
sudo augenrules --load
sudo auditctl -l   # Verify
```

### 12.3 Searching Audit Logs

```bash
# Find all identity file changes
sudo ausearch -k identity --interpret

# Find sudo usage in the last hour
sudo ausearch -k sudo_usage -ts recent

# Generate a summary report
sudo aureport --summary
sudo aureport --auth   # Authentication report
```

---

## 13. AIDE File Integrity

AIDE (Advanced Intrusion Detection Environment) creates a database of file hashes
and metadata. On subsequent runs, it reports any changes. This detects unauthorized
modifications to binaries, configs, and libraries.

### 13.1 Install and Initialize

```bash
sudo apt install aide

# Initialize the database (takes several minutes on large filesystems)
sudo aideinit

# The init creates two files; move the new one into place
sudo cp /var/lib/aide/aide.db.new /var/lib/aide/aide.db
```

### 13.2 Manual Check

```bash
sudo aide --check
```

Output shows Added, Removed, and Changed files with details on what changed
(permissions, checksum, size, mtime).

### 13.3 Configuration

Edit `/etc/aide/aide.conf` to customize what is monitored:

```
# Monitor critical directories
/etc    p+i+u+g+sha256
/usr/bin    p+i+u+g+sha256
/usr/sbin   p+i+u+g+sha256
/boot   p+i+u+g+sha256

# Exclude frequently changing files
!/var/log
!/var/cache
!/tmp
!/run
```

### 13.4 Cron Job

Run daily and email the report:

```bash
# /etc/cron.daily/aide-check
#!/bin/bash
/usr/bin/aide --check | mail -s "AIDE report $(hostname)" admin@example.com

# After legitimate changes (package updates), update the database:
# sudo aide --update
# sudo cp /var/lib/aide/aide.db.new /var/lib/aide/aide.db
```

```bash
sudo chmod 755 /etc/cron.daily/aide-check
```

After every legitimate change (apt upgrade, config edit), update the database so
future checks do not report known changes as anomalies.

---

## 14. Trivy Scanning

Trivy is a comprehensive vulnerability scanner for OS packages, container images,
filesystems, and IaC configs.

### 14.1 Install

```bash
sudo apt install -y wget apt-transport-https gnupg
wget -qO - https://aquasecurity.github.io/trivy-repo/deb/public.key | \
  sudo gpg --dearmor -o /usr/share/keyrings/trivy.gpg
echo "deb [signed-by=/usr/share/keyrings/trivy.gpg] https://aquasecurity.github.io/trivy-repo/deb generic main" | \
  sudo tee /etc/apt/sources.list.d/trivy.list
sudo apt update && sudo apt install -y trivy
```

### 14.2 Filesystem Scan

Scan the entire server for known vulnerabilities in installed packages:

```bash
# Full filesystem scan
trivy fs / --severity HIGH,CRITICAL

# Scan with JSON output for automation
trivy fs / --severity HIGH,CRITICAL -f json -o /var/log/trivy-fs.json
```

### 14.3 Container Image Scanning

```bash
# Scan an image before deploying
trivy image nginx:latest

# Scan all running containers
docker ps --format '{{.Image}}' | sort -u | while read img; do
  echo "=== $img ==="
  trivy image "$img" --severity HIGH,CRITICAL
done
```

### 14.4 Periodic Scanning

```bash
# /etc/cron.weekly/trivy-scan
#!/bin/bash
trivy fs / --severity HIGH,CRITICAL -f json -o /var/log/trivy-weekly.json
CRITICAL_COUNT=$(jq '[.Results[].Vulnerabilities[]? | select(.Severity=="CRITICAL")] | length' /var/log/trivy-weekly.json)
if [ "$CRITICAL_COUNT" -gt 0 ]; then
  echo "$CRITICAL_COUNT critical vulnerabilities found" | \
    mail -s "TRIVY ALERT: $(hostname)" admin@example.com
fi
```

---

## 15. Log Monitoring

Logs are only useful if someone (or something) reads them.

### 15.1 journald Configuration

Ensure journald persists logs across reboots and does not consume all disk:

```ini
# /etc/systemd/journald.conf
[Journal]
Storage=persistent
SystemMaxUse=500M
SystemMaxFileSize=50M
MaxRetentionSec=90day
Compress=yes
```

```bash
sudo systemctl restart systemd-journald
```

### 15.2 Essential Log Commands

```bash
# Failed SSH logins
journalctl -u ssh -g "Failed" --since "1 hour ago"

# All authentication events
journalctl SYSLOG_FACILITY=10 --since today

# Kernel messages (hardware errors, OOM kills)
journalctl -k --priority=err

# Follow multiple units
journalctl -u ssh -u fail2ban -u nginx -f
```

### 15.3 Loki + Grafana Stack

For multi-server environments or when you need dashboards and alerting:

```bash
# Install Promtail (log shipper) on each server
# It tails journal/files and pushes to Loki

# /etc/promtail/config.yml
server:
  http_listen_port: 9080
  grpc_listen_port: 0

positions:
  filename: /tmp/positions.yaml

clients:
  - url: http://loki.internal:3100/loki/api/v1/push

scrape_configs:
  - job_name: journal
    journal:
      max_age: 12h
      labels:
        job: systemd-journal
        host: myserver
    relabel_configs:
      - source_labels: ['__journal__systemd_unit']
        target_label: 'unit'
```

Bind Loki and Grafana to Tailscale IPs (see Section 9) so they are not exposed to
the public internet.

### 15.4 CrowdSec Dashboard

If using CrowdSec, its console provides a web dashboard at
`https://app.crowdsec.net/`. Register your instance:

```bash
sudo cscli console enroll <enrollment-key>
```

---

## 16. Recovery Playbooks

When things go wrong, speed matters. Have these procedures ready.

### 16.1 SSH Lockout

**Symptoms:** Cannot SSH in. Connection refused or timeout.

**Recovery path:**

1. Use provider web console (VNC/serial) to get a shell.
2. Log in as root (or the user with the root password).
3. Check what broke:

```bash
# Is sshd running?
systemctl status sshd

# Is the firewall blocking SSH?
ufw status
iptables -L -n | grep 22

# Is fail2ban blocking your IP?
fail2ban-client status sshd

# Is the config valid?
sshd -T
```

4. Fix the issue:

```bash
# Bad sshd_config: restore backup
cp /etc/ssh/sshd_config.bak /etc/ssh/sshd_config
systemctl reload ssh

# Firewall blocking: re-add SSH rule
ufw allow 22/tcp
ufw reload

# Fail2ban banned your IP:
fail2ban-client set sshd unbanip YOUR.IP.HERE
```

5. If the server does not boot at all, use rescue mode (provider-specific):
   - Boot into rescue
   - Mount root filesystem: `mount /dev/sda1 /mnt`
   - Edit configs under `/mnt/etc/`
   - Reboot into normal mode

### 16.2 Broken Firewall

**Symptoms:** All services unreachable, or wrong services exposed.

```bash
# Via console: disable UFW temporarily
ufw disable

# Check what rules existed
cat /etc/ufw/user.rules

# Re-enable with corrected rules
ufw reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
# ...add other rules...
ufw enable
```

### 16.3 Bad Sysctl

**Symptoms:** Server boots but services fail, networking broken, container runtime
crashes.

```bash
# Via console: check which sysctl file is causing issues
sysctl --system 2>&1 | grep -i error

# Disable the problematic file
mv /etc/sysctl.d/91-hardening-advanced.conf /etc/sysctl.d/91-hardening-advanced.conf.disabled
sysctl --system

# To reset a single value without reboot:
sysctl -w kernel.modules_disabled=0   # NOTE: This specific one cannot be unset without reboot
sysctl -w kernel.unprivileged_bpf_disabled=0
```

### 16.4 Failed Reboot

**Symptoms:** Server does not come back after reboot.

1. Wait 5 minutes (BIOS/cloud-init can be slow).
2. Try provider console.
3. If console shows a boot failure:
   - Boot into rescue mode
   - Mount root: `mount /dev/sda1 /mnt`
   - Check `/mnt/var/log/syslog` or `journalctl -D /mnt/var/log/journal/`
   - Common culprits: bad fstab entry, broken initramfs, kernel panic from
     `modules_disabled` set too early
4. Fix the issue, unmount, reboot normally.
5. If all else fails, restore the preflight snapshot.

---

## 17. CIS Benchmark Items

The CIS (Center for Internet Security) benchmarks are industry-standard hardening
checklists. These are high-value items from the Ubuntu CIS benchmark.

### 17.1 Disable Unused Filesystems

Prevent mounting of filesystems that have no legitimate use on a server. This
reduces the kernel attack surface.

```bash
# /etc/modprobe.d/cis-disable-filesystems.conf
install cramfs /bin/true
install freevxfs /bin/true
install jffs2 /bin/true
install hfs /bin/true
install hfsplus /bin/true
install squashfs /bin/true
install udf /bin/true
```

**Compatibility warning:** `squashfs` is required by snap packages. If you use
snaps, do NOT disable squashfs. `udf` is needed for reading UDF-formatted media
(rare on servers).

```bash
# Blacklist them too (prevents auto-loading)
echo "blacklist cramfs" | sudo tee -a /etc/modprobe.d/blacklist.conf
echo "blacklist freevxfs" | sudo tee -a /etc/modprobe.d/blacklist.conf
echo "blacklist jffs2" | sudo tee -a /etc/modprobe.d/blacklist.conf
echo "blacklist hfs" | sudo tee -a /etc/modprobe.d/blacklist.conf
echo "blacklist hfsplus" | sudo tee -a /etc/modprobe.d/blacklist.conf
```

### 17.2 /tmp Mount Options

`/tmp` should be a separate mount (or tmpfs) with restrictive options to prevent
executable code and privilege escalation via temporary files.

```bash
# /etc/fstab — add or modify the /tmp entry:
tmpfs /tmp tmpfs defaults,rw,nosuid,nodev,noexec,relatime,size=2G 0 0
```

```bash
# Apply without reboot
sudo mount -o remount /tmp

# Verify
mount | grep /tmp
# Should show: tmpfs on /tmp type tmpfs (rw,nosuid,nodev,noexec,relatime,size=2097152k)
```

**Options explained:**
- `nosuid`: Ignore SUID/SGID bits on files in /tmp
- `nodev`: Ignore device files in /tmp
- `noexec`: Prevent executing binaries from /tmp (breaks some installers; see below)

**Compatibility warning:** `noexec` on /tmp breaks software that compiles or runs
binaries from temp directories. Common offenders: some `apt` post-install scripts,
`pip install`, Java applications using /tmp for class loading. If you hit this, you
can temporarily remount: `sudo mount -o remount,exec /tmp`, run the installer, then
remount with noexec.

### 17.3 SUID/SGID and World-Writable File Audit

SUID binaries run as the file owner (often root) regardless of who executes them.
Every SUID binary is a potential privilege escalation vector. Audit regularly and
remove the SUID bit from anything not strictly needed.

```bash
# Find all SUID files
find / -perm /4000 -type f 2>/dev/null | sort

# Find all SGID files
find / -perm /2000 -type f 2>/dev/null | sort

# Find world-writable directories (excluding /tmp, /var/tmp, /proc, /sys)
find / -type d -perm -0002 ! -path "/proc/*" ! -path "/sys/*" \
  ! -path "/tmp" ! -path "/var/tmp" 2>/dev/null

# Find world-writable files
find / -type f -perm -0002 ! -path "/proc/*" ! -path "/sys/*" 2>/dev/null
```

**Typical SUID binaries to keep:** `sudo`, `su`, `passwd`, `mount`, `umount`, `ping`

**Candidates for SUID removal** (strip with `chmod u-s`):

```bash
# These are often not needed on a server:
sudo chmod u-s /usr/bin/chfn
sudo chmod u-s /usr/bin/chsh
sudo chmod u-s /usr/bin/newgrp
sudo chmod u-s /usr/bin/gpasswd
# Only remove mount/umount SUID if users never need to mount anything:
# sudo chmod u-s /usr/bin/mount
# sudo chmod u-s /usr/bin/umount
```

### 17.4 Service Removal

Disable and remove services that have no purpose on your server. Every running
service is attack surface.

```bash
# Common services to remove on a web/app server:
sudo systemctl disable --now avahi-daemon   # mDNS — not needed on a server
sudo systemctl disable --now cups           # Printing — not needed on a server
sudo systemctl disable --now bluetooth      # No Bluetooth on a VPS
sudo apt purge -y avahi-daemon cups bluetooth

# Check for other unnecessary listeners:
ss -tlnp | grep -v -E '(sshd|nginx|postgres|127\.0\.0\.1|::1)'
```

**Compatibility notes:**
- `avahi-daemon`: Required if your server uses mDNS for service discovery (rare on VPS, common in LAN setups)
- `cups`: Required only on print servers
- `rpcbind/nfs-common`: Required only if using NFS mounts
- `snapd`: Required if using snap packages (Ubuntu default); remove only if you manage packages via apt/docker exclusively

```bash
# Remove snapd if not using snaps (saves ~40MB RAM and attack surface)
sudo systemctl disable --now snapd snapd.socket
sudo apt purge -y snapd
sudo rm -rf /snap /var/snap /var/lib/snapd
```

### 17.5 Core Dump Restrictions

Core dumps can contain sensitive data (keys, passwords in memory). Disable for
non-debugging environments.

```bash
# /etc/security/limits.d/core.conf
*    hard    core    0
```

```ini
# /etc/sysctl.d/90-hardening.conf (add to existing file)
fs.suid_dumpable = 0
```

```ini
# /etc/systemd/coredump.conf
[Coredump]
Storage=none
ProcessSizeMax=0
```

### 17.6 Restrict cron and at

Limit who can schedule jobs:

```bash
# Only root and listed users can use cron
echo "root" | sudo tee /etc/cron.allow
echo "deploy" | sudo tee -a /etc/cron.allow
sudo rm -f /etc/cron.deny

# Same for at
echo "root" | sudo tee /etc/at.allow
sudo rm -f /etc/at.deny

# Restrict permissions on cron directories
sudo chmod 700 /etc/cron.d /etc/cron.daily /etc/cron.hourly \
  /etc/cron.weekly /etc/cron.monthly
```

---

## Quick-Reference Checklist

Use this after completing the hardening steps to verify coverage:

```
[ ] Snapshot taken before changes
[ ] Provider console access verified
[ ] SSH: ed25519 keys, password auth disabled, PermitRootLogin no
[ ] SSH: crypto whitelist applied, sshd -T clean
[ ] Firewall: default deny, only needed ports open
[ ] Firewall: Docker bypass addressed (if Docker is installed)
[ ] Fail2ban: sshd + recidive jails active
[ ] Sysctl: safe baseline applied
[ ] Sysctl: advanced settings tested individually (if applied)
[ ] Updates: unattended-upgrades enabled for security pocket
[ ] Users: no shared accounts, sudo logging enabled
[ ] Audit: auditd rules loaded, logs rotating
[ ] File integrity: AIDE initialized and cron scheduled
[ ] Logs: journald persistent, retention configured
[ ] Unused services removed
[ ] SUID audit completed
[ ] /tmp mounted with nosuid,nodev,noexec
[ ] Recovery: SSH lockout procedure documented and tested
```
