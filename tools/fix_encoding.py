"""Normalise project source files to UTF-8.

Some editors on this machine write UTF-16LE, which CPython refuses to import
("source code string cannot contain null bytes"). This rewrites any affected
source file in place as UTF-8.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUFFIXES = {".py", ".yaml", ".yml", ".json", ".md", ".txt", ".ts", ".tsx", ".css", ".html"}
SKIP_DIRS = {".venv", "node_modules", ".git", "data", "__pycache__", "dist", "build"}


def candidates() -> list[Path]:
    out = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        out.append(path)
    return out


def main() -> int:
    fixed = []
    for path in candidates():
        raw = path.read_bytes()
        if b"\x00" not in raw:
            continue
        for enc in ("utf-16", "utf-16-le", "utf-16-be"):
            try:
                text = raw.decode(enc)
            except (UnicodeDecodeError, UnicodeError):
                continue
            if "\x00" in text:
                continue
            path.write_text(text, encoding="utf-8", newline="\n")
            fixed.append(path.relative_to(ROOT).as_posix())
            break
        else:
            print(f"!! could not decode {path}", file=sys.stderr)
    if fixed:
        print("re-encoded to UTF-8:")
        for f in fixed:
            print("  ", f)
    else:
        print("all source files already UTF-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
