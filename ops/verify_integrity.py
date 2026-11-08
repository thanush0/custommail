#!/usr/bin/env python3
"""Safe SHA-256 integrity checker for CustomMail source files.

Usage:
    python3 ops/verify_integrity.py create
    python3 ops/verify_integrity.py verify

Never deletes files or executes project code.
"""
from __future__ import annotations
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "ops" / "integrity-manifest.json"
DIRS = ("app", "static", "tests", "ops")
SUFFIXES = {".py", ".js", ".css", ".html", ".sh"}
SKIP_PARTS = {"__pycache__", ".pytest_cache", ".venv", "venv"}
SKIP_NAMES = {"integrity-manifest.json"}

def files():
    result = []
    for dirname in DIRS:
        folder = ROOT / dirname
        if not folder.exists():
            continue
        for path in folder.rglob("*"):
            if path.is_file() and path.name not in SKIP_NAMES:
                if path.suffix.lower() in SUFFIXES and not any(x in SKIP_PARTS for x in path.parts):
                    result.append(path)
    return sorted(result, key=lambda p: p.relative_to(ROOT).as_posix())

def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def create():
    entries = {p.relative_to(ROOT).as_posix(): sha256(p) for p in files()}
    data = {
        "format": 1,
        "algorithm": "sha256",
        "copyright_holder": "Thanush Priyan R.",
        "repository": "https://github.com/thanush0/custommail",
        "files": entries,
    }
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Created {MANIFEST.relative_to(ROOT)} ({len(entries)} files).")
    return 0

def verify():
    if not MANIFEST.is_file():
        print("Manifest missing. Run: python3 ops/verify_integrity.py create")
        return 2
    try:
        expected = json.loads(MANIFEST.read_text(encoding="utf-8"))["files"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Cannot read manifest: {exc}")
        return 2
    current = {p.relative_to(ROOT).as_posix(): p for p in files()}
    old, now = set(expected), set(current)
    modified = sorted(n for n in old & now if sha256(current[n]) != expected[n])
    missing, added = sorted(old - now), sorted(now - old)
    if not (modified or missing or added):
        print(f"OK: all {len(old)} files match the SHA-256 manifest.")
        return 0
    print("INTEGRITY DIFFERENCES (no files were changed or deleted):")
    for name in modified: print(f"  MODIFIED: {name}")
    for name in missing: print(f"  MISSING:  {name}")
    for name in added: print(f"  NEW:      {name}")
    print("Review differences. If authorized, run 'create' and commit the new manifest.")
    return 1

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"create", "verify"}:
        raise SystemExit("Usage: python3 ops/verify_integrity.py {create|verify}")
    raise SystemExit(create() if sys.argv[1] == "create" else verify())
