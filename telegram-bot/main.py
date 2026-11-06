"""Точка входа Bothost (python main.py): aiogram polling + WebSocket-сервер в одном процессе."""
import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession

import bot as ui
import config
import database
import websocket_server


async def main() -> None:
    level = getattr(logging, config.LOG_LEVEL, logging.INFO)
    logging.basicConfig(
        level=level,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
    )
    logging.getLogger("aiogram").setLevel(logging.INFO)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    log = logging.getLogger("modconnect")

    log.info("старт ModConnect: WS-сервер и Telegram в одном процессе")
    log.info("WS %s:%s | DB %s", config.WS_HOST, config.WS_PORT, config.DB_PATH)
    log.info("Telegram через прокси: %s", config.proxy_display())

    database.init(config.DB_PATH)

    # прокси касается только Telegram; WS-сервер слушает напрямую
    bot_session = AiohttpSession(proxy=config.PROXY_URL) if config.PROXY_URL else AiohttpSession()
    bot = Bot(token=config.BOT_TOKEN, session=bot_session)
    dp = Dispatcher()
    dp.include_router(ui.router)
    ui.bind(bot)
    websocket_server.bind(bot, config.ADMIN_IDS)

    # сперва поднимаем WS, чтобы Java-клиенты могли подключаться немедленно
    ws_server = await websocket_server.start(config.WS_HOST, config.WS_PORT)

    try:
        await ui.setup_commands(bot)
        await ui.greet_admin(bot)
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        log.info("завершение: закрываем WS и Telegram-сессию")
        await websocket_server.stop(ws_server)
        await bot.session.close()


def entry() -> None:
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Остановлено вручную (Ctrl+C).", file=sys.stderr)
        sys.exit(0)
    except SystemExit:
        raise
    except Exception:
        logging.getLogger("modconnect").exception("ФАТАЛЬНАЯ ошибка — бот упал:")
        sys.exit(1)


if __name__ == "__main__":
    entry()
