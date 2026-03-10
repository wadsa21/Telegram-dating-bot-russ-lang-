"""
DaVinci Dating Bot 💫 — с Премиумом за Звёзды Telegram
Требования: pip install aiogram==3.x aiosqlite
Запуск: python bot.py

Telegram Stars (⭐️) — встроенная валюта Telegram.
Оплата работает через стандартный Invoice API прямо в боте.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup,
    InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton,
    ReplyKeyboardRemove, LabeledPrice, PreCheckoutQuery
)
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import aiosqlite

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = "твой бот токен"   # @BotFather → /newbot
DB_PATH   = "davinci.db"

# Цены в Telegram Stars (1 звезда ≈ $0.013)
PREMIUM_PLANS = {
    "week": {
        "label":  "⭐ Неделя",
        "stars":  75,
        "days":   7,
        "title":  "DaVinci Premium — 7 дней",
        "desc":   "Безлимитные лайки, суперлайки, «кто лайкнул», поднятие анкеты",
    },
    "month": {
        "label":  "⭐⭐ Месяц",
        "stars":  250,
        "days":   30,
        "title":  "DaVinci Premium — 30 дней",
        "desc":   "Безлимитные лайки, суперлайки, «кто лайкнул», поднятие анкеты",
    },
    "forever": {
        "label":  "👑 Навсегда",
        "stars":  999,
        "days":   36500,   # 100 лет ≈ навсегда
        "title":  "DaVinci Premium — Навсегда",
        "desc":   "Все возможности без ограничений навсегда",
    },
}

# Лимиты для бесплатных пользователей
FREE_LIKES_PER_DAY = 10

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher(storage=MemoryStorage())


# ========== СОСТОЯНИЯ FSM ==========
class Registration(StatesGroup):
    name        = State()
    age         = State()
    gender      = State()
    looking_for = State()
    city        = State()
    about       = State()
    photo       = State()

class Browsing(StatesGroup):
    viewing = State()

class Chatting(StatesGroup):
    messaging = State()


# ========== БАЗА ДАННЫХ ==========
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id       INTEGER PRIMARY KEY,
                username      TEXT,
                name          TEXT,
                age           INTEGER,
                gender        TEXT,
                looking_for   TEXT,
                city          TEXT,
                about         TEXT,
                photo_id      TEXT,
                active        INTEGER DEFAULT 1,
                premium_until TIMESTAMP,
                likes_today   INTEGER DEFAULT 0,
                likes_date    TEXT DEFAULT '',
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS likes (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                from_user  INTEGER,
                to_user    INTEGER,
                action     TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(from_user, to_user)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS matches (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user1      INTEGER,
                user2      INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user1, user2)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER,
                plan       TEXT,
                stars      INTEGER,
                payload    TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()

# ---------- пользователи ----------

async def save_user(user_id, username, data: dict):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO users
            (user_id, username, name, age, gender, looking_for, city, about, photo_id)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (user_id, username,
              data['name'], data['age'], data['gender'],
              data['looking_for'], data['city'], data['about'], data['photo']))
        await db.commit()

async def get_user(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)) as cur:
            return await cur.fetchone()

# ---------- премиум ----------

async def is_premium(user_id) -> bool:
    user = await get_user(user_id)
    if not user or not user['premium_until']:
        return False
    return datetime.fromisoformat(user['premium_until']) > datetime.utcnow()

async def activate_premium(user_id, days: int):
    until = datetime.utcnow() + timedelta(days=days)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET premium_until=? WHERE user_id=?",
            (until.isoformat(), user_id)
        )
        await db.commit()

async def save_payment(user_id, plan, stars, payload):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO payments (user_id,plan,stars,payload) VALUES (?,?,?,?)",
            (user_id, plan, stars, payload)
        )
        await db.commit()

# ---------- лимиты лайков ----------

async def can_like(user_id) -> bool:
    if await is_premium(user_id):
        return True
    user  = await get_user(user_id)
    today = datetime.utcnow().strftime("%Y-%m-%d")
    if user['likes_date'] != today:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE users SET likes_today=0, likes_date=? WHERE user_id=?",
                (today, user_id)
            )
            await db.commit()
        return True
    return user['likes_today'] < FREE_LIKES_PER_DAY

async def increment_likes(user_id):
    today = datetime.utcnow().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE users
            SET likes_today = CASE WHEN likes_date=? THEN likes_today+1 ELSE 1 END,
                likes_date  = ?
            WHERE user_id=?
        """, (today, today, user_id))
        await db.commit()

