"""Prints a template's reference program, found by its title. Only the browser E2E uses it (frontend/e2e): it plays a candidate
who solved the problem, so the whole path from typing to grading can be proved. It reads the repository, never the application."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from app.coding.reference import reference_program  # noqa: E402
from app.coding.templates import TEMPLATES  # noqa: E402


def main(title: str) -> int:
    for key, t in TEMPLATES.items():
        if t.title == title:
            sys.stdout.write(reference_program(key))
            return 0
    sys.stderr.write(f"no template titled {title!r}\n")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
