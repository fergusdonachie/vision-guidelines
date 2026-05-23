"""
GET /api/readcodes?q=...&limit=N
Search READ codes by code or label.
"""
import csv
import json
import os
from urllib.parse import parse_qs

_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'read_codes_extracted.csv')


def _load():
    codes = []
    with open(_CSV, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            if row['label'].strip():
                codes.append({'code': row['code'], 'label': row['label'].strip()})
    return codes


_CODES = _load()


def app(environ, start_response):
    qs = parse_qs(environ.get('QUERY_STRING', ''))
    q = qs.get('q', [''])[0].lower().strip()
    try:
        limit = max(1, min(50, int(qs.get('limit', ['20'])[0])))
    except ValueError:
        limit = 20

    if q:
        results = [c for c in _CODES if q in c['code'].lower() or q in c['label'].lower()][:limit]
    else:
        results = _CODES[:limit]

    body = json.dumps(results, ensure_ascii=False).encode('utf-8')
    start_response('200 OK', [
        ('Content-Type', 'application/json; charset=utf-8'),
        ('Access-Control-Allow-Origin', '*'),
        ('Content-Length', str(len(body))),
    ])
    return [body]
