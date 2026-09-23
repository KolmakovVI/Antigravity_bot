import io
import sys
import asyncio
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List

# Fix Windows console UTF-8 encoding for emojis
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup, default_state
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    Message,
    BufferedInputFile,
    BotCommand,
    BotCommandScopeDefault,
    MenuButtonCommands,
)
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

import config
import log_config
from bridge import AntigravityBridge
from watcher import ConversationWatcher, split_telegram_message

# Set up unified logging to console and bot.log file
log_config.setup_logging()
logger = logging.getLogger("AntigravityBot")

# FSM States
class BotStates(StatesGroup):
    idle = State()
    waiting_for_new_chat_prompt = State()

# In-memory user state (selected project and active chat)
user_sessions: Dict[int, Dict[str, Any]] = {}

def get_user_session(user_id: int) -> Dict[str, Any]:
    if user_id not in user_sessions:
        user_sessions[user_id] = {
            "project_id": None,
            "conversation_id": None,
            "detailed_mode": False,
            "history_limit": 6
        }
    return user_sessions[user_id]

# Active cancellations tracking
active_cancellations: Dict[str, asyncio.Event] = {}

# Initialize bridge & watcher
bridge = AntigravityBridge()
watcher = ConversationWatcher(bridge)

# Helper: Check if user is allowed
def is_user_allowed(user_id: int) -> bool:
    allowed = config.get_allowed_user_ids()
    if not allowed:
        # First user is automatically saved as owner
        config.add_allowed_user_id(user_id)
        logger.info(f"Auto-authorized first user as bot owner: {user_id}")
        return True
    return user_id in allowed

# Safe message sender
async def safe_send_markdown(message: Message, text: str, reply_markup=None):
    """Sends text splitting into chunks if needed, with markdown fallback and flood protection."""
    chunks = split_telegram_message(text)
    if not chunks:
        return
    for i, chunk in enumerate(chunks):
        if i > 0:
            await asyncio.sleep(0.5)
        markup = reply_markup if i == len(chunks) - 1 else None
        try:
            await message.answer(chunk, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)
        except TelegramRetryAfter as e:
            logger.warning(f"Flood limit exceeded on send, sleeping {e.retry_after}s...")
            await asyncio.sleep(e.retry_after + 1)
            try:
                await message.answer(chunk, parse_mode=None, reply_markup=markup)
            except Exception:
                pass
        except TelegramBadRequest:
            await message.answer(chunk, parse_mode=None, reply_markup=markup)

async def safe_edit_markdown(callback: CallbackQuery, text: str, reply_markup=None):
    try:
        await callback.message.edit_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
    except TelegramBadRequest:
        try:
            await callback.message.edit_text(text, parse_mode=None, reply_markup=reply_markup)
        except Exception:
            pass

