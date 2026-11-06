"""Фейковый Minecraft-клиент: тест бота без игры.
Запуск: python tools/fake_client.py ws://host:8765 <токен> [ником]
Команды в stdin: status <сервер> | command /l пароль | quit"""
import asyncio
import json
import sys
import uuid

from websockets.asyncio.client import connect


async def main() -> None:
    if len(sys.argv) < 3:
        print("usage: python fake_client.py ws://host:8765 <token> [ником]")
        sys.exit(1)
    url, token = sys.argv[1], sys.argv[2]
    nickname = sys.argv[3] if len(sys.argv) > 3 else "TestPlayer"
    iid = str(uuid.uuid4())

    async with connect(url) as ws:
        await ws.send(json.dumps({
            "type": "hello",
            "protocol": 1,
            "token": token,
            "installation_id": iid,
            "nickname": nickname,
        }))
        reply = json.loads(await ws.recv())
        print("<<<", reply)

        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(20)
                await ws.send(json.dumps({"type": "keepalive"}))

        hb = asyncio.create_task(heartbeat())

        async def reader() -> None:
            async for raw in ws:
                msg = json.loads(raw)
                print("<<<", msg)
                if msg.get("type") == "shutdown":
                    print("*** сервер просит закрыть игру — выходим")
                    sys.exit(0)

        rd = asyncio.create_task(reader())

        loop = asyncio.get_running_loop()
        while True:
            line = await loop.run_in_executor(None, input, "> ")
            parts = line.strip().split(" ", 1)
            if not parts or not parts[0]:
                continue
            if parts[0] == "quit":
                break
            if parts[0] == "status":
                await ws.send(json.dumps({
                    "type": "status",
                    "nickname": nickname,
                    "server": parts[1] if len(parts) > 1 else "",
                }))
                print(">>> status", parts[1] if len(parts) > 1 else "")
            elif parts[0] == "command" and len(parts) > 1:
                raw_cmd = parts[1]
                cmd = raw_cmd.split(" ", 1)[0]
                await ws.send(json.dumps({"type": "command", "command": cmd, "raw": raw_cmd}))
                print(">>> command", raw_cmd)
            else:
                print("unknown:", line.strip())

        hb.cancel()
        rd.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
