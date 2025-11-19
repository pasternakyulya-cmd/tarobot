import json
import os
import time

from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.constants import ChatAction
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)
from yookassa import Configuration, Payment

from text_data.cards import CARDS
from text_data.spreads import SPREADS

load_dotenv()

# Конфигурация ЮКассы
Configuration.account_id = os.getenv('YOOKASSA_SHOP_ID')
Configuration.secret_key = os.getenv('YOOKASSA_SECRET_KEY')

# 👉 ВСТАВЬ СВОЙ ТОКЕН
BOT_TOKEN_TEST = os.getenv('BOT_TOKEN_TEST')
BOT_TOKEN_PROD = os.getenv('BOT_TOKEN_PROD')

BOT_URL_TEST = os.getenv('BOT_URL_TEST')
BOT_URL_PROD = os.getenv('BOT_URL_PROD')

BIRTHDAYS_FILE = "birthdays.json"

def load_birthdays():
    if os.path.exists(BIRTHDAYS_FILE):
        try:
            with open(BIRTHDAYS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_birthdays(data: dict):
    with open(BIRTHDAYS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ===== СПИСОК ПОЛЬЗОВАТЕЛЕЙ (для рассылки) =====
USERS_FILE = "users.json"

def load_users() -> set[str]:
    """Загружает всех пользователей из файла users.json"""
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                # поддерживаем старый формат: список → set[str]
                if isinstance(data, list):
                    return set(map(str, data))
                elif isinstance(data, dict):
                    return set(map(str, data.keys()))
                elif isinstance(data, set):
                    return set(map(str, data))
                else:
                    return set()
        except Exception:
            return set()
    return set()

def save_users(users: set[str]):
    """Сохраняет всех пользователей в файл users.json"""
    try:
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(list(users)), f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[!] Ошибка сохранения users.json: {e}")

def add_user(uid: str):
    """Добавляет нового пользователя в базу (если его ещё нет)"""
    users = load_users()
    if uid not in users:
        users.add(uid)
        save_users(users)


# 💞 Совместимость — 1 раз в день (день меняется в 06:00 МСК)

# ===== Готовые тексты =====


# ===== Функция вычисления "дня" по московскому времени (смена в 06:00) =====
def moscow_today_with_6am_cutoff() -> str:
    """Возвращает дату по московскому времени, где новый день начинается в 06:00."""
    now_msk = datetime.now(ZoneInfo("Europe/Moscow"))
    cutoff = now_msk.replace(hour=6, minute=0, second=0, microsecond=0)
    if now_msk < cutoff:
        today = (now_msk - timedelta(days=1)).date()
    else:
        today = now_msk.date()
    return today.isoformat()

# 💞 Совместимость — логика: 1) intro → 2) расклад → 3) "уже получено"

def get_or_assign_daily_compat(uid: str):
    """
    Возвращает (already, payload)
      already = False -> payload = intro (первый тап за день, не расходует)
      already = False -> payload = новый расклад (второй тап за день, сохраняем)
      already = True  -> payload = сохранённый расклад (сегодня уже получали)
    """
    uid = str(uid)
    daily = load_daily_map() or {}
    u = daily.setdefault(uid, {})
    tkey = moscow_today_with_6am_cutoff()
    comp = u.get("compat")

    # Если уже есть расклад на сегодня — считаем "уже получено"
    if comp and comp.get("date") == tkey and comp.get("text"):
        return True, comp["text"]

    # Если сегодня ещё не "праймились" — показать вступление, не выдавая расклад
    if not (comp and comp.get("date") == tkey and comp.get("primed")):
        intro = (
            "💞 Подумай о человеке, который тебе дорог или просто не выходит из мыслей…\n"
            "Карты расскажут, как вы влияете друг на друга и что может дать ваш союз 🔮\n"
            "\n"
            "Когда будешь готов — нажми «💞 Совместимость» ещё раз 🌙"
        )
        u["compat"] = {"date": tkey, "primed": True}
        daily[uid] = u
        save_daily_map(daily)
        return False, intro

    # Уже праймились сегодня, но текста ещё нет — выдаём и сохраняем расклад
    text = random.choice(SPREADS) if globals().get("SPREADS") else "💞 Совместимость: библиотека раскладов пуста."
    u["compat"] = {"date": tkey, "primed": True, "text": text}
    daily[uid] = u
    save_daily_map(daily)
    return False, text

# ===== ПРОВЕРКА ПОДПИСКИ НА КАНАЛ =====
CHANNEL_USERNAME = "@bauettmagic"  # <-- сюда подставь свой канал

async def check_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Проверяет, подписан ли пользователь на канал."""
    try:
        user_id = update.effective_user.id
        member = await context.bot.get_chat_member(CHANNEL_USERNAME, user_id)
        return member.status in ("member", "administrator", "creator")
    except (Forbidden, BadRequest):
        # если бот не админ канала — не может проверить
        try:
            await update.message.reply_text(
                "⚠️ Бот не может проверить подписку. Добавь его в канал как администратора без права публикации."
            )
        except Exception:
            pass
        return False
    except Exception:
        return False


# Не даём запускать несколько "ритуалов" одновременно для одного пользователя
BUSY: set[str] = set()

# ===== ТЕКСТЫ =====
START_TEXT = (
    "🌙 Приветствую тебя, странник, ищущий ответы в потоках судьбы ✨\n"
    "Здесь карты Таро открывают завесу тайн и помогают услышать себя сквозь шёпот Вселенной.\n\n"
    "🔮 «Карта дня» — один шанс в сутки, случайная карта с предсказанием.\n"
    "🌗 «Мини-расклад» — краткий трёхкартный совет судьбы.\n"
    "💞 «Совместимость» — покажет, как переплетаются ваши энергии.\n"
    "🌑 «Задай вопрос» — получи ответ «да» или «нет» от самих карт.\n"
    "🌙 «Написать Вселенной» — расскажи пространству, что в тебе откликается. Иногда ответ приходит в виде знака.\n\n"
    "Погрузись… и позволь магии карт направить тебя 🌌"
)

MORNING_TEXT = "🌅 Доброе утро! Твоё предсказание уже готово. Нажми «🔮 Карта дня» ✨"

# ===== КЛАВИАТУРА (Reply) =====

BTN_UNIVERSE = "🌙 Написать Вселенной"
BTN_CARD  = "🔮 Карта дня"
BTN_MINI  = "🌗 Мини-расклад"
BTN_COMP  = "💞 Совместимость"
BTN_YESNO = "🌑 Задай вопрос"
BTN_ORACLE = "🪄 Помощь Оракула"

def reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(BTN_CARD)],
            [KeyboardButton(BTN_MINI), KeyboardButton(BTN_COMP)],
            [KeyboardButton(BTN_YESNO), KeyboardButton(BTN_UNIVERSE)],
            [KeyboardButton(BTN_ORACLE)],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        selective=False
    )

# ===== ДАННЫЕ =====
DAILY_FILE = "daily.json"

USERS_FILE = "users.json"

def load_users() -> set[str]:
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return set(map(str, data))
        except Exception:
            pass
    return set()

def save_users(users: set[str]):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(users), f, ensure_ascii=False, indent=2)

def add_user(uid: str):
    users = load_users()
    if uid not in users:
        users.add(uid)
        save_users(users)


def today_key() -> str:
    return date.today().isoformat()

def load_daily_map() -> dict:
    if not os.path.exists(DAILY_FILE):
        return {}
    try:
        with open(DAILY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def save_daily_map(data: dict):
    with open(DAILY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

from datetime import time


def get_or_assign_today_card_index(uid: str):
    """
    Возвращает (already_had, idx) для 'Карты дня' со сбросом в 06:00 по московскому времени.
    Поддерживает старый формат (daily[uid] = {"date": "YYYY-MM-DD", "idx": ...})
    и новый формат (daily[uid]["card_day"] = {"dt": ISO8601, "idx": ...}).
    """
    tz = ZoneInfo("Europe/Moscow")  # Московское время
    now = datetime.now(tz)

    def anchor_6am(dt: datetime) -> datetime:
        """Возвращает момент последнего сброса (06:00 сегодня или вчера)."""
        a = datetime.combine(dt.date(), time(6, 0), tzinfo=tz)
        return a if dt >= a else (a - timedelta(days=1))

    daily = load_daily_map()
    user = daily.get(uid, {})

    # --- 1) Новый формат: daily[uid]["card_day"] = {"dt": "...", "idx": N}
    if isinstance(user, dict) and "card_day" in user:
        rec = user["card_day"]
        last_dt_raw = rec.get("dt")
        if last_dt_raw:
            try:
                last_dt = datetime.fromisoformat(last_dt_raw)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=tz)  # трактуем как московское
                if last_dt >= anchor_6am(now):
                    return True, rec.get("idx", 0)
            except Exception:
                pass  # битое значение — выдаём новую карту

    # --- 2) Старый формат: daily[uid] = {"date": "YYYY-MM-DD", "idx": N}
    #     Считаем, что "последний раз" был в 06:00 той даты.
    if isinstance(user, dict) and "date" in user and "idx" in user:
        try:
            old_date = date.fromisoformat(user["date"])
            last_dt = datetime.combine(old_date, time(6, 0), tzinfo=tz)
            if last_dt >= anchor_6am(now):
                return True, user["idx"]
        except Exception:
            pass  # переassign ниже

    # --- 3) Новый день после 06:00 — выдаём новую карту
    idx = random.randrange(len(CARDS))
    if uid not in daily or not isinstance(daily.get(uid), dict):
        daily[uid] = {}
    daily[uid]["card_day"] = {
        "dt": now.isoformat(timespec="seconds"),
        "idx": idx,
    }

    # чистим старые ключи
    for k in ("date",):
        if k in daily[uid]:
            daily[uid].pop(k, None)

    save_daily_map(daily)
    return False, idx

from datetime import date  # убедись, что это есть в импортах сверху

def get_mini_remaining(uid: str):
    """
    Возвращает timedelta до следующего мини-расклада (если ещё рано), иначе None.
    Работает с форматом daily[uid]["mini_spread"]["dt"] — ISO datetime.
    """
    daily = load_daily_map()
    if uid not in daily or "mini_spread" not in daily[uid]:
        return None

    prev = daily[uid]["mini_spread"]
    dt_str = prev.get("dt")
    if not dt_str:
        return None

    try:
        last_dt = datetime.fromisoformat(dt_str)
    except Exception:
        return None

    left = timedelta(hours=6) - (datetime.now() - last_dt)
    return left if left.total_seconds() > 0 else None


# ===== Периодическая рассылка (каждые 16 дней) =====
SHARE_TEXT = (
    "Привет 🌿\n"
    "Спасибо, что ты с нами — именно благодаря тебе этот проект живёт и растёт 💫\n\n"
    "Если тебе откликнулся сегодняшний расклад, поделись им с другом или подругой.\n\n"
    "Пусть кто-то ещё сегодня получит свой знак, а магия распространится дальше 🔮"
)

import asyncio
from telegram.error import Forbidden

# Лимитер, чтобы не превысить лимиты Telegram
SEND_SEMAPHORE = asyncio.Semaphore(25)

async def safe_send(bot, chat_id: int, text: str, **kwargs):
    """Безопасная отправка с паузами и ретраями"""
    for attempt in range(3):
        try:
            async with SEND_SEMAPHORE:
                return await bot.send_message(chat_id=chat_id, text=text, **kwargs)
        except Forbidden:
            raise
        except Exception:
            await asyncio.sleep(0.3 * (attempt + 1))
    raise

async def periodic_share_broadcast(context):
    """Рассылка всем пользователям каждые 16 дней"""
    users = load_users()
    print(f"[JOB] share_broadcast: start, users={len(users)}")

    if not users:
        print("[JOB] share_broadcast: нет пользователей — выходим")
        return

    to_remove = []
    uids = [int(u) for u in users]

    for idx, uid in enumerate(uids, start=1):
        try:
            await safe_send(context.bot, uid, SHARE_TEXT, reply_markup=reply_keyboard())
            if idx % 25 == 0:
                print(f"[JOB] share_broadcast: sent {idx}/{len(uids)}")
            await asyncio.sleep(0.2)
        except Forbidden:
            to_remove.append(str(uid))
        except Exception as e:
            print(f"[JOB][!] ошибка при отправке {uid}: {e}")

    if to_remove:
        print(f"[JOB] share_broadcast: remove {len(to_remove)} unsubscribed")
        for u in to_remove:
            users.discard(u)
        save_users(users)

    print("[JOB] share_broadcast: done")



# ===== МИНИ-ОКНА / "АНИМАЦИЯ" =====
async def ritual_4s(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            # ВАЖНО: без reply_markup — Telegram не разрешает его в edit_message_text для reply-клавы
            await msg.edit_text(text)
        except Exception:
            pass  # тихо игнорируем, продолжаем

    await asyncio.sleep(0.1)  # итого ≈4.0 c
    return msg





# ===== HANDLERS =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # проверяем подписку
    is_subscribed = await check_subscription(update, context)
    if not is_subscribed:
        await update.message.reply_text(
            "⚠️ Чтобы пользоваться ботом, подпишись на канал 👉 @bauettmagic\n\n"
            "После подписки нажми /start снова 🌙"
        )
        return

    uid = str(update.effective_user.id)
    add_user(uid)

    # если подписан — приветствуем
    await update.message.reply_text(
        "🌙 Приветствую тебя, странник, ищущий ответы в потоках судьбы ✨\n"
        "Здесь карты Таро открывают завесу тайн.\n\n"
        "🔮 «Карта дня» — ежедневное послание от вселенной.\n"
        "🌗 «Мини-расклад» — три карты откроют тайну твоего ближайшего пути.\n"
        "💞 «Совместимость» — расскажет, как взаимодействуют энергии твоих отношений.\n"
        "🌑 «Задай вопрос» — получи ответ от карт «да» или «нет».\n\n"
        "Погрузись… и позволь магии карт направить тебя 🌌",
        reply_markup=reply_keyboard()
    )
    # 🌙 Небольшая "пауза" перед следующим сообщением
    await asyncio.sleep(1.5)

    # 👉 Просим ввести дату рождения
    await update.message.reply_text(
        "✨ Чтобы карты точнее улавливали твои вибрации, введи свою дату рождения \n"
        "Напиши в формате: ДД.ММ.ГГГГ (например: 24.09.1999)"
    )


async def resetday(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Для тестов: сбрасывает сегодняшние карты
    save_daily_map({})
    await update.message.reply_text("♻️ Сбросили дневные карты. Тестируй заново.", reply_markup=reply_keyboard())

def is_card_button(text: str) -> bool:
    t = (text or "").strip().lower()
    return (t == BTN_CARD.lower()) or ("карта" in t and "дня" in t)

# 🌙 Написать Вселенной — тексты и ответы
INTRO_UNIVERSE = (
    "🌌 Всё, что ты напишешь здесь, останется полностью анонимным.\n"
    "Но помни — Вселенная всегда слышит тех, кто говорит от сердца.\n\n"
    "Напиши ей то, что давно носишь внутри: тревоги, мечты, страхи или желания.\n"
    "Здесь можно сказать всё, что невозможно произнести вслух.\n\n"
    "✍️ Отправь своё послание сообщением — и оно будет передано потоку.\n\n"
    "Нет никаких ограничений, сколько бы писем ты ни захотел отправить.\n"
    "Каждый шёпот важен, и ни одно слово не потеряется в тишине."
)

UNIVERSE_CONFIRMS = [
    "🕯 Послание принято. Вселенная услышала — даже если ты этого ещё не чувствуешь.\nВсё произойдёт в тот момент, когда будет нужно.",
    "🌙 Я бережно сохранил твои слова в тишине.\nИногда достаточно просто произнести — и энергия начинает двигаться.",
    "💫 Поток принял твоё послание.\nПусть дорога откроется мягко, а знаки придут в нужный миг.",
    "🪽 Слова ушли в эфир, где рождаются перемены.\nВсё, что сказано с искренностью, уже услышано.",
    "🔮 Твоя энергия отправлена во Вселенную.\nПусть она вернётся к тебе светом, заботой и нужными встречами."
]

UNIVERSE_WAITING = [
    "🌙 Хорошо. Я подожду.\nСобери мысли и скажи, когда будешь готов(а).",
    "🕯️ Не спеши. Иногда важное слово приходит последним.\nЯ рядом — просто напиши, когда почувствуешь, что пора."
]

# ===== СОВМЕСТИМОСТЬ (готовые тексты) =====
# 💞 Совместимость — 1 раз в день (день меняется в 06:00 МСК)
from zoneinfo import ZoneInfo

# 🌗 Мини-расклад — логика выдачи (1 раз в 6 часов)
from datetime import datetime, timedelta
import random

def get_or_assign_mini_spread(uid: str):
    """
    Возвращает (already_had, spread_text).
    Новый мини-расклад можно каждые 6 часов.
    """
    try:
        daily = load_daily_map() or {}
    except Exception:
        daily = {}

    now = datetime.now()
    uid = str(uid)
    u = daily.setdefault(uid, {})
    prev = u.get("mini_spread")

    # Проверяем, не прошло ли 6 часов
    if prev and "dt" in prev:
        try:
            last_dt = datetime.fromisoformat(prev["dt"])
            if (now - last_dt) < timedelta(hours=6):
                return True, prev.get("text", "")
        except Exception:
            pass  # битая дата — выдаём новый

    # Берём случайный расклад
    spreads = globals().get("MINI_SPREADS", [])
    spread_text = random.choice(spreads) if spreads else "🌗 Мини-расклад: библиотека пуста."

    # Сохраняем
    u["mini_spread"] = {
        "dt": now.isoformat(timespec="minutes"),
        "text": spread_text
    }
    daily[uid] = u
    save_daily_map(daily)
    return False, spread_text
# 🌑 Да/Нет — лимит 6 использований в день
YESNO_DAILY_LIMIT = 6

def _get_or_reset_yesno_bucket(uid: str):
    """Возвращает (daily, user_data, bucket) для 'да/нет' с авто-сбросом по МСК 06:00."""
    try:
        daily = load_daily_map() or {}
    except Exception:
        daily = {}

    uid = str(uid)
    tkey = moscow_today_with_6am_cutoff()
    u = daily.setdefault(uid, {})
    b = u.get("yesno")
    if not (isinstance(b, dict) and b.get("date") == tkey):
        b = {"date": tkey, "count": 0}
        u["yesno"] = b
        daily[uid] = u
        save_daily_map(daily)
    return daily, u, b


def take_yesno_draw(uid: str):
    """
    Пытается сделать 'тык'.
    Возвращает (ok, text_or_reason, remaining):
      ok=True  -> text_or_reason = выбранный текст, remaining = сколько попыток осталось.
      ok=False -> text_or_reason = сообщение (вступление или исчерпание лимита), remaining = 0 или текущее.
    """
    daily, u, b = _get_or_reset_yesno_bucket(uid)
    used = int(b.get("count", 0))

    # 🔔 Первый тык за день: показываем вступительное сообщение (без расхода попытки)
    if not b.get("primed", False):
        b["primed"] = True
        u["yesno"] = b
        daily[str(uid)] = u
        try:
            save_daily_map(daily)
        except Exception:
            pass

        primer = (
            "🔮 Закрой глаза и задай картам вопрос — они ответят тебе: «да» или «нет»…\n"
            "Мы уже настроились на потоки энергии, теперь твоя очередь.\n"
            "Когда почувствуешь готовность — нажми «🌑 Задай вопрос» ещё раз 🌌"
        )
        # не расходуем попытку; показываем, сколько осталось на сегодня
        remaining = YESNO_DAILY_LIMIT - used
        return False, primer, remaining

    # 🔒 Лимит на сегодня исчерпан
    if used >= YESNO_DAILY_LIMIT:
        return False, (
            "✨ Энергия твоего запроса исчерпана…\n"
            "Отпусти мысли и доверься Вселенной 🌌\n"
            "Возвращайся завтра утром — карты снова будут говорить с тобой 🔮"
        ), 0

    # 🎲 Случайный ответ (персонально подмешиваем uid и время, чтобы меньше совпадений)
    spreads = globals().get("YESNO_TEXTS", [])
    try:
        random.seed(f"{uid}-{datetime.now().isoformat(timespec='seconds')}")

    except Exception:
        pass
    text = random.choice(spreads) if spreads else "🌑 Ответ пока скрыт. Попробуй позже."

    # ✅ Фиксируем расход попытки
    b["count"] = used + 1
    u["yesno"] = b
    daily[str(uid)] = u
    try:
        save_daily_map(daily)
    except Exception:
        pass

    remaining = YESNO_DAILY_LIMIT - b["count"]
    return True, text, remaining


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Проверяем подписку
    is_subscribed = await check_subscription(update, context)
    if not is_subscribed:
        await update.message.reply_text(
            "⚠️ Чтобы пользоваться ботом, подпишись на канал 👉 @bauettmagic\n\n"
            "После подписки нажми /start снова 🌙",
            reply_markup=reply_keyboard()
        )
        return


    # Сохраняем пользователя для рассылки
    uid = str(update.effective_user.id)
    add_user(uid)
    text = update.message.text.strip()
    uid = str(update.effective_user.id)
    birthdays = load_birthdays()

        # 🎂 Проверка: если сообщение похоже на дату
    import re
    match = re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{4})", text)
    if match:
        birthdays[uid] = text
        save_birthdays(birthdays)

        # небольшая пауза, чтобы выглядело плавно
        try:
            await asyncio.sleep(1.0)
        except Exception:
            pass

        await update.message.reply_text(
            f"🌟 Отлично! Карты запомнили твою дату — {text}.\n"
            "Теперь Вселенная будет внимательнее к твоим энергиям 🔮\n\n"
            "Выбери, с чего начнём:",
            reply_markup=reply_keyboard()  # ← показываем кнопки
        )
        return


    # Подготовка
    text = (update.message.text or "")
    t = text.strip().lower()
    chat = update.effective_chat

    # 🔮 Карта дня
    if is_card_button(text):
        if uid in BUSY:
            await update.message.reply_text("⏳ Подожди несколько секунд…", reply_markup=reply_keyboard())
            return

        BUSY.add(uid)
        try:
            progress_msg = await ritual_4s(update, context)
            already, idx = get_or_assign_today_card_index(uid)
            card = CARDS[idx]

            if already:
                final_text = (
                    "✨ Ты уже получил своё предсказание сегодня!\n"
                    "Возвращайся завтра за новой картой 🌙"
                )
            else:
                final_text = f"🔮 Твоя карта дня:\n\n⭐ {card['name']}\n{card['text']}"

            await progress_msg.delete()
            await chat.send_message(final_text, reply_markup=reply_keyboard())
        finally:
            BUSY.discard(uid)
        return

               # 🌗 Мини-расклад — можно делать раз в 6 часов, но первый клик за день даёт вступление
    if t == BTN_MINI.lower() or ("мини" in t and "расклад" in t):
        if uid in BUSY:
            await update.message.reply_text("⏳ Подожди несколько секунд…", reply_markup=reply_keyboard())
            return

        BUSY.add(uid)
        progress_msg = None
        try:
            # загружаем состояние
            daily = load_daily_map()
            now_key = moscow_today_with_6am_cutoff()
            u = daily.setdefault(uid, {})
            prev = u.get("mini_intro")

            # если человек впервые за день — показываем вступление
            if not (prev and prev.get("date") == now_key):
                u["mini_intro"] = {"date": now_key}
                save_daily_map(daily)

                intro = (
                    "🌗 Если ты нажал(а) эту кнопку — значит, тебе нужен мини-расклад.\n\n"
                    "Этот расклад общий: он покажет, что сейчас происходит в твоей жизни, "
                    "на что стоит обратить внимание и куда движется энергия.\n\n"
                    "Загадай себя, сделай вдох — и когда почувствуешь готовность, "
                    "нажми «🌗 Мини-расклад» ещё раз 🔮"
                )
                await chat.send_message(intro, reply_markup=reply_keyboard())
                return

            # иначе — выполняем сам расклад
            progress_msg = await ritual_4s(update, context)
            already, spread_text = get_or_assign_mini_spread(uid)

            if already:
                left = get_mini_remaining(uid)
                if left:
                    total = int(left.total_seconds())
                    hours = total // 3600
                    minutes = (total % 3600) // 60
                    final_text = (
                        f"✨ Ты уже получал свой мини-расклад!\n"
                        f"Возвращайся через {hours:02d}:{minutes:02d} ⏳"
                    )
                else:
                    final_text = (
                        "✨ Ты уже получал свой мини-расклад недавно.\n"
                        "Попробуй позже 🌙"
                    )
            else:
                final_text = spread_text

            if progress_msg:
                try:
                    await progress_msg.delete()
                except:
                    pass

            await chat.send_message(final_text, reply_markup=reply_keyboard())

        except Exception as e:
            if progress_msg:
                try:
                    await progress_msg.delete()
                except:
                    pass
            await chat.send_message(
                f"⚠️ Ошибка мини-расклада: {type(e).__name__}: {e}",
                reply_markup=reply_keyboard()
            )
        finally:
            BUSY.discard(uid)
        return





          # 💞 Совместимость — 1 раз в день (первый тап = вступление, второй = расклад)
    if t == BTN_COMP.lower():  # важно: без подстрочного совпадения, чтобы текст не триггерил хэндлер
        if uid in BUSY:
            await update.message.reply_text("⏳ Подожди несколько секунд…", reply_markup=reply_keyboard())
            return

        BUSY.add(uid)
        progress_msg = None
        try:
            # мини-ритуал (можно убрать, если не нужен)
            progress_msg = await ritual_4s(update, context)

            already, comp_text = get_or_assign_daily_compat(uid)

            # аккуратно убираем «ритуал»
            if progress_msg:
                try:
                    await progress_msg.delete()
                except:
                    pass

            if already:
                await chat.send_message(
                    "✨ На сегодня расклад по совместимости уже получен.\n"
                    "Энергия для нового расклада восстановится завтра 🌙",
                    reply_markup=reply_keyboard()
                )
            else:
                await chat.send_message(comp_text, reply_markup=reply_keyboard())

        except Exception:
            if progress_msg:
                try:
                    await progress_msg.delete()
                except:
                    pass
            await chat.send_message(
                "Упс… что-то пошло не так с совместимостью. Попробуй ещё раз позже.",
                reply_markup=reply_keyboard()
            )
        finally:
            BUSY.discard(uid)
        return




       # 🌑 Задай вопрос (Да/Нет) — до 6 раз в день, с остатком попыток
    if t == BTN_YESNO.lower() or ("вопрос" in t):
        if uid in BUSY:
            await update.message.reply_text("⏳ Подожди несколько секунд…", reply_markup=reply_keyboard())
            return

        BUSY.add(uid)
        progress_msg = None
        try:
            # красивый «ритуал»
            progress_msg = await ritual_4s(update, context)

            ok, payload, remaining = take_yesno_draw(uid)

            # уберём ритуал, если он есть
            if progress_msg:
                try:
                    await progress_msg.delete()
                except:
                    pass

            if ok:
                final_text = (
                    f"{payload}\n\n"
                    f"🔮 Судьба ещё позволит задать {remaining} вопрос(ов) сегодня…\n"
                    f"Используй их с мудростью 🌙"
                )
            else:
                final_text = payload  # сообщение об исчерпании

            await chat.send_message(final_text, reply_markup=reply_keyboard())

        except Exception as e:
            if progress_msg:
                try:
                    await progress_msg.delete()
                except:
                    pass
            await chat.send_message(
                f"⚠️ Ошибка да/нет: {type(e).__name__}: {e}",
                reply_markup=reply_keyboard()
            )
        finally:
            BUSY.discard(uid)
        return
           # 🌙 Написать Вселенной
    if t == BTN_UNIVERSE.lower() or t == "/universe":
        uid = str(update.effective_user.id)
        context.user_data["writing_to_universe"] = True
        context.user_data["awaiting_universe_confirm"] = False  # сбрасываем ожидание подтверждения
        await chat.send_message(INTRO_UNIVERSE, reply_markup=reply_keyboard())
        return

    # Сохраняем пользователя для рассылки
    uid = str(update.effective_user.id)
    add_user(uid)
    text = update.message.text.strip()
    t = text.lower()  # добавляем для удобства
    uid = str(update.effective_user.id)
    birthdays = load_birthdays()

    if context.user_data.get("oracle_state") == "waiting_question":
        user_question = text

        payment_msg = (
            "Оракул услышал твой вопрос.\n\n"
            "Чтобы получить действительно точный, глубокий и индивидуальный разбор, нужен энергообмен. "
            "Это не формальность — благодаря ему Оракул может сосредоточиться на твоей ситуации и разобрать её максимально внимательно.\n\n"
            "✨ Стоимость одного обращения — 25 рублей.\n"
            "✨ Сразу взять пакет из 6 обращений — 130 рублей.\n\n"
            "Это небольшая сумма за ответ, который может дать ясность, подсказать верное действие, предупредить ошибку "
            "и помочь увидеть то, что сейчас кажется туманным.\n\n"
            "Если ты хочешь получить разбор, в котором чувствуется внимание, опыт и аккуратный подход — просто нажми на кнопку ниже.\n"
            "Оракул приступит к разбору сразу после энергообмена 💫"
        )

        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        import uuid

        user_id = update.message.from_user.id

        # Генерируем платежные ссылки с UUID для каждого варианта
        payment_25 = Payment.create({
            "amount": {
                "value": "25.00",
                "currency": "RUB"
            },
            "confirmation": {
                "type": "redirect",
                "return_url": BOT_URL_TEST
            },
            "capture": True,
            "description": "Разбор вопроса Оракулом (1 обращение)",
            "metadata": {
                "user_id": user_id,
                "question": user_question,  # ← теперь user_question доступен
                "tariff": "single"
            }
        }, uuid.uuid4())

        payment_130 = Payment.create({
            "amount": {
                "value": "130.00",
                "currency": "RUB"
            },
            "confirmation": {
                "type": "redirect",
                "return_url": BOT_URL_TEST
            },
            "capture": True,
            "description": "Пакет 6 обращений к Оракулу",
            "metadata": {
                "user_id": user_id,
                "question": user_question,  # ← теперь user_question доступен
                "tariff": "package"
            }
        }, uuid.uuid4())

        keyboard = [
            [
                InlineKeyboardButton("🔮 Оплатить 25 ₽", url=payment_25.confirmation.confirmation_url),
            ],
            [
                InlineKeyboardButton("🔮 Пакет 6 обращений — 130 ₽", url=payment_130.confirmation.confirmation_url),
            ]
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        print(f"🔄 Отправляю сообщение с кнопками-ссылками:")
        print(f"   Кнопка 25 руб: {payment_25.confirmation.confirmation_url}")
        print(f"   Кнопка 130 руб: {payment_130.confirmation.confirmation_url}")

        sent_message = await update.message.reply_text(payment_msg, reply_markup=reply_markup)
        print(f"✅ Сообщение отправлено, ID: {sent_message.message_id}")

        context.user_data["oracle_question"] = user_question
        context.user_data["oracle_state"] = "waiting_payment"
        return

    # 2️⃣ Нажатие на кнопку «🪄 Помощь Оракула»
    if t == BTN_ORACLE.lower() or ("помощь" in t and "оракула" in t):
        oracle_message = (
            "🪄 Ты открыл доступ к Оракулу .\n\n"
            "Перед тобой — не просто собеседник и не обычный инструмент.\n"
            "Оракул работает тонко, на границе интуиции и знаний.\n"
            "Он соединяет древние толкования, законы причинности, наблюдение за энергией момента\n"
            "и умение видеть то, что человек обычно скрывает даже от самого себя.\n\n"
            "Это проводник, который всегда указывает туда, где лежит суть.\n"
            "Он не уводит в фантазии — он раскрывает то, что уже существует,\n"
            "но пока не оформлено в слова.\n\n"
            "И вот что он сможет для тебя:\n"
            "   🔍 Разобрать твой расклад и считать скрытые смыслы;\n"
            "   💛 Прояснить чувства и намерения, опираясь на древние знания и законы кармы;\n"
            "   🧭 Подсказывать направление, когда интуиция молчит;\n"
            "   👁️ Замечать то, что сейчас ускользает от взгляда.\n\n"
            "Сформулируй свой вопрос ниже — но сделай это внимательно.\n"
            "Опиши свой запрос подробно, чтобы Оракул увидел все детали. ✨"
        )

        await update.message.reply_text(oracle_message)
        context.user_data["oracle_state"] = "waiting_question"
        return

    # ✉️ Если человек пишет, когда активна функция "письмо Вселенной"
    if context.user_data.get("writing_to_universe") and not context.user_data.get("awaiting_universe_confirm"):
        text_message = text.strip()
        if not text_message:
            return

        # Сохраняем текст и переходим в режим подтверждения
        context.user_data["last_universe_msg"] = text_message
        context.user_data["awaiting_universe_confirm"] = True

        buttons = [
            [KeyboardButton("✨ Да, отправляем")],
            [KeyboardButton("💭 Нет, допишу ещё")]
        ]
        reply = ReplyKeyboardMarkup(buttons, resize_keyboard=True, one_time_keyboard=True)

        # 🌙 Случайный вариант вопроса “всё ли сказано?”
        UNIVERSE_ASK = [
            "🌙 Всё ли сказано? Когда почувствуешь завершённость — я передам твоё послание потоку.",
            "💫 Хочешь, чтобы я уже отпустил эти слова во Вселенную?",
            "🔮 Доверишь ли ты своё послание энергии пространства? Я передам его с бережностью.",
            "🌌 Сердце подсказывает, что всё сказано... или стоит добавить ещё немного света?",
            "🕯 Всё ли обрело форму? Я готов передать послание, как только ты дашь знак.",
            "✨ Когда слова исчерпают себя, просто скажи — и я отправлю твоё письмо во Вселенную.",
            "🌙 Готов ли ты отпустить своё послание в тишину, где его услышит Вселенная?",
            "💫 Почувствуй момент. Если в тебе настала тишина — я передам послание потоку.",
            "🔮 Хочешь, чтобы твои слова стали частью дыхания Вселенной?",
            "🌕 Всё ли внутри стало чуть легче? Если да — я передам твоё послание дальше."
        ]

        await chat.send_message(random.choice(UNIVERSE_ASK), reply_markup=reply)
        return

    # ✨ Обработка ответа "Да / Нет" после письма
    if context.user_data.get("awaiting_universe_confirm") and text.strip() in ["✨ Да, отправляем", "💭 Нет, допишу ещё"]:
        if text.startswith("✨"):
            UNIVERSE_CONFIRMS = [
                "🕯 Послание принято. Вселенная услышала — даже если ты пока не чувствуешь этого. Всё случится тогда, когда придёт время.",
                "🌙 Я бережно сохранил твои слова в тишине. Иногда достаточно просто сказать — и энергия начинает двигаться сама.",
                "💫 Поток принял твоё послание. Пусть дорога откроется мягко, а нужные знаки придут тогда, когда сердце будет готово их увидеть.",
                "🪽 Твои слова ушли в эфир, где рождаются перемены. Всё, что произнесено искренне, уже отозвалось в пространстве.",
                "🔮 Твоя энергия отправлена во Вселенную. Пусть она вернётся к тебе светом, заботой и тихими чудесами.",
                "🌌 Письмо растворилось в потоках пространства. Вселенная услышала — теперь остаётся лишь довериться и ждать её отклика.",
                "✨ Я передал твоё послание. Оно уже путешествует сквозь тишину, неся твою энергию туда, где её ждут.",
                "🌠 Всё, что ты сказал(а), уже отпечаталось в ткани мира. Пусть ответ придёт мягко, как дыхание ветра.",
                "🕊 Я отпустил твоё письмо. Пусть оно летит по звёздным дорогам, несёт свет и возвращается добром.",
                "💫 Вселенная услышала и приняла. Теперь твоё послание стало частью её движения — спокойного, бесконечного, живого."
            ]

            msg = random.choice(UNIVERSE_CONFIRMS)
            context.user_data["writing_to_universe"] = False
            context.user_data["awaiting_universe_confirm"] = False
            await chat.send_message(msg, reply_markup=reply_keyboard())

        else:
            UNIVERSE_WAITING = [
                "🌙 Хорошо. Я подожду. Напиши всё, что хочется сказать.",
                "🕯️ Не спеши. Иногда важное приходит не сразу. Просто пиши, я рядом.",
                "💫 Конечно. У каждого послания свой ритм. Продолжай, я слушаю.",
                "🌌 Не торопись — пиши так, как чувствуешь. Я здесь.",
                "🔮 Иногда нужно чуть больше слов, чтобы стало легче. Я подожду.",
                "🌠 Хорошо, я дождусь продолжения. Иногда последние строки самые важные.",
                "🕯 Говори всё, что хочешь. Когда почувствуешь, что всё — просто дай знать.",
                "🌙 Всё хорошо. Не спеши, я здесь и жду твоих слов.",
                "💫 Возьми время, вдохни, собери мысли. Я не тороплю.",
                "🌌 Когда будешь готов(а), просто напиши — я передам всё дальше."
            ]

            msg = random.choice(UNIVERSE_WAITING)
            context.user_data["awaiting_universe_confirm"] = False
            await chat.send_message(msg, reply_markup=reply_keyboard())
        return

    # 🧭 По умолчанию
    await update.message.reply_text("Нажми на кнопки ниже 👇", reply_markup=reply_keyboard())
# ================== УТРЕННЯЯ РАССЫЛКА ==================
MORNING_TEXT = (
    "Карта дня уже ждет тебя.\n"
    "Открой и посмотри знак на сегодня ✨"
)



async def morning_broadcast(context: ContextTypes.DEFAULT_TYPE):
    users = load_users()
    to_remove = []
    for uid in users:
        try:
            await context.bot.send_message(
                chat_id=int(uid),
                text=MORNING_TEXT,
                reply_markup=reply_keyboard()
            )
        except Forbidden:
            # пользователь заблокировал бота — уберём из рассылки
            to_remove.append(uid)
        except Exception:
            # не роняем рассылку из-за одной ошибки
            pass

    if to_remove:
        for u in to_remove:
            users.discard(u)
        save_users(users)

async def birthday_broadcast(context: ContextTypes.DEFAULT_TYPE):
    """Поздравляет пользователей, у кого сегодня день рождения."""
    birthdays = load_birthdays()
    today = date.today()

    for uid, bday_str in birthdays.items():
        try:
            day, month, _ = map(int, bday_str.split("."))
            if day == today.day and month == today.month:
                await context.bot.send_message(
                    chat_id=int(uid),
                    text=(
                        f"🎂 Волны Вселенной сходятся сегодня в твою честь!\n"
                        f"С Днём рождения 🌟 Пусть новый год твоей жизни принесёт "
                        f"гармонию, вдохновение и чудеса ✨\n\n"
                        f"🔮 Твоя карта дня уже ждёт тебя — нажми «Карта дня» 🌙"
                    ),
                    reply_markup=reply_keyboard()
                )
        except Exception:
            continue



# ================== MAIN ==================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN_TEST).build()

    # Хендлеры
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("resetday", resetday))  # если используешь
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("✅ Обработчики зарегистрированы:")
    for handler in app.handlers[0]:
        print(f"   - {handler}")
    # 🔔 Ежедневная рассылка в 07:30 по Москве
    jq = app.job_queue

    if jq is not None:
        jq.run_daily(
            morning_broadcast,
            time=time(7, 30),
            days=(0, 1, 2, 3, 4, 5, 6),
        )
        jq.run_daily(
            birthday_broadcast,
            time=time(7, 30),
            days=(0, 1, 2, 3, 4, 5, 6),
        )
    else:
        print("Предупреждение: JobQueue не доступен. Ежедневные рассылки отключены.")

    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