# Keyboards
def get_main_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    sess = get_user_session(user_id)
    buttons = [
        [
            InlineKeyboardButton(text="📁 Список проектов", callback_data="btn_projects"),
            InlineKeyboardButton(text="💬 Все чаты", callback_data="btn_all_chats"),
        ]
    ]
    if sess.get("conversation_id"):
        buttons.append([
            InlineKeyboardButton(text="📖 Открыть активный чат", callback_data=f"open_chat:{sess['conversation_id']}"),
        ])
    buttons.append([
        InlineKeyboardButton(text="➕ Новый диалог", callback_data="btn_new_chat"),
        InlineKeyboardButton(text="🔄 Статус Antigravity", callback_data="btn_status"),
    ])
    buttons.append([
        InlineKeyboardButton(text="📜 Логи бота", callback_data="btn_logs"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_projects_keyboard(projects: list) -> InlineKeyboardMarkup:
    buttons = []
    for p in projects:
        name = p["name"]
        pid = p["id"]
        chat_cnt = p.get("chat_count", 0)
        cnt_suffix = f" ({chat_cnt})" if chat_cnt else ""
        buttons.append([
            InlineKeyboardButton(text=f"📁 {name}{cnt_suffix}", callback_data=f"select_project:{pid}")
        ])
    buttons.append([
        InlineKeyboardButton(text="🔙 В главное меню", callback_data="btn_menu")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_chats_keyboard(chats: list, project_id: Optional[str] = None) -> InlineKeyboardMarkup:
    buttons = []
    for c in chats[:15]:  # Show top 15 recent chats
        cid = c["id"]
        icon = "🟢" if c["status"] == "IDLE" else "🟡"
        title = c["title"][:28]
        steps = c["step_count"]
        btn_text = f"{icon} {title} ({steps}ш)"
        buttons.append([
            InlineKeyboardButton(text=btn_text, callback_data=f"open_chat:{cid}")
        ])
    
    ctrl_row = []
    if project_id:
        ctrl_row.append(InlineKeyboardButton(text="➕ Новый чат в проекте", callback_data=f"new_chat_in:{project_id}"))
    ctrl_row.append(InlineKeyboardButton(text="🔙 Проекты", callback_data="btn_projects"))
    buttons.append(ctrl_row)
    buttons.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="btn_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def build_chat_pages(messages: list) -> list:
    """Group messages into turns (each turn has 'user', 'assistant' list, and 'tools' list)."""
    turns = []
    current_turn = None
    for m in messages:
        role = m["role"]
        if role == "user":
            if current_turn is not None:
                turns.append(current_turn)
            current_turn = {"user": m, "assistant": [], "tools": []}
        elif role == "assistant":
            if current_turn is None:
                current_turn = {"user": None, "assistant": [], "tools": []}
            current_turn["assistant"].append(m)
        elif role == "tool":
            if current_turn is None:
                current_turn = {"user": None, "assistant": [], "tools": []}
            current_turn["tools"].append(m)
    if current_turn is not None:
        turns.append(current_turn)
    return turns or [{"user": None, "assistant": [], "tools": []}]

def format_chat_page(conversation_id: str, page: int, total_pages: int, turn: dict, user_id: int) -> tuple[str, bool]:
    sess = get_user_session(user_id)
    detailed = sess.get("detailed_mode", False)
    chat_title = bridge.get_chat_title(conversation_id)
    projects = {p["id"]: p["name"] for p in bridge.list_projects()}
    pid = sess.get("project_id")
    pname = projects.get(pid, "Все проекты")
    status = bridge.get_chat_status(conversation_id)
    status_badge = "🟢 Свободен" if status == "IDLE" else "🟡 В процессе..."
    mode_desc = "🛠 *Подробный* (с инструментами)" if detailed else "💬 *Краткий* (диалог)"

    header = (
        f"💬 *Диалог:* «{chat_title}»\n"
        f"📁 *Проект:* {pname} | 📊 {status_badge}\n"
        f"⚙️ *Режим:* {mode_desc}\n"
        f"📄 *Страница диалога:* {page} из {total_pages}\n"
        f"➖➖➖➖➖➖➖➖➖➖\n\n"
    )

    body = ""
    if turn.get("user"):
        u_step = turn["user"].get("step_index", "")
        u_step_str = f" [шаг {u_step}]" if u_step else ""
        u_text = turn["user"]["text"].strip()
        if len(u_text) > 700:
            u_text = u_text[:700] + "..."
        body += f"👤 *Вы{u_step_str}:*\n{u_text}\n\n"
    else:
        body += "👤 *Вы:*\n_Начало диалога_\n\n"

    if detailed and turn.get("tools"):
        tools_summary = "\n".join(m["text"] for m in turn["tools"][-4:])
        if tools_summary:
            body += f"{tools_summary}\n\n"

    is_truncated = False
    if turn.get("assistant"):
        a_text = "\n\n".join(m["text"] for m in turn["assistant"]).strip()
        full_len = len(a_text)
        max_a_len = 2500
        if full_len > max_a_len:
            is_truncated = True
            a_text = (
                a_text[:max_a_len]
                + f"\n\n_... ✂️ [Показано 2 500 из {full_len:,} симв.]_\n"
                f"_💡 Нажмите «📖 Развернуть ответ полностью» ниже, чтобы прочитать целиком._"
            )
        body += f"🤖 *Antigravity:*\n{a_text}\n"
    else:
        if status == "RUNNING":
            body += "🤖 *Antigravity:*\n⏳ _Агент работает над запросом..._\n"
        else:
            body += "🤖 *Antigravity:*\n_Ожидание ответа..._\n"

    footer = "\n➖➖➖➖➖➖➖➖➖➖\n"
    if page == total_pages:
        footer += "✍️ *Актуальный шаг диалога!*\nОтправьте текст боту — он сразу уйдёт агенту в Antigravity."
    else:
        footer += f"📖 *Архивный шаг {page}/{total_pages}* (листайте кнопками страниц ниже)."

    return (header + body + footer, is_truncated)

def get_chat_page_keyboard(conversation_id: str, page: int, total_pages: int, user_id: int, is_truncated: bool = False) -> InlineKeyboardMarkup:
    sess = get_user_session(user_id)
    detailed = sess.get("detailed_mode", False)
    mode_text = "⚙️ Режим: 🛠 Подробный" if detailed else "⚙️ Режим: 💬 Краткий"
    pid = sess.get("project_id") or ""
    
    keyboard = []
    
    # 0. Expand and Download buttons if answer was truncated on this page
    if is_truncated:
        keyboard.append([
            InlineKeyboardButton(text="📖 Развернуть ответ полностью", callback_data=f"read_full:{conversation_id}:{page}"),
            InlineKeyboardButton(text="📥 Скачать (.md)", callback_data=f"dl_step:{conversation_id}:{page}"),
        ])

    # 1. Navigation arrows (if multiple pages)
    if total_pages > 1:
        nav_row = []
        if page > 1:
            nav_row.append(InlineKeyboardButton(text="⏮ 1", callback_data=f"chat_page:{conversation_id}:1"))
            nav_row.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"chat_page:{conversation_id}:{page - 1}"))
        
        nav_row.append(InlineKeyboardButton(text=f"· {page} / {total_pages} ·", callback_data=f"page_info:{page}:{total_pages}"))
        
        if page < total_pages:
            nav_row.append(InlineKeyboardButton(text="Вперёд ▶️", callback_data=f"chat_page:{conversation_id}:{page + 1}"))
            nav_row.append(InlineKeyboardButton(text=f"{total_pages} ⏭", callback_data=f"chat_page:{conversation_id}:{total_pages}"))
        keyboard.append(nav_row)
        
        # 2. Direct numbered page jump buttons
        jump_row = []
        if total_pages <= 6:
            for p in range(1, total_pages + 1):
                btn_txt = f"• {p} •" if p == page else str(p)
                jump_row.append(InlineKeyboardButton(text=btn_txt, callback_data=f"chat_page:{conversation_id}:{p}"))
        else:
            indices = sorted(set([1, max(1, page - 1), page, min(total_pages, page + 1), total_pages]))
            for p in indices:
                btn_txt = f"• {p} •" if p == page else str(p)
                jump_row.append(InlineKeyboardButton(text=btn_txt, callback_data=f"chat_page:{conversation_id}:{p}"))
        keyboard.append(jump_row)

    # 3. Actions: Refresh, Mode (and Stop if RUNNING)
    action_row = [
        InlineKeyboardButton(text="🔄 Обновить", callback_data=f"refresh_chat:{conversation_id}"),
        InlineKeyboardButton(text=mode_text, callback_data=f"toggle_mode:{conversation_id}"),
    ]
    if bridge.get_chat_status(conversation_id) == "RUNNING":
        action_row.insert(0, InlineKeyboardButton(text="🛑 Прервать", callback_data=f"stop_chat:{conversation_id}"))
    keyboard.append(action_row)
    
    # 4. Navigation: back to chats, new chat, menu
    keyboard.append([
        InlineKeyboardButton(text="🔙 К списку чатов", callback_data="btn_all_chats"),
        InlineKeyboardButton(text="➕ Новый чат", callback_data=f"new_chat_in:{pid}"),
    ])
    keyboard.append([
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="btn_menu")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# Alias for backward compatibility
get_chat_view_keyboard = lambda cid, uid: get_chat_page_keyboard(cid, 1, 1, uid)

# Handlers
dp = Dispatcher(storage=MemoryStorage())

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    logger.info(f"User {user_id} (@{username}) sent /start")

    if not is_user_allowed(user_id):
        logger.warning(f"Unauthorized access attempt by user {user_id} (@{username})")
        await message.answer("⛔ Доступ запрещен. Бот привязан к локальному Antigravity владельца.")
        return

    await state.clear()
    sess = get_user_session(user_id)
    connected = bridge.detect_connection()
    status_icon = "🟢 Подключен" if connected else "🔴 Не запущен (запустите Antigravity)"
    
    projects = {p["id"]: p["name"] for p in bridge.list_projects()}
    pname = projects.get(sess.get("project_id"), "Не выбран")
    cid = sess.get("conversation_id")
    ctitle = bridge.get_chat_title(cid) if cid else "Не выбран"

    text = (
        f"🤖 *Antigravity Telegram Синхронизатор*\n\n"
        f"Статус Antigravity: {status_icon}\n"
        f"Активный проект: *{pname}*\n"
        f"Активный чат: *{ctitle}*\n\n"
        f"💡 *Как это работает:*\n"
        f"1. Выберите проект и откройте нужный чат.\n"
        f"2. Всё, что вы напишете сюда, моментально уйдёт в Antigravity.\n"
        f"3. Ответ агента придёт прямо в этот диалог в Telegram!"
    )
    await message.answer(text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_menu_keyboard(user_id))

@dp.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext):
    if not is_user_allowed(message.from_user.id):
        return
    await state.clear()
    sess = get_user_session(message.from_user.id)
    connected = bridge.detect_connection()
    status_icon = "🟢 Подключен" if connected else "🔴 Не запущен"
    projects = {p["id"]: p["name"] for p in bridge.list_projects()}
    pname = projects.get(sess.get("project_id"), "Не выбран")
    cid = sess.get("conversation_id")
    ctitle = bridge.get_chat_title(cid) if cid else "Не выбран"
    text = (
        f"🤖 *Главное меню Antigravity*\n\n"
        f"Статус: {status_icon}\n"
        f"Проект: *{pname}*\n"
        f"Активный чат: *{ctitle}*"
    )
    await message.answer(text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_menu_keyboard(message.from_user.id))

@dp.message(Command("projects"))
async def cmd_projects(message: Message):
    if not is_user_allowed(message.from_user.id):
        return
    logger.info(f"User {message.from_user.id} requested projects menu")
    projects = bridge.list_projects()
    text = f"📁 *Проекты Antigravity* (по недавней активности, всего: {len(projects)}):\nВыберите проект для просмотра диалогов:"
    await message.answer(text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_projects_keyboard(projects))

@dp.message(Command("current"))
async def cmd_current(message: Message):
    if not is_user_allowed(message.from_user.id):
        return
    sess = get_user_session(message.from_user.id)
    cid = sess.get("conversation_id")
    if not cid:
        await message.answer(
            "⚠️ *Активный чат не выбран!*\nВыберите диалог из списка проектов или всех чатов:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_main_menu_keyboard(message.from_user.id)
        )
        return
    detailed = sess.get("detailed_mode", False)
    messages = bridge.get_chat_history(cid, limit=0, detailed=detailed)
    turns = build_chat_pages(messages)
    total_pages = len(turns)
    page = sess.get("current_page", total_pages)
    if page > total_pages or page < 1:
        page = total_pages
    sess["current_page"] = page
    turn = turns[page - 1]
    text, is_truncated = format_chat_page(cid, page, total_pages, turn, message.from_user.id)
    keyboard = get_chat_page_keyboard(cid, page, total_pages, message.from_user.id, is_truncated=is_truncated)
    await message.answer(text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)

@dp.message(Command("newchat"))
async def cmd_newchat_cmd(message: Message, state: FSMContext):
    if not is_user_allowed(message.from_user.id):
        return
    await state.set_state(BotStates.waiting_for_new_chat_prompt)
    sess = get_user_session(message.from_user.id)
    pid = sess.get("project_id")
    projects = {p["id"]: p["name"] for p in bridge.list_projects()}
    pname = projects.get(pid, "Вне проектов")
    text = (
        f"➕ *Создание нового диалога в Antigravity*\n"
        f"📁 Проект: *{pname}*\n\n"
        "Отправьте текст первого запроса (промпта).\n"
        "Агент создаст чат и сразу приступит к решению."
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="btn_menu")]
    ])
    await message.answer(text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)

