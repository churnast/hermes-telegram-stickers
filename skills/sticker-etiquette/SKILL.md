---
name: sticker-etiquette
description: When a Telegram sticker fits a conversation and when words are better.
---

# Sticker etiquette

A sticker is a person's quick reaction, not another way to deliver an answer. Nobody has to ask for
one: notice the moments yourself, the plugin keeps the pace.

On Telegram turns where a sticker is allowed now, the plugin adds a short hint starting with
"[telegram-stickers]" to the message. No hint usually means pacing or the chat's off switch says no sticker
now, unless the owner turned the hint off (`turn_hint: false`).

## When to use

- In place of a short emotional line: "haha", "oh no", "nice try", "well, that happened".
- On a joke, light or happy news, a playful jab, someone teasing you in a group.
- When you would otherwise answer with a single emoji ("🎉", "☕").
- After you already answered in words, when one more line would be filler.

## When not to

- Instead of a real answer to a real question.
- When someone is upset, grieving, anxious, or angry.
- In a serious work or money conversation.
- Twice in a row, or as an automatic reply to every incoming sticker.
- In a group where nobody addressed you.

## How

1. Pick the feeling first, then say it in a few English words: "cat rolling eyes", "thumbs up",
   "sleepy", "facepalm". `telegram_sticker_send` with those words picks the best match by
   description and by the emojis that usually go with that reaction.
2. Or pass one emoji (😏 smug, 🤣 laughing, 🙄 eye roll, 😢 sad, 🔥 impressed). If no pack has it,
   the nearest emoji in feeling is used when the plugin knows one.
3. Unsure what exists: `telegram_sticker_find` without arguments lists packs and emojis;
   with `query` it shows matching stickers and what each one shows (`about`).
4. The sticker replies to the message you are answering, in the same topic. Do not write
   "sent a sticker" or describe it. Hermes still sends your text after the call, and since 0.21.5
   a bare [SILENT] answer to a person shows up as an error notice, so end with one short line
   that carries the conversation on.
5. Got a sticker and want to acknowledge it without a new message: react with
   `send_message` (`action: "react"`) instead of sending a sticker back.
6. If Hermes hides plugin tools behind `tool_search` / `tool_describe` / `tool_call`, call
   `telegram_sticker_send` (`{"sticker": "facepalm"}`) and `telegram_sticker_find` (`query` or `emoji`)
   through `tool_call` directly: no need to search for them or describe them first, and each such
   lookup shows up as a line in the chat.

## Setup and settings in words

- A paragraph starting with "[telegram-stickers setup]" means the setup runs in this direct chat: answer
  what they wrote first, then ask the one question it names, in their language. Save each answer with
  `telegram_sticker_settings` and follow its `next` line.
- Later, when someone asks to change stickers ("less often", "only when I ask", "don't send this one",
  "remove the pack ...", "show my packs", "set up stickers"), use `telegram_sticker_settings` with the
  matching action. Before `forget_all`, list the packs and ask; call it with `confirm: true` only after a yes.
- In a group, only make stickers rarer, `on_request` or `off` there. For anything else, ask them to write
  to you directly.

## Errors

- "Wait N s" (cooldown) or "only N messages ago" (pacing): stickers are paced in this chat.
  Answer in words.
- "not in a Telegram chat": stickers only work in Telegram turns.
- "No sticker packs yet": tell the owner to send the bot a few stickers they like in a direct chat,
  or to add pack names in the plugin settings.
- "Stickers are switched off in this chat": someone asked for no stickers here, or the owner
  muted them. Answer in words.
- "No sticker fits" or "No sticker for": the message lists the emojis the packs have. Send the
  closest one once; if it fails again, answer in words. If nothing is described yet, the owner
  can run `/stickers describe`.
