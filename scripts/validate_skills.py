#!/usr/bin/env python3
"""Validate bundled skills with the Agent Skills reference validator."""
from pathlib import Path
import re
import sys

from skills_ref import validate


COPILOT_EXTENSION = re.compile(
    r"^Unexpected fields in frontmatter: disable-model-invocation\. "
)


def main() -> int:
    failed = False
    for skill_dir in sorted(Path("skills").iterdir()):
        if not skill_dir.is_dir():
            continue
        problems = [str(problem) for problem in validate(skill_dir)]
        problems = [problem for problem in problems if not COPILOT_EXTENSION.match(problem)]
        if problems:
            failed = True
            print(f"Validation failed for {skill_dir}:", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
        else:
            print(f"Validated {skill_dir}")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())