@dp.message(Command("help"))
async def cmd_help(message: Message):
    if not is_user_allowed(message.from_user.id):
        return
    text = (
        "ℹ️ *Команды бота Antigravity:*\n\n"
        "• /menu — Главное меню управления\n"
        "• /projects — Выбор проекта Antigravity\n"
        "• /chats — Список всех диалогов\n"
        "• /current — Открыть текущий активный диалог\n"
        "• /newchat — Создать новый диалог\n"
        "• /status — Проверить подключение к Antigravity\n"
        "• /logs — Посмотреть системные логи бота\n\n"
        "✍️ *Как общаться:* выберите диалог и просто отправляйте сообщения боту — они мгновенно транслируются агенту в Antigravity."
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="btn_menu")]
    ])
    await message.answer(text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)

@dp.message(Command("chats"))
async def cmd_chats(message: Message):
    if not is_user_allowed(message.from_user.id):
        return
    sess = get_user_session(message.from_user.id)
    pid = sess.get("project_id")
    logger.info(f"User {message.from_user.id} requested chats for project {pid}")
    chats = bridge.list_chats(pid)
    projects = {p["id"]: p["name"] for p in bridge.list_projects()}
    pname = projects.get(pid, pid)
    title = f"в проекте «{pname}»" if pid else "во всех проектах"
    text = f"💬 *Список чатов* ({title}):\nВсего найдено: {len(chats)}"
    await message.answer(text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_chats_keyboard(chats, pid))

