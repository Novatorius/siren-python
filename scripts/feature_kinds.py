"""Write the code that follows the kind snapshot.

The SDK ships Siren's docs/api/app-kinds.json as src/siren/app_kinds.json,
byte for byte. From it this writes:

- FeatureKind's members in src/siren/types.py. Each member gets a docstring
  in the one template the three SDKs share: the kind's name on Siren Cloud's
  screens, "Siren's code calls it the <term>." where the term differs, the
  description, and whether Siren asks or tells. An editor shows it on hover.
- One set of ``on()`` overloads per kind Siren sends in
  src/siren/app_requests.py, so a function's request and answer are typed by
  the kind. Each kind's types are named from the kind: ``<Kind>Question``
  and ``<Kind>Answer`` for an ask, ``<Kind>Tell`` for a tell.

tests/test_app_kinds_snapshot.py and tests/test_dx_pass3.py fail when the
code drifts from the snapshot.

    python scripts/feature_kinds.py          # rewrite the generated code
    python scripts/feature_kinds.py --check  # exit 1 when it drifts
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
HANDLER = ROOT / "src" / "siren" / "app_requests.py"
BEGIN = "    # Generated from app_kinds.json by scripts/feature_kinds.py. Do not edit.\n"
END = "    # End of the generated members.\n"
TYPEVARS_BEGIN = "# Generated function types, from app_kinds.json by scripts/feature_kinds.py. Do not edit.\n"
TYPEVARS_END = "# End of the generated function types.\n"
OVERLOADS_BEGIN = "    # Generated from app_kinds.json by scripts/feature_kinds.py. Do not edit.\n"
OVERLOADS_END = "    # End of the generated overloads.\n"


def member_name(kind: str) -> str:
    """``incentiveResolver`` as ``INCENTIVE_RESOLVER``."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", kind).upper()


def pascal(kind: str) -> str:
    """``incentiveResolver`` as ``IncentiveResolver``."""
    return kind[0].upper() + kind[1:]


#: The last sentence of a kind's hover text, by its mode.
MODE_SENTENCE = {
    "ask": "An ask: Siren waits for the app to answer.",
    "tell": "A tell: Siren lets the app know.",
}


def hover(entry: Dict[str, Any]) -> str:
    """The kind's hover text, the template the three SDKs share."""
    words = f"{entry['name']}."
    if entry["term"].lower() != entry["name"].lower():
        words += f" Siren's code calls it the {entry['term']}."
    words += f" {entry['description']}"
    if entry["mode"] in MODE_SENTENCE:
        words += f" {MODE_SENTENCE[entry['mode']]}"
    return words


def docstring(entry: Dict[str, Any]) -> str:
    words = hover(entry)
    lines = textwrap.wrap(words, width=72)
    if len(lines) == 1:
        return f'    """{lines[0]}"""\n'
    body = "".join(f"    {line}\n" for line in lines[1:])
    return f'    """{lines[0]}\n{body}    """\n'


def generated_block(kinds: List[Dict[str, Any]]) -> str:
    members = "\n".join(f'    {member_name(entry["kind"])} = "{entry["kind"]}"\n{docstring(entry)}' for entry in kinds)
    return BEGIN + members + END


def _sent(kinds: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [entry for entry in kinds if entry["mode"] in ("ask", "tell")]


def typevars_block(kinds: List[Dict[str, Any]]) -> str:
    """A TypeVar per kind for the decorator form, bound to the kind's
    function type, so ``@answers.on(kind)`` checks the function it gets."""
    lines = []
    for entry in _sent(kinds):
        name = pascal(entry["kind"])
        lines.append(f'_{name}Fn = TypeVar("_{name}Fn", bound={function_type(entry)})\n')
    return TYPEVARS_BEGIN + "".join(lines) + TYPEVARS_END


def function_type(entry: Dict[str, Any]) -> str:
    name = pascal(entry["kind"])
    if entry["mode"] == "ask":
        return f'"Callable[[AppAsk[{name}Question]], AppAskResult[{name}Answer]]"'
    return f'"Callable[[AppTell[{name}Tell]], object]"'


def overloads_block(kinds: List[Dict[str, Any]]) -> str:
    """Three overloads per kind: ``on(kind, key, fn)``, ``on(kind, fn)`` and
    the decorator ``on(kind, key=None)``. The kind is its text or its
    FeatureKind member."""
    parts = []
    for entry in _sent(kinds):
        name = pascal(entry["kind"])
        kind = f'Literal["{entry["kind"]}", FeatureKind.{member_name(entry["kind"])}]'
        fn = function_type(entry).strip('"')
        parts.append(
            "    @overload\n"
            f'    def on(self, kind: {kind}, key: str, fn: {fn}) -> "AppRequestHandler": ...\n'
            "\n"
            "    @overload\n"
            f'    def on(self, kind: {kind}, key: {fn}) -> "AppRequestHandler": ...\n'
            "\n"
            "    @overload\n"
            f"    def on(self, kind: {kind}, key: Optional[str] = None) -> Callable[[_{name}Fn], _{name}Fn]: ...\n"
            "\n"
        )
    return OVERLOADS_BEGIN + "".join(parts) + OVERLOADS_END


def _replace(source: str, begin: str, end: str, block: str) -> str:
    start, stop = source.index(begin), source.index(end) + len(end)
    return source[:start] + block + source[stop:]


def main(argv: List[str]) -> int:
    kinds = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    files = {
        TYPES: lambda source: _replace(source, BEGIN, END, generated_block(kinds)),
        HANDLER: lambda source: _replace(
            _replace(source, TYPEVARS_BEGIN, TYPEVARS_END, typevars_block(kinds)),
            OVERLOADS_BEGIN,
            OVERLOADS_END,
            overloads_block(kinds),
        ),
    }
    drifted = []
    for path, write in files.items():
        source = path.read_text(encoding="utf-8")
        updated = write(source)
        if updated != source:
            drifted.append(path.name)
            if "--check" not in argv:
                path.write_text(updated, encoding="utf-8")
    if "--check" in argv and drifted:
        print(f"{', '.join(drifted)} differ from app_kinds.json. Run python scripts/feature_kinds.py.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
