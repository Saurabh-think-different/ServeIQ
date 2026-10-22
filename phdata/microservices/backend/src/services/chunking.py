"""Markdown-aware section parents and bounded, indivisible semantic children."""

import hashlib
import re
from dataclasses import dataclass, field
from functools import lru_cache
from itertools import pairwise
from typing import Any

import tiktoken

CHUNKER_VERSION = "markdown-hierarchy-v2"
CHILD_TARGET = 350
CHILD_MIN = 250
CHILD_SOFT_MAX = 450
CHILD_MAX = 700
PARENT_MAX = 1600
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_QUESTION = re.compile(r"^\s*\*\*Q\s*:", re.IGNORECASE)
_NUMBERED = re.compile(r"^\d+[.)]\s+")
_WARNING = re.compile(
    r"warning|caution|prerequisite|precondition|safety|do not|never|must not|important|escalat",
    re.IGNORECASE,
)


@lru_cache(maxsize=8)
def _encoding(model: str) -> tiktoken.Encoding:
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str, model: str = "text-embedding-3-small") -> int:
    return len(_encoding(model).encode(text, disallowed_special=()))


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class ChunkDraft:
    content: str
    heading: list[str]
    line_start: int
    line_end: int
    token_count: int
    warnings: list[dict[str, Any]] = field(default_factory=list)
    children: list["ChunkDraft"] = field(default_factory=list)


@dataclass
class ChunkedDocument:
    title: str
    metadata: dict[str, Any]
    parents: list[ChunkDraft]


@dataclass
class _Unit:
    start: int
    end: int
    heading: list[str]
    kind: str
    table_header_start: int | None = None
    table_header_end: int | None = None


def _headings(lines: list[str]) -> dict[int, tuple[int, str]]:
    headings = {}
    fence = None
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        marker = re.match(r"(`{3,}|~{3,})", stripped)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            continue
        if fence is None and (match := _HEADING.match(line.rstrip())):
            headings[index] = (len(match.group(1)), match.group(2).strip())
    return headings


def _units(
    lines: list[str],
    start: int,
    end: int,
    headings: dict[int, tuple[int, str]],
    base_heading: list[str],
) -> list[_Unit]:
    units = []
    heading = list(base_heading)
    index = start
    while index < end:
        if not lines[index].strip():
            index += 1
            continue
        begin = index
        if index in headings:
            level, name = headings[index]
            heading = heading[: level - 1] + [name]
            units.append(_Unit(index, index + 1, list(heading), "heading"))
            index += 1
            continue
        line = lines[index]
        if _QUESTION.match(line):
            kind = "qa"
        elif _NUMBERED.match(line):
            kind = "step"
        elif re.match(r"^\s*(`{3,}|~{3,})", line):
            kind = "code"
        elif line.lstrip().startswith("|"):
            kind = "table"
        else:
            kind = "paragraph"
        index += 1
        fence = None
        # A step owns its nested lists, paragraphs, and fenced commands; Q/A owns the answer.
        first_marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if first_marker:
            fence = first_marker.group(1)
        while index < end:
            current = lines[index]
            marker = re.match(r"^\s*(`{3,}|~{3,})", current)
            if fence is not None:
                index += 1
                if (
                    marker
                    and marker.group(1)[0] == fence[0]
                    and (len(marker.group(1)) >= len(fence))
                ):
                    fence = None
                    if kind == "code":
                        break
                continue
            if index in headings or _QUESTION.match(current) or _NUMBERED.match(current):
                break
            if kind == "table" and not current.lstrip().startswith("|"):
                break
            if kind == "paragraph" and (
                not current.strip() or marker or current.lstrip().startswith("|")
            ):
                break
            if kind == "qa" and re.match(r"^\s*(?:---+|___+|\*\*\*+)\s*$", current):
                break
            if marker:
                fence = marker.group(1)
            index += 1
        while index > begin + 1 and not lines[index - 1].strip():
            index -= 1
        units.append(_Unit(begin, index, list(heading), kind))
    return units


def _common_heading(units: list[_Unit]) -> list[str]:
    heading = list(units[0].heading)
    for unit in units[1:]:
        heading = heading[
            : next(
                (i for i, pair in enumerate(zip(heading, unit.heading)) if pair[0] != pair[1]),
                min(len(heading), len(unit.heading)),
            )
        ]
    return heading


def _render(units: list[_Unit], lines: list[str]) -> str:
    start, end = units[0].start, units[-1].end
    first = units[0]
    prefix = ""
    if first.table_header_start is not None and first.table_header_start < start:
        prefix = "".join(lines[first.table_header_start : first.table_header_end])
    return prefix + "".join(lines[start:end])


def _budget(units: list[_Unit], lines: list[str], model: str) -> int:
    return count_tokens(" > ".join(_common_heading(units)) + "\n\n" + _render(units, lines), model)


def _split_tables(
    units: list[_Unit],
    lines: list[str],
    source: str,
    model: str,
) -> list[_Unit]:
    result = []
    for unit in units:
        if unit.kind != "table" or _budget([unit], lines, model) <= CHILD_MAX:
            result.append(unit)
            continue
        separator = lines[unit.start + 1].strip() if unit.end > unit.start + 1 else ""
        cells = separator.strip("|").split("|")
        if not cells or not all(re.fullmatch(r"\s*:?-{3,}:?\s*", cell) for cell in cells):
            raise ValueError(
                f"{source}: line {unit.start + 1}: oversized table needs a header separator"
            )
        header_end = unit.start + 2
        if header_end == unit.end:
            raise ValueError(
                f"{source}: line {unit.start + 1}: table header exceeds child token limit"
            )
        for row in range(header_end, unit.end):
            fragment = _Unit(row, row + 1, unit.heading, "table_row", unit.start, header_end)
            if _budget([fragment], lines, model) > CHILD_MAX:
                raise ValueError(
                    f"{source}: line {row + 1}: indivisible table row with header and breadcrumb "
                    f"exceeds {CHILD_MAX} tokens; split this source row semantically"
                )
            result.append(fragment)
    return result


