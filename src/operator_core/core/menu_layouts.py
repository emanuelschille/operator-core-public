from __future__ import annotations

PERSISTENT_MENU_REPLY_MARKUP = {
    "keyboard": [
        [{"text": "📋 Tagesplan"}, {"text": "💡 Neue Idee"}],
        [{"text": "📝 Voll Auto"}, {"text": "🎯 Modus"}],
        [{"text": "☰ Menü"}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
}

MENU_OVERLAY_REPLY_MARKUP = {
    "inline_keyboard": [
        [
            {"text": "📋 Tagesplan", "callback_data": "menu:plan"},
            {"text": "📊 Status", "callback_data": "menu:status"},
        ],
        [
            {"text": "💡 Idee", "callback_data": "menu:idea"},
            {"text": "📝 Voll Auto", "callback_data": "menu:vollauto"},
        ],
        [
            {"text": "🧩 Serie/Thema", "callback_data": "menu:serie"},
            {"text": "🏷️ Title", "callback_data": "menu:title"},
        ],
        [
            {"text": "🎣 Hook erstellen", "callback_data": "menu:hook"},
            {"text": "🪝 CTA erstellen", "callback_data": "menu:cta"},
        ],
        [
            {"text": "💬 Caption erstellen", "callback_data": "menu:caption"},
            {"text": "🎯 Modus", "callback_data": "menu:modus"},
        ],
    ]
}
