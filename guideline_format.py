"""
Vision GP Clinical Guideline .htm parser and exporter.

Format: custom Vision directives in HTML-comment-like tags (<!...>).
Parse line by line, preserve raw TD content for exact round-trip reproduction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class GuidelineMetadata:
    title: str
    mnemonic: str
    guid: str          # the ID field
    type_: str = "PLT003:Local"
    trigger: str = ""
    sex_filter: str = "GEN003: Both Sexes"
    line_count: int = 0  # original line count - preserved for round-trip


@dataclass
class Cell:
    """
    A single <TD>...</TD> block.  The raw_lines list stores the lines between
    <TD> and </TD> exactly as they appeared in the source, enabling pixel-perfect
    round-trip output.  The parsed fields are populated for programmatic access.
    """
    raw_lines: List[str] = field(default_factory=list)

    # Parsed convenience fields (derived from raw_lines)
    indent: Optional[int] = None
    width: Optional[str] = None
    newline_after: bool = False
    cell_type: str = "empty"      # "text", "button", "image", "empty"
    # text cells
    text: str = ""
    font_raw: str = ""
    is_text_directive: bool = False
    # button cells
    command_raw: str = ""
    label: str = ""
    hotspot: bool = False
    # image cells
    img_src: str = ""
    img_height: int = 0
    img_width: int = 0
    img_href: str = ""
    img_href_label: str = ""


@dataclass
class MultimediaBlock:
    properties_raw: str
    tables: List[List[Cell]] = field(default_factory=list)


@dataclass
class PatientDataQuery:
    data_code: str
    filter_expr: str
    description: str
    properties_raw: str


@dataclass
class DrugEntry:
    label: str
    label_properties_raw: str
    drug_code: str
    drug_name: str
    drug_age_filter: str
    dosage: str
    qty: int
    daily: int
    days: int
    pack_size: str
    max_issues: int
    repeat_for: str
    min_days: int
    max_days: int
    authorise: str
    display_properties_raw: str


@dataclass
class SubGuidelineLink:
    href: str
    label: str
    properties_raw: str


@dataclass
class FilterBlock:
    filter_raw: str
    description: str
    properties_raw: str


@dataclass
class AdviceText:
    text: str
    properties_raw: str


@dataclass
class H2Block:
    title: str
    properties_raw: str
    h_level: int = 2


@dataclass
class SnapcardField:
    field_id: str
    label: str


@dataclass
class SnapcardRow:
    raw_line: str          # the original raw line (for exact reproduction)
    label: str
    display_type: str
    fields: List[SnapcardField]
    properties_raw: str


@dataclass
class DataRecord:
    content: str
    properties_raw: str


@dataclass
class DataEntryButton:
    """A clickable button that opens a Vision data-entry dialog (e.g. record a
    clinical event with associated comment).  Exported as a minimal single-cell
    multimedia block."""
    read_code: str
    label: str
    properties_raw: str = "3;;;40;;0;;;;;;;;;;;16777202;"
    width: str = "490"
    indent: int = 10
    dialog_id: str = "272"  # 272 = clinical event entry; 62 = recall


@dataclass
class _RegimeMarker:
    """Internal: <!Regime=...> between drug entries in a section."""
    name: str


@dataclass
class _SnapcardMarker:
    """Internal: <!Snapcard> at body level (no enclosing H1)."""
    pass


@dataclass
class Section:
    title: str
    h_level: int
    properties_raw: str
    is_snapcard: bool = False
    regime: Optional[str] = None
    items: List[Any] = field(default_factory=list)


@dataclass
class Guideline:
    metadata: GuidelineMetadata
    intro_block: Optional[MultimediaBlock] = None
    sections: List[Section] = field(default_factory=list)
    triggered_from_parent: bool = False
    guideline_type: str = "unknown"
    top_level_items: List[Any] = field(default_factory=list)
    # Whether the body opens with <body><BLOCKQUOTE> or just <body>
    has_blockquote: bool = True
    # The text inside <P>...</P> in the blockquote (may differ from mnemonic)
    blockquote_label: str = ""


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_DRUG_FILTER_RE = re.compile(r'^<!Drug from Regime=([^:]+):Filter=([^>]+)>$')
_DOSAGE_RE = re.compile(r'^<!Dosage=(.*)>$')
_QTY_RE = re.compile(
    r'^<!Qty=(\d+):Daily=(\d+):Days=(\d+):PackSize=([^:]*):Max Issues=(\d+)'
    r':Repeat For=([^:]*):MinDays=(\d+):MaxDays=(\d+):Authorise=([^>]*)>$'
)
_PROPERTIES_RE = re.compile(r'^<!Properties=(.*)>$')
_H1_RE = re.compile(r'^<H1>(.*)</H1>$')
_H2_RE = re.compile(r'^<H2>(.*)</H2>$')
_H3_RE = re.compile(r'^<H3>(.*)</H3>$')
_H4_RE = re.compile(r'^<H4>(.*)</H4>$')
_GUIDELINE_HREF_RE = re.compile(r'^<A HREF="([^"]+)">(.+)</A>$')
_REGIME_RE = re.compile(r'^<!Regime=(.+)>$')
_FILTER_RE = re.compile(r'^<!Filter=(.+)>$')
_IMG_RE = re.compile(r'<IMG SRC=(\S+)\s+HEIGHT=(\d+)\}\s+WIDTH=(\d+)>')
_A_HREF_RAW_RE = re.compile(r'^<A HREF=(.+?)>(.+)</A>$')
_CITE_RE = re.compile(r'^<CITE>(.*)</CITE>$')
_INDENT_RE = re.compile(r'^<!Indent=(\d*)>$')
_WIDTH_RE = re.compile(r'^<!Width=(\S+)>$')
_FONT_RE = re.compile(r'^<!Font=(.+)>$')
_COMMAND_RE = re.compile(r'^<!Command=(.+)>$')
_HOTSPOT_RE = re.compile(r'^<!Hotspot=(.+)>$')


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

class _BodyParser:
    def __init__(self, lines: List[str]):
        self.lines = lines
        self.pos = 0

    def peek(self, offset: int = 0) -> Optional[str]:
        idx = self.pos + offset
        return self.lines[idx] if 0 <= idx < len(self.lines) else None

    def consume(self) -> str:
        line = self.lines[self.pos]
        self.pos += 1
        return line

    def at_end(self) -> bool:
        return self.pos >= len(self.lines)

    # ------------------------------------------------------------------
    def parse(self) -> Guideline:
        # Advance to <head>
        while not self.at_end():
            line = self.peek()
            if line == '<head>':
                self.consume()
                break
            self.consume()

        metadata = self._parse_head()

        # Advance to body
        while not self.at_end():
            if self.peek().startswith('<body'):
                break
            self.consume()

        triggered, intro_block, top_level, sections, has_blockquote, blockquote_label = self._parse_body()

        g = Guideline(
            metadata=metadata,
            intro_block=intro_block,
            sections=sections,
            triggered_from_parent=triggered,
            top_level_items=top_level,
            has_blockquote=has_blockquote,
            blockquote_label=blockquote_label,
        )
        g.guideline_type = detect_guideline_type(g)
        return g

    # ------------------------------------------------------------------
    def _parse_head(self) -> GuidelineMetadata:
        title = mnemonic = guid = ""
        type_ = "PLT003:Local"
        trigger = ""
        sex_filter = "GEN003: Both Sexes"
        line_count = 0
        while not self.at_end():
            line = self.consume()
            if line == '</head>':
                break
            if line.startswith('<TITLE>') and line.endswith('</TITLE>'):
                title = line[7:-8]
            elif line.startswith('<!Line Count=') and line.endswith('>'):
                try:
                    line_count = int(line[13:-1])
                except ValueError:
                    pass
            elif line.startswith('<!Mnemonic=') and line.endswith('>'):
                mnemonic = line[11:-1]
            elif line.startswith('<!ID=') and line.endswith('>'):
                guid = line[5:-1]
            elif line.startswith('<!Type=') and line.endswith('>'):
                type_ = line[7:-1]
            elif line.startswith('<!Trigger='):
                trigger = line[10:-1]
            elif line.startswith('<!Sex Filter=') and line.endswith('>'):
                sex_filter = line[13:-1]
        return GuidelineMetadata(
            title=title, mnemonic=mnemonic, guid=guid,
            type_=type_, trigger=trigger, sex_filter=sex_filter,
            line_count=line_count,
        )

    # ------------------------------------------------------------------
    def _parse_body(self) -> tuple:
        triggered_from_parent = False
        intro_block: Optional[MultimediaBlock] = None
        top_level_items: List[Any] = []
        sections: List[Section] = []
        current_section: Optional[Section] = None
        pending_props: Optional[str] = None
        in_blockquote = False
        blockquote_label = ""
        has_blockquote = False
        # Track whether intro MM has been placed
        intro_done = False

        def add_item(item: Any):
            if current_section is not None:
                current_section.items.append(item)
            else:
                top_level_items.append(item)

        while not self.at_end():
            line = self.peek()

            if line in ('</body>', '</html>'):
                self.consume()
                break

            # Body opening tags
            if line == '<body><BLOCKQUOTE>':
                self.consume()
                in_blockquote = True
                has_blockquote = True
                continue

            if line == '<body>':
                self.consume()
                continue

            if line == '<BLOCKQUOTE>':
                self.consume()
                in_blockquote = True
                has_blockquote = True
                continue

            if line == '</BLOCKQUOTE>':
                self.consume()
                in_blockquote = False
                continue

            if line in ('<P>', '</P>'):
                if line == '</P>':
                    self.consume()
                    # May be followed by <!Guideline>
                    if not self.at_end() and self.peek() == '<!Guideline>':
                        self.consume()
                        if not self.at_end():
                            a_line = self.consume()
                            m = _GUIDELINE_HREF_RE.match(a_line)
                            if m:
                                href = m.group(1)
                                lbl = m.group(2)
                                props = pending_props or ""
                                pending_props = None
                                add_item(SubGuidelineLink(href=href, label=lbl, properties_raw=props))
                else:
                    self.consume()
                continue

            # Inside blockquote: mnemonic/label or triggered marker
            if in_blockquote:
                self.consume()
                s = line.strip()
                if '*** Guideline triggered from parent' in s or (s.startswith('***') and '***' in s):
                    triggered_from_parent = True
                elif s:
                    blockquote_label = s
                continue

            # Properties
            m = _PROPERTIES_RE.match(line)
            if m:
                pending_props = m.group(1)
                self.consume()
                continue

            # Snapcard
            if line == '<!Snapcard>':
                self.consume()
                # Emit as an item (either in current section or at top level)
                add_item(_SnapcardMarker())
                continue

            # Multimedia block
            if line == '<!Multimedia Line>':
                self.consume()
                props = pending_props or ""
                pending_props = None
                mb = self._parse_multimedia(props)
                if not sections and not top_level_items and intro_block is None and not intro_done:
                    intro_block = mb
                    intro_done = True
                else:
                    add_item(mb)
                continue

            # H1
            m = _H1_RE.match(line)
            if m:
                self.consume()
                intro_done = True
                title = m.group(1)
                props = pending_props or ""
                pending_props = None
                sec = Section(title=title, h_level=1, properties_raw=props)
                sections.append(sec)
                current_section = sec
                continue

            # H2
            m = _H2_RE.match(line)
            if m:
                self.consume()
                title = m.group(1)
                props = pending_props or ""
                pending_props = None
                add_item(H2Block(title=title, properties_raw=props, h_level=2))
                continue

            # H3
            m = _H3_RE.match(line)
            if m:
                self.consume()
                title = m.group(1)
                props = pending_props or ""
                pending_props = None
                add_item(H2Block(title=title, properties_raw=props, h_level=3))
                continue

            # H4
            m = _H4_RE.match(line)
            if m:
                self.consume()
                title = m.group(1)
                props = pending_props or ""
                pending_props = None
                add_item(H2Block(title=title, properties_raw=props, h_level=4))
                continue

            # Regime
            m = _REGIME_RE.match(line)
            if m:
                self.consume()
                regime_name = m.group(1)
                if current_section is not None and not current_section.items:
                    current_section.regime = regime_name
                else:
                    add_item(_RegimeMarker(name=regime_name))
                continue

            # Drug subsection label block:
            # TEXT_LINE <!Dummy> <BR> Filter : ... <BR> <!Drug from Regime=...>
            if self._is_drug_label_block():
                entry = self._parse_drug_block(pending_props)
                pending_props = None
                add_item(entry)
                continue

            # Drug from Regime without preceding label
            if line.startswith('<!Drug from Regime='):
                entry = self._parse_drug_entry_raw("", pending_props or "")
                pending_props = None
                add_item(entry)
                continue

            # Patient Data
            # Format: <!Patient Data=CODE'FILTER'> then <!Properties=...> then description text
            if line.startswith('<!Patient Data='):
                self.consume()
                data_code, filter_expr = self._parse_patient_data(line)
                desc_props = ""
                desc = ""
                # Properties comes first (display settings for this query)
                nxt = self.peek()
                if nxt is not None:
                    m2 = _PROPERTIES_RE.match(nxt)
                    if m2:
                        desc_props = m2.group(1)
                        self.consume()
                # Then the description text line
                nxt = self.peek()
                if nxt is not None and not nxt.startswith('<') and not nxt.startswith('<!'):
                    desc = self.consume()
                add_item(PatientDataQuery(
                    data_code=data_code,
                    filter_expr=filter_expr,
                    description=desc,
                    properties_raw=desc_props,
                ))
                pending_props = None
                continue

            # Filter block
            m = _FILTER_RE.match(line)
            if m:
                self.consume()
                filter_raw = m.group(1)
                filter_props = ""
                nxt = self.peek()
                if nxt is not None:
                    m2 = _PROPERTIES_RE.match(nxt)
                    if m2:
                        filter_props = m2.group(1)
                        self.consume()
                filter_desc = ""
                nxt = self.peek()
                if nxt is not None and not nxt.startswith('<') and not nxt.startswith('<!'):
                    filter_desc = self.consume()
                add_item(FilterBlock(
                    filter_raw=filter_raw,
                    description=filter_desc,
                    properties_raw=filter_props,
                ))
                pending_props = None
                continue

            # Advice
            if line == '<!Advice>':
                self.consume()
                adv_props = pending_props or ""
                pending_props = None
                # May be followed by Properties then text, or just text
                nxt = self.peek()
                if nxt is not None:
                    m2 = _PROPERTIES_RE.match(nxt)
                    if m2:
                        adv_props = m2.group(1)
                        self.consume()
                adv_text = ""
                nxt = self.peek()
                if nxt is not None and not nxt.startswith('<') and not nxt.startswith('<!'):
                    adv_text = self.consume()
                add_item(AdviceText(text=adv_text, properties_raw=adv_props))
                continue

            # DataRecord
            if line == '<!DataRecord>':
                self.consume()
                dr_props = ""
                nxt = self.peek()
                if nxt is not None:
                    m2 = _PROPERTIES_RE.match(nxt)
                    if m2:
                        dr_props = m2.group(1)
                        self.consume()
                dr_content = ""
                nxt = self.peek()
                if nxt is not None and not nxt.startswith('<') and not nxt.startswith('<!'):
                    dr_content = self.consume()
                add_item(DataRecord(content=dr_content, properties_raw=dr_props))
                pending_props = None
                continue

            # Snapcard data row: non-directive text with <!vision>&gt; and \t
            # (the > in <!vision>> is HTML-encoded as &gt; in the file)
            if (not line.startswith('<')
                    and not line.startswith('<!') and '<!vision>&gt;' in line and '\t' in line):
                self.consume()
                sr = _parse_snapcard_row(line, pending_props or "")
                pending_props = None
                add_item(sr)
                continue

            # Generic text line - skip (stray content)
            if not line.startswith('<') and not line.startswith('<!'):
                self.consume()
                continue

            # Anything else
            self.consume()

        return triggered_from_parent, intro_block, top_level_items, sections, has_blockquote, blockquote_label

    # ------------------------------------------------------------------
    def _is_drug_label_block(self) -> bool:
        """Look ahead: current = text, [+1]=<!Dummy>, [+2]=<BR>, [+3]=Filter:, [+4]=<BR>, [+5]=<!Drug..."""
        line = self.peek(0)
        if line is None or line.startswith('<') or line.startswith('<!'):
            return False
        return (self.peek(1) == '<!Dummy>' and
                self.peek(2) == '<BR>' and
                (self.peek(3) or '').startswith('Filter :') and
                self.peek(4) == '<BR>' and
                (self.peek(5) or '').startswith('<!Drug from Regime='))

    def _parse_drug_block(self, label_props: Optional[str]) -> DrugEntry:
        label_text = self.consume()       # text line
        self.consume()                     # <!Dummy>
        self.consume()                     # <BR>
        self.consume()                     # Filter : All ages
        self.consume()                     # <BR>
        return self._parse_drug_entry_raw(label_text, label_props or "")

    def _parse_drug_entry_raw(self, label: str, label_props_raw: str) -> DrugEntry:
        drug_line = self.consume()
        m = _DRUG_FILTER_RE.match(drug_line)
        drug_code = drug_age_filter = ""
        if m:
            drug_code = m.group(1)
            drug_age_filter = m.group(2)

        dosage = ""
        nxt = self.peek()
        if nxt is not None and nxt.startswith('<!Dosage='):
            m2 = _DOSAGE_RE.match(self.consume())
            if m2:
                dosage = m2.group(1)

        qty = daily = days = max_issues = min_days = max_days = 0
        pack_size = repeat_for = authorise = ""
        nxt = self.peek()
        if nxt is not None and nxt.startswith('<!Qty='):
            m3 = _QTY_RE.match(self.consume())
            if m3:
                qty = int(m3.group(1))
                daily = int(m3.group(2))
                days = int(m3.group(3))
                pack_size = m3.group(4)
                max_issues = int(m3.group(5))
                repeat_for = m3.group(6)
                min_days = int(m3.group(7))
                max_days = int(m3.group(8))
                authorise = m3.group(9)

        display_props = ""
        nxt = self.peek()
        if nxt is not None and nxt.startswith('<!Properties='):
            m4 = _PROPERTIES_RE.match(self.consume())
            if m4:
                display_props = m4.group(1)

        drug_name = ""
        nxt = self.peek()
        if nxt is not None and not nxt.startswith('<') and not nxt.startswith('<!'):
            display_line = self.consume()
            drug_name = _extract_drug_name(display_line, dosage)

        return DrugEntry(
            label=label,
            label_properties_raw=label_props_raw,
            drug_code=drug_code,
            drug_name=drug_name,
            drug_age_filter=drug_age_filter,
            dosage=dosage,
            qty=qty,
            daily=daily,
            days=days,
            pack_size=pack_size,
            max_issues=max_issues,
            repeat_for=repeat_for,
            min_days=min_days,
            max_days=max_days,
            authorise=authorise,
            display_properties_raw=display_props,
        )

    # ------------------------------------------------------------------
    def _parse_multimedia(self, properties_raw: str) -> MultimediaBlock:
        block = MultimediaBlock(properties_raw=properties_raw)
        current_table: List[Cell] = []

        while not self.at_end():
            line = self.peek()
            if line == '<!End Line>':
                self.consume()
                if current_table:
                    block.tables.append(current_table)
                break
            line = self.consume()
            if line == '<TABLE>':
                if current_table:
                    block.tables.append(current_table)
                current_table = []
            elif line == '</TABLE>':
                pass
            elif line == '<TD>':
                cell = self._parse_td()
                current_table.append(cell)

        return block

    def _parse_td(self) -> Cell:
        """Parse <TD> content, storing raw lines for verbatim export."""
        raw_lines: List[str] = []
        cell = Cell(raw_lines=raw_lines)

        while not self.at_end():
            line = self.peek()
            if line == '</TD>':
                self.consume()
                break
            line = self.consume()
            raw_lines.append(line)

            # Also populate parsed fields for programmatic access
            m = _INDENT_RE.match(line)
            if m:
                val = m.group(1)
                cell.indent = int(val) if val else None
                continue

            m = _WIDTH_RE.match(line)
            if m:
                cell.width = m.group(1)
                continue

            if line == '<!Newline After>':
                cell.newline_after = True
                continue

            if line == '<!Text>':
                cell.is_text_directive = True
                if cell.cell_type == "empty":
                    cell.cell_type = "text"
                continue

            if line == '<!Bitmap>':
                cell.cell_type = "image"
                continue

            m = _FONT_RE.match(line)
            if m:
                cell.font_raw = m.group(1)
                if cell.cell_type == "empty":
                    cell.cell_type = "text"
                continue

            m = _COMMAND_RE.match(line)
            if m:
                cell.command_raw = m.group(1)
                cell.cell_type = "button"
                continue

            m = _HOTSPOT_RE.match(line)
            if m:
                cell.command_raw = m.group(1)
                cell.cell_type = "button"
                cell.hotspot = True
                continue

            m = _CITE_RE.match(line)
            if m:
                cell.label = m.group(1)
                continue

            img = _IMG_RE.search(line)
            if img:
                cell.img_src = img.group(1)
                cell.img_height = int(img.group(2))
                cell.img_width = int(img.group(3))
                cell.cell_type = "image"
                continue

            m = _A_HREF_RAW_RE.match(line)
            if m:
                cell.img_href = m.group(1)
                cell.img_href_label = m.group(2)
                continue

            if not line.startswith('<') and not line.startswith('<!'):
                if cell.cell_type in ("empty", "text"):
                    cell.cell_type = "text"
                    cell.text = line

        return cell

    def _parse_patient_data(self, line: str) -> tuple:
        inner = line[15:-1]  # strip <!Patient Data= and >
        if "'" in inner:
            idx = inner.index("'")
            code = inner[:idx]
            rest = inner[idx+1:]
            if rest.endswith("'"):
                rest = rest[:-1]
            return code, rest
        return inner, ""


# ---------------------------------------------------------------------------
# Snapcard row helpers
# ---------------------------------------------------------------------------

def _parse_snapcard_row(line: str, properties_raw: str) -> SnapcardRow:
    """Parse a snapcard data row line.
    Pattern: LABEL<!vision>&gt;DISPLAY_TYPE\tFIELD_ID+SEP+LABEL\t...
    (the > in the original Vision format is &gt; in the HTML file)
    """
    marker = '<!vision>&gt;'
    vis_idx = line.index(marker)
    row_label = line[:vis_idx]
    rest = line[vis_idx + len(marker):]  # skip <!vision>&gt;
    tab_idx = rest.index('\t') if '\t' in rest else len(rest)
    display_type = rest[:tab_idx]
    fields_str = rest[tab_idx+1:] if '\t' in rest else ""

    fields: List[SnapcardField] = []
    for chunk in fields_str.split('\t'):
        chunk_stripped = chunk.rstrip()
        if not chunk_stripped:
            continue
        # Field format: DIGITS+SEP+LABEL where SEP is first alpha after digits/?/@
        i = 0
        while i < len(chunk_stripped) and (chunk_stripped[i].isdigit() or chunk_stripped[i] in '?@'):
            i += 1
        if i < len(chunk_stripped):
            fid_sep = chunk_stripped[:i+1]  # includes separator char
            flabel = chunk_stripped[i+1:]
            fields.append(SnapcardField(field_id=fid_sep, label=flabel))

    return SnapcardRow(
        raw_line=line,
        label=row_label,
        display_type=display_type,
        fields=fields,
        properties_raw=properties_raw,
    )


# ---------------------------------------------------------------------------
# Drug name extraction
# ---------------------------------------------------------------------------

def _extract_drug_name(display_line: str, dosage: str) -> str:
    if '  Supply:  ' in display_line:
        before = display_line[:display_line.index('  Supply:  ')]
        if dosage and before.endswith(dosage):
            return before[:-len(dosage)].rstrip()
        return before
    if dosage and dosage in display_line:
        idx = display_line.index(dosage)
        return display_line[:idx].rstrip()
    return display_line.rstrip()


# ---------------------------------------------------------------------------
# Top-level parse entry point
# ---------------------------------------------------------------------------

def parse_guideline(text: str) -> Guideline:
    """Parse .htm file text → Guideline."""
    raw_lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    # Remove trailing empty line that split() leaves if text ends with \n
    parser = _BodyParser(raw_lines)
    g = parser.parse()
    g.guideline_type = detect_guideline_type(g)
    return g


# ---------------------------------------------------------------------------
# Exporter
# ---------------------------------------------------------------------------

def _build_drug_display_line(entry: DrugEntry) -> str:
    if entry.days > 0:
        return (f"{entry.drug_name} {entry.dosage}  Supply:  "
                f"{entry.qty} {entry.pack_size}  days:  {entry.days}   ")
    else:
        return (f"{entry.drug_name} {entry.dosage}  Supply:  "
                f"{entry.qty} {entry.pack_size}   ")


def _emit_cell(cell: Cell, lines: List[str]) -> None:
    """Emit a <TD>...</TD> block using raw_lines for exact reproduction."""
    lines.append('<TD>')
    lines.extend(cell.raw_lines)
    lines.append('</TD>')


def _emit_multimedia(mb: MultimediaBlock, lines: List[str]) -> None:
    lines.append(f'<!Properties={mb.properties_raw}>')
    lines.append('<!Multimedia Line>')
    for table in mb.tables:
        lines.append('<TABLE>')
        for cell in table:
            _emit_cell(cell, lines)
        lines.append('</TABLE>')
    lines.append('<!End Line>')


def _emit_item(item: Any, lines: List[str]) -> None:
    if isinstance(item, MultimediaBlock):
        _emit_multimedia(item, lines)

    elif isinstance(item, PatientDataQuery):
        lines.append(f"<!Patient Data={item.data_code}'{item.filter_expr}'>")
        if item.properties_raw:
            lines.append(f'<!Properties={item.properties_raw}>')
        if item.description:
            lines.append(item.description)

    elif isinstance(item, FilterBlock):
        lines.append(f'<!Filter={item.filter_raw}>')
        lines.append(f'<!Properties={item.properties_raw}>')
        if item.description:
            lines.append(item.description)

    elif isinstance(item, AdviceText):
        lines.append('<!Advice>')
        lines.append(f'<!Properties={item.properties_raw}>')
        lines.append(item.text)

    elif isinstance(item, SubGuidelineLink):
        lines.append(f'<!Properties={item.properties_raw}>')
        lines.append('</P>')
        lines.append('<!Guideline>')
        lines.append(f'<A HREF="{item.href}">{item.label}</A>')

    elif isinstance(item, H2Block):
        lines.append(f'<!Properties={item.properties_raw}>')
        lines.append(f'<H{item.h_level}>{item.title}</H{item.h_level}>')

    elif isinstance(item, SnapcardRow):
        lines.append(f'<!Properties={item.properties_raw}>')
        lines.append(item.raw_line)

    elif isinstance(item, DrugEntry):
        _emit_drug_entry(item, lines)

    elif isinstance(item, DataEntryButton):
        _emit_data_entry_button(item, lines)

    elif isinstance(item, DataRecord):
        lines.append('<!DataRecord>')
        lines.append(f'<!Properties={item.properties_raw}>')
        lines.append(item.content)

    elif isinstance(item, _RegimeMarker):
        lines.append(f'<!Regime={item.name}>')

    elif isinstance(item, _SnapcardMarker):
        lines.append('<!Snapcard>')


def _emit_data_entry_button(btn: DataEntryButton, lines: List[str]) -> None:
    label_escaped = btn.label.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    lines.append(f'<!Properties={btn.properties_raw}>')
    lines.append('<!Multimedia Line>')
    lines.append('<TABLE>')
    lines.append('<TD>')
    lines.append(f'<!Indent={btn.indent}>')
    lines.append(f'<!Width={btn.width}>')
    lines.append(f'<!Command=DialogAdd:#{btn.dialog_id}\\{btn.read_code}>')
    lines.append(f'<CITE>&lt;{label_escaped}&gt;</CITE>')
    lines.append('</TD>')
    lines.append('</TABLE>')
    lines.append('<!End Line>')


def _emit_drug_entry(entry: DrugEntry, lines: List[str]) -> None:
    lines.append(f'<!Properties={entry.label_properties_raw}>')
    lines.append(entry.label)
    lines.append('<!Dummy>')
    lines.append('<BR>')
    lines.append('Filter : All ages')
    lines.append('<BR>')
    lines.append(f'<!Drug from Regime={entry.drug_code}:Filter={entry.drug_age_filter}>')
    lines.append(f'<!Dosage={entry.dosage}>')
    lines.append(
        f'<!Qty={entry.qty}:Daily={entry.daily}:Days={entry.days}'
        f':PackSize={entry.pack_size}:Max Issues={entry.max_issues}'
        f':Repeat For={entry.repeat_for}:MinDays={entry.min_days}'
        f':MaxDays={entry.max_days}:Authorise={entry.authorise}>'
    )
    lines.append(f'<!Properties={entry.display_properties_raw}>')
    lines.append(_build_drug_display_line(entry))


def export_guideline(g: Guideline) -> str:
    """Guideline → .htm file text."""
    body_lines: List[str] = []

    # Body open
    if g.has_blockquote:
        body_lines.append('<body><BLOCKQUOTE>')
        body_lines.append('<P>')
        if g.triggered_from_parent:
            body_lines.append('*** Guideline triggered from parent  ***')
        elif g.blockquote_label:
            body_lines.append(g.blockquote_label)
        else:
            body_lines.append(g.metadata.mnemonic)
        body_lines.append('</P>')
        body_lines.append('</BLOCKQUOTE>')
    else:
        body_lines.append('<body>')

    # Intro block
    if g.intro_block is not None:
        _emit_multimedia(g.intro_block, body_lines)

    # Top-level items
    for item in g.top_level_items:
        _emit_item(item, body_lines)

    # Sections
    for sec in g.sections:
        body_lines.append(f'<!Properties={sec.properties_raw}>')
        body_lines.append(f'<H{sec.h_level}>{sec.title}</H{sec.h_level}>')
        if sec.regime is not None:
            body_lines.append(f'<!Regime={sec.regime}>')
        for item in sec.items:
            _emit_item(item, body_lines)

    body_lines.append('</body>')
    body_lines.append('</html>')

    # Build head – preserve original line_count
    head_lines: List[str] = [
        '<html>',
        '<head>',
        f'<TITLE>{g.metadata.title}</TITLE>',
        f'<!Line Count={g.metadata.line_count}>',
        f'<!Mnemonic={g.metadata.mnemonic}>',
        f'<!ID={g.metadata.guid}>',
        f'<!Type={g.metadata.type_}>',
        f'<!Trigger={g.metadata.trigger}>',
        f'<!Sex Filter={g.metadata.sex_filter}>',
        '</head>',
    ]

    all_lines = head_lines + body_lines
    return '\n'.join(all_lines)


# ---------------------------------------------------------------------------
# Manifest exporter
# ---------------------------------------------------------------------------

def export_manifest(g: Guideline, htm_filename: str) -> str:
    """Generate the .txt manifest file content."""
    lines = [
        'VG21',
        '**********************************************************************',
        '* Guidelines v2.1   Copyright \xa9 1994-2009 In Practice Systems Ltd. *',
        '*                                                                    *',
        '* Generated import definition file   DO NOT EDIT THIS FILE           *',
        '**********************************************************************',
        '',
        g.metadata.mnemonic,
        '',
        'The following files were created as a result of the export process.',
        'All files (including this one) should be transferred to the target system if this guideline is to function correctly.',
        '',
        f'H:\\{htm_filename}\t{g.metadata.title} * MAIN GUIDELINE DEFINITION',
        '',
    ]
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Type detection
# ---------------------------------------------------------------------------

def detect_guideline_type(g: Guideline) -> str:
    """Detect: prescribing, data_entry, index, results, qof."""

    def _all_items():
        yield from g.top_level_items
        for sec in g.sections:
            yield from sec.items

    has_regime = any(isinstance(i, (DrugEntry, _RegimeMarker)) for i in _all_items())
    if not has_regime:
        has_regime = any(s.regime for s in g.sections)

    has_snapcard_row = any(isinstance(i, SnapcardRow) for i in _all_items())

    non_link_top = [i for i in g.top_level_items if not isinstance(i, SubGuidelineLink)]
    all_links_top = bool(g.top_level_items) and not non_link_top and not g.sections

    has_qof_h2 = False
    for item in _all_items():
        if isinstance(item, H2Block):
            t = item.title.lower()
            if any(k in t for k in ['qof', 'quality indicator', 'dm0', 'chd0', 'hf0']):
                has_qof_h2 = True
                break

    if has_regime:
        return "prescribing"
    if has_snapcard_row:
        return "results"
    if all_links_top:
        return "index"
    if has_qof_h2:
        return "qof"
    return "data_entry"


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def load_guideline(path: Path) -> Guideline:
    text = path.read_text(encoding='latin-1', errors='replace')
    return parse_guideline(text)


def save_guideline(g: Guideline, htm_path: Path, write_manifest: bool = True):
    content = export_guideline(g)
    htm_path.write_text(content, encoding='latin-1')
    if write_manifest:
        manifest_path = htm_path.with_suffix('.txt')
        manifest_content = export_manifest(g, htm_path.name)
        manifest_path.write_text(manifest_content, encoding='latin-1')