def _pack(units: list[_Unit], lines: list[str], model: str, maximum: int) -> list[list[_Unit]]:
    groups: list[list[_Unit]] = []
    current: list[_Unit] = []
    for unit in units:
        size = _budget([*current, unit], lines, model)
        heading_boundary = bool(current and current[-1].heading != unit.heading)
        current_size = _budget(current, lines, model) if current else 0
        if current and (
            size > maximum
            or (
                not all(item.kind == "heading" for item in current)
                and (
                    heading_boundary
                    or (
                        maximum == CHILD_MAX
                        and current_size >= CHILD_MIN
                        and (size > CHILD_SOFT_MAX or current_size >= CHILD_TARGET)
                    )
                )
            )
        ):
            groups.append(current)
            current = []
        current.append(unit)
    if current:
        groups.append(current)
    return groups


def _draft(units: list[_Unit], lines: list[str], model: str) -> ChunkDraft:
    return ChunkDraft(
        _render(units, lines),
        _common_heading(units),
        units[0].start + 1,
        units[-1].end,
        _budget(units, lines, model),
    )


def chunk_markdown(
    source: str,
    text: str,
    model: str = "text-embedding-3-small",
) -> ChunkedDocument:
    """Preserve semantic units and source row citations within contextual token budgets.

    Oversized tables split only between rows, repeating the original header/separator.
    Fragment line ranges cite original data rows; metadata separately cites headers.
    Budgets include breadcrumbs and repeated headers. Oversized individual rows,
    Q/A pairs, steps, and fenced code remain indivisible and are rejected explicitly.
    """
    if not text.strip():
        raise ValueError("Knowledge document is empty")
    lines = text.splitlines(keepends=True)
    headings = _headings(lines)
    title = next((name for level, name in headings.values() if level == 1), source)
    section_starts = [index for index, (level, _) in headings.items() if level <= 2]
    boundaries = sorted({0, *section_starts, len(lines)})
    sections: list[list[_Unit]] = []
    for start, end in pairwise(boundaries):
        if units := _units(lines, start, end, headings, [title]):
            sections.append(units)
    # Dedicated document-level safety/precondition sections apply to every procedure.
    global_warnings: list[dict[str, Any]] = []
    for units in sections:
        if _WARNING.search(" > ".join(units[0].heading)):
            global_warnings.append(
                {
                    "text": "".join(lines[units[0].start : units[-1].end]),
                    "line_start": units[0].start + 1,
                    "line_end": units[-1].end,
                    "source": source,
                }
            )
    table_headers = [
        {
            "source": source,
            "line_start": unit.start + 1,
            "line_end": unit.start + 2,
            "text": "".join(lines[unit.start : unit.start + 2]),
            "citation": f"{source}#L{unit.start + 1}-L{unit.start + 2}",
        }
        for section in sections
        for unit in section
        if unit.kind == "table" and unit.end >= unit.start + 2
    ]
    parents = []
    for original_units in sections:
        units = _split_tables(original_units, lines, source, model)
        section_warnings = list(global_warnings)
        for unit in units:
            value = "".join(lines[unit.start : unit.end])
            if unit.kind != "heading" and _WARNING.search(value):
                warning = {
                    "text": value,
                    "line_start": unit.start + 1,
                    "line_end": unit.end,
                    "source": source,
                }
                if warning not in section_warnings:
                    section_warnings.append(warning)
            if _budget([unit], lines, model) > CHILD_MAX:
                raise ValueError(
                    f"{source}: lines {unit.start + 1}-{unit.end}: indivisible "
                    f"{unit.kind} exceeds {CHILD_MAX} tokens; split the source semantically"
                )
        text_size = _budget(units, lines, model)
        parent_groups = (
            [units] if text_size <= PARENT_MAX else _pack(units, lines, model, PARENT_MAX)
        )
        for parent_units in parent_groups:
            parent = _draft(parent_units, lines, model)
            parent.warnings = section_warnings
            for child_units in _pack(parent_units, lines, model, CHILD_MAX):
                child = _draft(child_units, lines, model)
                if child.token_count > CHILD_MAX:
                    raise ValueError(
                        f"{source}: heading and semantic unit exceed child token limit"
                    )
                child.warnings = section_warnings
                parent.children.append(child)
            parents.append(parent)
    return ChunkedDocument(
        title,
        {
            "source": source,
            "line_count": len(lines),
            "chunker_version": CHUNKER_VERSION,
            "warnings": global_warnings,
            "table_headers": table_headers,
            "token_budget_includes": ["breadcrumb", "content", "repeated_table_header"],
            "table_fragment_citations": "Original data-row lines; headers cited separately",
            "headings": [
                {"level": level, "title": name, "line": index + 1}
                for index, (level, name) in headings.items()
            ],
        },
        parents,
    )


def embedding_text(chunk: ChunkDraft | Any) -> str:
    """Embed the breadcrumb and relevant preconditions without altering source text."""
    return " > ".join(chunk.heading) + "\n\n" + chunk.content
