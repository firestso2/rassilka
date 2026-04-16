"""
SpamBot — Telegram бот для управления рассылкой через userbot-аккаунты.
Управляется через aiogram 3.x, userbot-аккаунты работают через Telethon.
"""

import asyncio
import logging
import os
import re
import random
import pytz
from datetime import datetime, time as dtime

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
)

from db import Database
from userbot import UserbotManager
from config import BOT_TOKEN, ADMIN_IDS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
db = Database()
ubm = UserbotManager(db)

MSK = pytz.timezone("Europe/Moscow")


# ─────────────────────────────────────────────
# FSM States
# ─────────────────────────────────────────────
class AddAccount(StatesGroup):
    waiting_api_id = State()
    waiting_api_hash = State()
    waiting_phone = State()
    waiting_code = State()
    waiting_2fa = State()


class CreateFlood(StatesGroup):
    waiting_chats = State()
    waiting_count = State()
    waiting_delay = State()
    waiting_text = State()
    waiting_stop_time = State()


class CreateFolderFlood(StatesGroup):
    waiting_folder = State()
    waiting_count = State()
    waiting_delay = State()
    waiting_text = State()
    waiting_stop_time = State()


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def spin_text(text: str) -> str:
    """Заменяет ?$(вар1|вар2|вар3) на случайный вариант."""
    pattern = r'\?\$\(([^)]+)\)'
    def replacer(m):
        variants = m.group(1).split('|')
        return random.choice(variants).strip()
    return re.sub(pattern, replacer, text)


def parse_stop_time(raw: str):
    """Парсит диапазон вида 23:00-12:00, возвращает (start_h, start_m, end_h, end_m) или None."""
    raw = raw.strip()
    m = re.match(r'^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$', raw)
    if not m:
        return None
    sh, sm, eh, em = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    return sh, sm, eh, em


def is_in_stop_window(sh, sm, eh, em) -> bool:
    """Проверяет, находится ли текущее московское время в окне остановки."""
    now = datetime.now(MSK).time()
    start = dtime(sh, sm)
    end = dtime(eh, em)
    if start <= end:
        return start <= now <= end
    else:  # переход через полночь
        return now >= start or now <= end


def kb_main():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📱 Аккаунты"), KeyboardButton(text="📋 Задачи")],
        [KeyboardButton(text="📢 Флуд по чатам"), KeyboardButton(text="📁 Флуд по папке")],
        [KeyboardButton(text="🗑 Удалить сообщения"), KeyboardButton(text="❓ Помощь")],
    ], resize_keyboard=True)


def kb_accounts(accounts):
    buttons = []
    for acc in accounts:
        status = "🟢" if acc['connected'] else "🔴"
        buttons.append([InlineKeyboardButton(
            text=f"{status} +{acc['phone']} (id={acc['id']})",
            callback_data=f"acc_info:{acc['id']}"
        )])
    buttons.append([InlineKeyboardButton(text="➕ Добавить аккаунт", callback_data="acc_add")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def kb_task_actions(task_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⏸ Пауза", callback_data=f"task_pause:{task_id}"),
            InlineKeyboardButton(text="▶️ Продолжить", callback_data=f"task_resume:{task_id}"),
            InlineKeyboardButton(text="⏹ Стоп", callback_data=f"task_stop:{task_id}"),
        ]
    ])


# ─────────────────────────────────────────────
# /start
# ─────────────────────────────────────────────
@dp.message(Command("start"))
async def cmd_start(msg: Message, state: FSMContext):
    await state.clear()
    user_id = msg.from_user.id
    db.ensure_user(user_id)
    await msg.answer(
        "👋 <b>SpamBot</b> — управление рассылкой через Telegram-аккаунты.\n\n"
        "Подписка активна для всех пользователей 🎉\n\n"
        "Используй кнопки ниже для управления:",
        parse_mode="HTML",
        reply_markup=kb_main()
    )


