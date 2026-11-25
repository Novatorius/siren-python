"""Write src/siren/_event_samples.json, the sample ``data`` of each webhook
event that ``siren.testing.sample_event_data()`` and ``webhook_event()``
give, from Siren's own event reference.

    python scripts/siren_event_samples.py <siren checkout>            # rewrite it
    python scripts/siren_event_samples.py <siren checkout> --check    # exit 1 when it drifts

Siren documents each event's payload in docs/api/webhooks-events.md: a
``### <event>`` heading, then a ``| Field | Type | Description |`` table.
Each row becomes one field of the sample, with a value its type gives:

- ``integer``, ``integer or null``: 101, 102 and on for a field named
  ``...Id``, in the table's order, and 1 for any other.
- ``integer[]``: a list holding the next id.
- ``number``: 25.
- ``boolean``: true.
- ``string``, ``string or null``: the first quoted value the description
  shows in backticks (``"stripe"``), else the first other backticked word
  that is not a field of the table, ``null``, ``true`` or ``false``, else
  "example".
- ``array``: an empty list.

An event whose payload line reads "the same fields as ``<event>``" gets a
copy of that event's sample. The Node SDK's scripts/siren-event-samples.mjs
follows the same rules, so the two SDKs ship the same samples.
tests/test_dx_pass4.py runs ``--check`` against SIREN_CHECKOUT.
"""

from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

DOC = "docs/api/webhooks-events.md"
OUT = Path(__file__).resolve().parent.parent / "src" / "siren" / "_event_samples.json"
SOURCE = f"Siren's {DOC} (Novatorius/siren feat/apps at 6d2abd936a)"

HEADING = re.compile(r"^### ([a-z_]+\.[a-z_]+)\s*$")
TABLE = re.compile(r"^\|\s*Field\s*\|\s*Type\s*\|")
SAME = re.compile(r"^\*\*Payload:\*\* the same fields as `([a-z_]+\.[a-z_]+)`")
FIELD = re.compile(r"^`([A-Za-z0-9_]+)`$")


def event_samples(markdown: str) -> Dict[str, Dict[str, Any]]:
    samples: Dict[str, Dict[str, Any]] = {}
    same: List[Tuple[str, str]] = []
    lines = markdown.split("\n")
    for i, line in enumerate(lines):
        heading = HEADING.match(line)
        if heading is None:
            continue
        event = heading.group(1)
        j = i + 1
        while j < len(lines) and not lines[j].startswith(("### ", "## ")) and not TABLE.match(lines[j]):
            copied = SAME.match(lines[j])
            if copied is not None:
                same.append((event, copied.group(1)))
            j += 1
        if j >= len(lines) or not lines[j].startswith("|"):
            continue
        rows = []
        j += 2
        while j < len(lines) and lines[j].startswith("|"):
            cells = [cell.strip() for cell in lines[j].split("|")[1:-1]]
            name = FIELD.match(cells[0] if cells else "")
            if name is not None:
                rows.append((name.group(1), cells[1] if len(cells) > 1 else "", "|".join(cells[2:])))
            j += 1
        fields = {row[0] for row in rows}
        data: Dict[str, Any] = {}
        next_id = 101
        for name, kind, description in rows:
            base = re.sub(r" or null$", "", kind)
            if base == "integer":
                if name.endswith("Id"):
                    data[name], next_id = next_id, next_id + 1
                else:
                    data[name] = 1
            elif base == "integer[]":
                data[name], next_id = [next_id], next_id + 1
            elif base == "number":
                data[name] = 25
            elif base == "boolean":
                data[name] = True
            elif base == "array":
                data[name] = []
            elif base == "string":
                data[name] = string_sample(description, fields)
            else:
                raise SystemExit(f"{DOC}: {event}.{name} has a type this script does not know: {kind}")
        samples[event] = data
    for event, source in same:
        if source not in samples:
            raise SystemExit(f"{DOC}: {event} copies {source}, which has no table")
        samples[event] = copy.deepcopy(samples[source])
    if not samples:
        raise SystemExit(f"{DOC}: no events found")
    return samples


def string_sample(description: str, fields: Set[str]) -> str:
    ticked = re.findall(r"`([^`]+)`", description)
    for value in ticked:
        if re.fullmatch(r'"[^"]*"', value):
            return value[1:-1]
    for value in ticked:
        if re.fullmatch(r"[a-z0-9_.-]+", value, re.I) and value not in fields and value not in ("null", "true", "false"):
            return value
    return "example"


def render(samples: Dict[str, Dict[str, Any]]) -> str:
    return json.dumps({"source": SOURCE, "events": samples}, indent=2, ensure_ascii=False) + "\n"


def main(argv: List[str]) -> int:
    if not argv:
        print("usage: python scripts/siren_event_samples.py <siren checkout> [--check]", file=sys.stderr)
        return 2
    samples = event_samples((Path(argv[0]) / DOC).read_text(encoding="utf-8"))
    if argv[1:] == ["--check"]:
        shipped = json.loads(OUT.read_text(encoding="utf-8"))["events"]
        if shipped != samples:
            print(f"{OUT.name} differs from {DOC} in {argv[0]}. Run python scripts/siren_event_samples.py {argv[0]}", file=sys.stderr)
            return 1
        return 0
    OUT.write_text(render(samples), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
