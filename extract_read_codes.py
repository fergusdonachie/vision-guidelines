#!/usr/bin/env python3
"""
Extract Read codes and labels from Vision guideline .htm files.

Two sources:
  1. DialogAdd commands  - <!Command=DialogAdd:#NNN\READCODE> + following <CITE> label
  2. Patient Data filters - READ_CODE = "..." expressions (code only, no label)
"""

import re
import os
import sys
import csv
from pathlib import Path
from html import unescape

# ── patterns ──────────────────────────────────────────────────────────────────

# <!Command=DialogAdd:#63\66AS.00> or <!Command=DialogAdd:#63\66AS.00  >
RE_DIALOG = re.compile(
    r'<!Command=DialogAdd:#\d+\\([A-Za-z0-9_\.\-]+)\s*>',
    re.IGNORECASE
)

# <CITE>&lt;Some Label&gt;</CITE>  (Vision uses HTML entities for < >)
RE_CITE = re.compile(r'<CITE>(.*?)</CITE>', re.IGNORECASE | re.DOTALL)

# READ_CODE = "246..00" or READ_CODE = "C10F.00 "  (may have trailing spaces)
RE_PATIENT_DATA_CODE = re.compile(
    r'READ_CODE\s*=\s*"([A-Za-z0-9_\.\-]+)\s*"',
    re.IGNORECASE
)

# READ_CODE2 = "66Z" style (second read code field)
RE_PATIENT_DATA_CODE2 = re.compile(
    r'READ_CODE2\s*=\s*"([A-Za-z0-9_\.\-]+)\s*"',
    re.IGNORECASE
)

# Snapcard field codes: e.g.  002BVHaem  003cVChest X-ray
# Format: CODE V LABEL  (tab-separated in snapcard lines)
# Snapcard row pattern: FIELDID then V then label, tab separated
RE_SNAPCARD_FIELD = re.compile(r'([A-Za-z0-9\?@]{4,6})V([^\t\n]+)')