@dp.message(Command("status"))
async def cmd_status(message: Message):
    if not is_user_allowed(message.from_user.id):
        return
    logger.info(f"User {message.from_user.id} requested Antigravity status")
    connected = bridge.detect_connection(force=True)
    if connected:
        text = (
            f"🟢 *Antigravity активен*\n"
            f"Порт: `{bridge.port}`\n"
            f"CSRF Token: `{bridge.csrf_token[:8]}...`\n"
            f"Всего проектов: {len(bridge.list_projects())}\n"
            f"Всего чатов: {len(bridge.list_chats())}"
        )
    else:
        text = "🔴 *Antigravity не обнаружен*\nУбедитесь, что приложение Antigravity запущено на вашем ПК."
    await message.answer(text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_menu_keyboard(message.from_user.id))

@dp.message(Command("logs"))
async def cmd_logs(message: Message):
    if not is_user_allowed(message.from_user.id):
        return
    logger.info(f"User {message.from_user.id} requested bot logs")
    logs_content = log_config.get_recent_logs(25)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить логи", callback_data="btn_refresh_logs")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="btn_menu")]
    ])
    await message.answer(f"📜 *Последние логи бота:*\n\n```text\n{logs_content}\n```", parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)

@dp.message(Command("stop"))
async def cmd_stop(message: Message):
    if not is_user_allowed(message.from_user.id):
        return
    sess = get_user_session(message.from_user.id)
    cid = sess.get("conversation_id")
    if not cid:
        await message.answer("⚠️ Нет активного выбранного диалога.")
        return
    chat_title = bridge.get_chat_title(cid)
    status = bridge.get_chat_status(cid)
    if status != "RUNNING" and cid not in active_cancellations:
        await message.answer(f"ℹ️ В диалоге «{chat_title}» нет выполняющихся задач.")
        return
    
    logger.info(f"User {message.from_user.id} requested /stop for chat {cid[:8]}...")
    bridge.stop_chat(cid)
    if cid in active_cancellations:
        active_cancellations[cid].set()
    await message.answer(f"⏹️ Выполнение в диалоге «{chat_title}» остановлено.")

@dp.callback_query(F.data.startswith("stop_chat:"))
async def cb_stop_chat(callback: CallbackQuery):
    if not is_user_allowed(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    cid = callback.data.split(":", 1)[1]
    chat_title = bridge.get_chat_title(cid)
    logger.info(f"User {callback.from_user.id} clicked stop button for chat {cid[:8]}...")
    bridge.stop_chat(cid)
    if cid in active_cancellations:
        active_cancellations[cid].set()
    await callback.answer("⏹️ Выполнение остановлено!", show_alert=False)
    try:
        await callback.message.edit_text(
            f"⏹️ *Выполнение в диалоге «{chat_title}» остановлено пользователем.*",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception:
        pass

# Callback query handlers
@dp.callback_query(F.data == "btn_menu")
async def cb_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    sess = get_user_session(callback.from_user.id)
    connected = bridge.detect_connection()
    status_icon = "🟢 Подключен" if connected else "🔴 Не запущен"
    projects = {p["id"]: p["name"] for p in bridge.list_projects()}
    pname = projects.get(sess.get("project_id"), "Не выбран")
    cid = sess.get("conversation_id")
    ctitle = bridge.get_chat_title(cid) if cid else "Не выбран"
    text = (
        f"🤖 *Главное меню Antigravity*\n\n"
        f"Статус: {status_icon}\n"
        f"Проект: *{pname}*\n"
        f"Активный чат: *{ctitle}*"
    )
    await safe_edit_markdown(callback, text, reply_markup=get_main_menu_keyboard(callback.from_user.id))
    await callback.answer()

@dp.callback_query(F.data == "btn_logs")
@dp.callback_query(F.data == "btn_refresh_logs")
async def cb_logs(callback: CallbackQuery):
    logs_content = log_config.get_recent_logs(25)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить логи", callback_data="btn_refresh_logs")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="btn_menu")]
    ])
    try:
        await callback.message.edit_text(f"📜 *Последние логи бота:*\n\n```text\n{logs_content}\n```", parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)
    except Exception:
        pass

