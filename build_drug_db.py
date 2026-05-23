"""
Build drugs.db from a dm+d VMP XML file.

Usage:
    python3 build_drug_db.py path/to/vmp2_*.xml [drugs.db]

Prescribing status codes included:
    0001  Valid as a prescribable product
    0009  Recently added / pending full classification
"""
import os
import sqlite3
import sys
import xml.etree.ElementTree as ET

PRES_STAT_INCLUDE = {"0001", "0009"}


def build(xml_path: str, db_path: str = "drugs.db") -> None:
    print(f"Parsing {xml_path} ...")
    tree = ET.parse(xml_path)
    root = tree.getroot()
    vmps = root.find("VMPS")
    if vmps is None:
        raise ValueError("No <VMPS> element found — is this a dm+d VMP file?")

    con = sqlite3.connect(db_path)
    con.executescript("""
        DROP TABLE IF EXISTS drugs_fts;
        DROP TABLE IF EXISTS drugs;
        CREATE TABLE drugs (
            vpid     TEXT PRIMARY KEY,
            nm       TEXT NOT NULL,
            abbrevnm TEXT,
            vtmid    TEXT
        );
        CREATE VIRTUAL TABLE drugs_fts USING fts5(
            vpid     UNINDEXED,
            nm,
            abbrevnm,
            content  = drugs,
            content_rowid = rowid
        );
    """)

    rows = []
    skipped = 0
    for vmp in vmps:
        if vmp.findtext("PRES_STATCD", "") not in PRES_STAT_INCLUDE:
            skipped += 1
            continue
        rows.append((
            vmp.findtext("VPID", ""),
            vmp.findtext("NM", ""),
            vmp.findtext("ABBREVNM") or "",
            vmp.findtext("VTMID") or "",
        ))

    con.executemany("INSERT INTO drugs VALUES (?,?,?,?)", rows)
    con.execute("INSERT INTO drugs_fts SELECT vpid, nm, abbrevnm FROM drugs")
    con.commit()

    count = con.execute("SELECT COUNT(*) FROM drugs").fetchone()[0]
    size_mb = os.path.getsize(db_path) / 1024 / 1024
    print(f"Loaded {count} drugs  (skipped {skipped} non-prescribable)")
    print(f"DB: {db_path}  ({size_mb:.1f} MB)")
    con.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    xml_path = sys.argv[1]
    db_path = sys.argv[2] if len(sys.argv) > 2 else "drugs.db"
    build(xml_path, db_path)
