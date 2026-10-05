"""Tool schemas: this is what the model reads when deciding to use a sticker."""

FIND = {
    "name": "telegram_sticker_find",
    "description": (
        "Look up stickers in the owner's Telegram sticker packs. With no arguments it lists the "
        "packs, which emojis they cover and how many stickers have a description. With query "
        "(a few English words about the picture, e.g. 'cat facepalm', 'sleepy', 'thumbs up') it "
        "returns the best-described matches; with an emoji (e.g. 😏) it returns stickers tagged "
        "with it. Results carry ids like 'pack_name:12' and, when known, 'about': what the "
        "sticker shows. You can also skip this and pass words or an emoji straight to "
        "telegram_sticker_send."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string",
                      "description": "A few English words about the picture you want, e.g. 'cat facepalm'."},
            "emoji": {"type": "string", "description": "One emoji to match, e.g. 😏 or 🤣."},
            "pack": {"type": "string", "description": "Limit the search to one pack (short name)."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            "refresh": {"type": "boolean", "default": False,
                        "description": "Reload the packs from Telegram (after the owner edits a pack)."},
        },
    },
}

SEND = {
    "name": "telegram_sticker_send",
    "description": (
        "Send one sticker in the current Telegram chat, as a reply to the message you are "
        "answering and in the same forum topic. Pass a few English words describing the picture "
        "('dog rolling eyes'), an emoji (a random sticker tagged with it) or an exact id from "
        "telegram_sticker_find. Stickers sent recently in the chat are skipped when another one "
        "fits. Use it the way a person would: instead of a short emotional reply ('haha', 'oh no', "
        "'nice try'), never instead of a real answer, at most one every few messages, never to "
        "someone who is upset. After sending, do not describe the sticker in text. To put an emoji "
        "reaction on a message without sending a new one, use send_message with action='react' instead."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "sticker": {"type": "string",
                        "description": "Words about the picture ('sleepy cat'), an emoji such as 😏, "
                                       "or an id like 'pack_name:12'."},
            "reply": {"type": "boolean", "default": True,
                      "description": "Reply to the message being answered (default). False sends it unanchored."},
            "chat_id": {"type": "string",
                        "description": "Only when the owner allowed other chats. Leave empty for the current chat."},
            "thread_id": {"type": "integer",
                          "description": "Forum topic id for another chat. The current topic is used automatically."},
        },
        "required": ["sticker"],
    },
}

MUTE = {
    "name": "telegram_sticker_mute",
    "description": (
        "Switch stickers off in the current Telegram chat. Use it as soon as anyone in the chat "
        "asks you to stop sending stickers or says they are annoying, then confirm in words. It "
        "cannot switch them back on: only the owner can, with /stickers on <chat id> in a direct chat "
        "with the bot."
    ),
    "parameters": {"type": "object", "properties": {}},
}
