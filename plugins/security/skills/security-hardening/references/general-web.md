# General Web Application Security Reference

> Actionable hardening guidance for production web applications.
> Every recommendation includes commands, configs, or code you can apply immediately.

---

## Table of Contents

1. [OWASP Top 10 (2021) Mitigations](#1-owasp-top-10-2021-mitigations)
2. [DNS Security](#2-dns-security)
3. [SSL/TLS](#3-ssltls)
4. [HTTP Security Headers](#4-http-security-headers)
5. [Cookie Security](#5-cookie-security)
6. [CORS](#6-cors)
7. [Rate Limiting](#7-rate-limiting)
8. [Bot Protection](#8-bot-protection)
9. [Backup & Disaster Recovery](#9-backup--disaster-recovery)
10. [Incident Response Plan](#10-incident-response-plan)
11. [Security Monitoring Stack](#11-security-monitoring-stack)
12. [Compliance](#12-compliance)
13. [Third-Party Script Security](#13-third-party-script-security)
14. [API Security Patterns](#14-api-security-patterns)
15. [Secret Management](#15-secret-management)
16. [Penetration Testing](#16-penetration-testing)
17. [security.txt & Disclosure](#17-securitytxt--disclosure)
18. [Supply Chain Security](#18-supply-chain-security)

---

## 1. OWASP Top 10 (2021) Mitigations

### A01: Broken Access Control

- Deny by default. Every endpoint requires explicit authorization.
- Enforce server-side. Client-side checks are cosmetic only.
- Disable directory listing: `Options -Indexes` (Apache) or `autoindex off;` (nginx).
- Use parameterized ownership checks on every data access:

```sql
-- WRONG: trusts user-supplied ID
SELECT * FROM orders WHERE id = $1;

-- RIGHT: scope to authenticated user
SELECT * FROM orders WHERE id = $1 AND user_id = $2;
```

- Invalidate JWTs server-side on logout (maintain a denylist in Redis with TTL matching token expiry).
- Rate-limit admin endpoints separately from public ones.

### A02: Cryptographic Failures

- Hash passwords with **argon2id** (memory-hard, resists GPU attacks):

```python
# Python (argon2-cffi)
from argon2 import PasswordHasher
ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)
hashed = ph.hash(password)
ph.verify(hashed, password)  # raises on mismatch
```

- Encrypt data at rest with AES-256-GCM. Use authenticated encryption always.
- Enforce TLS 1.2+ everywhere. Disable TLS 1.0/1.1.
- Never store secrets in source code. See [Secret Management](#15-secret-management).
- Rotate encryption keys annually. Use envelope encryption (KMS wraps data keys).

### A03: Injection

- Use parameterized queries for ALL database access. No exceptions.

```python
# Python (psycopg2)
cursor.execute("SELECT * FROM users WHERE email = %s", (email,))

# Node.js (pg)
await pool.query('SELECT * FROM users WHERE email = $1', [email]);
```

- Validate and sanitize all user input with allowlists, not denylists.
- Use ORMs with parameterized query builders. Audit any raw SQL.
- For OS commands, use language-native libraries instead of shell execution.

### A04: Insecure Design

- Implement threat modeling before writing code (STRIDE framework).
- Apply rate limiting to credential recovery flows.
- Require MFA for admin accounts and sensitive operations:

```
# TOTP setup (pyotp)
import pyotp
secret = pyotp.random_base32()
totp = pyotp.TOTP(secret)
totp.verify(user_code)  # time-based verification
```

- Use allowlists for file uploads: validate MIME type, extension, AND magic bytes.

### A05: Security Misconfiguration

- Harden with Infrastructure as Code (IaC). Manual configs drift.
- Remove default accounts, sample apps, and debug endpoints before deployment.
- Disable detailed error messages in production. Return generic 500s.
- Automate misconfiguration scanning:

```bash
# Scan IaC for misconfigs
trivy config --severity HIGH,CRITICAL ./terraform/
checkov -d ./terraform/
```

- Set restrictive file permissions: `chmod 600` for configs with secrets.

### A06: Vulnerable and Outdated Components

- Audit dependencies in CI:

```bash
# Node.js
npm audit --audit-level=high

# Python
pip-audit --strict

# Go
govulncheck ./...

# Rust
cargo audit
```

- Pin dependency versions. Use lockfiles. Review lockfile diffs in PRs.
- Subscribe to security advisories (GitHub Dependabot, Snyk).
- Maintain an SBOM. See [Supply Chain Security](#18-supply-chain-security).

### A07: Identification and Authentication Failures

- Enforce minimum password length of 12 characters. Check against breached password lists (HaveIBeenPwned API).
- Implement account lockout: 5 failed attempts triggers 15-minute lockout.
- Regenerate session IDs after successful login. See [Cookie Security](#5-cookie-security).
- Use secure session storage (server-side, not JWT-in-localStorage).

### A08: Software and Data Integrity Failures

- Verify third-party script integrity with SRI. See [Third-Party Script Security](#13-third-party-script-security).
- Sign CI/CD artifacts. Pin GitHub Actions to SHA, not tags.
- Validate deserialized data. Never deserialize untrusted input with `pickle`, `eval`, or `unserialize`.

### A09: Security Logging and Monitoring Failures

- Log authentication events, access control failures, input validation failures, and application errors.
- Use structured logging (JSON) for machine parsing:

```json
{
  "timestamp": "2025-01-15T10:30:00Z",
  "level": "warn",
  "event": "auth_failure",
  "ip": "203.0.113.50",
  "user": "admin@example.com",
  "reason": "invalid_password",
  "attempt": 3
}
```

- Set up alerting for anomalous patterns (spike in 401s, mass data export).
- Retain logs for at least 90 days (GDPR) or 1 year (PCI DSS).

### A10: Server-Side Request Forgery (SSRF)

- Block requests to internal IPs. Validate URLs against an allowlist.

```python
import ipaddress

BLOCKED_RANGES = [
    ipaddress.ip_network('10.0.0.0/8'),
    ipaddress.ip_network('172.16.0.0/12'),
    ipaddress.ip_network('192.168.0.0/16'),
    ipaddress.ip_network('127.0.0.0/8'),
    ipaddress.ip_network('169.254.0.0/16'),  # link-local
    ipaddress.ip_network('::1/128'),
    ipaddress.ip_network('fc00::/7'),         # IPv6 private
]

def is_safe_url(url):
    """Resolve hostname and check against blocked ranges."""
    import socket
    hostname = urllib.parse.urlparse(url).hostname
    for info in socket.getaddrinfo(hostname, None):
        addr = ipaddress.ip_address(info[4][0])
        if any(addr in net for net in BLOCKED_RANGES):
            return False
    return True
```

- Disable HTTP redirects in server-side HTTP clients, or re-validate after each redirect.
- Use a dedicated egress proxy for outbound requests from user-supplied URLs.

---

## 2. DNS Security

### DNSSEC

DNSSEC cryptographically signs DNS records, preventing spoofing and cache poisoning.

```bash
# Verify DNSSEC for a domain
dig +dnssec example.com A
dig +short example.com DS  # Check DS record at parent

# Full chain validation
delv @8.8.8.8 example.com A +rtrace
```

Enable DNSSEC through your DNS provider (Cloudflare, Route53, etc.) -- they handle key rotation.

### CAA Records

CAA records restrict which CAs can issue certificates for your domain. This prevents unauthorized certificate issuance.

```dns
; Only Let's Encrypt and DigiCert may issue certificates
example.com.  IN  CAA  0 issue "letsencrypt.org"
example.com.  IN  CAA  0 issue "digicert.com"

; No wildcard certificates except from Let's Encrypt
example.com.  IN  CAA  0 issuewild "letsencrypt.org"

; Report violations
example.com.  IN  CAA  0 iodef "mailto:security@example.com"
```

```bash
# Verify CAA records
dig CAA example.com +short
```

### Additional DNS Hardening

- **Registrar lock**: Enable clientTransferProhibited and clientDeleteProhibited at your registrar. Prevents domain hijacking.
- **Zone transfer prevention**: Restrict AXFR to known secondaries only.

```nginx
# BIND named.conf
zone "example.com" {
    type master;
    allow-transfer { 198.51.100.2; };  # Only your secondary NS
    allow-query { any; };
};
```

- **WHOIS privacy**: Enable registrar privacy protection. Exposed WHOIS data enables targeted phishing.
- **Monitor certificate transparency logs**: Use crt.sh or certspotter to detect unauthorized certs.

```bash
# Check CT logs for your domain
curl -s "https://crt.sh/?q=%.example.com&output=json" | jq '.[].name_value' | sort -u
```

---

## 3. SSL/TLS

### Certificate Management with Let's Encrypt

```bash
# Install certbot
sudo apt install certbot python3-certbot-nginx

# Obtain certificate (nginx plugin handles config)
sudo certbot --nginx -d example.com -d www.example.com

# Verify auto-renewal
sudo certbot renew --dry-run

# Renewal runs via systemd timer
sudo systemctl list-timers | grep certbot
```

### OCSP Stapling (nginx)

OCSP stapling improves TLS handshake performance and privacy. The server fetches the OCSP response and staples it to the TLS handshake, so the client does not need to contact the CA.

```nginx
# /etc/nginx/conf.d/ssl-ocsp.conf
ssl_stapling on;
ssl_stapling_verify on;
ssl_trusted_certificate /etc/letsencrypt/live/example.com/chain.pem;
resolver 1.1.1.1 8.8.8.8 valid=300s;
resolver_timeout 5s;
```

### TLS 1.2/1.3 Configuration (nginx)

```nginx
# /etc/nginx/conf.d/ssl-hardened.conf
ssl_protocols TLSv1.2 TLSv1.3;

# TLS 1.3 ciphers (configured separately, always strong)
ssl_conf_command Ciphersuites TLS_AES_256_GCM_SHA384:TLS_CHACHA20_POLY1305_SHA256:TLS_AES_128_GCM_SHA256;

# TLS 1.2 ciphers (explicit, no weak ciphers)
ssl_ciphers ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256;
ssl_prefer_server_ciphers on;

# Session settings (reduce handshake overhead)
ssl_session_timeout 1d;
ssl_session_cache shared:SSL:10m;
ssl_session_tickets off;  # Disable for forward secrecy

# DH parameters (generate once: openssl dhparam -out /etc/nginx/dhparam.pem 4096)
ssl_dhparam /etc/nginx/dhparam.pem;
```

### Testing TLS Configuration

```bash
# testssl.sh -- comprehensive local testing
git clone --depth 1 https://github.com/drwetter/testssl.sh.git
./testssl.sh/testssl.sh https://example.com

# SSL Labs (online, thorough, gives letter grade)
# https://www.ssllabs.com/ssltest/analyze.html?d=example.com

# Quick check with openssl
openssl s_client -connect example.com:443 -tls1_3
openssl s_client -connect example.com:443 -tls1_1  # Should fail
```

---

## 4. HTTP Security Headers

### Full nginx Configuration Block

```nginx
# /etc/nginx/conf.d/security-headers.conf
# Include this in your server blocks: include /etc/nginx/conf.d/security-headers.conf;

# Prevent MIME type sniffing (stops browsers from guessing content types)
add_header X-Content-Type-Options "nosniff" always;

# Prevent clickjacking (page cannot be embedded in iframes)
add_header X-Frame-Options "DENY" always;

# Control referrer information leakage
add_header Referrer-Policy "strict-origin-when-cross-origin" always;

# Force HTTPS for 2 years, include subdomains, allow HSTS preload list
add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;

# Restrict browser features (disable what you don't use)
add_header Permissions-Policy "accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()" always;

# Content Security Policy (customize per application)
add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; upgrade-insecure-requests;" always;

# Cross-Origin policies
# Baseline-safe default:
add_header Cross-Origin-Opener-Policy "same-origin" always;

# Advanced / opt-in only:
# Cross-Origin-Embedder-Policy and a strict Cross-Origin-Resource-Policy can break
# third-party embeds, assets, analytics, payments, and other cross-origin flows.
# Only enable after validating your app actually needs cross-origin isolation.
# add_header Cross-Origin-Embedder-Policy "require-corp" always;
# add_header Cross-Origin-Resource-Policy "same-origin" always;
```

### CSP Reporting

Deploy CSP in report-only mode first to catch violations before enforcing:

```nginx
# Report-only mode (does not block, only reports)
add_header Content-Security-Policy-Report-Only "default-src 'self'; report-uri /csp-report; report-to csp-endpoint;" always;

# Reporting API configuration
add_header Reporting-Endpoints 'csp-endpoint="https://example.com/csp-report"' always;
```

Handle reports server-side:

```python
# Flask CSP report endpoint
@app.route('/csp-report', methods=['POST'])
def csp_report():
    report = request.get_json(force=True)
    logger.warning("CSP violation", extra={"csp_report": report})
    return '', 204
```

### Testing Headers

```bash
# Quick check
curl -sI https://example.com | grep -iE '(x-content|x-frame|strict-transport|content-security|referrer-policy|permissions-policy|cross-origin)'

# Comprehensive scan (gives letter grade)
# https://securityheaders.com/?q=example.com

# Mozilla Observatory
# https://observatory.mozilla.org/
```

---

## 5. Cookie Security

### Secure Cookie Attributes

| Attribute | Purpose | Recommendation |
|-----------|---------|----------------|
| `Secure` | Only transmit over HTTPS | **Always set** on all cookies |
| `HttpOnly` | Inaccessible to JavaScript (prevents XSS exfiltration) | **Always set** on session cookies |
| `SameSite=Lax` | Sent on top-level navigations only (CSRF defense) | Default for most cookies |
| `SameSite=Strict` | Only sent on same-site requests | For sensitive actions |
| `SameSite=None; Secure` | Cross-site (required for third-party embeds) | Avoid unless necessary |
| `Path=/` | Cookie scope | Set to narrowest needed path |
| `Domain` | Omit to restrict to exact origin | Do not set unless subdomains need it |
| `Max-Age` | Expiry in seconds | Set finite lifetime |

### Cookie Prefixes

Cookie prefixes provide additional guarantees enforced by the browser:

```
# __Host- prefix: requires Secure, no Domain, Path=/
Set-Cookie: __Host-session=abc123; Secure; HttpOnly; SameSite=Lax; Path=/

# __Secure- prefix: requires Secure flag
Set-Cookie: __Secure-token=xyz789; Secure; HttpOnly; SameSite=Strict; Path=/
```

`__Host-` is stronger: the browser rejects the cookie if `Secure` is missing, `Domain` is set, or `Path` is not `/`. This prevents subdomain attacks.

### Session Management

```python
# After successful login: regenerate session ID
# This prevents session fixation attacks
from flask import session
session.regenerate()  # Or framework equivalent

# Framework-agnostic approach
old_data = session.copy()
session.clear()
session.update(old_data)
session.sid = generate_new_session_id()  # New ID, same data
```

- **Regenerate session ID** after login, privilege escalation, and password change.
- **Absolute timeout**: Expire sessions after 12 hours regardless of activity.
- **Idle timeout**: Expire after 30 minutes of inactivity.
- **Server-side storage**: Store session data server-side (Redis, database). The cookie holds only an opaque session ID.
- **Invalidate on logout**: Delete the server-side session record. Do not rely on cookie expiry alone.

---

## 6. CORS

### nginx Configuration with Origin Allowlist

```nginx
# /etc/nginx/conf.d/cors.conf

# Map allowed origins (allowlist approach)
map $http_origin $cors_origin {
    default "";
    "https://app.example.com"     "https://app.example.com";
    "https://staging.example.com" "https://staging.example.com";
    "https://admin.example.com"   "https://admin.example.com";
}

server {
    # ... other config ...

    # CORS headers (only set if origin is in allowlist)
    add_header Access-Control-Allow-Origin $cors_origin always;
    add_header Access-Control-Allow-Methods "GET, POST, PUT, DELETE, OPTIONS" always;
    add_header Access-Control-Allow-Headers "Authorization, Content-Type, X-Requested-With" always;
    add_header Access-Control-Allow-Credentials "true" always;
    add_header Access-Control-Max-Age "7200" always;

    # CRITICAL: Vary by Origin so caches don't serve wrong CORS headers
    add_header Vary "Origin" always;

    # Handle preflight requests
    if ($request_method = 'OPTIONS') {
        return 204;
    }
}
```

### Anti-Patterns to Avoid

```nginx
# NEVER: Wildcard with credentials (browsers reject this)
add_header Access-Control-Allow-Origin "*";
add_header Access-Control-Allow-Credentials "true";

# NEVER: Reflect arbitrary Origin header (defeats purpose of CORS)
add_header Access-Control-Allow-Origin $http_origin;

# NEVER: Overly broad methods
add_header Access-Control-Allow-Methods "*";
```

- Always use a strict allowlist of origins.
- Set `Vary: Origin` so CDN/proxy caches key on origin.
- Set `Access-Control-Max-Age` to cache preflight responses (7200s = 2 hours).
- For public APIs that never use cookies, `Access-Control-Allow-Origin: *` without credentials is acceptable.

---

## 7. Rate Limiting

### nginx Rate Limiting

```nginx
# /etc/nginx/conf.d/rate-limiting.conf

# Define rate limit zones (stored in shared memory)
limit_req_zone $binary_remote_addr zone=general:10m rate=10r/s;
limit_req_zone $binary_remote_addr zone=login:10m rate=5r/m;
limit_req_zone $binary_remote_addr zone=api:10m rate=30r/s;

# General pages
location / {
    limit_req zone=general burst=20 nodelay;
    limit_req_status 429;
    # ...
}

# Login endpoint (strict)
location /auth/login {
    limit_req zone=login burst=3 nodelay;
    limit_req_status 429;
    # ...
}

# API endpoints
location /api/ {
    limit_req zone=api burst=50 nodelay;
    limit_req_status 429;
    # ...
}
```

### Tiered Rate Limits by Endpoint Type

| Endpoint Type | Rate | Burst | Rationale |
|---------------|------|-------|-----------|
| Static assets | 50/s | 100 | High volume, low cost |
| Public pages | 10/s | 20 | Normal browsing |
| API (authenticated) | 30/s | 50 | Higher for legitimate apps |
| Login / register | 5/min | 3 | Credential stuffing defense |
| Password reset | 3/min | 1 | Brute force prevention |
| Admin endpoints | 10/s | 10 | Authenticated, still limit |
| Webhooks inbound | 100/s | 200 | Burst-tolerant |

### Application-Level Rate Limiting

Per-user rate limiting (IP-based limiting is insufficient for authenticated APIs):

```python
# Redis sliding window (Python)
import redis, time

r = redis.Redis()

def check_rate_limit(user_id, limit=100, window=60):
    """Sliding window rate limit: `limit` requests per `window` seconds."""
    key = f"ratelimit:{user_id}"
    now = time.time()
    pipe = r.pipeline()
    pipe.zremrangebyscore(key, 0, now - window)  # Remove expired
    pipe.zadd(key, {f"{now}": now})               # Add current
    pipe.zcard(key)                                # Count in window
    pipe.expire(key, window)                       # TTL cleanup
    _, _, count, _ = pipe.execute()
    return count <= limit
```

### Response Headers

Include rate limit info in responses so clients can self-throttle:

```
RateLimit-Limit: 100
RateLimit-Remaining: 42
RateLimit-Reset: 1705312800
Retry-After: 30
```

### Tools

- **redis-cell**: Redis module implementing GCRA (generic cell rate algorithm). High performance, atomic.
- **express-rate-limit**: Simple middleware for Express.js apps.
- **Cloud WAF**: AWS WAF, Cloudflare Rate Limiting, GCP Cloud Armor -- offload at the edge.

---

## 8. Bot Protection

### CAPTCHA

- **hCaptcha**: Privacy-focused, GDPR-compliant, free tier available. Preferred over reCAPTCHA.
- **Cloudflare Turnstile**: No visual challenge (invisible), free, privacy-first.

Deploy on: login, registration, password reset, contact forms. Not on every page load.

### Honeypot Fields

Hidden form fields that humans never fill but bots auto-populate:

```html
<form action="/register" method="POST">
  <!-- Honeypot: hidden via CSS, not display:none (some bots skip display:none) -->
  <div style="position:absolute;left:-9999px;" aria-hidden="true">
    <label for="website">Website</label>
    <input type="text" id="website" name="website" tabindex="-1" autocomplete="off">
  </div>

  <label for="email">Email</label>
  <input type="email" id="email" name="email" required>

  <button type="submit">Register</button>
</form>
```

```python
# Server-side: reject if honeypot is filled
if request.form.get('website'):
    logger.warning("Honeypot triggered", extra={"ip": request.remote_addr})
    abort(422)
```

### Behavioral Analysis

- Track mouse movement, scroll patterns, time-on-page, keypress timing.
- Bots complete forms in <2 seconds; humans rarely do.
- Flag requests with no JS execution (headless browsers can be detected via navigator properties).

### TLS Fingerprinting

JA3/JA4 fingerprints identify clients by their TLS ClientHello parameters. Useful for detecting known bot libraries.

```bash
# Capture JA3 fingerprints with tshark
tshark -r capture.pcap -Y "tls.handshake.type == 1" \
  -T fields -e tls.handshake.ja3

# Compare against known bot fingerprints
# https://ja3er.com/
```

- Use CrowdSec or Cloudflare Bot Management for JA4 fingerprinting at scale.
- Do not rely solely on fingerprinting; determined attackers rotate fingerprints.

---

## 9. Backup & Disaster Recovery

### 3-2-1 Rule

Keep **3** copies of data, on **2** different media types, with **1** offsite. This is the minimum viable backup strategy.

### PostgreSQL Backup to S3

```bash
#!/bin/bash
# /usr/local/bin/pg-backup.sh
set -euo pipefail

DB_NAME="appdb"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="/var/backups/postgres"
S3_BUCKET="s3:my-backups/postgres"
RETENTION_DAYS=30

mkdir -p "$BACKUP_DIR"

# Compressed backup with checksums
pg_dump -Fc --no-owner "$DB_NAME" > "$BACKUP_DIR/${DB_NAME}_${TIMESTAMP}.dump"
sha256sum "$BACKUP_DIR/${DB_NAME}_${TIMESTAMP}.dump" > "$BACKUP_DIR/${DB_NAME}_${TIMESTAMP}.sha256"

# Upload to S3-compatible storage via rclone
rclone copy "$BACKUP_DIR/${DB_NAME}_${TIMESTAMP}.dump" "$S3_BUCKET/" --checksum
rclone copy "$BACKUP_DIR/${DB_NAME}_${TIMESTAMP}.sha256" "$S3_BUCKET/" --checksum

# Clean up local backups older than retention period
find "$BACKUP_DIR" -name "*.dump" -mtime +$RETENTION_DAYS -delete
find "$BACKUP_DIR" -name "*.sha256" -mtime +$RETENTION_DAYS -delete

echo "Backup completed: ${DB_NAME}_${TIMESTAMP}.dump"
```

```bash
# Schedule via cron (daily at 2 AM)
echo "0 2 * * * /usr/local/bin/pg-backup.sh >> /var/log/pg-backup.log 2>&1" | crontab -
```

### RTO/RPO Tiers

| Tier | RPO (max data loss) | RTO (max downtime) | Strategy |
|------|--------------------|--------------------|----------|
| Tier 1 (critical) | < 1 min | < 15 min | Streaming replication + auto-failover |
| Tier 2 (important) | < 1 hour | < 1 hour | WAL archiving + warm standby |
| Tier 3 (standard) | < 24 hours | < 4 hours | Daily pg_dump + S3 |
| Tier 4 (archival) | < 7 days | < 24 hours | Weekly dumps + offsite |

### Backup Checklist

- [ ] Backups run on schedule (check cron logs)
- [ ] Backups are encrypted at rest (rclone crypt or server-side encryption)
- [ ] Backup integrity verified monthly (restore to test environment)
- [ ] Offsite copy exists (different cloud region or provider)
- [ ] Retention policy enforced (old backups pruned)
- [ ] Monitoring alerts on backup failure
- [ ] Backup credentials are separate from application credentials

### Quarterly DR Drill Template

```
DR Drill: Q[N] [YYYY]
Date: ___
Facilitator: ___

Scenario: [Primary database server is destroyed]

Steps:
1. [ ] Identify most recent backup: ___
2. [ ] Provision replacement infrastructure: ___ min
3. [ ] Restore database from backup: ___ min
4. [ ] Verify data integrity (row counts, checksums): ___ min
5. [ ] Switch DNS / load balancer: ___ min
6. [ ] Run smoke tests on restored service: ___ min

Total recovery time: ___ min (target: ___ min)
Data loss: ___ min (target: ___ min)

Issues found:
- ___

Action items:
- ___
```

---

## 10. Incident Response Plan

### 5 Phases

#### Phase 1: Preparation

- Maintain an updated asset inventory (servers, domains, data stores).
- Establish an incident response team with clear roles (IC, comms, engineering).
- Maintain out-of-band communication (phone tree, Signal group, not dependent on compromised infra).
- Pre-authorize containment actions (e.g., "engineers may isolate servers without VP approval").
- Rehearse quarterly with DR drills and tabletop exercises.

#### Phase 2: Detection & Analysis

- Monitor alerts from SIEM, IDS, application logs.
- Classify severity immediately (see table below).
- Preserve evidence: snapshot disk, capture network traffic, export logs.
- Document timeline from first indicator onward.

#### Phase 3: Containment

- **Short-term**: Isolate affected systems (firewall rules, security group changes). Do not shut down -- you lose volatile memory.
- **Long-term**: Patch vulnerability, rotate compromised credentials, deploy clean systems.
- Communicate status to stakeholders per severity protocol.

#### Phase 4: Eradication & Recovery

- Remove attacker access (revoke sessions, rotate all keys, patch entry vector).
- Restore from known-good backups.
- Monitor restored systems closely for 72 hours post-recovery.
- Verify no persistence mechanisms remain (cron jobs, SSH keys, webshells).

#### Phase 5: Post-Incident Review

- Conduct blameless post-mortem within 5 business days.
- Document: timeline, root cause, what worked, what failed, action items.
- Update runbooks and detection rules based on findings.
- Share lessons across the organization.

### Severity Classification

| Severity | Description | Response Time | Examples |
|----------|-------------|---------------|----------|
| P1 (Critical) | Active breach, data exfiltration, service down | 15 min | Ransomware, DB dump, admin compromise |
| P2 (High) | Confirmed vulnerability being exploited, partial outage | 1 hour | SQLi in production, exposed credentials |
| P3 (Medium) | Vulnerability found, no active exploitation | 4 hours | Unpatched CVE, misconfiguration |
| P4 (Low) | Security improvement, informational | Next sprint | Hardening task, policy update |

### Template File Structure

```
incidents/
  YYYY-MM-DD-incident-title/
    timeline.md          # Chronological event log
    evidence/            # Screenshots, log excerpts, pcaps
    containment-actions.md
    root-cause-analysis.md
    post-mortem.md       # Blameless retrospective
    action-items.md      # Follow-up tasks with owners and deadlines
```

---

## 11. Security Monitoring Stack

### SIEM & Log Platforms

| Tool | Type | Cost | Best For |
|------|------|------|----------|
| **Wazuh** | SIEM + HIDS | Free/OSS | On-prem, compliance reporting |
| **Grafana Loki** | Log aggregation | Free/OSS | Kubernetes, existing Grafana users |
| **ELK Stack** | Log search + analytics | Free/OSS (self-hosted) | Full-text log search |
| **CrowdSec** | Collaborative IPS | Free/OSS | IP reputation, community blocklists |
| **Datadog** | Full observability | Paid | SaaS, low-ops overhead |

### IDS/IPS

```bash
# Install Suricata (network IDS)
sudo apt install suricata
sudo suricata-update  # Fetch latest rulesets
sudo systemctl enable --now suricata

# Verify rules loaded
sudo suricata --build-info | grep -i "rules"
tail -f /var/log/suricata/fast.log

# Install CrowdSec (collaborative IPS)
curl -s https://install.crowdsec.net | sudo sh
sudo apt install crowdsec crowdsec-firewall-bouncer-iptables
sudo cscli hub update
sudo cscli collections install crowdsecurity/nginx
sudo systemctl enable --now crowdsec
```

### Log Aggregation Essentials

- Centralize logs from all servers. Never rely on logs only on the host (attacker deletes them).
- Ship logs in real-time (not batch). Use rsyslog, Fluent Bit, or Vector.
- Ensure log timestamps are synchronized (NTP).
- Protect log storage: append-only, separate credentials, different security domain.

### Minimal Viable Monitoring Setup

For small teams with limited budget, deploy at minimum:

1. **CrowdSec** on all public-facing servers (free, community intelligence).
2. **Fail2ban** for SSH and web auth brute force (already on most servers).
3. **Uptime monitoring**: Uptime Kuma (self-hosted, free), or Better Stack.
4. **Log shipping**: Fluent Bit to S3 or Loki. Searchable and retained.
5. **Alert on**: failed logins > threshold, new SSH keys, config file changes, unusual outbound traffic.

---

## 12. Compliance

### GDPR Checklist

GDPR applies if you process data of EU residents, regardless of where your company is based.

- [ ] **Lawful basis**: Document the legal basis for each data processing activity (consent, legitimate interest, contract, legal obligation).
- [ ] **Privacy policy**: Clear, plain-language policy covering what you collect, why, how long, and who you share with.
- [ ] **Right to access**: Users can request all data you hold on them. Response within 30 days.
- [ ] **Right to erasure**: Users can request deletion. Implement a "delete my account" flow that purges PII from all systems (including backups, after retention period).
- [ ] **Right to portability**: Export user data in machine-readable format (JSON, CSV).
- [ ] **Breach notification**: Notify supervisory authority within 72 hours of discovering a breach involving personal data. Notify affected users if high risk.
- [ ] **DPO**: Appoint a Data Protection Officer if you process data at large scale or handle sensitive categories.
- [ ] **ROPA**: Maintain a Record of Processing Activities documenting all data flows.
- [ ] **Data minimization**: Only collect data you actually need. Purge what you no longer need.
- [ ] **Consent**: Freely given, specific, informed, unambiguous. No pre-checked boxes. Easy withdrawal.

### PCI DSS Basics

If you accept credit card payments:

- **Use Stripe, Braintree, or similar**: They handle card data. You never see card numbers. This puts you in SAQ-A (simplest compliance level).
- **Never store CVV/CVC**: Not even encrypted. No exceptions.
- **Never log card numbers**: Scrub them from all logs.
- **Use tokenization**: Let the payment processor tokenize card data.
- If you must handle card data directly (SAQ-D), hire a QSA and prepare for significant compliance overhead.

### SOC 2 Overview

SOC 2 Type II certifies that your security controls operate effectively over time (typically 6-12 month observation period).

Five trust service criteria:

1. **Security** (required): Firewalls, access control, encryption, monitoring.
2. **Availability**: Uptime SLAs, DR plans, capacity planning.
3. **Processing integrity**: Data processing is complete, valid, accurate, timely.
4. **Confidentiality**: Sensitive data is protected (encryption, access controls).
5. **Privacy**: Personal information is handled per your privacy policy.

Start with Security only. Use Vanta, Drata, or Secureframe to automate evidence collection.

---

## 13. Third-Party Script Security

### Subresource Integrity (SRI)

SRI ensures that fetched resources match an expected hash. If a CDN is compromised, the browser refuses to execute the tampered script.

```bash
# Generate SRI hash for a script
curl -s https://cdn.example.com/lib.js | openssl dgst -sha384 -binary | openssl base64 -A
# Output: oqVuAfXRKap7fdgcCY5uykM6+R9GqQ8K/...
```

```html
<script src="https://cdn.example.com/lib.js"
        integrity="sha384-oqVuAfXRKap7fdgcCY5uykM6+R9GqQ8K/..."
        crossorigin="anonymous"></script>

<link rel="stylesheet" href="https://cdn.example.com/style.css"
      integrity="sha384-abc123..."
      crossorigin="anonymous">
```

### CSP for Third-Party Scripts

```nginx
# Allow specific CDN in CSP
add_header Content-Security-Policy "script-src 'self' https://cdn.example.com; style-src 'self' https://cdn.example.com;" always;
```

### Self-Host Critical Libraries

Do not rely on public CDNs for critical dependencies. If the CDN goes down or is compromised, your application breaks.

```bash
# Download and vendor critical libs
curl -o /var/www/static/vendor/htmx-1.9.10.min.js https://unpkg.com/htmx.org@1.9.10
# Generate SRI hash for local copy too (defense in depth)
```

### Quarterly Third-Party Review

Every quarter, audit all third-party scripts:

1. List all external domains in your CSP and HTML.
2. Verify each is still needed.
3. Check for known vulnerabilities in included library versions.
4. Update SRI hashes after any version bump.
5. Remove unused scripts.

---

## 14. API Security Patterns

### OAuth2 + PKCE

PKCE (Proof Key for Code Exchange) prevents authorization code interception. Required for SPAs and mobile apps. Recommended for all OAuth2 flows.

```
1. Client generates code_verifier (random 43-128 chars)
2. Client computes code_challenge = base64url(sha256(code_verifier))
3. Client sends code_challenge in /authorize request
4. After redirect, client sends code_verifier in /token request
5. Server verifies sha256(code_verifier) == stored code_challenge
```

### API Key Best Practices

| Practice | Rationale |
|----------|-----------|
| Prefix keys (e.g., `sk_live_`, `pk_test_`) | Identify key type at a glance, enables secret scanning |
| Hash keys before storage | Compromised DB does not leak keys |
| Scope keys to specific permissions | Least privilege |
| Set expiry (90 days max) | Limit blast radius of leaked keys |
| Allow revocation | Immediate response to compromise |
| Rate limit per key | Prevent abuse |
| Transmit in header, not URL | URLs appear in logs and referrer headers |

```python
# API key generation
import secrets, hashlib

def generate_api_key(prefix="sk_live"):
    raw = secrets.token_urlsafe(32)
    key = f"{prefix}_{raw}"
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    return key, key_hash  # Return key to user once, store only hash
```

### Webhook HMAC Verification

Verify that incoming webhooks actually come from the expected sender:

```python
import hmac, hashlib

def verify_webhook(payload: bytes, signature: str, secret: str) -> bool:
    """Verify HMAC-SHA256 webhook signature.

    Args:
        payload: Raw request body (bytes, not parsed JSON).
        signature: Value from X-Signature-256 header.
        secret: Shared webhook secret.

    Returns:
        True if signature is valid.
    """
    expected = "sha256=" + hmac.new(
        secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)

# Usage in Flask
@app.route('/webhook', methods=['POST'])
def handle_webhook():
    sig = request.headers.get('X-Signature-256', '')
    if not verify_webhook(request.data, sig, WEBHOOK_SECRET):
        abort(403)
    # Process webhook...
```

### GraphQL-Specific Security

```python
# Disable introspection in production
# (Introspection exposes your entire schema to attackers)
from ariadne import make_executable_schema
schema = make_executable_schema(type_defs, resolvers, introspection=False)

# Query depth limiting (prevents deeply nested queries that cause N+1)
# Maximum depth of 10 is reasonable for most applications
from graphql_depth_limit import DepthLimitValidator
app.add_middleware(DepthLimitValidator(max_depth=10))

# Query complexity limiting (prevents wide queries)
# Assign cost to each field, reject queries exceeding budget
MAX_COMPLEXITY = 1000
```

- Disable introspection in production.
- Limit query depth (10 is a reasonable default).
- Limit query complexity (assign cost per field, reject over budget).
- Disable batched queries unless explicitly needed.
- Apply per-operation rate limiting, not just per-request.

---

## 15. Secret Management

### Hierarchy (Best to Worst)

| Tier | Solution | Use When |
|------|----------|----------|
| 1 | **HashiCorp Vault** | Multi-service, dynamic secrets, enterprise |
| 2 | **Cloud-native** (AWS Secrets Manager, GCP Secret Manager) | Cloud-hosted, managed rotation |
| 3 | **SOPS** (Mozilla) | Encrypted files in git, small teams |
| 4 | **Sealed Secrets** | Kubernetes-native, GitOps |
| 5 | **Environment variables** | Simple apps, single server (last resort) |

Never commit plaintext secrets to git. Environment variables are acceptable only when the alternative is plaintext in a config file.

### SOPS Example

SOPS encrypts values in YAML/JSON files while keeping keys readable. Supports AWS KMS, GCP KMS, Azure Key Vault, age, and PGP.

```bash
# Install SOPS
brew install sops  # macOS
# or download from https://github.com/getsops/sops/releases

# Create .sops.yaml config
cat > .sops.yaml << 'EOF'
creation_rules:
  - path_regex: \.enc\.yaml$
    age: >-
      age1<your-age-recipient-public-key>
EOF

# Encrypt a secrets file
sops -e secrets.yaml > secrets.enc.yaml

# Edit encrypted file (decrypts in-memory, re-encrypts on save)
sops secrets.enc.yaml

# Decrypt for application use
sops -d secrets.enc.yaml > /tmp/secrets.yaml  # ephemeral only
```

### Git Secrets Prevention

```bash
# Install git-secrets (prevents committing secrets)
git clone https://github.com/awslabs/git-secrets.git
cd git-secrets && sudo make install

# Register AWS patterns (catches access keys, etc.)
git secrets --register-aws --global

# Install hook in every new repo
git secrets --install ~/.git-templates/git-secrets
git config --global init.templateDir ~/.git-templates/git-secrets

# Add custom patterns
git secrets --add --global 'PRIVATE.KEY'
git secrets --add --global '[A-Za-z0-9]{40}'  # Generic 40-char tokens
```

### Scanning for Leaked Secrets

```bash
# truffleHog (scans git history)
trufflehog git file://. --since-commit HEAD~50 --only-verified

# gitleaks (fast, CI-friendly)
gitleaks detect --source . --verbose
gitleaks detect --source . --baseline-path .gitleaks-baseline.json
```

### Rotation Schedule

| Secret Type | Rotation Frequency | Method |
|-------------|-------------------|--------|
| API keys | 90 days | Generate new, deploy, revoke old |
| Database passwords | 90 days | Vault dynamic credentials or manual |
| TLS certificates | Auto (Let's Encrypt) | certbot renew |
| SSH keys | Annually | Generate new, distribute, revoke old |
| Encryption keys | Annually | Envelope encryption, re-wrap data keys |
| Service account tokens | 90 days | Automated via CI/CD |

---

## 16. Penetration Testing

### Schedule

| Test Type | Frequency | Performed By |
|-----------|-----------|--------------|
| Automated scan (DAST) | Weekly (CI) | Automated tools |
| Manual pentest (external) | Annually | Third-party firm |
| Manual pentest (internal) | Semi-annually | Internal red team or contractor |
| Bug bounty (continuous) | Ongoing | Community researchers |

### Free Tools

| Tool | Purpose | Command |
|------|---------|---------|
| **OWASP ZAP** | Web app scanner (DAST) | `zap-cli quick-scan https://example.com` |
| **Nikto** | Web server scanner | `nikto -h https://example.com` |
| **Nuclei** | Template-based vuln scanner | `nuclei -u https://example.com -t cves/` |
| **nmap** | Network/port scanner | `nmap -sV -sC -p- example.com` |
| **SQLMap** | SQL injection testing | `sqlmap -u "https://example.com/search?q=test" --batch` |
| **WPScan** | WordPress scanner | `wpscan --url https://example.com` |

### Paid Tools

- **Burp Suite Professional**: Industry-standard web app testing proxy.
- **Nessus**: Comprehensive vulnerability scanner.
- **Cobalt Strike / Metasploit Pro**: Red team engagement platforms.

### Testing Process

1. **Scope**: Define targets, rules of engagement, excluded systems.
2. **Reconnaissance**: DNS enumeration, port scanning, technology fingerprinting.
3. **Vulnerability scanning**: Run automated tools against in-scope targets.
4. **Manual testing**: Attempt exploitation of findings. Focus on business logic flaws that scanners miss.
5. **Reporting**: Document findings with severity, evidence (screenshots, request/response), and remediation guidance.
6. **Remediation**: Fix findings, prioritized by severity.
7. **Retest**: Verify fixes are effective.

---

## 17. security.txt & Disclosure

### RFC 9116 Template

Place at `/.well-known/security.txt` (and optionally `/security.txt`). Sign with PGP.

```
# Security policy for example.com
# https://securitytxt.org/

Contact: mailto:security@example.com
Contact: https://example.com/security/report
Expires: 2026-12-31T23:59:59.000Z
Encryption: https://example.com/.well-known/pgp-key.txt
Preferred-Languages: en
Canonical: https://example.com/.well-known/security.txt
Policy: https://example.com/security/policy
Hiring: https://example.com/careers
```

```nginx
# Serve security.txt
location /.well-known/security.txt {
    alias /var/www/security.txt;
    default_type text/plain;
}
```

### Vulnerability Disclosure Policy Outline

Publish at the URL referenced in `Policy:` above:

1. **Scope**: What is in scope (domains, applications, APIs) and out of scope.
2. **Safe harbor**: Researchers acting in good faith will not face legal action.
3. **Reporting process**: Where and how to report (email, form, HackerOne).
4. **Response timeline**: Acknowledge within 3 business days. Triage within 10. Fix critical within 30.
5. **Recognition**: Credit researchers publicly (with permission). Consider bounties.
6. **Exclusions**: Social engineering, physical attacks, DoS testing, third-party services.

### Safe Harbor Clause

```
We consider security research conducted consistent with this policy to be:
- Authorized concerning any applicable anti-hacking laws
- Authorized concerning any relevant anti-circumvention laws
- Exempt from restrictions in our Terms of Service that would interfere
  with conducting security research

We will not pursue civil action or initiate a complaint to law enforcement
for accidental, good-faith violations of this policy.
```

---

## 18. Supply Chain Security

### Lockfile Integrity

Lockfiles pin exact dependency versions and integrity hashes. Always commit lockfiles.

```bash
# Node.js: verify lockfile integrity
npm ci  # Fails if lockfile doesn't match package.json (use instead of npm install in CI)

# Python: pip with hash checking
pip install --require-hashes -r requirements.txt

# Generate hashed requirements
pip-compile --generate-hashes requirements.in -o requirements.txt
```

Review lockfile diffs in every PR. A changed lockfile with no corresponding package.json change is suspicious.

### SBOM Generation

Software Bill of Materials (SBOM) catalogs every component in your application.

```bash
# Generate SBOM with Syft
syft dir:. -o spdx-json > sbom.spdx.json
syft dir:. -o cyclonedx-json > sbom.cdx.json

# Scan SBOM for known vulnerabilities with Grype
grype sbom:sbom.spdx.json --fail-on critical

# Combined: generate and scan in one pipeline
syft dir:. -o spdx-json | grype --fail-on high
```

### Container Scanning

```bash
# Trivy: scan container images for CVEs
trivy image myapp:latest --severity HIGH,CRITICAL
trivy image myapp:latest --exit-code 1  # Fail CI on findings

# Scan filesystem (not just containers)
trivy fs --security-checks vuln,config .
```

### GitHub Actions: Pin to SHA

Tags are mutable. An attacker who compromises an action repository can point a tag to malicious code. Pin to the immutable commit SHA.

```yaml
# WRONG: mutable tag
- uses: actions/checkout@v4

# RIGHT: pinned to SHA (find SHA from the release tag)
- uses: actions/checkout@<40-char-commit-sha> # v4.1.1
```

Use `pin-github-action` or Dependabot to automate SHA pinning and updates.

### Dependency Confusion Prevention

Dependency confusion attacks publish malicious packages to public registries with the same name as your private packages. The package manager installs the public (malicious) version.

```ini
# .npmrc -- scope private packages to your registry
@mycompany:registry=https://npm.mycompany.com/
# Public packages still come from npmjs.org

# Alternatively, proxy all packages through your private registry
registry=https://npm.mycompany.com/
```

```ini
# pip.conf -- restrict private package index
[global]
index-url = https://pypi.mycompany.com/simple/
extra-index-url = https://pypi.org/simple/
```

- Claim your internal package names on the public registry (publish empty placeholders).
- Use scoped packages (`@mycompany/pkg`) in npm to namespace private packages.
- Configure registries explicitly in CI; never rely on defaults.

---

## Quick Reference: Testing & Validation Commands

```bash
# TLS configuration
testssl.sh https://example.com

# HTTP security headers
curl -sI https://example.com | grep -iE '(x-content|x-frame|strict-transport|csp|referrer|permissions|cross-origin)'

# DNS records
dig CAA example.com +short
dig +dnssec example.com

# Certificate transparency
curl -s "https://crt.sh/?q=%.example.com&output=json" | jq '.[0:5]'

# Dependency vulnerabilities
npm audit && pip-audit && cargo audit

# Secret scanning
gitleaks detect --source .
trufflehog git file://. --only-verified

# Container scanning
trivy image myapp:latest --severity HIGH,CRITICAL

# SBOM + vuln scan
syft dir:. -o spdx-json | grype --fail-on high

# Open ports
nmap -sV -p- --top-ports 1000 example.com
```
