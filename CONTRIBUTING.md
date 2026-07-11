# Contributing to the Siren SDK for Python

Thanks for your interest in improving the Siren Python SDK. This guide covers
how to get set up and what we look for in a contribution.

## Development setup

Clone the repository and create a virtual environment:

```bash
git clone https://github.com/Novatorius/siren-python.git
cd siren-python

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -e ".[dev]"
```

This installs the SDK in editable mode along with the development dependencies
(`pytest`, `respx`, `build`).

## Running the tests

```bash
pytest
```

The suite is fully offline — HTTP is mocked with `respx`, so no network access
or live Siren account is required. Please keep it that way: add mocked tests
for new behavior rather than reaching for a real API.

## Making a change

1. Create a branch off `main`.
2. Write or update tests that cover your change. New functionality requires new
   tests; bug fixes should include a regression test.
3. Run `pytest` and make sure everything is green.
4. Keep the public surface typed — the package ships `py.typed`.
5. Update `README.md` and `CHANGELOG.md` (under `[Unreleased]`) when your change
   is user-facing.

## Pull requests

- Keep PRs focused on a single change and give them a clear description of what
  and why.
- Reference any related issue.
- CI runs the test suite across Python 3.9–3.13; it must pass before review.

By contributing, you agree that your contributions are licensed under the
project's [MIT License](./LICENSE) and that you will follow the
[Code of Conduct](./CODE_OF_CONDUCT.md).
