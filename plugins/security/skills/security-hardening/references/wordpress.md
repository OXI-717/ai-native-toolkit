# WordPress Security Hardening Reference

Actionable hardening guide for WordPress on Linux (Nginx or Apache), PHP-FPM, MySQL/MariaDB. Every recommendation includes the command, config, or code to implement it.

---

## Table of Contents

1. [Server-Level Hardening](#1-server-level-hardening)
2. [WordPress Core Configuration](#2-wordpress-core-configuration)
3. [Authentication Hardening](#3-authentication-hardening)
4. [Database Security](#4-database-security)
5. [Plugin and Theme Security](#5-plugin-and-theme-security)
6. [File System Hardening](#6-file-system-hardening)
7. [SSL/TLS Configuration](#7-ssltls-configuration)
8. [Security Headers](#8-security-headers)
9. [Web Application Firewall](#9-web-application-firewall)
10. [Monitoring and Integrity](#10-monitoring-and-integrity)
11. [Backups](#11-backups)
12. [DDoS Protection](#12-ddos-protection)
13. [Malware Scanning](#13-malware-scanning)
14. [Email Security](#14-email-security)

---

## 1. Server-Level Hardening

### PHP Hardening (php.ini)

Restrict dangerous functions and reduce information leakage. Edit the FPM pool's `php.ini` (not CLI).

```ini
; /etc/php/8.2/fpm/php.ini (adjust version)

; Remove functions that enable code execution or filesystem abuse
disable_functions = exec,passthru,shell_exec,system,proc_open,popen,curl_exec,curl_multi_exec,parse_ini_file,show_source,highlight_file,dl

; Hide PHP version from HTTP response headers
expose_php = Off

; Prevent remote file inclusion attacks
allow_url_fopen = Off
allow_url_include = Off

; Limit upload size to what the site actually needs
upload_max_filesize = 10M
post_max_size = 10M
max_input_vars = 1000

; Session hardening — use strict mode to reject uninitialized session IDs
session.use_strict_mode = 1
session.use_only_cookies = 1
session.cookie_httponly = 1
session.cookie_secure = 1
session.cookie_samesite = Strict
session.name = __Secure-PHPSESSID

; Limit execution time and memory to reduce abuse surface
max_execution_time = 30
memory_limit = 256M

; Disable dangerous PHP functions in error display
display_errors = Off
log_errors = On
error_log = /var/log/php-fpm/error.log
```

Restart after changes:

```bash
sudo systemctl restart php8.2-fpm
```

**Why disable_functions matters:** If an attacker gets code execution via a plugin vulnerability, these functions are the first thing they call. Removing them breaks most PHP webshells.

**Why allow_url_fopen = Off:** Prevents PHP from fetching remote files, which stops remote file inclusion (RFI) attacks. Some plugins (e.g., those fetching RSS feeds) may need this on — evaluate per site.

### File Permissions

WordPress files should be owned by the web server user but not writable by it where unnecessary.

```bash
# Set ownership: www-data owns files, your user owns for deployment
sudo chown -R www-data:www-data /var/www/wordpress

# Directories: readable + executable, not world-writable
sudo find /var/www/wordpress -type d -exec chmod 755 {} \;

# Files: readable, not executable, not world-writable
sudo find /var/www/wordpress -type f -exec chmod 644 {} \;

# wp-config.php: readable only by owner — this file has DB creds and salts
sudo chmod 400 /var/www/wordpress/wp-config.php
sudo chown www-data:www-data /var/www/wordpress/wp-config.php
```

### Protect wp-config.php at the Web Server

Even with correct file permissions, block HTTP requests to wp-config.php directly.

**Nginx:**

```nginx
location ~* /wp-config\.php$ {
    deny all;
    return 404;
}
```

**Apache (.htaccess):**

```apache
<Files wp-config.php>
    Require all denied
</Files>
```

### Move wp-config.php Above Web Root

WordPress automatically checks one directory above the web root for wp-config.php. This removes it from the publicly accessible directory entirely.

```bash
# Move it
sudo mv /var/www/wordpress/wp-config.php /var/www/wp-config.php

# Verify ownership and permissions
sudo chown www-data:www-data /var/www/wp-config.php
sudo chmod 400 /var/www/wp-config.php
```

No code change is needed — WordPress resolves `../wp-config.php` automatically. This prevents any misconfigured server from serving the file as plaintext.

---

## 2. WordPress Core Configuration

Add these constants to `wp-config.php` above the `/* That's all, stop editing! */` line.

### Force HTTPS for Admin and Logins

```php
define('FORCE_SSL_ADMIN', true);
define('FORCE_SSL_LOGIN', true);
```

**Why:** Ensures cookies and credentials for the admin panel are never sent over plaintext HTTP, even if the user types `http://`.

### Disable File Editing and Modifications

```php
// Removes the Theme Editor and Plugin Editor from the dashboard
// Prevents attackers with admin access from injecting PHP via the UI
define('DISALLOW_FILE_EDIT', true);

// Prevents all file modifications: plugin/theme install, update, delete via dashboard
// Use WP-CLI or deployment pipelines instead
define('DISALLOW_FILE_MODS', true);
```

### Disable Debug in Production

```php
define('WP_DEBUG', false);
define('WP_DEBUG_LOG', false);
define('WP_DEBUG_DISPLAY', false);
```

**Why:** Debug output can leak file paths, database queries, and PHP errors to attackers.

### Limit Post Revisions

```php
// Reduces database bloat and limits stored content an attacker could exfiltrate
define('WP_POST_REVISIONS', 5);
```

### Custom Content Directory

Rename `wp-content` to break automated scanners that target default paths.

```php
define('WP_CONTENT_DIR', dirname(__FILE__) . '/assets');
define('WP_CONTENT_URL', 'https://example.com/assets');
```

Then rename the directory:

```bash
mv /var/www/wordpress/wp-content /var/www/wordpress/assets
```

### Auto-Update Core

```php
// Enable automatic minor (security) updates — these are critical patches
define('WP_AUTO_UPDATE_CORE', 'minor');
```

### Hide WordPress Version

The generator meta tag and version query strings on scripts/styles leak your exact WP version to attackers.

Add to the theme's `functions.php` or a mu-plugin:

```php
// Remove the <meta name="generator" content="WordPress X.Y.Z"> tag
remove_action('wp_head', 'wp_generator');

// Strip version query strings from enqueued scripts and styles
function wpsh_remove_version_strings($src) {
    if (strpos($src, 'ver=')) {
        $src = remove_query_arg('ver', $src);
    }
    return $src;
}
add_filter('style_loader_src', 'wpsh_remove_version_strings', 9999);
add_filter('script_loader_src', 'wpsh_remove_version_strings', 9999);

// Remove WordPress version from RSS feeds
add_filter('the_generator', '__return_empty_string');
```

### Rotate Salts via WP-CLI

Salts in wp-config.php are used to hash cookies. Rotate them periodically or after a suspected breach to invalidate all logged-in sessions.

```bash
# Regenerate all salts (invalidates every session immediately)
wp config shuffle-salts --path=/var/www/wordpress

# Verify they changed
wp config get AUTH_KEY --path=/var/www/wordpress
```

Add to a monthly cron:

```bash
echo "0 3 1 * * www-data /usr/local/bin/wp config shuffle-salts --path=/var/www/wordpress" | sudo tee /etc/cron.d/wp-salt-rotation
```

---

## 3. Authentication Hardening

### Disable XML-RPC Completely

XML-RPC enables brute force amplification (system.multicall), pingback DDoS, and SSRF attacks. Unless you use the WordPress mobile app or Jetpack, disable it entirely.

**PHP filter (wp-config.php or mu-plugin):**

```php
add_filter('xmlrpc_enabled', '__return_false');
```

**Nginx — block before PHP even processes the request:**

```nginx
location = /xmlrpc.php {
    deny all;
    access_log off;
    log_not_found off;
    return 444;
}
```

**Apache (.htaccess):**

```apache
<Files xmlrpc.php>
    Require all denied
</Files>
```

**Why both layers:** The PHP filter disables the API but still loads WordPress. The web server block stops the request before any PHP execution, saving resources under attack.

### Restrict REST API to Authenticated Users

The REST API exposes user enumeration (`/wp-json/wp/v2/users`) and content to unauthenticated requests by default.

```php
// mu-plugin: /var/www/wordpress/wp-content/mu-plugins/restrict-rest-api.php
<?php
add_filter('rest_authentication_errors', function($result) {
    if (true === $result || is_wp_error($result)) {
        return $result;
    }
    if (!is_user_logged_in()) {
        return new WP_Error(
            'rest_not_logged_in',
            __('Authentication required.'),
            ['status' => 401]
        );
    }
    return $result;
});
```

**Caveat:** This blocks the REST API for unauthenticated users entirely. If your theme uses the REST API on the frontend (e.g., block themes, headless WP), whitelist specific routes instead.

### Block User Enumeration

Attackers query `/?author=1`, `/?author=2`, etc. to discover valid usernames.

```php
// mu-plugin: /var/www/wordpress/wp-content/mu-plugins/block-enumeration.php
<?php
if (!is_admin() && isset($_REQUEST['author']) && is_numeric($_REQUEST['author'])) {
    wp_redirect(home_url(), 301);
    exit;
}
```

**Nginx approach:**

```nginx
if ($args ~* "author=\d+") {
    return 403;
}
```

### Limit Login Attempts

Brute force is the most common WordPress attack vector.

**WP-CLI install:**

```bash
wp plugin install limit-login-attempts-reloaded --activate --path=/var/www/wordpress
```

**Manual configuration via wp-config.php (if using a custom solution):**

```php
// Set lockout policy
define('LIMIT_LOGIN_LOCKOUT_DURATION', 1200);     // 20 minutes
define('LIMIT_LOGIN_MAX_RETRIES', 3);
define('LIMIT_LOGIN_LOCKOUT_NOTIFY', 'email');
```

### Two-Factor Authentication

Install a 2FA plugin. The "Two Factor" plugin by Plugin Contributors is maintained by WordPress core contributors.

```bash
wp plugin install two-factor --activate --path=/var/www/wordpress
```

For enforced 2FA across all admin accounts, use WP 2FA:

```bash
wp plugin install wp-2fa --activate --path=/var/www/wordpress
```

### Password Policy Enforcement

```php
// mu-plugin: /var/www/wordpress/wp-content/mu-plugins/password-policy.php
<?php
add_action('user_profile_update_errors', function($errors, $update, $user) {
    if (isset($user->user_pass) && strlen($user->user_pass) < 16) {
        $errors->add('weak_password', '<strong>Error:</strong> Password must be at least 16 characters.');
    }
}, 10, 3);
```

### Disable Application Passwords

WordPress 5.6+ includes application passwords for REST API auth. If unused, disable them.

```php
add_filter('wp_is_application_passwords_available', '__return_false');
```

---

## 4. Database Security

### Custom Table Prefix

Set during installation or change in wp-config.php and the database. The default `wp_` prefix makes SQL injection exploitation trivial for automated tools.

**In wp-config.php:**

```php
$table_prefix = 'xk7m_';
```

**Rename existing tables (run once, carefully):**

```bash
# Generate the rename SQL
wp db query "SELECT CONCAT('RENAME TABLE ', table_name, ' TO ', REPLACE(table_name, 'wp_', 'xk7m_'), ';') FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name LIKE 'wp_%';" --path=/var/www/wordpress --skip-column-names | wp db query --path=/var/www/wordpress

# Update options and usermeta references
wp db query "UPDATE xk7m_options SET option_name = REPLACE(option_name, 'wp_', 'xk7m_') WHERE option_name LIKE 'wp_%';" --path=/var/www/wordpress
wp db query "UPDATE xk7m_usermeta SET meta_key = REPLACE(meta_key, 'wp_', 'xk7m_') WHERE meta_key LIKE 'wp_%';" --path=/var/www/wordpress
```

### Dedicated Database User with Minimal Privileges

WordPress only needs four operations at runtime. Grant only those.

```sql
-- Create a dedicated user
CREATE USER 'wp_site'@'localhost' IDENTIFIED BY 'STRONG_RANDOM_PASSWORD_HERE';

-- Grant only what WordPress needs at runtime
GRANT SELECT, INSERT, UPDATE, DELETE ON wordpress_db.* TO 'wp_site'@'localhost';
FLUSH PRIVILEGES;

-- CREATE, DROP, ALTER are only needed during installs and updates.
-- Grant them temporarily, then revoke:
-- GRANT CREATE, DROP, ALTER ON wordpress_db.* TO 'wp_site'@'localhost';
-- (run update)
-- REVOKE CREATE, DROP, ALTER ON wordpress_db.* FROM 'wp_site'@'localhost';
```

**Why:** If SQL injection occurs, the attacker cannot DROP tables, CREATE new ones, or ALTER schema.

### Use Unix Socket Connection

Avoid TCP overhead and skip network-level auth by connecting via socket.

```php
// wp-config.php
define('DB_HOST', 'localhost:/var/run/mysqld/mysqld.sock');
```

### MySQL/MariaDB Hardening

```ini
# /etc/mysql/mysql.conf.d/security.cnf

[mysqld]
# Only listen on localhost — no remote connections
bind-address = 127.0.0.1

# Disable LOAD DATA LOCAL INFILE — prevents local file reads via SQL injection
local-infile = 0

# Disable symbolic links
symbolic-links = 0

# Log slow queries for analysis
slow_query_log = 1
slow_query_log_file = /var/log/mysql/slow.log
long_query_time = 2
```

```bash
sudo systemctl restart mysql
```

---

## 5. Plugin and Theme Security

### Plugin Vetting Checklist

Before installing any plugin, verify:

1. **Last updated** < 6 months ago (check wordpress.org page)
2. **Active installs** > 10,000 (lower is higher risk)
3. **Tested up to** matches your WP version
4. **No known vulnerabilities** — check WPScan Vulnerability Database: https://wpscan.com/
5. **Review the code** for obvious red flags: `eval()`, `base64_decode()`, external HTTP calls to unknown domains

### WP-CLI Plugin Management

```bash
# List all plugins with status and version
wp plugin list --path=/var/www/wordpress

# Update all plugins
wp plugin update --all --path=/var/www/wordpress

# Verify plugin file integrity against wordpress.org checksums
wp plugin verify-checksums --all --path=/var/www/wordpress

# Delete inactive plugins — they are still exploitable even when deactivated
wp plugin list --status=inactive --field=name --path=/var/www/wordpress | xargs -I {} wp plugin delete {} --path=/var/www/wordpress

# Same for themes — keep only the active theme and one default theme
wp theme list --status=inactive --field=name --path=/var/www/wordpress | xargs -I {} wp theme delete {} --path=/var/www/wordpress
```

### Must-Use Plugins (mu-plugins)

Place critical security code in `wp-content/mu-plugins/`. These load automatically, cannot be deactivated via the dashboard, and execute before regular plugins.

```bash
sudo mkdir -p /var/www/wordpress/wp-content/mu-plugins
```

All security filters shown in this guide (XML-RPC disable, REST API restrict, enumeration block) belong here.

### Automated WPScan

```bash
# Install WPScan
sudo gem install wpscan

# Run a comprehensive scan
wpscan --url https://example.com \
  --enumerate vp,vt,u \
  --api-token YOUR_WPSCAN_API_TOKEN \
  --output /var/log/wpscan/report-$(date +%F).txt

# Cron: weekly scan
echo "0 4 * * 0 root /usr/local/bin/wpscan --url https://example.com --enumerate vp,vt --api-token TOKEN -o /var/log/wpscan/weekly-\$(date +\%F).txt" | sudo tee /etc/cron.d/wpscan
```

---

## 6. File System Hardening

### Disable Directory Listing

**Nginx** does this by default (autoindex is off). Verify:

```nginx
# Should NOT appear in your server block:
# autoindex on;
```

**Apache (.htaccess):**

```apache
Options -Indexes
```

### Prevent PHP Execution in Uploads

The uploads directory should never execute PHP. Uploaded files are the most common malware vector.

**Nginx:**

```nginx
location ~* /wp-content/uploads/.*\.php$ {
    deny all;
    return 403;
}
```

**Apache (/var/www/wordpress/wp-content/uploads/.htaccess):**

```apache
<FilesMatch "\.php$">
    Require all denied
</FilesMatch>
```

### Protect wp-includes

```nginx
# Nginx: block direct access to wp-includes PHP files
location ~* /wp-includes/.*\.php$ {
    deny all;
}

# Allow tinymce and js — they need to be served
location ~* /wp-includes/js/tinymce/langs/.+\.php$ {
    allow all;
}
```

### Comprehensive .htaccess Security (Apache)

```apache
# /var/www/wordpress/.htaccess

# Disable server signature
ServerSignature Off

# Prevent directory browsing
Options -Indexes

# Protect .htaccess itself
<Files .htaccess>
    Require all denied
</Files>

# Protect wp-config.php
<Files wp-config.php>
    Require all denied
</Files>

# Block xmlrpc.php
<Files xmlrpc.php>
    Require all denied
</Files>

# Block access to sensitive files
<FilesMatch "(^\.htaccess|\.htpasswd|wp-config\.php|readme\.html|license\.txt|debug\.log)$">
    Require all denied
</FilesMatch>

# Block PHP in uploads
<Directory "/var/www/wordpress/wp-content/uploads">
    <FilesMatch "\.php$">
        Require all denied
    </FilesMatch>
</Directory>

# Prevent script injection via query strings
<IfModule mod_rewrite.c>
    RewriteEngine On
    RewriteCond %{QUERY_STRING} (<|%3C).*script.*(>|%3E) [NC,OR]
    RewriteCond %{QUERY_STRING} GLOBALS(=|[|%[0-9A-Z]{0,2}) [OR]
    RewriteCond %{QUERY_STRING} _REQUEST(=|[|%[0-9A-Z]{0,2})
    RewriteRule .* - [F,L]
</IfModule>
```

---

## 7. SSL/TLS Configuration

### Nginx Modern Profile

Use the Mozilla SSL Configuration Generator's modern profile. This drops TLS 1.0/1.1 and weak ciphers.

```nginx
# /etc/nginx/snippets/ssl-params.conf

ssl_protocols TLSv1.2 TLSv1.3;
ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305;
ssl_prefer_server_ciphers off;

# OCSP Stapling — server provides certificate validity proof, client does not need to contact CA
ssl_stapling on;
ssl_stapling_verify on;
resolver 1.1.1.1 8.8.8.8 valid=300s;
resolver_timeout 5s;

# DH parameters for DHE ciphers
ssl_dhparam /etc/nginx/dhparam.pem;

# Session caching for performance
ssl_session_timeout 1d;
ssl_session_cache shared:SSL:10m;
ssl_session_tickets off;
```

### Generate DH Parameters

```bash
sudo openssl dhparam -out /etc/nginx/dhparam.pem 4096
```

This takes several minutes. Run it once.

### Let's Encrypt with Certbot

```bash
# Install certbot
sudo apt install certbot python3-certbot-nginx

# Obtain and install certificate
sudo certbot --nginx -d example.com -d www.example.com --redirect --staple-ocsp

# Auto-renewal is set up by certbot's systemd timer
sudo systemctl status certbot.timer

# Test renewal
sudo certbot renew --dry-run
```

### WordPress HTTPS Enforcement

```php
// wp-config.php
define('FORCE_SSL_ADMIN', true);

// If behind a reverse proxy (Cloudflare, load balancer)
if (isset($_SERVER['HTTP_X_FORWARDED_PROTO']) && $_SERVER['HTTP_X_FORWARDED_PROTO'] === 'https') {
    $_SERVER['HTTPS'] = 'on';
}
```

Update URLs in the database:

```bash
wp search-replace 'http://example.com' 'https://example.com' --all-tables --path=/var/www/wordpress
```

---

## 8. Security Headers

### Nginx Implementation

```nginx
# /etc/nginx/snippets/security-headers.conf

# Prevent clickjacking — only allow your own site to frame content
add_header X-Frame-Options "SAMEORIGIN" always;

# Stop browsers from MIME-sniffing the content type
add_header X-Content-Type-Options "nosniff" always;

# Control referrer information sent with requests
add_header Referrer-Policy "strict-origin-when-cross-origin" always;

# Disable browser features you do not use
add_header Permissions-Policy "camera=(), microphone=(), geolocation=(), payment=()" always;

# HSTS — force HTTPS for 2 years, include subdomains
# WARNING: only enable after confirming HTTPS works perfectly; this is hard to undo
add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;

# Content Security Policy — starter policy for WordPress
# Adjust per site: fonts, analytics, CDNs, embeds all need whitelisting
add_header Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; font-src 'self' data:; connect-src 'self'; frame-ancestors 'self'; base-uri 'self'; form-action 'self';" always;
```

Include in your server block:

```nginx
server {
    include snippets/ssl-params.conf;
    include snippets/security-headers.conf;
    # ...
}
```

**Why unsafe-inline/unsafe-eval in CSP:** WordPress core and most plugins inject inline scripts and use eval. A strict CSP breaks the admin dashboard. Start with this permissive policy, then tighten using nonces if your setup supports it.

### Apache Implementation

```apache
# /etc/apache2/conf-available/security-headers.conf

Header always set X-Frame-Options "SAMEORIGIN"
Header always set X-Content-Type-Options "nosniff"
Header always set Referrer-Policy "strict-origin-when-cross-origin"
Header always set Permissions-Policy "camera=(), microphone=(), geolocation=(), payment=()"
Header always set Strict-Transport-Security "max-age=63072000; includeSubDomains; preload"
Header always set Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; font-src 'self' data:; connect-src 'self'; frame-ancestors 'self'; base-uri 'self'; form-action 'self';"
```

```bash
sudo a2enmod headers
sudo a2enconf security-headers
sudo systemctl reload apache2
```

---

## 9. Web Application Firewall

### Wordfence

```bash
wp plugin install wordfence --activate --path=/var/www/wordpress
```

**Recommended settings (free tier):**

- Enable brute force protection (limit to 3 attempts/20 min lockout)
- Enable rate limiting for crawlers
- Block IPs that send > 30 requests/min
- Enable live traffic monitoring
- Enable scan scheduling (daily)

**Premium adds:** Real-time firewall rules, real-time IP blocklist, country blocking.

**WP-CLI Wordfence scan:**

```bash
wp wordfence scan --path=/var/www/wordpress
```

### Sucuri

```bash
wp plugin install sucuri-scanner --activate --path=/var/www/wordpress
```

Sucuri provides file integrity monitoring, security notifications, and a remote scanner. The premium tier adds a cloud WAF/CDN.

### Cloudflare WAF Configuration

If you use Cloudflare as a reverse proxy:

1. **SSL/TLS mode**: Set to "Full (Strict)" — encrypts between Cloudflare and your origin with certificate validation
2. **Security Level**: "Medium" or "High"
3. **Bot Fight Mode**: Enable
4. **Under Attack Mode**: Keep off; enable only during active attacks

**Page rules for wp-login and wp-admin:**

```
URL: example.com/wp-login.php
Settings: Security Level = I'm Under Attack, Cache Level = Bypass

URL: example.com/wp-admin/*
Settings: Security Level = High, Cache Level = Bypass
```

**WAF custom rules (Cloudflare dashboard > Security > WAF):**

```
# Block XML-RPC
Rule name: Block XML-RPC
When: URI Path equals /xmlrpc.php
Then: Block

# Challenge non-whitelisted countries on wp-login
Rule name: Geo-restrict admin login
When: URI Path equals /wp-login.php AND Country is not in {US, GB}
Then: Managed Challenge
```

### Patchstack

Patchstack provides virtual patching for known plugin/theme vulnerabilities before official patches are released.

```bash
wp plugin install patchstack --activate --path=/var/www/wordpress
```

### Server-Level Firewall (UFW + fail2ban)

```bash
# UFW: allow only SSH, HTTP, HTTPS
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

**fail2ban WordPress jail:**

```ini
# /etc/fail2ban/jail.d/wordpress.conf
[wordpress-auth]
enabled  = true
port     = http,https
filter   = wordpress-auth
logpath  = /var/log/auth.log
maxretry = 3
bantime  = 3600
findtime = 600

[wordpress-xmlrpc]
enabled  = true
port     = http,https
filter   = wordpress-xmlrpc
logpath  = /var/log/nginx/access.log
maxretry = 2
bantime  = 86400
findtime = 60
```

```ini
# /etc/fail2ban/filter.d/wordpress-auth.conf
[Definition]
failregex = ^<HOST> .* "POST /wp-login\.php
ignoreregex =
```

```ini
# /etc/fail2ban/filter.d/wordpress-xmlrpc.conf
[Definition]
failregex = ^<HOST> .* "POST /xmlrpc\.php
ignoreregex =
```

```bash
sudo systemctl restart fail2ban
sudo fail2ban-client status wordpress-auth
```

---

## 10. Monitoring and Integrity

### AIDE (File Integrity Monitoring)

```bash
sudo apt install aide

# Initialize the database
sudo aideinit
sudo cp /var/lib/aide/aide.db.new /var/lib/aide/aide.db

# Check for changes
sudo aide --check

# Cron: daily integrity check
echo "0 5 * * * root /usr/bin/aide --check | mail -s 'AIDE Report' admin@example.com" | sudo tee /etc/cron.d/aide-check
```

### WP-CLI Integrity Checks

```bash
# Verify WordPress core files against official checksums
wp core verify-checksums --path=/var/www/wordpress

# Verify plugin files
wp plugin verify-checksums --all --path=/var/www/wordpress

# Check core version
wp core version --path=/var/www/wordpress
```

Add to a daily cron:

```bash
#!/bin/bash
# /usr/local/bin/wp-integrity-check.sh
WP_PATH="/var/www/wordpress"
LOG="/var/log/wp-integrity.log"

echo "=== $(date) ===" >> "$LOG"
sudo -u www-data /usr/local/bin/wp core verify-checksums --path="$WP_PATH" >> "$LOG" 2>&1
sudo -u www-data /usr/local/bin/wp plugin verify-checksums --all --path="$WP_PATH" >> "$LOG" 2>&1

if grep -q "Warning" "$LOG"; then
    mail -s "WP Integrity Alert" admin@example.com < "$LOG"
fi
```

### WP Activity Log

Tracks all user actions in the dashboard: logins, content changes, plugin installs, setting changes.

```bash
wp plugin install wp-security-audit-log --activate --path=/var/www/wordpress
```

### Access Log Analysis with GoAccess

```bash
sudo apt install goaccess

# Real-time terminal dashboard
sudo goaccess /var/log/nginx/access.log --log-format=COMBINED

# Generate HTML report
sudo goaccess /var/log/nginx/access.log --log-format=COMBINED -o /var/www/reports/access.html

# Filter for wp-login attempts
grep "wp-login.php" /var/log/nginx/access.log | goaccess --log-format=COMBINED -o /var/www/reports/login-attempts.html
```

### Uptime Monitoring

**UptimeRobot (free tier):** 50 monitors, 5-minute interval. Set up HTTP(S) monitors for:
- Homepage
- wp-login.php (check for 200 response)
- wp-admin (check for 302 redirect when not logged in)

**Uptime Kuma (self-hosted alternative):**

```bash
docker run -d --restart=always -p 3001:3001 -v uptime-kuma:/app/data --name uptime-kuma louislam/uptime-kuma:1
```

---

## 11. Backups

### Automated WP-CLI Backup Script

```bash
#!/bin/bash
# /usr/local/bin/wp-backup.sh
set -euo pipefail

WP_PATH="/var/www/wordpress"
BACKUP_DIR="/var/backups/wordpress"
DATE=$(date +%F-%H%M)
S3_BUCKET="s3://your-bucket/wp-backups"
RETENTION_DAYS=30

mkdir -p "$BACKUP_DIR"

# Database backup
sudo -u www-data /usr/local/bin/wp db export \
    "$BACKUP_DIR/db-$DATE.sql" \
    --path="$WP_PATH" \
    --add-drop-table

gzip "$BACKUP_DIR/db-$DATE.sql"

# File backup (exclude cache and backup dirs)
tar czf "$BACKUP_DIR/files-$DATE.tar.gz" \
    --exclude='*/cache/*' \
    --exclude='*/backups/*' \
    --exclude='*/upgrade/*' \
    -C /var/www wordpress/

# Sync to S3
aws s3 sync "$BACKUP_DIR/" "$S3_BUCKET/" --storage-class STANDARD_IA

# Clean local backups older than retention period
find "$BACKUP_DIR" -type f -mtime +$RETENTION_DAYS -delete

echo "Backup completed: $DATE"
```

```bash
sudo chmod 700 /usr/local/bin/wp-backup.sh
echo "0 2 * * * root /usr/local/bin/wp-backup.sh >> /var/log/wp-backup.log 2>&1" | sudo tee /etc/cron.d/wp-backup
```

### Backup Plugins

**UpdraftPlus (free):** Scheduled backups to S3, Google Drive, Dropbox. Handles both database and files.

```bash
wp plugin install updraftplus --activate --path=/var/www/wordpress
```

**BlogVault (premium):** Real-time incremental backups, 1-click restore, staging. Good for high-traffic sites.

### Backup Testing Script

Backups are worthless if you cannot restore from them. Test monthly.

```bash
#!/bin/bash
# /usr/local/bin/wp-backup-test.sh
set -euo pipefail

BACKUP_DIR="/var/backups/wordpress"
TEST_DIR="/tmp/wp-backup-test"
LATEST_DB=$(ls -t "$BACKUP_DIR"/db-*.sql.gz | head -1)
LATEST_FILES=$(ls -t "$BACKUP_DIR"/files-*.tar.gz | head -1)

rm -rf "$TEST_DIR"
mkdir -p "$TEST_DIR"

# Test database backup
echo "Testing database backup: $LATEST_DB"
gunzip -t "$LATEST_DB" && echo "DB backup: OK" || echo "DB backup: CORRUPT"

# Test file backup
echo "Testing file backup: $LATEST_FILES"
tar tzf "$LATEST_FILES" > /dev/null && echo "File backup: OK" || echo "File backup: CORRUPT"

# Verify wp-config.php exists in the archive
tar tzf "$LATEST_FILES" | grep -q "wp-config.php" && echo "wp-config.php: FOUND" || echo "wp-config.php: MISSING"

rm -rf "$TEST_DIR"
```

---

## 12. DDoS Protection

### Cloudflare Configuration

1. Enable "Under Attack Mode" during active DDoS via the dashboard or API:

```bash
curl -X PATCH "https://api.cloudflare.com/client/v4/zones/ZONE_ID/settings/security_level" \
    -H "Authorization: Bearer API_TOKEN" \
    -H "Content-Type: application/json" \
    --data '{"value":"under_attack"}'
```

2. Enable rate limiting rules in Cloudflare dashboard.
3. Configure IP Access Rules to block known-bad ASNs.

### Nginx Rate Limiting

```nginx
# /etc/nginx/conf.d/rate-limiting.conf

# Define rate limit zones
# Login page: 1 request per second per IP
limit_req_zone $binary_remote_addr zone=wp_login:10m rate=1r/s;

# Admin AJAX: 10 requests per second per IP
limit_req_zone $binary_remote_addr zone=wp_ajax:10m rate=10r/s;

# XML-RPC: if not blocked entirely, severely limit it
limit_req_zone $binary_remote_addr zone=wp_xmlrpc:10m rate=1r/m;

# General: 30 requests per second per IP
limit_req_zone $binary_remote_addr zone=wp_general:10m rate=30r/s;
```

```nginx
# In server block
location = /wp-login.php {
    limit_req zone=wp_login burst=3 nodelay;
    include fastcgi_params;
    fastcgi_pass unix:/run/php/php8.2-fpm.sock;
    fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
}

location = /wp-admin/admin-ajax.php {
    limit_req zone=wp_ajax burst=20 nodelay;
    include fastcgi_params;
    fastcgi_pass unix:/run/php/php8.2-fpm.sock;
    fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
}

location = /xmlrpc.php {
    limit_req zone=wp_xmlrpc burst=1 nodelay;
    # Better: just deny all (see section 3)
}
```

### Kernel SYN Flood Protection

```bash
# /etc/sysctl.d/99-ddos-protection.conf

# Enable SYN cookies — responds to SYN floods without allocating resources
net.ipv4.tcp_syncookies = 1

# Reduce SYN-ACK retries (default 5, reduce to 2)
net.ipv4.tcp_synack_retries = 2

# Increase SYN backlog
net.ipv4.tcp_max_syn_backlog = 4096

# Reduce TIME_WAIT sockets
net.ipv4.tcp_fin_timeout = 15

# Enable reverse path filtering (drop spoofed source IPs)
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1

# Ignore ICMP broadcasts (Smurf attack protection)
net.ipv4.icmp_echo_ignore_broadcasts = 1

# Log suspicious packets
net.ipv4.conf.all.log_martians = 1
```

```bash
sudo sysctl --system
```

---

## 13. Malware Scanning

### WP-CLI Checksum Verification

Detects modified core or plugin files by comparing against official checksums.

```bash
# Core files
wp core verify-checksums --path=/var/www/wordpress

# All plugins from wordpress.org
wp plugin verify-checksums --all --path=/var/www/wordpress
```

Any file that fails checksum verification has been modified and may contain injected malware.

### Grep for Common Malware Patterns

```bash
# Search for common PHP malware signatures in the WordPress directory
# Run from the WordPress root

# Base64-encoded payloads (most common obfuscation)
grep -rn "eval\s*(base64_decode" /var/www/wordpress/wp-content/ --include="*.php"

# eval() with variable input
grep -rn "eval\s*(\$" /var/www/wordpress/wp-content/ --include="*.php"

# Common malware functions
grep -rn "str_rot13\|gzinflate\|gzuncompress\|gzdecode\|str_replace.*chr(" /var/www/wordpress/wp-content/ --include="*.php"

# Hidden backdoors via preg_replace with /e modifier (code execution)
grep -rn "preg_replace.*\/e" /var/www/wordpress/wp-content/ --include="*.php"

# Files with suspicious names in uploads
find /var/www/wordpress/wp-content/uploads -name "*.php" -o -name "*.phtml" -o -name "*.php5"

# Recently modified files (last 3 days) — useful during incident response
find /var/www/wordpress -name "*.php" -mtime -3 -ls

# One-liner: comprehensive malware pattern scan
grep -rn --include="*.php" -E "(eval|assert|preg_replace.*\/e|create_function|call_user_func)\s*\(" /var/www/wordpress/wp-content/ | grep -v "node_modules"
```

### ClamAV

```bash
sudo apt install clamav clamav-daemon

# Update virus definitions
sudo freshclam

# Scan WordPress directory
sudo clamscan -r --infected --log=/var/log/clamav/wp-scan.log /var/www/wordpress/

# Cron: weekly ClamAV scan
echo "0 3 * * 0 root /usr/bin/clamscan -r --infected --log=/var/log/clamav/wp-scan-\$(date +\%F).log /var/www/wordpress/" | sudo tee /etc/cron.d/clamav-wp
```

### Scanning Plugins

**Wordfence** (see section 9) includes a malware scanner that checks against their signature database.

**Anti-Malware Security and Brute-Force Firewall (GOTMLS):**

```bash
wp plugin install gotmls --activate --path=/var/www/wordpress
```

---

## 14. Email Security

Properly configured email authentication prevents attackers from spoofing your domain in phishing campaigns and ensures WordPress notification emails are delivered.

### SPF Record

Specifies which servers are allowed to send email for your domain.

```
; DNS TXT record for example.com
; Allow your mail server and any transactional email service
example.com. IN TXT "v=spf1 mx include:_spf.google.com include:amazonses.com -all"
```

- `mx` — your MX servers can send
- `include:` — authorize third-party senders (Google Workspace, SES, etc.)
- `-all` — hard fail: reject mail from unauthorized senders

### DKIM Record

Signs outgoing email with a domain key so recipients can verify it was not tampered with. The setup depends on your mail provider:

```
; DNS TXT record — selector depends on provider
; Example for Google Workspace:
google._domainkey.example.com. IN TXT "v=DKIM1; k=rsa; p=MIIBIjANBg..."

; Example for Amazon SES:
; SES provides three CNAME records for DKIM — add all three
```

### DMARC Record

Tells receiving servers what to do with email that fails SPF and DKIM checks.

```
; Start with monitoring mode (p=none) to collect reports without blocking
_dmarc.example.com. IN TXT "v=DMARC1; p=none; rua=mailto:dmarc@example.com; ruf=mailto:dmarc@example.com; pct=100"

; After reviewing reports and confirming all legitimate mail passes, enforce:
_dmarc.example.com. IN TXT "v=DMARC1; p=reject; rua=mailto:dmarc@example.com; ruf=mailto:dmarc@example.com; pct=100"
```

- `p=none` — monitor only (start here)
- `p=quarantine` — send failures to spam
- `p=reject` — drop failures entirely (goal state)
- `rua` — aggregate report destination
- `ruf` — forensic report destination

### WP Mail SMTP

WordPress uses PHP's `mail()` by default, which often ends up in spam and provides no authentication. Use SMTP instead.

```bash
wp plugin install wp-mail-smtp --activate --path=/var/www/wordpress
```

Configure via wp-config.php for version-controlled, non-UI settings:

```php
// wp-config.php
define('WPMS_ON', true);
define('WPMS_MAILER', 'smtp');
define('WPMS_SMTP_HOST', 'smtp.example.com');
define('WPMS_SMTP_PORT', 587);
define('WPMS_SSL', 'tls');
define('WPMS_SMTP_AUTH', true);
define('WPMS_SMTP_USER', 'noreply@example.com');
define('WPMS_SMTP_PASS', 'app-specific-password');
define('WPMS_SMTP_AUTOTLS', true);
```

### Transactional Email Services

For reliable WordPress email delivery, use a dedicated transactional service instead of your own SMTP:

- **Amazon SES** — lowest cost at scale ($0.10/1000 emails)
- **Postmark** — best deliverability, WordPress-focused
- **SendGrid** — free tier (100 emails/day), good API
- **Mailgun** — good for developers, flexible API

Each service provides either SMTP credentials or a WordPress plugin for direct API integration, which is faster and more reliable than SMTP.

---

## Quick Audit Checklist

Run this after hardening to verify:

```bash
#!/bin/bash
# /usr/local/bin/wp-security-audit.sh
set -euo pipefail
WP_PATH="/var/www/wordpress"

echo "=== WordPress Security Audit ==="

echo -n "WP Version: "
wp core version --path="$WP_PATH"

echo -n "Core checksums: "
wp core verify-checksums --path="$WP_PATH" 2>&1 | tail -1

echo -n "Plugin checksums: "
wp plugin verify-checksums --all --path="$WP_PATH" 2>&1 | tail -1

echo -n "Inactive plugins: "
wp plugin list --status=inactive --format=count --path="$WP_PATH"

echo -n "Inactive themes: "
wp theme list --status=inactive --format=count --path="$WP_PATH"

echo -n "WP_DEBUG: "
wp config get WP_DEBUG --path="$WP_PATH" 2>/dev/null || echo "not set"

echo -n "DISALLOW_FILE_EDIT: "
wp config get DISALLOW_FILE_EDIT --path="$WP_PATH" 2>/dev/null || echo "not set"

echo -n "DISALLOW_FILE_MODS: "
wp config get DISALLOW_FILE_MODS --path="$WP_PATH" 2>/dev/null || echo "not set"

echo -n "FORCE_SSL_ADMIN: "
wp config get FORCE_SSL_ADMIN --path="$WP_PATH" 2>/dev/null || echo "not set"

echo -n "Table prefix: "
wp config get table_prefix --path="$WP_PATH"

echo -n "PHP in uploads: "
find "$WP_PATH/wp-content/uploads" -name "*.php" 2>/dev/null | wc -l
echo " PHP files found"

echo -n "wp-config permissions: "
stat -c "%a" "$WP_PATH/wp-config.php" 2>/dev/null || stat -c "%a" "$WP_PATH/../wp-config.php" 2>/dev/null || echo "not found"

echo -n "SSL certificate: "
curl -sI https://$(wp option get siteurl --path="$WP_PATH" | sed 's|https\?://||') 2>/dev/null | grep -i "strict-transport" | head -1 || echo "HSTS not found"

echo "=== Audit complete ==="
```

```bash
sudo chmod 700 /usr/local/bin/wp-security-audit.sh
```