@dp.message(Command("help"))
@dp.message(F.text == "❓ Помощь")
async def cmd_help(msg: Message):
    await msg.answer(
        "<b>Доступные функции:</b>\n\n"
        "📱 <b>Аккаунты</b> — добавить/удалить Telegram-аккаунты (API ID + API Hash)\n"
        "📢 <b>Флуд по чатам</b> — рассылка по указанным чатам/юзернеймам\n"
        "📁 <b>Флуд по папке</b> — рассылка по чатам из папки Telegram\n"
        "📋 <b>Задачи</b> — список активных рассылок, пауза/стоп\n"
        "🗑 <b>Удалить сообщения</b> — удалить последние N сообщений в чате\n\n"
        "<b>Спин-текст:</b>\n"
        "В тексте рассылки используй <code>?$(вариант1|вариант2|вариант3)</code> — "
        "при каждой отправке бот выберет случайный вариант.\n\n"
        "<b>Пример:</b> <code>?$(Привет|Хай|Здарова), как дела?</code>\n\n"
        "<b>Стоп по времени:</b>\n"
        "При создании рассылки можно задать период остановки в формате <code>23:00-09:00</code> (МСК). "
        "В это время рассылка будет приостанавливаться автоматически.",
        parse_mode="HTML"
    )


# ─────────────────────────────────────────────
# Accounts
# ─────────────────────────────────────────────
@dp.message(F.text == "📱 Аккаунты")
async def accounts_list(msg: Message):
    user_id = msg.from_user.id
    accounts = db.get_accounts(user_id)
    if not accounts:
        await msg.answer(
            "У вас нет добавленных аккаунтов.\nНажмите кнопку ниже, чтобы добавить.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Добавить аккаунт", callback_data="acc_add")]
            ])
        )
    else:
        await msg.answer("Ваши аккаунты:", reply_markup=kb_accounts(accounts))


@dp.callback_query(F.data == "acc_add")
async def cb_acc_add(cb: CallbackQuery, state: FSMContext):
    await cb.message.answer(
        "Введите <b>API ID</b> вашего Telegram-аккаунта.\n\n"
        "Получить можно на https://my.telegram.org → App configuration",
        parse_mode="HTML"
    )
    await state.set_state(AddAccount.waiting_api_id)
    await cb.answer()


@dp.message(AddAccount.waiting_api_id)
async def acc_got_api_id(msg: Message, state: FSMContext):
    api_id = msg.text.strip()
    if not api_id.isdigit():
        await msg.answer("❌ API ID должен быть числом. Попробуйте ещё раз:")
        return
    await state.update_data(api_id=int(api_id))
    await msg.answer("Теперь введите <b>API Hash</b>:", parse_mode="HTML")
    await state.set_state(AddAccount.waiting_api_hash)


@dp.message(AddAccount.waiting_api_hash)
async def acc_got_api_hash(msg: Message, state: FSMContext):
    api_hash = msg.text.strip()
    await state.update_data(api_hash=api_hash)
    await msg.answer("Введите номер телефона (формат: <code>+79001234567</code>):", parse_mode="HTML")
    await state.set_state(AddAccount.waiting_phone)


@dp.message(AddAccount.waiting_phone)
async def acc_got_phone(msg: Message, state: FSMContext):
    phone = msg.text.strip()
    data = await state.get_data()
    api_id = data['api_id']
    api_hash = data['api_hash']

    status_msg = await msg.answer("⏳ Подключаюсь к Telegram и отправляю код...")

    try:
        phone_hash = await ubm.send_code(api_id, api_hash, phone)
        await state.update_data(phone=phone, phone_hash=phone_hash)
        await status_msg.edit_text(
            "📲 Код отправлен на ваш Telegram.\nВведите код (формат: <code>1 2 3 4 5</code> или <code>12345</code>):",
            parse_mode="HTML"
        )
        await state.set_state(AddAccount.waiting_code)
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка при отправке кода: {e}")
        await state.clear()


