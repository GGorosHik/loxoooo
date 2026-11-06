# ModConnect — Telegram-бот (aiogram 3 + WebSocket-сервер)

Один Python-процесс: бот Telegram и WebSocket-сервер для Minecraft-клиентов.

## Файлы
- `main.py` — точка входа (хостинг запускает `python main.py`)
- `config.py` — ENV-конфиг + загрузчик .env
- `bot.py` — все Telegram-команды и кнопки
- `websocket_server.py` — WS-сервер, реестр клиентов, sweeper мёртвых сессий
- `database.py` — SQLite: токены, клиенты, настройки
- `tools/fake_client.py` — тестовый клиент без Minecraft

## Установка на Bothost
1. Создай бота у @BotFather → получи `BOT_TOKEN`.
2. Узнай свой Telegram ID у @userinfobot → это `ADMIN_ID`.
3. Залей папку `telegram-bot` целиком (структуру не менять).
4. Задай переменные окружения (панель хостинга или файл `.env` по образцу `.env.example`):
   `BOT_TOKEN`, `ADMIN_ID`, `WS_PORT`, `DB_PATH`, `LOG_LEVEL`.
5. Python 3.11+. Зависимости: `pip install -r requirements.txt`.
6. Команда запуска: `python main.py`. Логи идут в stderr.
7. **Открой WS-порт** (`WS_PORT`, по умолчанию 8765) в панели хостинга — нужен публичный
   TCP-порт, чтобы Minecraft-мод мог подключиться. Если тариф портов не даёт —
   для связи с модом нужен любой хост с открытым портом (подойдёт дешёвый VPS).

## Пользование
- `/gentoken <метка>` — токен для одной установки мода (вставляется в `config/modconnect.json`)
- `/players` и кнопка «Список игроков» — кто онлайн (мод есть только у своих клиентов)
- `/close <ник>` — штатно закрыть игру у клиента (клиент сам вызывает `MinecraftClient.stop()`)
- `/manage` — кнопки закрытия по каждому клиенту
- `/tokens`, `/revoke <ник|id>` — аудит и отзыв токенов
- `/notify` — подключения/отключения/ввод команд `/login /l /register /reg` в личку

## Безопасность
- Команды доступны только владельцу: роутер режет по `ADMIN_ID`, чужие апдейты игнорируются.
- Токены хранятся только как sha256; открытый токен показывается один раз.
- Один токен = одна установка: `installation_id` привязывается при первом коннекте (код 4003 иначе).
- Без hello с валидным токеном соединение закрывается (коды 4001/1008/4002/4003/4005).
- TLS: задай `WSS_CERT` и `WSS_KEY` — сервер поднимется как `wss://`. Есть открытый порт,
  но нет домена/сертификата — самый простой путь: дешёвый VPS + Let's Encrypt,
  или Cloudflare перед WS (`proxy` + `wss`).

## Тест без Minecraft
```
python tools/fake_client.py ws://localhost:8765 <токен> TestPlayer
```
команды fake-клиента: `status hypixel.net`, `command /l testkey`, `quit`.
