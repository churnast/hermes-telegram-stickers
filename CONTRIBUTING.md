# Contributing

Thanks for taking the time to help. Issues and pull requests are welcome.

## Before you start

- For a bug, open an issue with your Hermes version (`hermes --version`), what you did, what you
  expected and what happened instead. Remove tokens, chat ids and personal data from any log.
- For a new feature, open an issue first, so we can agree on the shape before you write code.

## Development setup

```bash
git clone https://github.com/churnast/hermes-telegram-stickers.git
cd hermes-telegram-stickers
python -m pip install pytest ruff
python -m pytest -c tests/pytest.ini --rootdir tests tests
ruff check .
```

To check the plugin the way the Hermes catalog does, run this in a Hermes Agent 0.21.5+ environment:

```bash
hermes plugins validate .
hermes plugins doctor . --ci
```

## Pull requests

- Keep each pull request to one change, with tests for it.
- Tests stay offline: no real Telegram calls and no real model calls (see the fake Bot API in `tests/`).
- Add a line to `CHANGELOG.md` under "Unreleased".
- Any new network call, file, hook or credential goes into the README section "Privacy and safety".
  The Hermes plugin catalog requires that disclosure.

## Releases

The maintainer bumps `version` in `plugin.yaml`, moves the changelog lines under the new version,
tags `vX.Y.Z` and publishes a GitHub release. The catalog entry in `NousResearch/hermes-agent` is then
re-pinned to the new commit.
