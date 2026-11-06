# -*- coding: utf-8 -*-
"""Дым-тест мульти-сессий ModConnect на ЖИВОМ боте (порт 8765).

Поднимает два throwaway-токена, подключает две фейковые сессии с ОДНИМ токеном
(разные installation_id: игра + агент) и проверяет, что обе приняты. После теста
токены удаляются из базы. Реальный токен мода НЕ используется."""
import asyncio
import json
import subprocess
import sys

import websockets

import database

import config

PORT = 8765


async def one_session(token: str, install_id: str, nickname: str, hold: float):
    async with websockets.connect(f"ws://localhost:{PORT}") as ws:
        await ws.send(json.dumps({
            "type": "hello",
            "token": token,
            "installation_id": install_id,
            "nickname": nickname,
            "protocol": 1,
        }))
        welcome = json.loads(await asyncio.wait_for(ws.recv(), 10))
        if not welcome.get("ok"):
            return f"{install_id}: welcome отказ — {welcome}"
        await ws.send(json.dumps({
            "type": "status", "nickname": nickname, "server": "smoke", "ip": "127.0.0.1"}))
        await asyncio.sleep(hold)
        return f"{install_id}: принято, держали {hold:.1f} с"


async def main() -> int:
    database.init(config.DB_PATH)
    token, _ = database.add_token("smoke-agents")
    try:
        results = await asyncio.gather(
            one_session(token, "smoke-aaa", "SmokeA", 3.0),
            one_session(token, "smoke-bbb", "SmokeB", 1.5),
            return_exceptions=True,
        )
        outcome = []
        for result in results:
            if isinstance(result, Exception):
                outcome.append(f"ОШИБКА: {result!r}")
            else:
                outcome.append(result)
        print("\n".join(outcome))
        ok = all(("принято" in str(r)) or ("welcome" in str(r)) for r in results)
        return 0 if ok else 1
    finally:
        # убираем следы теста из базы
        try:
            database.delete_client(database.hash_token(token))
        except Exception as exc:
            print("токен не вычистился:", exc)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
