# Security policy

## Supported versions

Only the latest release gets fixes.

## Reporting a vulnerability

Please do not open a public issue for a security problem. Report it privately instead: the
"Report a vulnerability" button on the [Security tab](https://github.com/churnast/hermes-telegram-stickers/security/advisories/new) of this
repository. We will reply as soon as we can.

Helpful details: Hermes Agent version, plugin version, steps to reproduce and what an attacker could
achieve.

## In scope

Exposure of the bot token, stickers sent to a chat other than the current one without `allow_other_chats`, and data sent anywhere except the Telegram Bot API and the vision provider configured in Hermes.
