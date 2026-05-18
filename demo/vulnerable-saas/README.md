# Vulnerable SaaS Demo

A deliberately vulnerable Next.js + Supabase application for demonstrating AI-native code review and security audit tools.

**DO NOT deploy this application.** It contains intentional security vulnerabilities for educational purposes.

## What's inside

A simple notes app with user authentication — the kind of project you'd see in a typical SaaS starter kit. It has several security issues that `review` and `pentest` plugins are designed to catch.

## Usage

```bash
# 1. Scaffold AI context
/ctx-init

# 2. Run code review
/review

# 3. Run security audit
/pentest --level L1
```

## Stack

- Next.js 14 (App Router)
- Supabase (Auth + Database)
- TypeScript
