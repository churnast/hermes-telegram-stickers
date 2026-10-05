# Changelog

All notable changes to this project are written down here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] (2026-10-05)

First public release.

### Added

- `telegram_sticker_send`: sends one sticker from the owner's packs by a few words, an emoji or an exact id, as a reply to the message being answered and in the same forum topic.
- `telegram_sticker_find`: lists packs and emojis, finds stickers by words or emoji, with a short description of each.
- `telegram_sticker_mute`: lets the agent switch stickers off in a chat when someone asks.
- `/stickers` command: status, `sync`, `describe [n]`, `off` and `on`, `ban` and `unban`, `about`.
- Picking by meaning: reuses Hermes' own sticker descriptions and describes the rest with the configured vision model on request, animated and video stickers through their still preview.
- Pacing: about one sticker per 8 messages and a 20 second cooldown per chat, kept across restarts; the last five stickers sent in a chat are not repeated.
- Bundled `sticker-etiquette` skill.

[Unreleased]: https://github.com/churnast/hermes-telegram-stickers/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/churnast/hermes-telegram-stickers/releases/tag/v0.1.0