@dp.callback_query(F.data == "btn_projects")
async def cb_projects(callback: CallbackQuery):
    logger.info(f"User {callback.from_user.id} clicked projects button")
    projects = bridge.list_projects()
    text = f"📁 *Проекты Antigravity* (по недавней активности, всего: {len(projects)}):\nВыберите проект:"
    await safe_edit_markdown(callback, text, reply_markup=get_projects_keyboard(projects))

@dp.callback_query(F.data == "btn_all_chats")
async def cb_all_chats(callback: CallbackQuery):
    sess = get_user_session(callback.from_user.id)
    pid = sess.get("project_id")
    logger.info(f"User {callback.from_user.id} clicked all chats (filter pid={pid})")
    chats = bridge.list_chats(pid)
    projects = {p["id"]: p["name"] for p in bridge.list_projects()}
    pname = projects.get(pid, pid)
    title = f"в проекте «{pname}»" if pid else "во всех проектах"
    text = f"💬 *Чаты* ({title}, найдено: {len(chats)}):\nНажмите на чат, чтобы открыть и продолжить диалог:"
    await safe_edit_markdown(callback, text, reply_markup=get_chats_keyboard(chats, pid))
    await callback.answer()

@dp.callback_query(F.data.startswith("select_project:"))
async def cb_select_project(callback: CallbackQuery):
    pid = callback.data.split("select_project:")[1]
    sess = get_user_session(callback.from_user.id)
    sess["project_id"] = pid
    
    projects = {p["id"]: p["name"] for p in bridge.list_projects()}
    pname = projects.get(pid, pid)
    logger.info(f"User {callback.from_user.id} selected project '{pname}' (ID: {pid})")
    
    chats = bridge.list_chats(pid)
    logger.info(f"Found {len(chats)} chats for project '{pname}'")
    
    if not chats:
        text = f"📁 Проект: *{pname}*\n\n💬 В этом проекте пока нет диалогов.\nВы можете создать первый чат прямо сейчас:"
    else:
        text = f"📁 Проект: *{pname}*\n\n💬 Чаты проекта ({len(chats)}):\nВыберите чат для работы:"

    await safe_edit_markdown(callback, text, reply_markup=get_chats_keyboard(chats, pid))
    await callback.answer()

@dp.callback_query(F.data.startswith("open_chat:"))
@dp.callback_query(F.data.startswith("refresh_chat:"))
async def cb_open_chat(callback: CallbackQuery):
    cid = callback.data.split(":")[1]
    sess = get_user_session(callback.from_user.id)
    sess["conversation_id"] = cid
    
    detailed = sess.get("detailed_mode", False)
    messages = bridge.get_chat_history(cid, limit=0, detailed=detailed)
    turns = build_chat_pages(messages)
    total_pages = len(turns)
    
    # If opening a chat or refreshing, default to the latest page (or keep current if valid)
    if callback.data.startswith("open_chat:"):
        page = total_pages
    else:
        page = sess.get("current_page", total_pages)
        if page > total_pages or page < 1:
            page = total_pages
            
    sess["current_page"] = page
    logger.info(f"User {callback.from_user.id} opened chat {cid[:8]} (page {page}/{total_pages})")
    
    turn = turns[page - 1]
    text, is_truncated = format_chat_page(cid, page, total_pages, turn, callback.from_user.id)
    reply_markup = get_chat_page_keyboard(cid, page, total_pages, callback.from_user.id, is_truncated=is_truncated)
    
    await safe_edit_markdown(callback, text, reply_markup=reply_markup)
    try:
        await callback.answer()
    except Exception:
        pass

@dp.callback_query(F.data.startswith("chat_page:"))
async def cb_chat_page(callback: CallbackQuery):
    parts = callback.data.split(":")
    cid = parts[1]
    target_page = int(parts[2])
    
    sess = get_user_session(callback.from_user.id)
    sess["conversation_id"] = cid
    
    detailed = sess.get("detailed_mode", False)
    messages = bridge.get_chat_history(cid, limit=0, detailed=detailed)
    turns = build_chat_pages(messages)
    total_pages = len(turns)
    
    page = max(1, min(target_page, total_pages))
    sess["current_page"] = page
    logger.info(f"User {callback.from_user.id} navigated to page {page}/{total_pages} in chat {cid[:8]}")
    
    turn = turns[page - 1]
    text, is_truncated = format_chat_page(cid, page, total_pages, turn, callback.from_user.id)
    reply_markup = get_chat_page_keyboard(cid, page, total_pages, callback.from_user.id, is_truncated=is_truncated)
    
    await safe_edit_markdown(callback, text, reply_markup=reply_markup)
    try:
        await callback.answer(f"Страница {page} из {total_pages}", show_alert=False)
    except Exception:
        pass

@dp.callback_query(F.data.startswith("page_info:"))
async def cb_page_info(callback: CallbackQuery):
    parts = callback.data.split(":")
    p = parts[1]
    tot = parts[2]
    await callback.answer(f"Страница {p} из {tot}. Нажимайте на кнопки со стрелками или номерами для перелистывания.", show_alert=False)

@dp.callback_query(F.data.startswith("toggle_mode:"))
async def cb_toggle_mode(callback: CallbackQuery):
    cid = callback.data.split("toggle_mode:")[1]
    sess = get_user_session(callback.from_user.id)
    sess["detailed_mode"] = not sess.get("detailed_mode", False)
    mode_name = "🛠 Подробный (с инструментами)" if sess["detailed_mode"] else "💬 Краткий (только диалог)"
    logger.info(f"User {callback.from_user.id} toggled detailed mode to {sess['detailed_mode']}")
    await callback.answer(f"Включен: {mode_name}", show_alert=False)
    
    detailed = sess.get("detailed_mode", False)
    messages = bridge.get_chat_history(cid, limit=0, detailed=detailed)
    turns = build_chat_pages(messages)
    total_pages = len(turns)
    page = max(1, min(sess.get("current_page", total_pages), total_pages))
    sess["current_page"] = page
    
    turn = turns[page - 1]
    text, is_truncated = format_chat_page(cid, page, total_pages, turn, callback.from_user.id)
    reply_markup = get_chat_page_keyboard(cid, page, total_pages, callback.from_user.id, is_truncated=is_truncated)
    
    await safe_edit_markdown(callback, text, reply_markup=reply_markup)

