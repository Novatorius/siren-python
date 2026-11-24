"""DX pass 2: the README's words for the mental model. The "How Siren talks
to your app" block is the same in the three SDKs' READMEs, word for word, so
this test keeps its own copy and compares."""

from __future__ import annotations

from pathlib import Path

import siren

README = (Path(__file__).resolve().parent.parent / "README.md").read_text()

MODEL_BLOCK = """## How Siren talks to your app

- **An app** is your own code that works with Siren. It holds a key, which it calls Siren with, and an address, where Siren sends it signed requests.
- **The signing secret** belongs to the address. Siren signs every request with it, and the handler checks each one.
- **A feature** is what the app adds to one of Siren's pickers. Its kind says what it does, such as `distributionResolver`, and its key names it in your app.
- **A tell** lets the app know something happened, such as an approved conversion. The handler runs your function and replies 200.
- **An ask** needs an answer, and the work it's about waits for it. The handler replies 200 at once, runs your function and posts what it returns.
- **A custom event** goes the other way: the app sends it to Siren.
"""


def section(heading: str) -> str:
    start = README.index(heading)
    rest = README[start + len(heading):]
    ends = [i for i in (rest.find("\n## "), rest.find("\n### ")) if i >= 0]
    return rest[: min(ends)] if ends else rest


def test_the_model_block_sits_after_the_tagline_and_before_what_you_can_do():
    assert MODEL_BLOCK in README
    tagline = README.index("Answer Siren Cloud from your own app with one request handler")
    assert tagline < README.index(MODEL_BLOCK) < README.index("## What you can do")


def test_the_reply_table_says_what_happens_when_your_function_fails():
    table = section("### What the handler replies")
    [row] = [line for line in table.splitlines() if line.startswith("| When your function fails")]
    assert "500" in row and "1, 2, 4 and 8 minutes" in row
    assert "answer window closes" in row and "60 minutes" in row and "resend" in row


def test_the_readme_says_an_ask_runs_more_than_once_beside_the_tells_advice():
    words = (
        "An ask's function can run more than once for one request too: on a repeat delivery, and again on each "
        "send after its answer window closes. Write once by `request.id` and return the same answer each time. "
        "Siren takes the same answer again as a duplicate and refuses a different one with 409."
    )
    assert words in " ".join(README.split())
    first_lines = section("## Answer Siren in a few lines")
    assert "more than once" in first_lines and "keyed by request.id" in first_lines


def test_the_readme_teaches_later_and_drops_none_as_answer_later():
    flat = " ".join(README.split())
    assert "answers.later()" in flat
    assert "function that returns `None` leaves its answer to this path" not in flat
    assert "An ask's function that returns nothing is an error" in flat


def test_the_readme_names_extra_kinds_and_the_refused_kinds():
    options = " ".join(section("### Options").split())
    assert "`extra_kinds`" in options
    register = " ".join(section("### Register functions").split())
    assert "Did you mean distributionResolver?" in register


def test_the_request_type_names_the_id_to_dedupe_on():
    doc = " ".join(siren.AppRequest.__doc__.split())
    assert "the same on every send" in doc and "write once by ``id``" in doc
    assert "``item[\"id\"]`` names the feature" in doc
    assert "changes on every send" in doc
