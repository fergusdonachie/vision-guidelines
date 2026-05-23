import json
import os
import re
import sqlite3
import urllib.parse

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "drugs.db")

_conn = None


def _get_conn():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
    return _conn


def _search(q, limit):
    q = q.strip()
    if not q:
        return []
    q = re.sub(r"[^\w\s\-]", " ", q, flags=re.UNICODE)
    tokens = [t for t in q.split() if t]
    if not tokens:
        return []
    fts_query = " ".join(t + "*" for t in tokens)
    con = _get_conn()
    rows = con.execute(
        "SELECT vpid, nm, abbrevnm FROM drugs_fts WHERE drugs_fts MATCH ? ORDER BY rank LIMIT ?",
        (fts_query, limit),
    ).fetchall()
    return [{"vpid": r["vpid"], "nm": r["nm"], "abbrevnm": r["abbrevnm"] or None} for r in rows]


def app(environ, start_response):
    params = urllib.parse.parse_qs(environ.get("QUERY_STRING", ""))
    q = params.get("q", [""])[0]
    try:
        limit = min(int(params.get("limit", ["20"])[0]), 100)
    except (ValueError, IndexError):
        limit = 20

    try:
        results = _search(q, limit)
        body = json.dumps(results, ensure_ascii=False).encode("utf-8")
        start_response("200 OK", [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Access-Control-Allow-Origin", "*"),
        ])
    except Exception as exc:
        body = json.dumps({"error": str(exc)}).encode("utf-8")
        start_response("500 Internal Server Error", [
            ("Content-Type", "application/json"),
        ])
    return [body]