@dp.callback_query(F.data.startswith("read_full:"))
async def cb_read_full(callback: CallbackQuery):
    parts = callback.data.split(":")
    cid = parts[1]
    page = int(parts[2])
    
    sess = get_user_session(callback.from_user.id)
    detailed = sess.get("detailed_mode", False)
    messages = bridge.get_chat_history(cid, limit=0, detailed=detailed)
    turns = build_chat_pages(messages)
    
    if not (1 <= page <= len(turns)):
        await callback.answer("Шаг не найден", show_alert=True)
        return
        
    turn = turns[page - 1]
    a_text = "\n\n".join(m["text"] for m in turn.get("assistant", [])).strip()
    if not a_text:
        await callback.answer("У этого шага нет ответа", show_alert=True)
        return
        
    await callback.answer("Отправляю полный ответ без сокращений... ⬇️")
    chat_title = bridge.get_chat_title(cid)
    prefix = f"📖 *Полный ответ Antigravity* (шаг {page} из {len(turns)}, «{chat_title}»):\n\n"
    await safe_send_markdown(callback.message, prefix + a_text)

@dp.callback_query(F.data.startswith("dl_step:"))
async def cb_dl_step(callback: CallbackQuery):
    parts = callback.data.split(":")
    cid = parts[1]
    page = int(parts[2])
    
    sess = get_user_session(callback.from_user.id)
    detailed = sess.get("detailed_mode", False)
    messages = bridge.get_chat_history(cid, limit=0, detailed=detailed)
    turns = build_chat_pages(messages)
    
    if not (1 <= page <= len(turns)):
        await callback.answer("Шаг не найден", show_alert=True)
        return
        
    turn = turns[page - 1]
    a_text = "\n\n".join(m["text"] for m in turn.get("assistant", [])).strip()
    u_text = turn.get("user", {}).get("text", "")
    chat_title = bridge.get_chat_title(cid)
    
    md_content = f"# Диалог: {chat_title}\n## Шаг {page} из {len(turns)}\n\n"
    if u_text:
        md_content += f"### Запрос пользователя:\n{u_text}\n\n"
    md_content += f"### Ответ Antigravity:\n{a_text}\n"
    
    file_bytes = md_content.encode("utf-8")
    input_file = BufferedInputFile(file_bytes, filename=f"antigravity_step_{page}.md")
    await callback.answer("Формирую файл...")
    await callback.message.answer_document(
        input_file,
        caption=f"📄 Полный ответ шага {page} ({len(a_text):,} символов)"
    )

@dp.callback_query(F.data == "btn_status")
async def cb_status(callback: CallbackQuery):
    connected = bridge.detect_connection(force=True)
    if connected:
        text = (
            f"🟢 *Antigravity активен*\n"
            f"Порт: `{bridge.port}`\n"
            f"CSRF Token: `{bridge.csrf_token[:8]}...`\n"
            f"Проектов: {len(bridge.list_projects())} | Чатов: {len(bridge.list_chats())}"
        )
    else:
        text = "🔴 *Antigravity не обнаружен*\nЗапустите приложение Antigravity на компьютере."
    await safe_edit_markdown(callback, text, reply_markup=get_main_menu_keyboard(callback.from_user.id))

@dp.callback_query(F.data == "btn_new_chat")
@dp.callback_query(F.data.startswith("new_chat_in:"))
async def cb_new_chat(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.waiting_for_new_chat_prompt)
    if ":" in callback.data:
        pid = callback.data.split(":")[1]
        sess = get_user_session(callback.from_user.id)
        sess["project_id"] = pid
        logger.info(f"User {callback.from_user.id} initiated new chat in project {pid}")
    else:
        logger.info(f"User {callback.from_user.id} initiated new chat (global)")
    
    text = (
        "➕ *Создание нового диалога в Antigravity*\n\n"
        "Отправьте текст вашего первого запроса (промпта).\n"
        "Будет создан новый чат и агент начнёт выполнение задачи."
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="btn_menu")]
    ])
    await safe_edit_markdown(callback, text, reply_markup=keyboard)

