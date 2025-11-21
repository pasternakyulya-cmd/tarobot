import asyncio

from telegram.constants import ChatAction
from telegram import Update

# ===== МИНИ-ОКНА / "АНИМАЦИЯ" =====
async def ritual_4s(update: Update):
    """
    4 секунды «ритуала» одним сообщением (без клавиатуры при редактировании).
    Никакого спама — только одно сообщение, которое правим 3 раза.
    """
    chat = update.effective_chat

    # 1) отправляем первое сообщение
    await chat.send_action(ChatAction.TYPING)
    msg = await update.message.reply_text("🔮 Судьба думает…")

    # 2) три правки = ~4 сек суммарно
    steps = [
        ("🪄 Перетасовываем колоду…", 1.3),
        ("👁️ Связываемся с духами…", 1.3),
        ("✨ Читаем знаки…",         1.3),
    ]
    for text, delay in steps:
        await asyncio.sleep(delay)
        await chat.send_action(ChatAction.TYPING)
        try:
            await msg.edit_text(text)
        except Exception:
            pass

    await asyncio.sleep(0.1)
    return msg