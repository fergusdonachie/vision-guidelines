"""
POST /api/parse — accept raw .htm guideline file, return structured JSON.

The JSON has every dataclass field plus a "_type" discriminator.
"""
import json
import os
import sys
from dataclasses import fields, is_dataclass

# Vercel bundles guideline_format.py next to api/ — find it in the parent dir
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from guideline_format import (
    parse_guideline,
    Guideline,
    GuidelineMetadata,
    Section,
    Cell,
    DrugEntry,
    AdviceText,
    SubGuidelineLink,
    FilterBlock,
    MultimediaBlock,
    H2Block,
    SnapcardRow,
    SnapcardField,
    DataRecord,
    PatientDataQuery,
    _RegimeMarker,
    _SnapcardMarker,
)

MAX_BODY = 5 * 1024 * 1024  # 5 MB


def _serialize(obj):
    """Recursively convert a dataclass tree to JSON-serializable dicts."""
    if isinstance(obj, _SnapcardMarker):
        return {"_type": "SnapcardMarker"}

    if isinstance(obj, _RegimeMarker):
        return {"_type": "RegimeMarker", "name": obj.name}

    if is_dataclass(obj) and not isinstance(obj, type):
        type_name = type(obj).__name__
        d = {"_type": type_name}
        for f in fields(obj):
            d[f.name] = _serialize(getattr(obj, f.name))
        return d

    if isinstance(obj, list):
        return [_serialize(item) for item in obj]

    # Primitives — str, int, float, bool, None
    return obj


def _cors_headers():
    return [
        ("Access-Control-Allow-Origin", "*"),
        ("Access-Control-Allow-Methods", "POST, OPTIONS"),
        ("Access-Control-Allow-Headers", "Content-Type"),
    ]


def app(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET").upper()

    # CORS preflight
    if method == "OPTIONS":
        start_response("204 No Content", _cors_headers())
        return [b""]

    if method != "POST":
        start_response("405 Method Not Allowed", _cors_headers())
        return [b'{"error": "POST required"}']

    try:
        length = int(environ.get("CONTENT_LENGTH", 0) or 0)
    except ValueError:
        length = 0

    if length > MAX_BODY:
        start_response("413 Request Entity Too Large", _cors_headers())
        return [b'{"error": "File too large (max 5 MB)"}']

    wsgi_input = environ.get("wsgi.input")
    raw_bytes = wsgi_input.read(length) if wsgi_input else b""

    # Try UTF-8 first, fall back to latin-1
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = raw_bytes.decode("latin-1", errors="replace")

    try:
        g = parse_guideline(text)
        payload = _serialize(g)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = _cors_headers() + [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
        ]
        start_response("200 OK", headers)
    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        body = json.dumps({"error": str(exc), "traceback": tb}).encode("utf-8")
        headers = _cors_headers() + [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
        ]
        start_response("500 Internal Server Error", headers)

    return [body]
