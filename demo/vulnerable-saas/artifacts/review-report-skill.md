# Full Project Review Report (via review plugin pipeline)

**Project**: `demo/vulnerable-saas/`
**Scope**: full project (12 source files)
**Mode**: `--no-fix` (report only)
**Iterations**: 1

## Pipeline

```
TeamCreate("demo-review")
  → 5 Sonnet agents in parallel (3 chunk reviewers + architecture + security)
  → 3 Haiku scorer agents (confidence re-evaluation)
  → Deduplicate & filter (>= 80)
  → Final report
```

**Agents used**: chunk1-reviewer, chunk2-reviewer, chunk3-reviewer, arch-reviewer, security-scanner
**Scorers**: 3 Haiku batches (8+8+9 findings)
**Raw findings**: 31 → **25 unique after dedup** → **25 survived scoring (all >= 80)**

---

## Critical Issues (90–100)

| # | Issue | Score | File | Found by |
|---|-------|-------|------|----------|
| 1 | Hardcoded Supabase service role key in source | 99 | `lib/supabase.ts:6` | chunk3, security, arch |
| 2 | IDOR — unauthenticated read of any user's notes | 99 | `app/api/notes/route.ts:6-18` | chunk1, security |
| 3 | IDOR — unauthenticated delete of any note | 99 | `app/api/notes/route.ts:44-59` | chunk1, security |
| 4 | Mass assignment — privilege escalation via profile update | 99 | `app/api/users/route.ts:27-43` | chunk1, security |
| 5 | No authentication on ANY API route | 98 | all `app/api/*/route.ts` | arch, security |
| 6 | Stored XSS via unsanitized HTML rendering | 98 | `components/NotesList.tsx:33` | chunk2, security |
| 7 | Unauthenticated note creation, no input validation | 97 | `app/api/notes/route.ts:23-41` | chunk1, security |
| 8 | Service role client (bypasses RLS) used as default | 95 | `lib/supabase.ts:11` + all routes | chunk3, arch, security |
| 9 | Insecure cookie — no httpOnly, secure, sameSite | 95 | `app/api/auth/route.ts:30-34` | chunk1, security |
| 10 | User enumeration via login error differentiation | 97 | `app/api/auth/route.ts:16-25` | chunk1, security |
| 11 | User enumeration via password reset | 98 | `app/api/auth/reset/route.ts:16-20` | chunk1, security |
| 12 | No rate limiting on password reset (email bombing) | 95 | `app/api/auth/reset/route.ts` | chunk1, security |
| 13 | Wildcard CORS on all API routes | 92 | `next.config.js:9` | chunk3, arch, security |
| 14 | Internal DB error details leaked to client | 94 | `app/api/users/route.ts:16-19` | chunk1, arch, security |
| 15 | SQL injection risk — raw search to RPC | 92 | `app/api/users/route.ts:9-11` | security |
| 16 | Import statement after code (invalid module structure) | 92 | `app/api/auth/reset/route.ts:35` | chunk1, arch |
| 17 | URL query parameter not encoded | 90 | `components/UserSearch.tsx:10` | chunk2, security |
| 18 | Hardcoded userId — no session management | 90 | `app/page.tsx:6` | chunk2, arch |

## Important Issues (80–89)

| # | Issue | Score | File | Found by |
|---|-------|-------|------|----------|
| 19 | No server-only guard on supabaseAdmin export | 87 | `lib/supabase.ts` | arch |
| 20 | PII exposure — emails and roles in user search | 85 | `app/api/users/route.ts` + `UserSearch.tsx` | chunk2, security |
| 21 | No error handling in deleteNote (optimistic UI) | 85 | `components/NotesList.tsx:16-18` | chunk2 |
| 22 | No error handling in search function | 85 | `components/UserSearch.tsx:9-12` | chunk2 |
| 23 | Missing label for search input (accessibility) | 85 | `components/UserSearch.tsx:19-22` | chunk2 |
| 24 | `any[]` typed state — no TypeScript safety | 82 | `NotesList.tsx:6`, `UserSearch.tsx:7` | chunk2, arch |
| 25 | `strict: false` in tsconfig — type safety disabled | 82 | `tsconfig.json:7` | chunk3 |

---

## Architecture Assessment

- **Structure**: poor (intentionally for demo)
- **Auth layer**: completely absent — no middleware.ts, no shared auth utility
- **RLS bypass**: supabaseAdmin used everywhere, RLS provides zero protection
- **Circular dependencies**: none
- **God files**: none (largest 60 lines)
- **Positive**: correct App Router conventions, proper client/server boundaries, small components

## Security Assessment

- **Risk level**: CRITICAL
- **Attack surface**: entire API is unauthenticated + admin-privileged
- **OWASP Top 10 coverage**: 7 of 10 categories hit (A01 Broken Access Control, A02 Crypto Failures, A03 Injection, A04 Insecure Design, A05 Security Misconfiguration, A07 Auth Failures, A09 Logging/Monitoring)

## Dependency Audit

| Package | Version | Issue |
|---------|---------|-------|
| `next` | 14.2.5 | 1 critical CVE (SSRF + cache poisoning) — upgrade to 14.2.35+ |
| `@supabase/supabase-js` | 2.44.0 | 1 low — upgrade to 2.106.0 |

## Attack Chains

**Chain 1: Complete account takeover (4 requests, no auth)**
1. `POST /api/notes` — inject XSS payload in content
2. Victim views notes → script executes
3. Script reads `document.cookie` (not httpOnly) → exfiltrates JWT
4. `PATCH /api/users?id=victim` with `{"role":"admin"}` → full control

**Chain 2: Data exfiltration**
1. `GET /api/users?search=` → dump all emails
2. `GET /api/notes?user_id=<each-uuid>` → read all private notes

**Chain 3: Brute-force login**
1. `POST /api/auth/login` per email → 404/401 confirms existence
2. Unlimited attempts (no rate limit) → password cracked

---

## Verdict: ISSUES REMAINING (25 issues, 0 fixed — `--no-fix` mode)

## Code Health Metrics

- Total files reviewed: 12
- Issues per file (density): 2.1
- Most problematic: `app/api/notes/route.ts` (4 issues), `app/api/users/route.ts` (4 issues)
- Cleanest: `app/layout.tsx` (0 issues)