def clean_label(raw):
    """Strip HTML entities and Vision angle-bracket wrappers, return plain text.

    CITE content looks like: &lt;Phlebotomy&gt;
    After unescape that becomes: <Phlebotomy>
    We want: Phlebotomy
    """
    text = unescape(raw)          # &lt;Phlebotomy&gt; → <Phlebotomy>
    text = text.strip()
    # Strip wrapping < > that Vision uses as display decoration
    text = re.sub(r'^<\s*', '', text)
    text = re.sub(r'\s*>$', '', text)
    # Remove <!vision> markers and their adjacent special chars (<br>, &, <, >)
    text = re.sub(r'<!vision>\s*(&lt;|&gt;|&amp;|<br>|<|>|&)', lambda m: {
        '&lt;': '<', '&gt;': '>', '&amp;': '&', '<br>': ' ', '<': '<', '>': '>', '&': '&'
    }.get(m.group(1), ''), text)
    text = re.sub(r'<!vision>', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def clean_code(code):
    return code.strip().rstrip('.')


def looks_like_read_code(code):
    """Rough filter — Read v2 codes are 5–7 alphanumeric chars, often with dots."""
    code = code.strip()
    if len(code) < 3 or len(code) > 10:
        return False
    # Must contain at least one letter
    if not any(c.isalpha() for c in code):
        return False
    return True


def extract_from_file(path):
    """
    Returns list of dicts: {code, label, source, file}
    """
    results = []
    text = path.read_text(encoding='utf-8', errors='replace')
    fname = path.name

    # ── 1. DialogAdd + CITE pairs ────────────────────────────────────────────
    # Walk through the file finding DialogAdd commands; the CITE label
    # appears shortly after in the same <TD> block.
    # Strategy: split on DialogAdd occurrences, look for first CITE after each.
    segments = RE_DIALOG.split(text)
    # split() on a group gives: [before, code1, after1, code2, after2, ...]
    # segments[0] = text before first match
    # segments[1] = captured code from first match
    # segments[2] = text after first match (up to next match)
    i = 1
    while i < len(segments):
        raw_code = segments[i].strip()
        after_text = segments[i + 1] if (i + 1) < len(segments) else ''
        # Find the first CITE in the after_text
        cite_match = RE_CITE.search(after_text[:500])  # limit lookahead
        label = clean_label(cite_match.group(1)) if cite_match else ''
        code = clean_code(raw_code)
        if looks_like_read_code(code):
            results.append({
                'code': code,
                'label': label,
                'source': 'DialogAdd',
                'file': fname,
            })
        i += 2

    # ── 2. Patient Data READ_CODE filters ────────────────────────────────────
    for m in RE_PATIENT_DATA_CODE.finditer(text):
        code = clean_code(m.group(1))
        if looks_like_read_code(code):
            results.append({
                'code': code,
                'label': '',
                'source': 'PatientData',
                'file': fname,
            })

    for m in RE_PATIENT_DATA_CODE2.finditer(text):
        code = clean_code(m.group(1))
        if looks_like_read_code(code):
            results.append({
                'code': code,
                'label': '',
                'source': 'PatientData2',
                'file': fname,
            })

    # ── 3. Snapcard field rows ───────────────────────────────────────────────
    # Lines like: Overview<!vision>>1D\t002BVHaem\t002MVMean...
    # after the >1D marker
    for line in text.splitlines():
        if '>1D\t' in line or '>1D' in line:
            for m in RE_SNAPCARD_FIELD.finditer(line):
                code = m.group(1).strip()
                label = m.group(2).strip()
                if looks_like_read_code(code):
                    results.append({
                        'code': code,
                        'label': label,
                        'source': 'Snapcard',
                        'file': fname,
                    })

    return results


def main():
    # Collect all htm files
    search_dirs = [
        Path('/tmp/guidelines_extract'),
        Path('/root/.claude/uploads'),
    ]

    all_files = []
    for d in search_dirs:
        if d.exists():
            all_files.extend(d.rglob('*.htm'))

    print(f"Scanning {len(all_files)} files...", file=sys.stderr)

    all_results = []
    for f in sorted(all_files):
        try:
            rows = extract_from_file(f)
            all_results.extend(rows)
        except Exception as e:
            print(f"  ERROR {f.name}: {e}", file=sys.stderr)

    print(f"Total extractions (with duplicates): {len(all_results)}", file=sys.stderr)

    # ── Deduplicate: prefer DialogAdd (has label) over PatientData (no label)
    # For same code, merge labels; prefer non-empty label
    code_map = {}  # code -> {label, sources, files}
    for row in all_results:
        code = row['code']
        if code not in code_map:
            code_map[code] = {
                'code': code,
                'label': row['label'],
                'sources': {row['source']},
                'files': {row['file']},
            }
        else:
            entry = code_map[code]
            # Upgrade label if we now have one and didn't before
            if not entry['label'] and row['label']:
                entry['label'] = row['label']
            entry['sources'].add(row['source'])
            entry['files'].add(row['file'])

    print(f"Unique codes: {len(code_map)}", file=sys.stderr)

    # ── Output CSV
    out_path = Path('/tmp/read_codes_extracted.csv')
    with out_path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['code', 'label', 'sources', 'files'])
        writer.writeheader()
        for entry in sorted(code_map.values(), key=lambda x: x['code']):
            writer.writerow({
                'code': entry['code'],
                'label': entry['label'],
                'sources': '|'.join(sorted(entry['sources'])),
                'files': '|'.join(sorted(entry['files'])),
            })

    print(f"Written to {out_path}", file=sys.stderr)

    # ── Summary stats
    with_labels = sum(1 for e in code_map.values() if e['label'])
    without_labels = len(code_map) - with_labels
    print(f"  With labels:    {with_labels}", file=sys.stderr)
    print(f"  Without labels: {without_labels}", file=sys.stderr)


if __name__ == '__main__':
    main()