async def likes_left(user_id) -> int:
    if await is_premium(user_id):
        return 9999
    user  = await get_user(user_id)
    today = datetime.utcnow().strftime("%Y-%m-%d")
    if user['likes_date'] != today:
        return FREE_LIKES_PER_DAY
    return max(0, FREE_LIKES_PER_DAY - user['likes_today'])

# ---------- анкеты / мэтчи ----------

async def get_next_profile(viewer_id, gender_filter):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM users
            WHERE user_id != ?
              AND active = 1
              AND gender = ?
              AND user_id NOT IN (SELECT to_user FROM likes WHERE from_user=?)
            ORDER BY RANDOM()
            LIMIT 1
        """, (viewer_id, gender_filter, viewer_id)) as cur:
            return await cur.fetchone()

async def save_action(from_user, to_user, action):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR IGNORE INTO likes (from_user, to_user, action) VALUES (?,?,?)
        """, (from_user, to_user, action))
        await db.commit()

async def check_match(user1, user2) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT COUNT(*) FROM likes
            WHERE from_user=? AND to_user=? AND action IN ('like','superlike')
        """, (user2, user1)) as cur:
            row = await cur.fetchone()
            if row[0] > 0:
                u1, u2 = min(user1, user2), max(user1, user2)
                await db.execute(
                    "INSERT OR IGNORE INTO matches (user1,user2) VALUES (?,?)", (u1, u2)
                )
                await db.commit()
                return True
    return False

async def get_who_liked_me(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT u.* FROM users u
            JOIN likes l ON l.from_user = u.user_id
            WHERE l.to_user = ?
              AND l.action IN ('like','superlike')
              AND u.user_id NOT IN (
                  SELECT to_user FROM likes WHERE from_user=?
              )
        """, (user_id, user_id)) as cur:
            return await cur.fetchall()

