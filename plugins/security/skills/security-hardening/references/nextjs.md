# Next.js / React / Node.js Security Reference

> Actionable hardening guide for Next.js 14+ (App Router). Every recommendation
> includes a code snippet you can drop into a project. Organised by attack
> surface, not by framework feature.

---

## Table of Contents

1. [Server Components Security](#1-server-components-security)
2. [Server Actions Validation](#2-server-actions-validation)
3. [Middleware Auth & Route Protection](#3-middleware-auth--route-protection)
4. [Authentication](#4-authentication)
5. [API Security](#5-api-security)
6. [Headers & CSP](#6-headers--csp)
7. [Environment Variables](#7-environment-variables)
8. [Database Security](#8-database-security)
9. [Dependency Security](#9-dependency-security)
10. [XSS Prevention](#10-xss-prevention)
11. [SSRF Prevention](#11-ssrf-prevention)
12. [File Upload Security](#12-file-upload-security)
13. [Deployment](#13-deployment)
14. [Monitoring](#14-monitoring)
15. [CI/CD Security](#15-cicd-security)
16. [WAF](#16-waf)

---

## 1. Server Components Security

Server Components run only on the server, but careless prop passing can leak
secrets to the browser. Two rules prevent this.

### Never pass sensitive data as props to Client Components

When a Server Component renders a Client Component, every prop is serialised
into the RSC payload that ships to the browser. Database rows, tokens, and
internal IDs must be stripped before crossing the boundary.

```tsx
// app/dashboard/page.tsx  (Server Component)
import { db } from "@/lib/db";
import UserCard from "./user-card"; // "use client"

export default async function DashboardPage() {
  const user = await db.user.findUniqueOrThrow({
    where: { id: session.userId },
  });

  // BAD — hashedPassword and internalNotes travel to the browser
  // return <UserCard user={user} />;

  // GOOD — pick only what the client needs
  return (
    <UserCard
      user={{
        id: user.id,
        name: user.name,
        avatarUrl: user.avatarUrl,
      }}
    />
  );
}
```

### Use the `server-only` package

Mark any module that touches secrets or DB connections so that an accidental
import from a Client Component fails at build time instead of leaking code.

```bash
npm i server-only
```

```ts
// lib/db.ts
import "server-only";
import { PrismaClient } from "@prisma/client";

export const db = new PrismaClient();
```

If a `"use client"` file tries to import `lib/db.ts`, the build will error
immediately. This is a zero-cost compile-time guard.

---

## 2. Server Actions Validation

Server Actions are public HTTP endpoints. Any client can call them with
arbitrary payloads. Treat every Server Action like a POST route: validate
input, authenticate the caller, authorise the operation.

```ts
// app/actions/update-profile.ts
"use server";

import { z } from "zod";
import { auth } from "@/lib/auth";
import { db } from "@/lib/db";
import { revalidatePath } from "next/cache";

const UpdateProfileSchema = z.object({
  displayName: z.string().min(1).max(100),
  bio: z.string().max(500).optional(),
});

export async function updateProfile(formData: FormData) {
  // 1. Authenticate
  const session = await auth();
  if (!session?.user?.id) {
    throw new Error("Unauthenticated");
  }

  // 2. Validate
  const parsed = UpdateProfileSchema.safeParse({
    displayName: formData.get("displayName"),
    bio: formData.get("bio"),
  });
  if (!parsed.success) {
    return { error: parsed.error.flatten().fieldErrors };
  }

  // 3. Authorise (user can only edit own profile)
  const existing = await db.profile.findUnique({
    where: { userId: session.user.id },
  });
  if (!existing) {
    throw new Error("Forbidden");
  }

  // 4. Mutate
  await db.profile.update({
    where: { userId: session.user.id },
    data: parsed.data,
  });

  revalidatePath("/profile");
  return { success: true };
}
```

Why each step matters:

- **Authenticate** prevents anonymous callers from reaching business logic.
- **Validate with zod** rejects malformed or oversized input before it touches
  the database.
- **Authorise** ensures the caller owns the resource. Without this, an
  authenticated user could modify another user's profile by tampering with the
  request.

---

## 3. Middleware Auth & Route Protection

`middleware.ts` runs on every request at the edge before the page renders. Use
it as the single chokepoint for authentication and role-based routing.

```ts
// middleware.ts
import { NextRequest, NextResponse } from "next/server";
import { jwtVerify } from "jose";

const PUBLIC_PATHS = new Set(["/", "/login", "/register", "/api/health"]);
const PUBLIC_PREFIXES = ["/api/public/", "/_next/", "/favicon.ico"];

const JWT_SECRET = new TextEncoder().encode(process.env.JWT_SECRET!);

function isPublic(pathname: string): boolean {
  if (PUBLIC_PATHS.has(pathname)) return true;
  return PUBLIC_PREFIXES.some((p) => pathname.startsWith(p));
}

export async function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;

  if (isPublic(pathname)) {
    return NextResponse.next();
  }

  const token = req.cookies.get("session-token")?.value;
  if (!token) {
    return NextResponse.redirect(new URL("/login", req.url));
  }

  try {
    const { payload } = await jwtVerify(token, JWT_SECRET, {
      algorithms: ["HS256"],
    });

    // Role-based routing
    if (pathname.startsWith("/admin") && payload.role !== "admin") {
      return NextResponse.redirect(new URL("/", req.url));
    }

    // Propagate user info to downstream handlers via headers
    const res = NextResponse.next();
    res.headers.set("x-user-id", String(payload.sub));
    res.headers.set("x-user-role", String(payload.role));
    return res;
  } catch {
    // Token expired or tampered — clear it and redirect
    const res = NextResponse.redirect(new URL("/login", req.url));
    res.cookies.delete("session-token");
    return res;
  }
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
```

Why middleware and not per-page checks: a single file is easier to audit than
scattered `getServerSession()` calls. If a developer forgets the check on one
page, the middleware still blocks the request.

---

## 4. Authentication

### Auth.js (NextAuth v5) with PrismaAdapter

```ts
// lib/auth.ts
import NextAuth from "next-auth";
import { PrismaAdapter } from "@auth/prisma-adapter";
import Credentials from "next-auth/providers/credentials";
import GitHub from "next-auth/providers/github";
import { db } from "@/lib/db";
import bcrypt from "bcryptjs";
import { z } from "zod";

const LoginSchema = z.object({
  email: z.string().email(),
  password: z.string().min(8),
});

export const { handlers, auth, signIn, signOut } = NextAuth({
  adapter: PrismaAdapter(db),
  session: { strategy: "jwt", maxAge: 60 * 60 }, // 1 hour
  pages: { signIn: "/login" },
  providers: [
    GitHub({
      clientId: process.env.GITHUB_CLIENT_ID!,
      clientSecret: process.env.GITHUB_CLIENT_SECRET!,
    }),
    Credentials({
      async authorize(credentials) {
        const parsed = LoginSchema.safeParse(credentials);
        if (!parsed.success) return null;

        const user = await db.user.findUnique({
          where: { email: parsed.data.email },
        });
        if (!user?.hashedPassword) return null;

        const valid = await bcrypt.compare(
          parsed.data.password,
          user.hashedPassword
        );
        return valid ? user : null;
      },
    }),
  ],
  callbacks: {
    async jwt({ token, user }) {
      if (user) {
        token.role = user.role;   // Persist role in JWT
        token.sub = user.id;
      }
      return token;
    },
    async session({ session, token }) {
      session.user.id = token.sub!;
      session.user.role = token.role as string;
      return session;
    },
  },
});
```

```ts
// app/api/auth/[...nextauth]/route.ts
import { handlers } from "@/lib/auth";
export const { GET, POST } = handlers;
```

### JWT best practices

| Rule | Why |
|------|-----|
| Store JWT in an `httpOnly`, `Secure`, `SameSite=Lax` cookie | Prevents JS access (XSS cannot steal the token) |
| Keep token lifetime short (15-60 min) | Limits blast radius of a compromised token |
| Use a refresh token in a separate httpOnly cookie | Allows silent renewal without re-login |
| Rotate signing keys periodically | Limits damage from key compromise |
| Validate `iss`, `aud`, `exp` on every request | Prevents token reuse across services |

### CSRF protection

Auth.js includes built-in CSRF tokens for form-based sign-in. For custom
Server Actions, Next.js automatically binds the action to the origin, so
cross-origin form submissions are rejected. Verify the `Origin` header in
custom API routes:

```ts
// lib/csrf.ts
export function verifyCsrf(req: Request): boolean {
  const origin = req.headers.get("origin");
  const host = req.headers.get("host");
  if (!origin || !host) return false;
  return new URL(origin).host === host;
}
```

---

## 5. API Security

### Rate limiting with @upstash/ratelimit

```ts
// lib/rate-limit.ts
import { Ratelimit } from "@upstash/ratelimit";
import { Redis } from "@upstash/redis";

const redis = Redis.fromEnv();

// General API limiter: 60 requests per 60 seconds
export const apiLimiter = new Ratelimit({
  redis,
  limiter: Ratelimit.slidingWindow(60, "60 s"),
  analytics: true,
  prefix: "rl:api",
});

// Stricter limiter for auth endpoints: 5 attempts per 60 seconds
export const authLimiter = new Ratelimit({
  redis,
  limiter: Ratelimit.slidingWindow(5, "60 s"),
  analytics: true,
  prefix: "rl:auth",
});
```

```ts
// app/api/login/route.ts
import { authLimiter } from "@/lib/rate-limit";
import { NextRequest, NextResponse } from "next/server";

export async function POST(req: NextRequest) {
  const ip = req.headers.get("x-forwarded-for") ?? req.ip ?? "unknown";
  const { success, remaining, reset } = await authLimiter.limit(ip);

  if (!success) {
    return NextResponse.json(
      { error: "Too many requests" },
      {
        status: 429,
        headers: {
          "X-RateLimit-Remaining": String(remaining),
          "X-RateLimit-Reset": String(reset),
          "Retry-After": String(Math.ceil((reset - Date.now()) / 1000)),
        },
      }
    );
  }

  // ... handle login
}
```

### Input validation

Every API route must validate its body against a zod schema before processing:

```ts
const CreatePostSchema = z.object({
  title: z.string().min(1).max(200),
  body: z.string().min(1).max(50_000),
  tags: z.array(z.string().max(30)).max(10).optional(),
});

export async function POST(req: NextRequest) {
  const parsed = CreatePostSchema.safeParse(await req.json());
  if (!parsed.success) {
    return NextResponse.json(
      { error: parsed.error.flatten() },
      { status: 400 }
    );
  }
  // use parsed.data
}
```

### CORS allowlist

```ts
// lib/cors.ts
const ALLOWED_ORIGINS = new Set([
  "https://app.example.com",
  "https://admin.example.com",
]);

export function corsHeaders(req: Request): HeadersInit {
  const origin = req.headers.get("origin") ?? "";
  const headers: Record<string, string> = {
    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
    "Access-Control-Max-Age": "86400",
  };
  if (ALLOWED_ORIGINS.has(origin)) {
    headers["Access-Control-Allow-Origin"] = origin;
    headers["Vary"] = "Origin";
  }
  return headers;
}
```

Never set `Access-Control-Allow-Origin: *` on authenticated endpoints. An
explicit allowlist prevents credential leakage to arbitrary origins.

---

## 6. Headers & CSP

### next.config.js security headers

```js
// next.config.js
/** @type {import('next').NextConfig} */
const nextConfig = {
  poweredBy: false, // Remove X-Powered-By header

  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          {
            key: "Strict-Transport-Security",
            value: "max-age=63072000; includeSubDomains; preload",
          },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          {
            key: "Permissions-Policy",
            value:
              "camera=(), microphone=(), geolocation=(), payment=(self)",
          },
          {
            key: "Content-Security-Policy",
            value: [
              "default-src 'self'",
              "script-src 'self' 'nonce-{{nonce}}'",
              "style-src 'self' 'unsafe-inline'",
              "img-src 'self' data: https:",
              "font-src 'self'",
              "connect-src 'self' https://api.example.com",
              "frame-ancestors 'none'",
              "base-uri 'self'",
              "form-action 'self'",
              "upgrade-insecure-requests",
            ].join("; "),
          },
        ],
      },
    ];
  },
};

module.exports = nextConfig;
```

### Nonce-based CSP via middleware

For inline scripts (analytics, Next.js hydration), generate a per-request
nonce and inject it into the CSP header and the script tags.

```ts
// middleware.ts (append to existing middleware)
import { NextResponse } from "next/server";
import crypto from "crypto";

export function middleware(req: NextRequest) {
  const nonce = crypto.randomBytes(16).toString("base64");
  const csp = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}'`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: https:",
    "font-src 'self'",
    "connect-src 'self'",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
  ].join("; ");

  const res = NextResponse.next();
  res.headers.set("Content-Security-Policy", csp);
  res.headers.set("x-nonce", nonce); // Read in layout.tsx
  return res;
}
```

```tsx
// app/layout.tsx
import { headers } from "next/headers";

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const nonce = (await headers()).get("x-nonce") ?? "";

  return (
    <html lang="en">
      <body>
        <script nonce={nonce} src="/analytics.js" />
        {children}
      </body>
    </html>
  );
}
```

---

## 7. Environment Variables

### NEXT_PUBLIC_ exposure risk

Any variable prefixed with `NEXT_PUBLIC_` is inlined into the client bundle at
build time. This means it is visible to every user in the browser. Never put
API keys, database URLs, or secrets behind this prefix.

```bash
# .env
DATABASE_URL=postgresql://...      # Server only (safe)
JWT_SECRET=change-me-to-a-32-char-minimum-string # Server only
NEXT_PUBLIC_API_URL=https://...    # Inlined in browser JS (public)
NEXT_PUBLIC_STRIPE_KEY=pk_live_... # Public key — OK
# NEXT_PUBLIC_STRIPE_SECRET=...   # NEVER — this is a secret key
```

### Validate at build time with @t3-oss/env-nextjs

Catch missing or mistyped variables before the app starts rather than at
runtime when a user hits the broken codepath.

```ts
// lib/env.ts
import { createEnv } from "@t3-oss/env-nextjs";
import { z } from "zod";

export const env = createEnv({
  server: {
    DATABASE_URL: z.string().url(),
    JWT_SECRET: z.string().min(32),
    GITHUB_CLIENT_ID: z.string().min(1),
    GITHUB_CLIENT_SECRET: z.string().min(1),
    UPSTASH_REDIS_REST_URL: z.string().url(),
    UPSTASH_REDIS_REST_TOKEN: z.string().min(1),
  },
  client: {
    NEXT_PUBLIC_API_URL: z.string().url(),
  },
  runtimeEnv: {
    DATABASE_URL: process.env.DATABASE_URL,
    JWT_SECRET: process.env.JWT_SECRET,
    GITHUB_CLIENT_ID: process.env.GITHUB_CLIENT_ID,
    GITHUB_CLIENT_SECRET: process.env.GITHUB_CLIENT_SECRET,
    UPSTASH_REDIS_REST_URL: process.env.UPSTASH_REDIS_REST_URL,
    UPSTASH_REDIS_REST_TOKEN: process.env.UPSTASH_REDIS_REST_TOKEN,
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL,
  },
});
```

### .env.example pattern

Commit a `.env.example` with placeholder values and no secrets. This documents
every required variable so new developers (and CI) can set up quickly:

```bash
# .env.example — commit this file
DATABASE_URL=postgresql://user:pass@localhost:5432/mydb
JWT_SECRET=change-me-to-a-32-char-minimum-string
GITHUB_CLIENT_ID=
GITHUB_CLIENT_SECRET=
# Set public variables below.
NEXT_PUBLIC_API_URL=http://localhost:3000
```

---

## 8. Database Security

### Prisma parameterised queries

Prisma's query builder uses parameterised queries by default, which prevents
SQL injection. Always use the builder:

```ts
// SAFE — parameterised automatically
const user = await db.user.findUnique({ where: { email } });
```

### Danger of $queryRawUnsafe

`$queryRawUnsafe` passes the string directly to the database. Never
interpolate user input into it:

```ts
// DANGEROUS — SQL injection
await db.$queryRawUnsafe(`SELECT * FROM users WHERE email = '${email}'`);

// SAFE — use $queryRaw with tagged template
await db.$queryRaw`SELECT * FROM users WHERE email = ${email}`;
```

### Connection pooling for serverless

Each serverless invocation creates a new database connection. Without pooling,
you will exhaust the connection limit within minutes under load. Use PgBouncer
or Prisma Accelerate:

```bash
# .env — point at the pooler, not the database directly
DATABASE_URL="postgresql://user:pass@pooler.example.com:6432/mydb?pgbouncer=true"
```

### Row-level security pattern

Enforce data isolation at the query level so that even a logic bug in the
application layer cannot leak another tenant's data:

```ts
// lib/db-scoped.ts
import "server-only";
import { db } from "@/lib/db";
import { auth } from "@/lib/auth";

export async function scopedDb() {
  const session = await auth();
  if (!session?.user?.id) throw new Error("Unauthenticated");

  return {
    posts: {
      findMany: (args?: Parameters<typeof db.post.findMany>[0]) =>
        db.post.findMany({
          ...args,
          where: { ...args?.where, authorId: session.user.id },
        }),
    },
  };
}
```

### Credentials rotation

Never hard-code database credentials. Use a secrets manager (Doppler, Infisical,
AWS Secrets Manager) and rotate credentials on a schedule. The application
reads the current credential at startup or via a sidecar that injects it as an
environment variable.

---

## 9. Dependency Security

### npm audit

Run `npm audit` in CI on every pull request. Fail the build on critical or
high severity vulnerabilities:

```bash
npm audit --audit-level=high
```

### GitHub Dependabot config

```yaml
# .github/dependabot.yml
version: 2
updates:
  - package-ecosystem: npm
    directory: /
    schedule:
      interval: weekly
    open-pull-requests-limit: 10
    reviewers:
      - security-team
    labels:
      - dependencies
      - security
```

### Socket.dev and Snyk

Socket.dev detects supply-chain attacks (typosquatting, install scripts,
obfuscated code) that `npm audit` misses. Snyk provides deeper vulnerability
analysis with fix PRs. Use both as GitHub Apps — they run automatically on PRs.

### Lockfile integrity

Always install with `npm ci` in CI to respect the lockfile exactly. Verify
package signatures when available:

```bash
npm ci
npm audit signatures  # Verify registry signatures on installed packages
```

---

## 10. XSS Prevention

### React's built-in escaping

React escapes all string values rendered in JSX. This is safe by default:

```tsx
// Safe — React escapes the string
<p>{userInput}</p>
```

### dangerouslySetInnerHTML + DOMPurify

When you must render HTML (e.g., from a CMS), sanitise it first:

```tsx
// components/safe-html.tsx
"use client";

import DOMPurify from "dompurify";

interface SafeHtmlProps {
  html: string;
  className?: string;
  allowedTags?: string[];
}

export function SafeHtml({ html, className, allowedTags }: SafeHtmlProps) {
  const clean = DOMPurify.sanitize(html, {
    ALLOWED_TAGS: allowedTags ?? [
      "p", "br", "strong", "em", "a", "ul", "ol", "li",
      "h1", "h2", "h3", "h4", "code", "pre", "blockquote",
    ],
    ALLOWED_ATTR: ["href", "target", "rel", "class"],
    ALLOW_DATA_ATTR: false,
  });

  return (
    <div
      className={className}
      dangerouslySetInnerHTML={{ __html: clean }}
    />
  );
}
```

### URL validation for href attributes

Attackers can inject `javascript:` URLs into anchor tags. Always validate:

```ts
export function isSafeUrl(url: string): boolean {
  try {
    const parsed = new URL(url, "https://placeholder.invalid");
    return ["https:", "http:", "mailto:"].includes(parsed.protocol);
  } catch {
    return false;
  }
}
```

```tsx
{isSafeUrl(link.url) ? (
  <a href={link.url} rel="noopener noreferrer">{link.label}</a>
) : (
  <span>{link.label}</span>
)}
```

---

## 11. SSRF Prevention

Server-side code that fetches user-supplied URLs can be tricked into hitting
internal services (metadata endpoints, admin panels, databases). Block this
with a `safeFetch` wrapper.

```ts
// lib/safe-fetch.ts
import "server-only";
import { isIP } from "net";
import dns from "dns/promises";

const PRIVATE_RANGES = [
  /^127\./, /^10\./, /^172\.(1[6-9]|2\d|3[01])\./, /^192\.168\./,
  /^169\.254\./, /^0\./, /^fc00:/i, /^fe80:/i, /^::1$/, /^fd/i,
];

function isPrivateIp(ip: string): boolean {
  return PRIVATE_RANGES.some((r) => r.test(ip));
}

export async function safeFetch(
  url: string,
  init?: RequestInit
): Promise<Response> {
  // 1. Parse and validate URL scheme
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    throw new Error("Invalid URL");
  }

  if (!["https:", "http:"].includes(parsed.protocol)) {
    throw new Error(`Blocked protocol: ${parsed.protocol}`);
  }

  // 2. Resolve DNS and check for private IPs
  const hostname = parsed.hostname;
  if (isIP(hostname)) {
    if (isPrivateIp(hostname)) throw new Error("Blocked private IP");
  } else {
    const addresses = await dns.resolve4(hostname).catch(() => []);
    const addresses6 = await dns.resolve6(hostname).catch(() => []);
    const all = [...addresses, ...addresses6];
    if (all.length === 0) throw new Error("DNS resolution failed");
    if (all.some(isPrivateIp)) throw new Error("Blocked private IP after DNS");
  }

  // 3. Fetch with timeout, no redirects (prevent redirect-to-internal)
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10_000);

  try {
    const res = await fetch(parsed.toString(), {
      ...init,
      signal: controller.signal,
      redirect: "error", // Block redirects that might hit internal hosts
    });
    return res;
  } finally {
    clearTimeout(timeout);
  }
}
```

Why `redirect: "error"`: an attacker can host a URL that 302-redirects to
`http://169.254.169.254/latest/meta-data/` (the AWS metadata endpoint). By
rejecting redirects and requiring the caller to handle them explicitly, you
prevent this bypass.

---

## 12. File Upload Security

### Full upload handler

```ts
// app/api/upload/route.ts
import { NextRequest, NextResponse } from "next/server";
import { fileTypeFromBuffer } from "file-type";
import sharp from "sharp";
import crypto from "crypto";
import { auth } from "@/lib/auth";
import { uploadToS3 } from "@/lib/s3";

const ALLOWED_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);
const MAX_SIZE = 5 * 1024 * 1024; // 5 MB

export async function POST(req: NextRequest) {
  const session = await auth();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthenticated" }, { status: 401 });
  }

  const formData = await req.formData();
  const file = formData.get("file") as File | null;
  if (!file) {
    return NextResponse.json({ error: "No file provided" }, { status: 400 });
  }

  // 1. Size check
  if (file.size > MAX_SIZE) {
    return NextResponse.json({ error: "File too large" }, { status: 413 });
  }

  const buffer = Buffer.from(await file.arrayBuffer());

  // 2. Magic bytes check (do not trust Content-Type header)
  const detected = await fileTypeFromBuffer(buffer);
  if (!detected || !ALLOWED_TYPES.has(detected.mime)) {
    return NextResponse.json({ error: "Invalid file type" }, { status: 415 });
  }

  // 3. Re-encode with sharp to strip EXIF, embedded scripts, polyglots
  const sanitised = await sharp(buffer)
    .resize(2048, 2048, { fit: "inside", withoutEnlargement: true })
    .toFormat("webp", { quality: 80 })
    .toBuffer();

  // 4. Random filename to prevent enumeration
  const filename = `${crypto.randomUUID()}.webp`;

  // 5. Upload to a separate domain / S3 bucket
  const url = await uploadToS3(sanitised, filename, "image/webp");

  return NextResponse.json({ url });
}
```

Why re-encode instead of just checking magic bytes: a valid JPEG can contain
embedded JavaScript (polyglot file) that executes if served with the wrong
Content-Type. Re-encoding strips all non-image data.

Serve uploads from a separate domain (e.g., `cdn.example.com`) so that
any XSS in the upload cannot access cookies on the main domain.

---

## 13. Deployment

### Docker multi-stage build

```dockerfile
# Dockerfile
FROM node:20-alpine AS base
RUN apk add --no-cache dumb-init

FROM base AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev

FROM base AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM base AS runner
WORKDIR /app
ENV NODE_ENV=production

# Non-root user
RUN addgroup -g 1001 -S appgroup && \
    adduser -S appuser -u 1001 -G appgroup
USER appuser

COPY --from=deps --chown=appuser:appgroup /app/node_modules ./node_modules
COPY --from=build --chown=appuser:appgroup /app/.next ./.next
COPY --from=build --chown=appuser:appgroup /app/public ./public
COPY --from=build --chown=appuser:appgroup /app/package.json ./

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD wget -qO- http://localhost:3000/api/health || exit 1

# dumb-init handles PID 1 and signal forwarding
ENTRYPOINT ["dumb-init", "--"]
CMD ["node", "node_modules/.bin/next", "start"]
```

Run the container with additional hardening:

```bash
docker run \
  --read-only \
  --tmpfs /tmp \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  -p 3000:3000 \
  myapp:latest
```

- `--read-only`: prevents the process from writing to the filesystem (supply
  chain malware cannot drop files)
- `--cap-drop ALL`: removes all Linux capabilities (the app does not need
  `NET_RAW`, `SYS_ADMIN`, etc.)
- `dumb-init`: ensures graceful shutdown on SIGTERM

### Secrets management on Vercel / Railway

Never commit secrets. Inject them via the platform's environment variable UI
or CLI:

```bash
vercel env add JWT_SECRET production
railway variables set JWT_SECRET=...
```

---

## 14. Monitoring

### Sentry setup with PII scrubbing

```ts
// sentry.server.config.ts
import * as Sentry from "@sentry/nextjs";

Sentry.init({
  dsn: process.env.SENTRY_DSN,
  environment: process.env.NODE_ENV,
  tracesSampleRate: 0.1,

  beforeSend(event) {
    // Strip PII from error reports
    if (event.request?.cookies) {
      event.request.cookies = {};
    }
    if (event.request?.headers) {
      delete event.request.headers["authorization"];
      delete event.request.headers["cookie"];
    }
    // Remove user IP
    if (event.user) {
      delete event.user.ip_address;
    }
    return event;
  },
});
```

### Error boundaries

```tsx
// app/error.tsx — catches errors in a route segment
"use client";

import * as Sentry from "@sentry/nextjs";
import { useEffect } from "react";

export default function ErrorBoundary({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    Sentry.captureException(error);
  }, [error]);

  return (
    <div>
      <h2>Something went wrong</h2>
      {/* Never show error.message to users — it may contain internal details */}
      <p>An unexpected error occurred. Please try again.</p>
      <button onClick={reset}>Retry</button>
    </div>
  );
}
```

```tsx
// app/global-error.tsx — catches errors in root layout
"use client";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html>
      <body>
        <h2>Something went wrong</h2>
        <button onClick={reset}>Retry</button>
      </body>
    </html>
  );
}
```

### Structured logging with pino

```ts
// lib/logger.ts
import pino from "pino";

export const logger = pino({
  level: process.env.LOG_LEVEL ?? "info",
  redact: {
    paths: [
      "req.headers.authorization",
      "req.headers.cookie",
      "password",
      "hashedPassword",
      "token",
      "secret",
      "creditCard",
      "ssn",
    ],
    censor: "[REDACTED]",
  },
  serializers: {
    err: pino.stdSerializers.err,
    req: pino.stdSerializers.req,
    res: pino.stdSerializers.res,
  },
});
```

Why pino over console.log: structured JSON logs are searchable in log
aggregators (Datadog, Grafana Loki). The `redact` config prevents accidental
PII leakage into logs.

---

## 15. CI/CD Security

### GitHub Actions workflow

```yaml
# .github/workflows/security.yml
name: Security Checks

on:
  pull_request:
    branches: [main]
  push:
    branches: [main]

permissions:
  contents: read
  security-events: write

jobs:
  security:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: 20
          cache: npm

      - name: Install dependencies
        run: npm ci

      - name: TypeScript check
        run: npx tsc --noEmit

      - name: npm audit
        run: npm audit --audit-level=high

      - name: Gitleaks (secret detection)
        uses: gitleaks/gitleaks-action@v2
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}

      - name: Semgrep SAST
        uses: returntocorp/semgrep-action@v1
        with:
          config: >-
            p/nextjs
            p/react
            p/owasp-top-ten
            p/typescript
```

### ESLint security plugin

```bash
npm i -D eslint-plugin-security
```

```js
// .eslintrc.js
module.exports = {
  plugins: ["security"],
  extends: ["plugin:security/recommended-legacy"],
};
```

This catches patterns like `eval()`, `child_process.exec()` with string
interpolation, and non-literal `require()` calls.

### Husky pre-commit hooks

```bash
npm i -D husky lint-staged
npx husky init
```

```bash
# .husky/pre-commit
npx lint-staged
```

```json
// package.json
{
  "lint-staged": {
    "*.{ts,tsx}": ["eslint --fix", "prettier --write"],
    "*.{json,md}": ["prettier --write"]
  }
}
```

---

## 16. WAF

### Arcjet

Arcjet provides bot detection, rate limiting, and shield protection as an
SDK that runs at the edge with no external proxy.

```ts
// lib/arcjet.ts
import arcjet, { shield, detectBot, tokenBucket } from "@arcjet/next";

export const aj = arcjet({
  key: process.env.ARCJET_KEY!,
  characteristics: ["ip.src"],
  rules: [
    // Block common attack patterns (SQLi, XSS payloads in headers)
    shield({ mode: "LIVE" }),

    // Block automated clients (scrapers, headless browsers)
    detectBot({
      mode: "LIVE",
      allow: ["CATEGORY:SEARCH_ENGINE", "CATEGORY:MONITOR"],
    }),

    // Rate limit: 100 tokens, refill 10 per second
    tokenBucket({
      mode: "LIVE",
      refillRate: 10,
      interval: 1,
      capacity: 100,
    }),
  ],
});
```

```ts
// app/api/protected/route.ts
import { aj } from "@/lib/arcjet";
import { NextRequest, NextResponse } from "next/server";

export async function GET(req: NextRequest) {
  const decision = await aj.protect(req);

  if (decision.isDenied()) {
    if (decision.reason.isRateLimit()) {
      return NextResponse.json({ error: "Rate limited" }, { status: 429 });
    }
    if (decision.reason.isBot()) {
      return NextResponse.json({ error: "Bot detected" }, { status: 403 });
    }
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  }

  return NextResponse.json({ data: "protected content" });
}
```

### Cloudflare WAF managed rulesets

If your app sits behind Cloudflare, enable these managed rulesets in the
dashboard or via Terraform:

```hcl
# cloudflare-waf.tf
resource "cloudflare_ruleset" "waf" {
  zone_id = var.zone_id
  name    = "WAF managed rules"
  kind    = "zone"
  phase   = "http_request_firewall_managed"

  rules {
    action = "execute"
    action_parameters {
      id = "efb7b8c949ac4650a09736fc376e9aee" # Cloudflare Managed Ruleset
    }
    expression  = "true"
    description = "Execute Cloudflare Managed Ruleset"
    enabled     = true
  }

  rules {
    action = "execute"
    action_parameters {
      id = "c2e184081120413c86c3ab7e14069605" # OWASP Core Ruleset
    }
    expression  = "true"
    description = "Execute OWASP Core Ruleset"
    enabled     = true
  }
}
```

Enable "Bot Fight Mode" and "Super Bot Fight Mode" in the Cloudflare dashboard
for automated bot detection without application-level changes.

---

## Quick Checklist

Use this as a PR review checklist:

- [ ] No secrets in `NEXT_PUBLIC_` variables
- [ ] All Server Actions validate input, authenticate, and authorise
- [ ] `server-only` on modules that touch secrets
- [ ] Security headers set (HSTS, CSP, X-Frame-Options, nosniff)
- [ ] Rate limiting on auth and sensitive endpoints
- [ ] User-supplied URLs go through `safeFetch`
- [ ] File uploads validated by magic bytes, re-encoded, served from separate domain
- [ ] `dangerouslySetInnerHTML` uses DOMPurify
- [ ] No `$queryRawUnsafe` with interpolated values
- [ ] Docker runs as non-root with `--cap-drop ALL`
- [ ] Sentry strips PII before sending
- [ ] CI runs npm audit, gitleaks, and Semgrep
- [ ] Dependencies updated via Dependabot weekly