@dp.message(AddAccount.waiting_code)
async def acc_got_code(msg: Message, state: FSMContext):
    code = msg.text.strip().replace(" ", "")
    data = await state.get_data()

    status_msg = await msg.answer("⏳ Проверяю код...")
    try:
        result = await ubm.sign_in(
            data['api_id'], data['api_hash'],
            data['phone'], data['phone_hash'], code
        )
        if result == "2fa":
            await status_msg.edit_text("🔐 Введите пароль двухфакторной аутентификации:")
            await state.set_state(AddAccount.waiting_2fa)
        else:
            # result is (client, session_string)
            client, session_string = result
            acc_id = db.add_account(
                msg.from_user.id, data['api_id'], data['api_hash'],
                data['phone'], session_string
            )
            ubm.store_client(acc_id, client)
            await status_msg.edit_text(
                f"✅ Аккаунт <code>{data['phone']}</code> успешно добавлен!",
                parse_mode="HTML"
            )
            await state.clear()
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка: {e}\nПопробуйте добавить аккаунт заново.")
        await state.clear()


@dp.message(AddAccount.waiting_2fa)
async def acc_got_2fa(msg: Message, state: FSMContext):
    password = msg.text.strip()
    data = await state.get_data()

    status_msg = await msg.answer("⏳ Проверяю пароль...")
    try:
        client, session_string = await ubm.sign_in_2fa(
            data['api_id'], data['api_hash'], data['phone'], password
        )
        acc_id = db.add_account(
            msg.from_user.id, data['api_id'], data['api_hash'],
            data['phone'], session_string
        )
        ubm.store_client(acc_id, client)
        await status_msg.edit_text(
            f"✅ Аккаунт <code>{data['phone']}</code> успешно добавлен!",
            parse_mode="HTML"
        )
        await state.clear()
    except Exception as e:
        await status_msg.edit_text(f"❌ Неверный пароль или ошибка: {e}")
        await state.clear()


@dp.callback_query(F.data.startswith("acc_info:"))
async def cb_acc_info(cb: CallbackQuery):
    acc_id = int(cb.data.split(":")[1])
    acc = db.get_account(acc_id)
    if not acc or acc['user_id'] != cb.from_user.id:
        await cb.answer("Аккаунт не найден.", show_alert=True)
        return

    status = "🟢 Подключён" if acc['connected'] else "🔴 Не подключён"
    await cb.message.answer(
        f"📱 <b>Аккаунт #{acc_id}</b>\n"
        f"Телефон: <code>{acc['phone']}</code>\n"
        f"API ID: <code>{acc['api_id']}</code>\n"
        f"Статус: {status}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Удалить аккаунт", callback_data=f"acc_del:{acc_id}")]
        ])
    )
    await cb.answer()


@dp.callback_query(F.data.startswith("acc_del:"))
async def cb_acc_del(cb: CallbackQuery):
    acc_id = int(cb.data.split(":")[1])
    acc = db.get_account(acc_id)
    if not acc or acc['user_id'] != cb.from_user.id:
        await cb.answer("Аккаунт не найден.", show_alert=True)
        return
    await ubm.disconnect_account(acc_id)
    db.delete_account(acc_id)
    await cb.message.edit_text(f"🗑 Аккаунт #{acc_id} удалён.")
    await cb.answer()


