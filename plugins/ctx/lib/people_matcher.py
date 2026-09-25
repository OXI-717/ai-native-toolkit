"""Find vault people cards by free-form name.

Shared by `ctx-lint.py` and `ctx-people.py`. Both resolve key_people names
from AGENTS.md frontmatter into actual vault card files. Using the same
matcher guarantees consistency.

Vault cards use format `Фамилия-Имя.md`. The `name` argument can be any of:
  "Фамилия" / "Фамилия Имя" / "Фамилия Имя Отчество" / "Имя Фамилия" /
  "Alexander Povaliaev (tg:123)" (with parenthetical).

Returns (matches, glob_label):
  - matches is a list of pathlib.Path; single element on narrow match,
    multiple if ambiguous, empty if nothing found
  - glob_label is the pattern that produced the result (for reporting)
"""

import glob as _glob
import re
from pathlib import Path


def find_person_cards(name, people_dir):
    """Resolve a free-form person name into vault card paths.

    Strategy — try in order, stop at first narrow match (≤1 result):
      1. Exact surname+firstname hyphenated: `Surname-Firstname.md`
      2. Firstname-first swap: `Lastname-Firstname.md` (for "Имя Фамилия")
      3. Broad surname glob: `Surname*.md` (first word)
      4. Firstname as second component: `*-Firstname.md` (first word as given)
      5. Second word as second component (when first is full surname)

    Error labels in glob_label:
      "empty"            — name is None, empty, or has no word parts
      "dir-missing:..."  — people_dir does not exist (vault unmounted?)
      "glob-error:..."   — OSError/ValueError during glob (permissions, bad pattern)
    """
    if not isinstance(name, str) or not name.strip():
        return [], "empty"

    people_dir = Path(people_dir)
    if not people_dir.exists():
        return [], f"dir-missing:{people_dir}"

    cleaned = re.sub(r"\s*\([^)]*\)\s*", " ", name).strip()
    parts = cleaned.split()
    if not parts:
        return [], "empty"

    candidates_attempted = []

    # Attempt 1 & 2: exact hyphenated match in both orderings.
    # Path construction + .exists() — no glob metachars risk here.
    if len(parts) >= 2:
        p1, p2 = parts[0], parts[1]
        for first_comp, second_comp in ((p1, p2), (p2, p1)):
            target = people_dir / f"{first_comp}-{second_comp}.md"
            candidates_attempted.append(target.name)
            if target.exists():
                return [target], target.name

    # Escape glob metacharacters in name parts before using them in glob patterns.
    # Names like "O'[Brien]" or "*Star" would otherwise be interpreted as glob
    # patterns and cause wrong matches or ValueError in the glob engine.
    p0 = _glob.escape(parts[0])

    # Attempt 3: broad surname glob (first word)
    try:
        matches = sorted(people_dir.glob(f"{p0}*.md"))
    except (OSError, ValueError) as e:
        return [], f"glob-error:{e}"
    candidates_attempted.append(f"{parts[0]}*.md")
    if len(matches) == 1:
        return matches, f"{parts[0]}*.md"

    # Attempt 4: firstname as second component (for "Имя ..." style)
    try:
        name_glob = sorted(people_dir.glob(f"*-{p0}.md"))
    except (OSError, ValueError) as e:
        return [], f"glob-error:{e}"
    candidates_attempted.append(f"*-{parts[0]}.md")
    if len(name_glob) == 1:
        return name_glob, f"*-{parts[0]}.md"

    # Attempt 5: second word as second component
    second_glob = []
    if len(parts) >= 2:
        p1_escaped = _glob.escape(parts[1])
        try:
            second_glob = sorted(people_dir.glob(f"*-{p1_escaped}.md"))
        except (OSError, ValueError) as e:
            return [], f"glob-error:{e}"
        candidates_attempted.append(f"*-{parts[1]}.md")
        if len(second_glob) == 1:
            return second_glob, f"*-{parts[1]}.md"

    # Merge all broad matches, dedup by full path (not basename)
    seen = set()
    merged = []
    for collection in (matches, name_glob, second_glob):
        for m in collection:
            if m not in seen:
                seen.add(m)
                merged.append(m)

    return sorted(merged), ", ".join(candidates_attempted)
