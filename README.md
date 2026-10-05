# Telegram Stickers for Hermes Agent

**Lets your Hermes agent answer with stickers from your own Telegram packs: picked by meaning, sent as a reply in the right topic, paced like a person.**

[![tests](https://img.shields.io/github/actions/workflow/status/churnast/hermes-telegram-stickers/ci.yml?branch=main&label=tests)](https://github.com/churnast/hermes-telegram-stickers/actions/workflows/ci.yml)
[![release](https://img.shields.io/github/v/release/churnast/hermes-telegram-stickers?display_name=tag&sort=semver)](https://github.com/churnast/hermes-telegram-stickers/releases)
[![license](https://img.shields.io/github/license/churnast/hermes-telegram-stickers)](LICENSE)
[![python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org)
[![Hermes Agent](https://img.shields.io/badge/Hermes_Agent-0.21.5%2B-3b82f6)](https://hermes-agent.nousresearch.com)

![Telegram Stickers: your agent replies with your own stickers, picked by meaning and paced like a person](docs/banner.png)

Hermes already understands the stickers people send it and can react with an emoji, but it cannot send a sticker back. This plugin adds that, the way a person does it: a sticker instead of "haha" or "nice try", as a reply to the message, in the same forum topic, and not five in a row.

## Install

```bash
hermes plugins install churnast/hermes-telegram-stickers --enable
```

Tell it which packs to use, favourite first, in `~/.hermes/config.yaml`, then restart the gateway:

```yaml
plugins:
  entries:
    telegram-stickers:
      settings:
        packs:
          - MyFavouritePack                       # the part after t.me/addstickers/
          - https://t.me/addstickers/AnotherPack  # or the whole link
```

Then, in your Telegram chat with the bot:

1. `/stickers` shows the packs it loaded.
2. `/stickers describe` lets your vision model describe up to 30 stickers per run, so the agent can pick by meaning. Run it again for the next batch.
3. Talk as usual. When a sticker fits better than words, the agent sends one.

Any public sticker set works; you do not need to own it.

## What it adds

| Kind | Name | What it does |
|---|---|---|
| Tool | `telegram_sticker_send` | Sends one sticker to the current chat: by a few words ("cat rolling eyes"), by emoji, or by exact id. Replies to the message being answered, in its topic. |
| Tool | `telegram_sticker_find` | Lists your packs and their emojis, or finds stickers by words or emoji, with a short description of each. |
| Tool | `telegram_sticker_mute` | Switches stickers off in the current chat as soon as someone asks. Only you can switch them back on, with `/stickers on <chat id>` in a direct chat with the bot. |
| Command | `/stickers` | Status (in a direct chat, with the chats where stickers are off), `sync`, `describe [n]`, `off` / `on` for this chat, `off` / `on <chat id>` for any chat from a direct chat, `ban` / `unban <id>`, `about <id> <text>`. |
| Skill | `telegram-stickers:sticker-etiquette` | When a sticker fits and when words are better. |

On Hermes 0.21 and newer, plugin tools may sit behind the tool-search bridge (`tool_search`, `tool_describe`, `tool_call`), so the agent does not see them until it looks. The bundled skill tells it to call `telegram_sticker_send` and `telegram_sticker_find` through `tool_call` directly, which saves a search and a schema lookup before each sticker.

## How it picks a sticker

A Telegram sticker carries one emoji and nothing else, so an emoji alone is a blunt tool: the same 😂 can be a laughing cat or a crying frog. The plugin keeps a one-sentence description of each sticker and matches the agent's words against it.

- **Free first.** Hermes already describes static stickers that people send to the bot and keeps those descriptions in `sticker_cache.json`. They are reused at no cost.
- **The rest on request.** `/stickers describe` sends each remaining sticker, once, to the vision model you configured in Hermes. Animated and video stickers are described from their still preview, which Hermes itself skips. A sticker that fails twice is not retried.
- **You have the last word.** `/stickers about pack:12 sleepy cat under a blanket` replaces a description, and `/stickers ban pack:12` takes a sticker out of use.
- **No repeats.** The last five stickers sent in a chat are skipped when another one fits.

## Pacing and manners

- **Current chat only** by default. Other chats need `allow_other_chats: true`.
- **About one sticker per 8 messages** in a chat (`min_messages_between`, counted by Telegram message numbers, so it is approximate) and never two within 20 seconds (`cooldown_seconds`). Both survive a restart.
- **An opt-out that sticks.** When someone asks the agent to stop, it can mute the chat itself; `/stickers off` does the same by hand. To switch a chat back on, open a direct chat with the bot: `/stickers` there lists the muted chats with their ids, and `/stickers on <chat id>` (for example `/stickers on -1001234567890`) switches one back on. This works even when commands typed in the group never reach the bot (for example in a group that needs a mention), and only in a direct chat, because anyone in a group may be able to run slash commands.
- **Reply anchoring that does not fail.** If the bot cannot see the message it is answering (privacy mode in a group), the sticker goes out without the reply instead of erroring.
- The forum **General topic** is handled: stickers go there without a thread id.

## Configuration

| Setting | Default | What it does |
|---|---|---|
| `packs` | `[]` | Sticker set short names or `t.me/addstickers/...` links, favourite first. |
| `min_messages_between` | `8` | Messages between two stickers in one chat. `0` turns pacing off. |
| `cooldown_seconds` | `20` | Seconds between two stickers in one chat. `0` turns it off. |
| `describe_batch` | `30` | Stickers per `/stickers describe` run, one vision call each. |
| `allow_other_chats` | `false` | Lets the agent pass a `chat_id` other than the current chat. |
| `api_base` | `https://api.telegram.org` | Change only for a self-hosted Bot API server. |

A complete example is in [`config.example.yaml`](config.example.yaml).

## Requirements

- Hermes Agent 0.21.5 or newer, with the Telegram gateway running.
- `TELEGRAM_BOT_TOKEN` in `~/.hermes/.env`: the same token the gateway uses.
- A vision model configured in Hermes, only for `/stickers describe`.
- No extra Python packages.

## Privacy and safety

- **Network.** Calls the Telegram Bot API (`api.telegram.org`, or your `api_base`) with the gateway's `TELEGRAM_BOT_TOKEN`: `getStickerSet` for each configured pack (at most once a week, on `/stickers sync`, or when the token changes), `sendSticker` when the agent sends one, and `getFile` plus a file download for each sticker that `/stickers describe` processes. No other hosts.
- **Vision model.** Used only by `/stickers describe`, which you start. Each sticker image, or its still preview, is saved to a temporary file, passed to Hermes' own vision call (and so to the vision provider you configured), then deleted. The vision model gets the image, never a link with the token in it.
- **Files** in `<HERMES_HOME>/plugin-data/telegram-stickers/`: `catalog.json` (pack titles, emojis, Telegram file ids), `descriptions.json` (one sentence per sticker), `state.json` (muted chats, banned stickers, the last stickers sent per chat) and `tmp/` (images being described, removed right away). Reads Hermes' `sticker_cache.json` to reuse its descriptions.
- **Credentials.** Reads `TELEGRAM_BOT_TOKEN` from the environment. Never writes or logs it, and never puts it in an error message.
- No hooks, no background processes, no shell commands, no telemetry.

## Troubleshooting

| You see | Do this |
|---|---|
| "No sticker packs are configured" | Add `packs` to the plugin settings and restart the gateway. |
| A pack shows "not loaded (STICKERSET_INVALID)" | Check the short name: it is the part after `t.me/addstickers/`. |
| "No sticker description fits" | Run `/stickers describe`, or let the agent use an emoji. |
| "the vision model gave no description" | Configure a vision model in Hermes (`hermes setup`), then run `/stickers describe` again. |
| The agent never sends stickers | Check that `/stickers` says they are on in this chat and that the plugin is enabled. |
| Stickers stay off in a group | Send `/stickers` to the bot in a direct chat to see the muted chats, then `/stickers on <chat id>` there. |
| The agent says it has no sticker tools | On Hermes 0.21 and newer they may sit behind `tool_search`. The agent can call `telegram_sticker_send` through `tool_call` without searching. |

## Update and remove

```bash
hermes plugins update telegram-stickers
hermes plugins disable telegram-stickers
hermes plugins remove telegram-stickers
```

## Development

```bash
python -m pip install pytest ruff
python -m pytest -c tests/pytest.ini --rootdir tests tests
ruff check .
hermes plugins validate .
```

The tests run offline against a fake Bot API. CI also validates the plugin against a real Hermes Agent 0.21.5. See [CONTRIBUTING.md](CONTRIBUTING.md) and [CHANGELOG.md](CHANGELOG.md).

## Related

- [Memory Shield](https://github.com/churnast/hermes-memory-shield): keeps your agent from rewriting or wiping what it remembers about you.

## License

[MIT](LICENSE) © 2026 churnast