# ─────────────────────────────────────────────
# Flood (по чатам)
# ─────────────────────────────────────────────
@dp.message(F.text == "📢 Флуд по чатам")
async def flood_start(msg: Message, state: FSMContext):
    user_id = msg.from_user.id
    accounts = db.get_accounts(user_id, connected_only=True)
    if not accounts:
        await msg.answer(
            "❌ Нет подключённых аккаунтов. Добавьте аккаунт через меню '📱 Аккаунты'."
        )
        return

    # Выбор аккаунта
    buttons = [[InlineKeyboardButton(
        text=f"+{a['phone']}", callback_data=f"flood_acc:{a['id']}"
    )] for a in accounts]
    await msg.answer("Выберите аккаунт для рассылки:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@dp.callback_query(F.data.startswith("flood_acc:"))
async def cb_flood_acc(cb: CallbackQuery, state: FSMContext):
    acc_id = int(cb.data.split(":")[1])
    await state.update_data(acc_id=acc_id, flood_type="chats")
    await cb.message.answer(
        "Введите чаты/юзернеймы для рассылки — <b>по одному на строке</b>.\n"
        "Можно использовать username, ссылку или числовой ID чата.\n\n"
        "Пример:\n<code>@mychat\nhttps://t.me/mychat2\n-1001234567890</code>",
        parse_mode="HTML"
    )
    await state.set_state(CreateFlood.waiting_chats)
    await cb.answer()


@dp.message(CreateFlood.waiting_chats)
async def flood_got_chats(msg: Message, state: FSMContext):
    chats = [line.strip() for line in msg.text.strip().splitlines() if line.strip()]
    await state.update_data(chats=chats)
    await msg.answer("Сколько раз отправить сообщение в каждый чат? (число)")
    await state.set_state(CreateFlood.waiting_count)


@dp.message(CreateFlood.waiting_count)
async def flood_got_count(msg: Message, state: FSMContext):
    text = msg.text.strip()
    if not text.isdigit() or int(text) < 1:
        await msg.answer("❌ Введите целое положительное число:")
        return
    await state.update_data(count=int(text))
    await msg.answer(
        "Задержка между сообщениями в секундах (дробное число, например <code>3.5</code>).\n"
        "Введите <code>0</code> для максимальной скорости (не рекомендуется).",
        parse_mode="HTML"
    )
    await state.set_state(CreateFlood.waiting_delay)


@dp.message(CreateFlood.waiting_delay)
async def flood_got_delay(msg: Message, state: FSMContext):
    try:
        delay = float(msg.text.strip())
        if delay < 0:
            raise ValueError
    except ValueError:
        await msg.answer("❌ Введите число (например 1 или 2.5):")
        return
    await state.update_data(delay=delay)
    await msg.answer(
        "Введите текст рассылки.\n\n"
        "💡 Используй спин-текст: <code>?$(Привет|Хай|Здарова)</code> — "
        "при каждой отправке будет выбран случайный вариант.",
        parse_mode="HTML"
    )
    await state.set_state(CreateFlood.waiting_text)


@dp.message(CreateFlood.waiting_text)
async def flood_got_text(msg: Message, state: FSMContext):
    await state.update_data(text=msg.text)
    await msg.answer(
        "⏰ Задать период <b>остановки</b> рассылки (МСК)?\n\n"
        "Например: <code>23:00-09:00</code> — рассылка будет останавливаться с 23:00 до 09:00.\n\n"
        "Введите время или напишите <code>нет</code> чтобы пропустить.",
        parse_mode="HTML"
    )
    await state.set_state(CreateFlood.waiting_stop_time)


@dp.message(CreateFlood.waiting_stop_time)
async def flood_got_stop_time(msg: Message, state: FSMContext):
    raw = msg.text.strip().lower()
    stop_time = None
    if raw not in ("нет", "no", "skip", "-", ""):
        parsed = parse_stop_time(raw)
        if parsed is None:
            await msg.answer(
                "❌ Неверный формат. Введите время в формате <code>23:00-09:00</code> или <code>нет</code>:",
                parse_mode="HTML"
            )
            return
        stop_time = raw

    data = await state.get_data()
    await state.clear()

    # Запускаем задачу
    task_id = db.create_task(
        user_id=msg.from_user.id,
        acc_id=data['acc_id'],
        task_type="flood",
        chats=data.get('chats'),
        folder=data.get('folder'),
        count=data['count'],
        delay=data['delay'],
        text=data['text'],
        stop_time=stop_time
    )

    summary_chats = "\n".join(data.get('chats', [])[:5])
    if len(data.get('chats', [])) > 5:
        summary_chats += f"\n...и ещё {len(data['chats']) - 5}"

    stop_info = f"\n⏰ Остановка: <code>{stop_time}</code> (МСК)" if stop_time else ""

    status_msg = await msg.answer(
        f"🚀 <b>Рассылка #{task_id} запущена!</b>\n\n"
        f"Чаты:\n<code>{summary_chats}</code>\n"
        f"Повторений: <b>{data['count']}</b>\n"
        f"Задержка: <b>{data['delay']}с</b>"
        f"{stop_info}",
        parse_mode="HTML",
        reply_markup=kb_task_actions(task_id)
    )

    asyncio.create_task(
        run_flood_task(task_id, msg.from_user.id, data, stop_time, status_msg)
    )


# ─────────────────────────────────────────────
# Flood (по папке)
# ─────────────────────────────────────────────
@dp.message(F.text == "📁 Флуд по папке")
async def folder_flood_start(msg: Message, state: FSMContext):
    user_id = msg.from_user.id
    accounts = db.get_accounts(user_id, connected_only=True)
    if not accounts:
        await msg.answer("❌ Нет подключённых аккаунтов.")
        return

    buttons = [[InlineKeyboardButton(
        text=f"+{a['phone']}", callback_data=f"fflood_acc:{a['id']}"
    )] for a in accounts]
    await msg.answer("Выберите аккаунт:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@dp.callback_query(F.data.startswith("fflood_acc:"))
async def cb_fflood_acc(cb: CallbackQuery, state: FSMContext):
    acc_id = int(cb.data.split(":")[1])
    await state.update_data(acc_id=acc_id, flood_type="folder")

    # Получаем список папок
    status_msg = await cb.message.answer("⏳ Получаю список папок...")
    try:
        folders = await ubm.get_folders(acc_id)
        if not folders:
            await status_msg.edit_text("❌ Папки не найдены или аккаунт не подключён.")
            await state.clear()
        else:
            buttons = [[InlineKeyboardButton(
                text=f"📁 {f['title']}", callback_data=f"fflood_folder:{acc_id}:{f['id']}"
            )] for f in folders]
            await status_msg.edit_text(
                "Выберите папку для рассылки:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
            )
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка: {e}")
        await state.clear()
    await cb.answer()


@dp.callback_query(F.data.startswith("fflood_folder:"))
async def cb_fflood_folder(cb: CallbackQuery, state: FSMContext):
    _, acc_id, folder_id = cb.data.split(":")
    await state.update_data(acc_id=int(acc_id), folder_id=int(folder_id))
    await cb.message.answer("Сколько раз отправить сообщение в каждый чат?")
    await state.set_state(CreateFolderFlood.waiting_count)
    await cb.answer()


@dp.message(CreateFolderFlood.waiting_count)
async def fflood_got_count(msg: Message, state: FSMContext):
    if not msg.text.strip().isdigit():
        await msg.answer("❌ Введите число:")
        return
    await state.update_data(count=int(msg.text.strip()))
    await msg.answer("Задержка между сообщениями (секунды, например 3.5):")
    await state.set_state(CreateFolderFlood.waiting_delay)


@dp.message(CreateFolderFlood.waiting_delay)
async def fflood_got_delay(msg: Message, state: FSMContext):
    try:
        delay = float(msg.text.strip())
    except ValueError:
        await msg.answer("❌ Введите число:")
        return
    await state.update_data(delay=delay)
    await msg.answer(
        "Введите текст рассылки.\n"
        "💡 Спин-текст: <code>?$(Вариант1|Вариант2)</code>",
        parse_mode="HTML"
    )
    await state.set_state(CreateFolderFlood.waiting_text)


@dp.message(CreateFolderFlood.waiting_text)
async def fflood_got_text(msg: Message, state: FSMContext):
    await state.update_data(text=msg.text)
    await msg.answer(
        "⏰ Период остановки (МСК), например <code>23:00-09:00</code>, или <code>нет</code>:",
        parse_mode="HTML"
    )
    await state.set_state(CreateFolderFlood.waiting_stop_time)


@dp.message(CreateFolderFlood.waiting_stop_time)
async def fflood_got_stop_time(msg: Message, state: FSMContext):
    raw = msg.text.strip().lower()
    stop_time = None
    if raw not in ("нет", "no", "skip", "-", ""):
        parsed = parse_stop_time(raw)
        if parsed is None:
            await msg.answer("❌ Неверный формат. Пример: <code>23:00-09:00</code> или <code>нет</code>:", parse_mode="HTML")
            return
        stop_time = raw

    data = await state.get_data()
    await state.clear()

    # Получаем чаты из папки
    status_msg = await msg.answer("⏳ Получаю чаты из папки...")
    try:
        folder_chats = await ubm.get_folder_chats(data['acc_id'], data['folder_id'])
        if not folder_chats:
            await status_msg.edit_text("❌ В папке нет чатов.")
            return

        task_id = db.create_task(
            user_id=msg.from_user.id,
            acc_id=data['acc_id'],
            task_type="folder_flood",
            chats=folder_chats,
            folder=str(data['folder_id']),
            count=data['count'],
            delay=data['delay'],
            text=data['text'],
            stop_time=stop_time
        )

        stop_info = f"\n⏰ Остановка: <code>{stop_time}</code> (МСК)" if stop_time else ""
        await status_msg.edit_text(
            f"🚀 <b>Рассылка по папке #{task_id} запущена!</b>\n"
            f"Чатов в папке: <b>{len(folder_chats)}</b>\n"
            f"Повторений: <b>{data['count']}</b>\n"
            f"Задержка: <b>{data['delay']}с</b>"
            f"{stop_info}",
            parse_mode="HTML",
            reply_markup=kb_task_actions(task_id)
        )

        asyncio.create_task(
            run_flood_task(task_id, msg.from_user.id, {**data, 'chats': folder_chats}, stop_time, status_msg)
        )
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка: {e}")


# ─────────────────────────────────────────────
# Delete messages
# ─────────────────────────────────────────────
@dp.message(F.text == "🗑 Удалить сообщения")
async def delete_msgs_start(msg: Message):
    await msg.answer(
        "Чтобы удалить сообщения, отправьте команду в формате:\n"
        "<code>/del [аккаунт_id] [chat_username] [количество]</code>\n\n"
        "Пример: <code>/del 1 @mychat 10</code>",
        parse_mode="HTML"
    )


@dp.message(Command("del"))
async def cmd_del(msg: Message):
    parts = msg.text.split()
    if len(parts) != 4:
        await msg.answer("Формат: <code>/del [acc_id] [chat] [количество]</code>", parse_mode="HTML")
        return
    _, acc_id_s, chat, count_s = parts
    try:
        acc_id = int(acc_id_s)
        count = int(count_s)
    except ValueError:
        await msg.answer("❌ acc_id и количество должны быть числами.")
        return

    acc = db.get_account(acc_id)
    if not acc or acc['user_id'] != msg.from_user.id:
        await msg.answer("❌ Аккаунт не найден.")
        return

    status_msg = await msg.answer(f"⏳ Удаляю {count} сообщений из {chat}...")
    try:
        deleted = await ubm.delete_messages(acc_id, chat, count)
        await status_msg.edit_text(f"✅ Удалено {deleted} сообщений из {chat}.")
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка: {e}")


# ─────────────────────────────────────────────
# Tasks
# ─────────────────────────────────────────────
@dp.message(F.text == "📋 Задачи")
async def tasks_list(msg: Message):
    tasks = db.get_tasks(msg.from_user.id)
    if not tasks:
        await msg.answer("📋 Нет активных задач.")
        return

    text = "📋 <b>Активные задачи:</b>\n\n"
    buttons = []
    for t in tasks:
        status_icon = {"running": "▶️", "paused": "⏸", "stopped": "⏹"}.get(t['status'], "❓")
        text += f"{status_icon} <b>#{t['id']}</b> — {t['task_type']} | {t['status']}\n"
        buttons.append([
            InlineKeyboardButton(text=f"⏸ #{t['id']}", callback_data=f"task_pause:{t['id']}"),
            InlineKeyboardButton(text=f"▶️", callback_data=f"task_resume:{t['id']}"),
            InlineKeyboardButton(text=f"⏹", callback_data=f"task_stop:{t['id']}"),
        ])

    await msg.answer(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@dp.callback_query(F.data.startswith("task_pause:"))
async def cb_task_pause(cb: CallbackQuery):
    task_id = int(cb.data.split(":")[1])
    db.set_task_status(task_id, "paused")
    await cb.answer(f"⏸ Задача #{task_id} на паузе.", show_alert=False)
    await cb.message.edit_reply_markup(reply_markup=kb_task_actions(task_id))


@dp.callback_query(F.data.startswith("task_resume:"))
async def cb_task_resume(cb: CallbackQuery):
    task_id = int(cb.data.split(":")[1])
    db.set_task_status(task_id, "running")
    await cb.answer(f"▶️ Задача #{task_id} продолжена.", show_alert=False)


@dp.callback_query(F.data.startswith("task_stop:"))
async def cb_task_stop(cb: CallbackQuery):
    task_id = int(cb.data.split(":")[1])
    db.set_task_status(task_id, "stopped")
    await cb.answer(f"⏹ Задача #{task_id} остановлена.", show_alert=True)
    await cb.message.edit_reply_markup(reply_markup=None)


# ─────────────────────────────────────────────
# Flood runner (async task)
# ─────────────────────────────────────────────
async def run_flood_task(task_id: int, user_id: int, data: dict, stop_time, status_msg):
    acc_id = data['acc_id']
    chats = data['chats']
    count = data['count']
    delay = data['delay']
    text_template = data['text']

    stop_window = None
    if stop_time:
        stop_window = parse_stop_time(stop_time)

    sent_total = 0
    errors_total = 0

    for chat in chats:
        for i in range(count):
            task = db.get_task(task_id)
            if not task or task['status'] == "stopped":
                await bot.send_message(
                    user_id,
                    f"⏹ Рассылка #{task_id} остановлена. Отправлено: {sent_total}, ошибок: {errors_total}"
                )
                db.set_task_status(task_id, "stopped")
                return

            # Ждём, если пауза
            while True:
                task = db.get_task(task_id)
                if task['status'] == "stopped":
                    return
                if task['status'] == "paused":
                    await asyncio.sleep(5)
                    continue
                # Проверка временного окна
                if stop_window and is_in_stop_window(*stop_window):
                    await asyncio.sleep(30)
                    continue
                break

            final_text = spin_text(text_template)
            try:
                await ubm.send_message(acc_id, chat, final_text)
                sent_total += 1
            except Exception as e:
                errors_total += 1
                logger.warning(f"Task {task_id}: error sending to {chat}: {e}")

            if delay > 0:
                await asyncio.sleep(delay)

    db.set_task_status(task_id, "stopped")
    await bot.send_message(
        user_id,
        f"✅ <b>Рассылка #{task_id} завершена!</b>\n"
        f"Отправлено: <b>{sent_total}</b>\n"
        f"Ошибок: <b>{errors_total}</b>",
        parse_mode="HTML"
    )


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
async def main():
    db.init()
    await ubm.reconnect_all()
    logger.info("Bot starting...")
    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
