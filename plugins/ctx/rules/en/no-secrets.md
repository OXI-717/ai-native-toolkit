# Rule: secrets

- **Never** commit `.env`, `.env.*`, `credentials.json`, `*.pem`, `*.key`, or service-account JSON.
- Before `git add -A`, verify that `.gitignore` covers all sensitive files.
- If you find an already-committed secret — **immediately** notify the user. Do not try to silently remove it via amend.
- Credentials in code only through env vars (`os.getenv`, `process.env`, `System.getenv`) or a secret manager.
- Never print full tokens in logs — show only the first 4 and last 4 characters, mask the middle (e.g. `sk_li...wxyz`).
