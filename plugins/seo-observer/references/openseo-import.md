# OpenSEO Import

OpenSEO evidence is imported as a read-only normalized artifact. The import keeps:

- source project metadata;
- rank tracker rows when structured MCP output is available;
- saved keyword basket, deterministic `kwbasket:` hash, and collection window;
- comparability status for the current basket against a previous basket hash.

Text-only MCP fallback is intentionally partial. The importer stores the raw text but does not derive positions, visibility, or other exact metrics from prose.
