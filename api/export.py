"""
POST /api/export — accept guideline JSON, return the .htm file as a download.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from guideline_format import (
    export_guideline,
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

MAX_BODY = 10 * 1024 * 1024  # 10 MB — JSON is larger than the original .htm

_TYPE_MAP = {
    "Guideline": Guideline,
    "GuidelineMetadata": GuidelineMetadata,
    "Section": Section,
    "Cell": Cell,
    "DrugEntry": DrugEntry,
    "AdviceText": AdviceText,
    "SubGuidelineLink": SubGuidelineLink,
    "FilterBlock": FilterBlock,
    "MultimediaBlock": MultimediaBlock,
    "H2Block": H2Block,
    "SnapcardRow": SnapcardRow,
    "SnapcardField": SnapcardField,
    "DataRecord": DataRecord,
    "PatientDataQuery": PatientDataQuery,
    # markers with class name as stored by _serialize
    "RegimeMarker": _RegimeMarker,
    "SnapcardMarker": _SnapcardMarker,
}


def _deserialize(obj):
    """Recursively convert JSON dicts back to dataclass instances."""
    if isinstance(obj, list):
        return [_deserialize(item) for item in obj]

    if not isinstance(obj, dict):
        return obj

    type_name = obj.get("_type")
    if type_name is None:
        return obj

    # Marker special cases
    if type_name == "SnapcardMarker":
        return _SnapcardMarker()

    if type_name == "RegimeMarker":
        return _RegimeMarker(name=obj.get("name", ""))

    cls = _TYPE_MAP.get(type_name)
    if cls is None:
        # Unknown type — return as plain dict without the _type key
        return {k: _deserialize(v) for k, v in obj.items() if k != "_type"}

    from dataclasses import fields as dc_fields
    field_names = {f.name for f in dc_fields(cls)}
    kwargs = {}
    for k, v in obj.items():
        if k == "_type":
            continue
        if k in field_names:
            kwargs[k] = _deserialize(v)

    return cls(**kwargs)


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
        return [b'{"error": "Payload too large (max 10 MB)"}']

    wsgi_input = environ.get("wsgi.input")
    raw_bytes = wsgi_input.read(length) if wsgi_input else b""

    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
        g = _deserialize(payload)

        if not isinstance(g, Guideline):
            raise ValueError(f"Expected Guideline at root, got {type(g).__name__}")

        htm_text = export_guideline(g)
        body = htm_text.encode("latin-1", errors="replace")

        mnemonic = g.metadata.mnemonic or "guideline"
        # Sanitize for use as filename
        safe_name = "".join(c for c in mnemonic if c.isalnum() or c in "-_")
        filename = f"{safe_name}.htm"

        headers = _cors_headers() + [
            ("Content-Type", "text/html; charset=iso-8859-1"),
            ("Content-Disposition", f'attachment; filename="{filename}"'),
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
