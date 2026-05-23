#!/usr/bin/env python3
"""
Round-trip test for guideline_format.py

Loads every .htm file, parses it, exports it, and compares line by line.
"""

import sys
from pathlib import Path

# Make sure the module is importable
sys.path.insert(0, str(Path(__file__).parent))

from guideline_format import load_guideline, export_guideline


DIRS = [
    Path('/tmp/guidelines_extract'),
    Path('/root/.claude/uploads/6c2ae38c-d4a2-4a17-890d-4378d3e8e7f8'),
]

CONTEXT_LINES = 3


def find_htm_files():
    paths = []
    for d in DIRS:
        if d.exists():
            for f in sorted(d.rglob('*.htm')):
                paths.append(f)
    return paths


def compare_lines(original: str, exported: str, path: Path):
    orig_lines = original.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    exp_lines = exported.split('\n')

    # Remove trailing empty line if both have it
    while orig_lines and not orig_lines[-1].strip():
        orig_lines.pop()
    while exp_lines and not exp_lines[-1].strip():
        exp_lines.pop()

    diffs = []
    max_lines = max(len(orig_lines), len(exp_lines))
    for i in range(max_lines):
        orig_line = orig_lines[i] if i < len(orig_lines) else '<MISSING>'
        exp_line = exp_lines[i] if i < len(exp_lines) else '<MISSING>'
        if orig_line != exp_line:
            diffs.append((i + 1, orig_line, exp_line))

    return orig_lines, exp_lines, diffs


def run_tests():
    files = find_htm_files()
    print(f"Found {len(files)} .htm files\n")

    passed = []
    failed = []
    errors = []

    for path in files:
        try:
            original = path.read_text(encoding='latin-1', errors='replace')
            # Skip binary/corrupted files (e.g. files consisting mostly of null bytes)
            if original.count('\x00') > len(original) * 0.5:
                print(f"  SKIP  {path.name}  (binary/corrupted file)")
                continue
            g = load_guideline(path)
            exported = export_guideline(g)

            orig_lines, exp_lines, diffs = compare_lines(original, exported, path)

            if not diffs:
                passed.append(path)
                print(f"  PASS  {path.name}")
            else:
                failed.append((path, diffs, orig_lines, exp_lines))
                print(f"  FAIL  {path.name}  ({len(diffs)} differing line(s))")
        except Exception as e:
            errors.append((path, e))
            print(f"  ERROR {path.name}: {e}")

    print(f"\n{'='*70}")
    print(f"Results: {len(passed)} passed, {len(failed)} failed, {len(errors)} errors")
    print(f"{'='*70}")

    if errors:
        print(f"\nERRORS ({len(errors)}):")
        for path, e in errors:
            import traceback
            print(f"\n  {path.name}: {e}")
            traceback.print_exc()

    if failed:
        print(f"\nFAILURES ({len(failed)}):")
        for path, diffs, orig_lines, exp_lines in failed:
            print(f"\n--- {path} ---")
            print(f"  Total differences: {len(diffs)}")
            # Show first 5 diffs with context
            shown = 0
            for line_no, orig, exp in diffs[:5]:
                print(f"\n  Line {line_no}:")
                print(f"    ORIGINAL: {repr(orig)}")
                print(f"    EXPORTED: {repr(exp)}")
                shown += 1
            if len(diffs) > 5:
                print(f"\n  ... and {len(diffs) - 5} more differences")

    return len(failed) == 0 and len(errors) == 0


if __name__ == '__main__':
    success = run_tests()
    sys.exit(0 if success else 1)
