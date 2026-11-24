"""Write FeatureKind's members in src/siren/types.py from the kind snapshot.

The SDK ships Siren's docs/api/app-kinds.json as src/siren/app_kinds.json,
byte for byte. Each member gets a docstring with the kind's name on Siren
Cloud's screens, Siren's own term where it differs, and the description, so
an editor shows them on hover. tests/test_app_kinds_snapshot.py fails when
the members drift from the snapshot.

    python scripts/feature_kinds.py          # rewrite the members
    python scripts/feature_kinds.py --check  # exit 1 when they drift
"""

from __future__ import annotations

import json
import re
import sys
import textwrap
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "src" / "siren" / "app_kinds.json"
TYPES = ROOT / "src" / "siren" / "types.py"
BEGIN = "    # Generated from app_kinds.json by scripts/feature_kinds.py. Do not edit.\n"
END = "    # End of the generated members.\n"


def member_name(kind: str) -> str:
    """``incentiveResolver`` as ``INCENTIVE_RESOLVER``."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", kind).upper()


def docstring(entry: Dict[str, Any]) -> str:
    words = f"{entry['name']}."
    if entry["term"].lower() != entry["name"].lower():
        words += f" Siren's own term is {entry['term']}."
    words += f" {entry['description']}"
    lines = textwrap.wrap(words, width=72)
    if len(lines) == 1:
        return f'    """{lines[0]}"""\n'
    body = "".join(f"    {line}\n" for line in lines[1:])
    return f'    """{lines[0]}\n{body}    """\n'


def generated_block(kinds: List[Dict[str, Any]]) -> str:
    members = "\n".join(f'    {member_name(entry["kind"])} = "{entry["kind"]}"\n{docstring(entry)}' for entry in kinds)
    return BEGIN + members + END


def main(argv: List[str]) -> int:
    kinds = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    source = TYPES.read_text(encoding="utf-8")
    start, end = source.index(BEGIN), source.index(END) + len(END)
    updated = source[:start] + generated_block(kinds) + source[end:]
    if "--check" in argv:
        if updated != source:
            print("FeatureKind's members differ from app_kinds.json. Run python scripts/feature_kinds.py.")
            return 1
        return 0
    TYPES.write_text(updated, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
