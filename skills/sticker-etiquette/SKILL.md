---
name: sticker-etiquette
description: When a Telegram sticker fits a conversation and when words are better.
---

# Sticker etiquette

A sticker is a person's quick reaction, not another way to deliver an answer.

## When to use

- In place of a short emotional line: "haha", "oh no", "nice try", "well, that happened".
- On a joke, light news, a playful jab, someone teasing you in a group.
- After you already answered in words, when one more line would be filler.

## When not to

- Instead of a real answer to a real question.
- When someone is upset, grieving, anxious, or angry.
- In a serious work or money conversation.
- Twice in a row, or as an automatic reply to every incoming sticker.
- In a group where nobody addressed you.

## How

1. Pick the feeling first, then say it in a few English words: "cat rolling eyes", "thumbs up",
   "sleepy", "facepalm". `telegram_sticker_send` with those words picks the best-described sticker.
2. No description fits, or the packs are not described yet: use an emoji instead
   (😏 smug, 🤣 laughing, 🙄 eye roll, 😢 sad, 🔥 impressed).
3. Unsure what exists: `telegram_sticker_find` without arguments lists packs and emojis;
   with `query` it shows matching stickers and what each one shows (`about`).
4. The sticker replies to the message you are answering, in the same topic. Do not write
   "sent a sticker" or describe it: the sticker is the reply.
5. Got a sticker and want to acknowledge it without a new message: react with
   `send_message` (`action: "react"`) instead of sending a sticker back.

## Errors

- "Wait N s": the per-chat cooldown is on. Answer in words or stay quiet.
- "not in a Telegram chat": stickers only work in Telegram turns.
- "No sticker packs are configured": tell the owner to add pack names in the plugin settings.
- "switched stickers off in this chat": the owner muted stickers here. Answer in words.
- "No sticker description fits": try other words or an emoji. If nothing is described yet, the
  owner can run `/stickers describe`.