# Handler for new chat prompt input (supports text and photo/document)
@dp.message(BotStates.waiting_for_new_chat_prompt)
async def process_new_chat_prompt(message: Message, state: FSMContext):
    if not is_user_allowed(message.from_user.id):
        return

    prompt = (message.text or message.caption or "").strip()
    has_image = bool(message.photo or (message.document and message.document.mime_type and message.document.mime_type.startswith("image/")))
    
    if not prompt and has_image:
        prompt = "Посмотри на это изображение."
    elif not prompt:
        await message.answer("Пожалуйста, введите текстовый запрос или прикрепите изображение.")
        return

    await state.clear()
    sess = get_user_session(message.from_user.id)
    logger.info(f"User {message.from_user.id} creating new conversation with prompt: {prompt[:60]} (has_image={has_image})")
    
    wait_msg = await message.answer("🚀 *Создаю новый диалог в Antigravity...*", parse_mode=ParseMode.MARKDOWN)
    
    try:
        title = prompt[:50]
        cid = bridge.create_new_chat(prompt, title=title)
        sess["conversation_id"] = cid
        logger.info(f"Successfully created conversation {cid} for user {message.from_user.id}")

        if has_image:
            file_id = None
            ext = ".png"
            mime_type = "image/png"
            if message.photo:
                photo = message.photo[-1]
                file_id = photo.file_id
                ext = ".jpg"
                mime_type = "image/jpeg"
            elif message.document:
                file_id = message.document.file_id
                mime_type = message.document.mime_type
                if message.document.file_name:
                    suffix = Path(message.document.file_name).suffix.lower()
                    if suffix in [".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"]:
                        ext = suffix

            if file_id:
                file_obj = await message.bot.get_file(file_id)
                bio = io.BytesIO()
                await message.bot.download_file(file_obj.file_path, destination=bio)
                img_bytes = bio.getvalue()
                img_path = bridge.save_uploaded_image(cid, img_bytes, ext=ext)
                img_payload = bridge.build_image_payload(img_path, img_bytes, mime_type=mime_type)
                bridge.send_user_message(cid, prompt, images=[img_payload])

        await wait_msg.edit_text(
            f"✅ *Диалог создан!*\nID: `{cid[:8]}...`\n⏳ Ожидаю первый ответ агента...",
            parse_mode=ParseMode.MARKDOWN
        )

        async def on_progress(p_text: str):
            try:
                await wait_msg.edit_text(f"⏳ *Antigravity думает...*\n{p_text}", parse_mode=ParseMode.MARKDOWN)
            except Exception:
                pass

        reply = await watcher.wait_for_response(cid, initial_step_count=1, on_progress=on_progress, timeout=300)
        
        messages = bridge.get_chat_history(cid, limit=0, detailed=sess.get("detailed_mode", False))
        turns = build_chat_pages(messages)
        total_pages = len(turns)
        sess["current_page"] = total_pages
        last_turn = turns[-1] if turns else {}
        is_tr = len("\n\n".join(m["text"] for m in last_turn.get("assistant", []))) > 2500
        keyboard = get_chat_page_keyboard(cid, total_pages, total_pages, message.from_user.id, is_truncated=is_tr)

        if reply:
            await safe_send_markdown(message, f"🤖 *Antigravity:*\n\n{reply}", reply_markup=keyboard)
        else:
            await message.answer("⚠️ Агент начал работу. Проверьте статус через меню чата.", reply_markup=keyboard)

    except Exception as e:
        logger.exception(f"Error creating chat: {e}")
        await wait_msg.edit_text(f"❌ Ошибка создания диалога:\n`{e}`", parse_mode=ParseMode.MARKDOWN)


async def _run_message_flow(
    message: Message,
    cid: str,
    user_text: str,
    chat_title: str,
    sess: dict,
    images: Optional[List[Dict[str, Any]]] = None,
    intro_text: Optional[str] = None
):
    """Common pipeline to send text/images to Antigravity, track progress, and send paginated response."""
    initial_steps = bridge.get_transcript_step_count(cid)
    
    status_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🛑 Прервать выполнение", callback_data=f"stop_chat:{cid}")]]
    )
    status_prompt = intro_text or (
        f"⏳ *Отправлено в диалог:* «{chat_title}»\n🧠 *Antigravity думает и выполняет задачу...*"
    )
    status_msg = await message.answer(
        status_prompt,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=status_keyboard
    )
    
    try:
        bridge.send_user_message(cid, user_text, images=images)
    except Exception as e:
        logger.exception(f"Failed to send message: {e}")
        await status_msg.edit_text(f"❌ Ошибка отправки в Antigravity:\n`{e}`", parse_mode=ParseMode.MARKDOWN)
        return

    async def on_progress(p_text: str):
        try:
            await status_msg.edit_text(
                f"⏳ *Antigravity:* «{chat_title}»\n{p_text}",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=status_keyboard
            )
        except Exception:
            pass

    async def send_typing_loop():
        while True:
            try:
                await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
            except Exception:
                pass
            await asyncio.sleep(4.0)

    typing_task = asyncio.create_task(send_typing_loop())
    cancel_event = asyncio.Event()
    active_cancellations[cid] = cancel_event
    
    try:
        reply = await watcher.wait_for_response(
            conversation_id=cid,
            initial_step_count=initial_steps,
            on_progress=on_progress,
            timeout=400,
            cancel_event=cancel_event
        )
    finally:
        typing_task.cancel()
        active_cancellations.pop(cid, None)

    try:
        await status_msg.delete()
    except Exception:
        pass

    messages = bridge.get_chat_history(cid, limit=0, detailed=sess.get("detailed_mode", False))
    turns = build_chat_pages(messages)
    total_pages = len(turns)
    sess["current_page"] = total_pages
    last_turn = turns[-1] if turns else {}
    is_tr = len("\n\n".join(m["text"] for m in last_turn.get("assistant", []))) > 2500
    keyboard = get_chat_page_keyboard(cid, total_pages, total_pages, message.from_user.id, is_truncated=is_tr)

    if reply == "__STOPPED__":
        await message.answer(
            f"⏹️ *Выполнение в диалоге «{chat_title}» остановлено.*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard
        )
    elif reply:
        logger.info(f"Delivering response to user {message.from_user.id} ({len(reply)} chars)")
        await safe_send_markdown(
            message,
            f"🤖 *Antigravity:* «{chat_title}»\n\n{reply}",
            reply_markup=keyboard
        )
    else:
        logger.warning(f"No response produced or error in Antigravity for chat {cid}")
        err_detail = bridge.get_recent_antigravity_error()
        diag_section = f"\n\n🔍 *Диагностика системы:*\n{err_detail}" if err_detail else ""
        await message.answer(
            f"⚠️ *Antigravity не сформировал ответ.*{diag_section}\n\n"
            "Возможные причины:\n"
            "• В приложении Antigravity произошёл сбой выполнения (`Error Unknown`)\n"
            "• Отключён VPN/прокси на компьютере\n"
            "• Проверьте состояние диалога в окне Antigravity на ПК.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard
        )