async def get_matches(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT u.* FROM users u
            JOIN matches m ON (
                (m.user1=? AND m.user2=u.user_id) OR
                (m.user2=? AND m.user1=u.user_id)
            )
            WHERE u.active=1
        """, (user_id, user_id)) as cur:
            return await cur.fetchall()


# ========== КЛАВИАТУРЫ ==========

def main_menu_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="👁 Смотреть анкеты")],
        [KeyboardButton(text="👤 Мой профиль"), KeyboardButton(text="💞 Мои мэтчи")],
        [KeyboardButton(text="👑 Премиум"),      KeyboardButton(text="⚙️ Настройки")],
    ], resize_keyboard=True)

def gender_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="👨 Мужчина"), KeyboardButton(text="👩 Женщина")]
    ], resize_keyboard=True, one_time_keyboard=True)

def looking_for_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="👩 Девушку"), KeyboardButton(text="👨 Парня")],
        [KeyboardButton(text="🌈 Всех")]
    ], resize_keyboard=True, one_time_keyboard=True)

def like_dislike_kb(profile_id, show_superlike=False):
    rows = [[
        InlineKeyboardButton(text="👻 Дизлайк",   callback_data=f"dislike_{profile_id}"),
        InlineKeyboardButton(text="❤️ Лайк",      callback_data=f"like_{profile_id}"),
    ]]
    if show_superlike:
        rows.append([
            InlineKeyboardButton(text="💫 Суперлайк", callback_data=f"superlike_{profile_id}"),
        ])
    rows.append([
        InlineKeyboardButton(text="⏭ Пропустить", callback_data=f"skip_{profile_id}"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def premium_plans_kb():
    buttons = []
    for key, plan in PREMIUM_PLANS.items():
        buttons.append([InlineKeyboardButton(
            text=f"{plan['label']} — {plan['stars']} ⭐",
            callback_data=f"buy_{key}"
        )])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def matches_kb(matches):
    buttons = []
    for m in matches:
        buttons.append([InlineKeyboardButton(
            text=f"💬 {m['name']}, {m['age']}",
            callback_data=f"chat_{m['user_id']}"
        )])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def who_liked_me_kb(likers):
    buttons = []
    for u in likers:
        buttons.append([InlineKeyboardButton(
            text=f"👀 {u['name']}, {u['age']}",
            callback_data=f"viewliker_{u['user_id']}"
        )])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ========== ХЕНДЛЕРЫ ==========

# ---------- /start ----------

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    user = await get_user(message.from_user.id)
    if user:
        prem  = await is_premium(message.from_user.id)
        badge = " 👑" if prem else ""
        await message.answer(
            f"✨ С возвращением, *{user['name']}*{badge}!\n\nЧто будем делать?",
            parse_mode="Markdown",
            reply_markup=main_menu_kb()
        )
    else:
        await message.answer(
            "🎨 *Добро пожаловать в DaVinci* — место, где рождаются настоящие связи.\n\n"
            "Давай создадим твой профиль.\n\n"
            "Как тебя зовут?",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove()
        )
        await state.set_state(Registration.name)


# ---------- РЕГИСТРАЦИЯ ----------

@dp.message(Registration.name)
async def reg_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if len(name) < 2 or len(name) > 30:
        return await message.answer("Имя должно быть от 2 до 30 символов. Попробуй ещё раз:")
    await state.update_data(name=name)
    await message.answer(f"Приятно познакомиться, *{name}*! 👋\n\nСколько тебе лет?",
                         parse_mode="Markdown")
    await state.set_state(Registration.age)

@dp.message(Registration.age)
async def reg_age(message: Message, state: FSMContext):
    if not message.text.isdigit() or not (16 <= int(message.text) <= 80):
        return await message.answer("Введи возраст числом от 16 до 80:")
    await state.update_data(age=int(message.text))
    await message.answer("Твой пол?", reply_markup=gender_kb())
    await state.set_state(Registration.gender)

@dp.message(Registration.gender, F.text.in_(["👨 Мужчина", "👩 Женщина"]))
async def reg_gender(message: Message, state: FSMContext):
    gender = "male" if "Мужчина" in message.text else "female"
    await state.update_data(gender=gender)
    await message.answer("Кого ищешь?", reply_markup=looking_for_kb())
    await state.set_state(Registration.looking_for)

@dp.message(Registration.looking_for, F.text.in_(["👩 Девушку", "👨 Парня", "🌈 Всех"]))
async def reg_looking_for(message: Message, state: FSMContext):
    mapping = {"👩 Девушку": "female", "👨 Парня": "male", "🌈 Всех": "any"}
    await state.update_data(looking_for=mapping[message.text])
    await message.answer("В каком городе ты живёшь?", reply_markup=ReplyKeyboardRemove())
    await state.set_state(Registration.city)

@dp.message(Registration.city)
async def reg_city(message: Message, state: FSMContext):
    await state.update_data(city=message.text.strip())
    await message.answer(
        "Расскажи немного о себе — интересы, хобби, чего ищешь:\n_(до 300 символов)_",
        parse_mode="Markdown"
    )
    await state.set_state(Registration.about)

@dp.message(Registration.about)
async def reg_about(message: Message, state: FSMContext):
    await state.update_data(about=message.text.strip()[:300])
    await message.answer("Отправь своё фото для анкеты 📸")
    await state.set_state(Registration.photo)

@dp.message(Registration.photo, F.photo)
async def reg_photo(message: Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    await state.update_data(photo=photo_id)
    data = await state.get_data()
    await save_user(message.from_user.id, message.from_user.username, data)
    await state.clear()

    gender_text = "Мужчина" if data['gender'] == 'male' else "Женщина"
    looking_map = {"female": "Девушку", "male": "Парня", "any": "Всех"}

    await message.answer_photo(
        photo_id,
        caption=(
            f"✅ *Анкета создана!*\n\n"
            f"👤 *{data['name']}*, {data['age']} лет\n"
            f"📍 {data['city']}\n"
            f"⚧ {gender_text} · Ищу: {looking_map[data['looking_for']]}\n\n"
            f"_{data['about']}_"
        ),
        parse_mode="Markdown",
        reply_markup=main_menu_kb()
    )


# ---------- ПРОСМОТР АНКЕТ ----------

@dp.message(F.text == "👁 Смотреть анкеты")
async def browse_profiles(message: Message, state: FSMContext):
    viewer = await get_user(message.from_user.id)
    if not viewer:
        return await message.answer("Сначала создай анкету! /start")

    gender_filter = viewer['looking_for']
    if gender_filter == 'any':
        gender_filter = 'female' if viewer['gender'] == 'male' else 'male'

    profile = await get_next_profile(message.from_user.id, gender_filter)
    if not profile:
        return await message.answer("😔 Анкеты закончились. Загляни позже!",
                                    reply_markup=main_menu_kb())

    prem = await is_premium(message.from_user.id)
    left = await likes_left(message.from_user.id)
    hint = "👑 *Премиум:* безлимитные лайки" if prem else \
           f"❤️ Осталось лайков сегодня: *{left}/{FREE_LIKES_PER_DAY}*"
    await message.answer(hint, parse_mode="Markdown")
    await state.set_state(Browsing.viewing)
    await show_profile(message.chat.id, profile, prem)

async def show_profile(chat_id, profile, premium=False):
    caption = (
        f"✨ *{profile['name']}*, {profile['age']} лет\n"
        f"📍 {profile['city']}\n\n"
        f"_{profile['about']}_"
    )
    await bot.send_photo(
        chat_id,
        photo=profile['photo_id'],
        caption=caption,
        parse_mode="Markdown",
        reply_markup=like_dislike_kb(profile['user_id'], show_superlike=premium)
    )

async def _next_or_end(chat_id, viewer_id, state):
    viewer = await get_user(viewer_id)
    gender_filter = viewer['looking_for'] if viewer['looking_for'] != 'any' else (
        'female' if viewer['gender'] == 'male' else 'male'
    )
    profile = await get_next_profile(viewer_id, gender_filter)
    prem    = await is_premium(viewer_id)
    if profile:
        await show_profile(chat_id, profile, prem)
    else:
        await bot.send_message(chat_id, "😔 Анкеты закончились!", reply_markup=main_menu_kb())
        await state.clear()

# Лайк
@dp.callback_query(F.data.startswith("like_"))
async def handle_like(callback: CallbackQuery, state: FSMContext):
    to_user   = int(callback.data.split("_")[1])
    from_user = callback.from_user.id

    if not await can_like(from_user):
        await callback.answer(
            f"❌ Лимит {FREE_LIKES_PER_DAY} лайков/день исчерпан.\n"
            "Купи Премиум для безлимитных лайков! 👑",
            show_alert=True
        )
        return

    await save_action(from_user, to_user, 'like')
    await increment_likes(from_user)
    await callback.message.delete()

    if await check_match(from_user, to_user):
        await _notify_match(from_user, to_user)
        return

    await callback.answer("❤️")
    await _next_or_end(callback.message.chat.id, from_user, state)

# Суперлайк (только премиум)
@dp.callback_query(F.data.startswith("superlike_"))
async def handle_superlike(callback: CallbackQuery, state: FSMContext):
    to_user   = int(callback.data.split("_")[1])
    from_user = callback.from_user.id

    if not await is_premium(from_user):
        await callback.answer("👑 Суперлайки доступны только в Премиуме!", show_alert=True)
        return

    await save_action(from_user, to_user, 'superlike')
    await callback.message.delete()

    sender = await get_user(from_user)
    try:
        await bot.send_message(
            to_user,
            f"💫 *{sender['name']}* поставил(а) тебе суперлайк! Ты ему нравишься 😏",
            parse_mode="Markdown"
        )
    except Exception:
        pass

    if await check_match(from_user, to_user):
        await _notify_match(from_user, to_user)
        return

    await callback.answer("💫 Суперлайк!")
    await _next_or_end(callback.message.chat.id, from_user, state)

# Дизлайк / Пропустить
@dp.callback_query(F.data.startswith("dislike_") | F.data.startswith("skip_"))
async def handle_dislike(callback: CallbackQuery, state: FSMContext):
    parts   = callback.data.split("_")
    to_user = int(parts[1])
    action  = 'dislike' if 'dislike' in callback.data else 'skip'

    await save_action(callback.from_user.id, to_user, action)
    await callback.message.delete()
    await callback.answer("👻" if action == 'dislike' else "⏭")
    await _next_or_end(callback.message.chat.id, callback.from_user.id, state)

async def _notify_match(user1_id, user2_id):
    u1 = await get_user(user1_id)
    u2 = await get_user(user2_id)
    for uid, other in [(user1_id, u2), (user2_id, u1)]:
        try:
            await bot.send_message(
                uid,
                f"🎉 *Мэтч!* Вы понравились друг другу с *{other['name']}*!\n\n"
                f"Нажми «💞 Мои мэтчи» чтобы написать.",
                parse_mode="Markdown",
                reply_markup=main_menu_kb()
            )
        except Exception:
            pass


# ========== ПРЕМИУМ ==========

@dp.message(F.text == "👑 Премиум")
async def show_premium(message: Message):
    prem = await is_premium(message.from_user.id)

    if prem:
        user  = await get_user(message.from_user.id)
        until = datetime.fromisoformat(user['premium_until']).strftime("%d.%m.%Y")
        await message.answer(
            f"👑 *У тебя активен Премиум* до {until}!\n\n"
            "✅ Безлимитные лайки\n"
            "✅ Суперлайки 💫\n"
            "✅ Кто меня лайкнул 👀\n"
            "✅ Поднятие анкеты 🔝",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="👀 Кто меня лайкнул", callback_data="who_liked"),
                InlineKeyboardButton(text="🔝 Поднять анкету",   callback_data="boost"),
            ]])
        )
    else:
        await message.answer(
            "👑 *DaVinci Premium*\n\n"
            "Разблокируй все возможности:\n\n"
            "❤️ Безлимитные лайки _(сейчас: 10/день)_\n"
            "💫 Суперлайки — тебя заметят первым\n"
            "👀 Смотри кто уже лайкнул тебя\n"
            "🔝 Поднятие анкеты в топ\n\n"
            "💳 Оплата в ⭐ Telegram Stars — мгновенно и безопасно:",
            parse_mode="Markdown",
            reply_markup=premium_plans_kb()
        )

# Нажата кнопка плана → отправить инвойс
@dp.callback_query(F.data.startswith("buy_"))
async def buy_plan(callback: CallbackQuery):
    key  = callback.data.split("_", 1)[1]
    plan = PREMIUM_PLANS.get(key)
    if not plan:
        return await callback.answer("Неизвестный план")

    payload = f"premium_{key}_{callback.from_user.id}"

    # XTR = код валюты Telegram Stars
    # provider_token оставляем пустым — Stars не требует провайдера
    await bot.send_invoice(
        chat_id       = callback.from_user.id,
        title         = plan['title'],
        description   = plan['desc'],
        payload       = payload,
        currency      = "XTR",
        prices        = [LabeledPrice(label=plan['title'], amount=plan['stars'])],
        provider_token= "",          # Пустой для Stars
    )
    await callback.answer()

# Pre-checkout — всегда подтверждаем
@dp.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)

# Успешная оплата ⭐
@dp.message(F.successful_payment)
async def successful_payment(message: Message):
    payment  = message.successful_payment
    payload  = payment.invoice_payload        # "premium_week_123456"
    plan_key = payload.split("_")[1]          # "week" | "month" | "forever"
    plan     = PREMIUM_PLANS.get(plan_key)

    if not plan:
        return await message.answer("⚠️ Ошибка обработки платежа. Обратись в поддержку.")

    await activate_premium(message.from_user.id, plan['days'])
    await save_payment(message.from_user.id, plan_key, payment.total_amount, payload)

    until_label = (
        "навсегда 👑" if plan_key == "forever"
        else (datetime.utcnow() + timedelta(days=plan['days'])).strftime("до %d.%m.%Y")
    )

    await message.answer(
        f"✅ *Оплата прошла!* {payment.total_amount} ⭐\n\n"
        f"👑 *DaVinci Premium* активирован {until_label}!\n\n"
        "Теперь тебе доступны:\n"
        "❤️ Безлимитные лайки\n"
        "💫 Суперлайки\n"
        "👀 Просмотр тех, кто лайкнул\n"
        "🔝 Поднятие анкеты",
        parse_mode="Markdown",
        reply_markup=main_menu_kb()
    )

# Кто меня лайкнул (только премиум)
@dp.callback_query(F.data == "who_liked")
async def who_liked(callback: CallbackQuery):
    if not await is_premium(callback.from_user.id):
        await callback.answer("👑 Только для Premium!", show_alert=True)
        return
    likers = await get_who_liked_me(callback.from_user.id)
    if not likers:
        await callback.message.answer("😔 Пока никто тебя не лайкнул.")
    else:
        await callback.message.answer(
            f"👀 *Тебя лайкнули* ({len(likers)}):",
            parse_mode="Markdown",
            reply_markup=who_liked_me_kb(likers)
        )
    await callback.answer()

# Поднятие анкеты (только премиум)
@dp.callback_query(F.data == "boost")
async def boost_profile(callback: CallbackQuery):
    if not await is_premium(callback.from_user.id):
        await callback.answer("👑 Только для Premium!", show_alert=True)
        return
    # Здесь можно добавить логику поднятия (например, обновить поле boost_until в БД)
    await callback.message.answer(
        "🔝 *Анкета поднята в топ* на 1 час!\n\nТебя увидят больше людей ✨",
        parse_mode="Markdown"
    )
    await callback.answer("🔝 Поднято!")

# Просмотр анкеты лайкнувшего
@dp.callback_query(F.data.startswith("viewliker_"))
async def view_liker(callback: CallbackQuery):
    liker_id = int(callback.data.split("_")[1])
    profile  = await get_user(liker_id)
    if not profile:
        return await callback.answer("Анкета не найдена")
    caption = (
        f"✨ *{profile['name']}*, {profile['age']} лет\n"
        f"📍 {profile['city']}\n\n_{profile['about']}_"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❤️ Лайкнуть в ответ", callback_data=f"like_{liker_id}"),
        InlineKeyboardButton(text="👻 Пропустить",        callback_data=f"skip_{liker_id}"),
    ]])
    await bot.send_photo(callback.from_user.id, profile['photo_id'],
                         caption=caption, parse_mode="Markdown", reply_markup=kb)
    await callback.answer()


# ---------- МОЙ ПРОФИЛЬ ----------

@dp.message(F.text == "👤 Мой профиль")
async def my_profile(message: Message):
    user = await get_user(message.from_user.id)
    if not user:
        return await message.answer("Анкета не найдена. /start")

    prem        = await is_premium(message.from_user.id)
    badge       = " 👑" if prem else ""
    gender_text = "Мужчина" if user['gender'] == 'male' else "Женщина"
    looking_map = {"female": "Девушку", "male": "Парня", "any": "Всех"}

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✏️ Редактировать", callback_data="edit_profile"),
        InlineKeyboardButton(text="🗑 Удалить анкету",  callback_data="delete_profile"),
    ]])
    await message.answer_photo(
        user['photo_id'],
        caption=(
            f"👤 *{user['name']}*{badge}, {user['age']} лет\n"
            f"📍 {user['city']}\n"
            f"⚧ {gender_text} · Ищу: {looking_map[user['looking_for']]}\n\n"
            f"_{user['about']}_"
        ),
        parse_mode="Markdown",
        reply_markup=kb
    )


# ---------- МОИ МЭТЧИ ----------

@dp.message(F.text == "💞 Мои мэтчи")
async def my_matches(message: Message):
    matches = await get_matches(message.from_user.id)
    if not matches:
        return await message.answer(
            "💔 У тебя пока нет мэтчей.\n\nПродолжай смотреть анкеты!",
            reply_markup=main_menu_kb()
        )
    await message.answer(
        f"💞 *Твои мэтчи* ({len(matches)}):\n\nВыбери с кем написать:",
        parse_mode="Markdown",
        reply_markup=matches_kb(matches)
    )

@dp.callback_query(F.data.startswith("chat_"))
async def start_chat(callback: CallbackQuery, state: FSMContext):
    target_id = int(callback.data.split("_")[1])
    target    = await get_user(target_id)
    await state.update_data(chatting_with=target_id)
    await state.set_state(Chatting.messaging)
    await callback.message.answer(
        f"💬 Чат с *{target['name']}*\n\nПиши сообщение — оно будет доставлено!",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="🔙 Выйти из чата")]],
            resize_keyboard=True
        )
    )
    await callback.answer()

@dp.message(Chatting.messaging, F.text == "🔙 Выйти из чата")
async def exit_chat(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Вышел из чата.", reply_markup=main_menu_kb())

@dp.message(Chatting.messaging)
async def relay_message(message: Message, state: FSMContext):
    data      = await state.get_data()
    target_id = data.get('chatting_with')
    if not target_id:
        return
    sender = await get_user(message.from_user.id)
    try:
        if message.photo:
            await bot.send_photo(target_id, message.photo[-1].file_id,
                                 caption=f"💌 *{sender['name']}*: {message.caption or ''}",
                                 parse_mode="Markdown")
        else:
            await bot.send_message(target_id,
                                   f"💌 *{sender['name']}*: {message.text}",
                                   parse_mode="Markdown")
        await message.answer("✅ Отправлено!")
    except Exception:
        await message.answer("❌ Не удалось доставить сообщение.")


# ---------- УТИЛИТЫ ----------

@dp.callback_query(F.data == "delete_profile")
async def delete_profile(callback: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET active=0 WHERE user_id=?",
                         (callback.from_user.id,))
        await db.commit()
    await callback.message.answer(
        "🗑 Анкета скрыта. Напиши /start чтобы создать новую.",
        reply_markup=ReplyKeyboardRemove()
    )
    await callback.answer()

@dp.callback_query(F.data == "back_menu")
async def back_to_menu(callback: CallbackQuery):
    await callback.message.delete()
    await callback.answer()


# ========== ЗАПУСК ==========
async def main():
    await init_db()
    print("🎨 DaVinci Bot запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
