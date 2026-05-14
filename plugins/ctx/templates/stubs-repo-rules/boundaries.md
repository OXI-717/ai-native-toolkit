# Boundaries — what not to touch and why

TODO: list files, folders, or systems that should not be modified, and why.

Example:
- `legacy/` — old code, unsupported, migration in progress
- `migrations/0001_*` — finalized migrations, do not modify
- `.env.prod` — production credentials only via secret manager
