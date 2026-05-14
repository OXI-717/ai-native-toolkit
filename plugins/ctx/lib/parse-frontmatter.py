#!/usr/bin/env python3
"""Parse YAML frontmatter from a markdown file, emit JSON on stdout.

Usage: parse-frontmatter.py <file.md>
Output: JSON object with frontmatter fields, or {} if no frontmatter.
Exits 0 on success, 1 on error (file not found, invalid yaml).
"""
import sys
import json

try:
    import yaml
except ImportError:
    print(json.dumps({"_error": "pyyaml not installed"}), file=sys.stderr)
    sys.exit(1)

def main():
    if len(sys.argv) != 2:
        print("usage: parse-frontmatter.py <file.md>", file=sys.stderr)
        sys.exit(1)
    path = sys.argv[1]
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        print(json.dumps({}))
        return
    if not content.startswith("---\n"):
        print(json.dumps({}))
        return
    end = content.find("\n---\n", 4)
    if end == -1:
        print(json.dumps({}))
        return
    fm = content[4:end]
    try:
        data = yaml.safe_load(fm) or {}
    except yaml.YAMLError as e:
        print(json.dumps({"_error": f"yaml parse error: {e}"}), file=sys.stderr)
        sys.exit(1)
    print(json.dumps(data, ensure_ascii=False))

if __name__ == "__main__":
    main()