# Main Message Router: Forward text to Antigravity active chat!
@dp.message(~StateFilter(BotStates.waiting_for_new_chat_prompt), F.text)
async def handle_user_chat_message(message: Message, state: FSMContext):
    if not is_user_allowed(message.from_user.id):
        return

    sess = get_user_session(message.from_user.id)
    cid = sess.get("conversation_id")
    
    if not cid:
        text = (
            "⚠️ *Активный чат не выбран!*\n\n"
            "Чтобы отправить сообщение агенту, сначала выберите существующий чат или создайте новый через меню ниже:"
        )
        await message.answer(text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_menu_keyboard(message.from_user.id))
        return

    user_text = message.text.strip()
    chat_title = bridge.get_chat_title(cid)
    logger.info(f"Forwarding message from user {message.from_user.id} to Antigravity chat {cid[:8]}... ('{chat_title}'): {user_text[:60]}")
    await _run_message_flow(message, cid, user_text, chat_title, sess)


# Main Message Router: Forward photo / image to Antigravity active chat!
@dp.message(~StateFilter(BotStates.waiting_for_new_chat_prompt), F.photo | F.document)
async def handle_user_image_message(message: Message, state: FSMContext):
    if not is_user_allowed(message.from_user.id):
        return

    sess = get_user_session(message.from_user.id)
    cid = sess.get("conversation_id")
    
    if not cid:
        text = (
            "⚠️ *Активный чат не выбран!*\n\n"
            "Чтобы отправить изображение агенту, сначала выберите существующий чат или создайте новый через меню ниже:"
        )
        await message.answer(text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_menu_keyboard(message.from_user.id))
        return

    file_id = None
    ext = ".png"
    mime_type = "image/png"

    if message.photo:
        photo = message.photo[-1]
        file_id = photo.file_id
        ext = ".jpg"
        mime_type = "image/jpeg"
    elif message.document:
        doc = message.document
        if not (doc.mime_type and doc.mime_type.startswith("image/")):
            await message.answer("⚠️ Поддерживается отправка только изображений (JPG, PNG, WEBP).")
            return
        file_id = doc.file_id
        mime_type = doc.mime_type
        if doc.file_name:
            suffix = Path(doc.file_name).suffix.lower()
            if suffix in [".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"]:
                ext = suffix

    if not file_id:
        return

    chat_title = bridge.get_chat_title(cid)
    caption = (message.caption or "").strip() or "Посмотри на прикрепленное изображение."
    logger.info(f"Forwarding image from user {message.from_user.id} to Antigravity chat {cid[:8]}... ('{chat_title}'): caption='{caption[:50]}'")

    # Download image from Telegram into memory
    try:
        file_obj = await message.bot.get_file(file_id)
        bio = io.BytesIO()
        await message.bot.download_file(file_obj.file_path, destination=bio)
        image_bytes = bio.getvalue()
        img_path = bridge.save_uploaded_image(cid, image_bytes, ext=ext)
        img_payload = bridge.build_image_payload(img_path, image_bytes, mime_type=mime_type)
    except Exception as e:
        logger.exception(f"Failed to download/process image: {e}")
        await message.answer(f"❌ Ошибка обработки изображения:\n`{e}`", parse_mode=ParseMode.MARKDOWN)
        return

    intro = f"🖼 *Изображение загружено в диалог:* «{chat_title}»\n🧠 *Antigravity начинает анализ изображения...*"
    await _run_message_flow(
        message=message,
        cid=cid,
        user_text=caption,
        chat_title=chat_title,
        sess=sess,
        images=[img_payload],
        intro_text=intro
    )

async def start_bot():
    token = config.get_bot_token()
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN is not set!")
        return

    bot = Bot(token=token)
    logger.info("Initializing Telegram Antigravity Bot...")
    try:
        bot_info = await bot.get_me()
        logger.info(f"Bot @{bot_info.username} successfully initialized (id={bot_info.id})")

        # Set up Telegram Menu Button commands list (appears directly next to input box)
        bot_commands = [
            BotCommand(command="menu", description="🏠 Главное меню"),
            BotCommand(command="projects", description="📁 Список проектов"),
            BotCommand(command="chats", description="💬 Все чаты / диалоги"),
            BotCommand(command="current", description="📌 Открыть активный чат"),
            BotCommand(command="stop", description="🛑 Остановить текущее действие"),
            BotCommand(command="newchat", description="➕ Создать новый чат"),
            BotCommand(command="status", description="📊 Статус Antigravity"),
            BotCommand(command="logs", description="📜 Системные логи бота"),
            BotCommand(command="help", description="ℹ️ Справка и помощь"),
        ]
        await bot.set_my_commands(bot_commands, scope=BotCommandScopeDefault())
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        logger.info("Telegram input menu button and commands registered successfully")

        print(f"\n🚀 Бот @{bot_info.username} успешно запущен и готов к работе!")
        print(f"Откройте в Telegram: https://t.me/{bot_info.username}\n")
        while True:
            try:
                await dp.start_polling(bot)
                break
            except (KeyboardInterrupt, asyncio.CancelledError):
                break
            except Exception as e:
                logger.error(f"Polling error: {e}. Retrying in 5 seconds...")
                await asyncio.sleep(5)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(start_bot())
