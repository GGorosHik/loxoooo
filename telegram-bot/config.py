"""Конфигурация из переменных окружения / файла .env. Секреты в коде не хранятся."""
import json
import os
import sys
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent
ENV_FILE = BOT_DIR / ".env"
ADMIN_FILE = BOT_DIR / "admins.json"   # админы, добавленные через бота, живут здесь


def _load_env_file(path: Path) -> None:
    """Мини-загрузчик .env: берём только то, чего ещё нет в os.environ."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_env_file(ENV_FILE)


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        sys.stderr.write(
            f"[config] ОШИБКА: не задано {name}.\n"
            f"         Открой файл .env рядом с main.py и заполни BOT_TOKEN и ADMIN_ID,\n"
            f"         затем запусти start_bot.bat снова.\n"
        )
        sys.exit(1)
    return value


def _ints_required(name: str) -> tuple[int, ...]:
    raw = _required(name)
    parts = [
        chunk
        for chunk in (piece.strip() for piece in raw.replace(";", ",").replace(" ", ",").split(","))
        if chunk
    ]
    ids: list[int] = []
    for piece in parts:
        try:
            ids.append(int(piece))
        except ValueError:
            sys.stderr.write(f"[config] ОШИБКА: {name}={raw!r} — элементы должны быть целыми числами (Telegram user id).\n")
            sys.exit(1)
    if not ids:
        sys.stderr.write(f"[config] ОШИБКА: {name} пуст.\n")
        sys.exit(1)
    return tuple(ids)


BOT_TOKEN: str = _required("BOT_TOKEN")
# ADMIN_ID можно указывать один или несколько через запятую: 123456, 654321
# первичные идут из .env, добавленные через бота — из admins.json
_ENV_ADMIN_SEED: tuple[int, ...] = _ints_required("ADMIN_ID")


def _load_extra_admins() -> tuple[int, ...]:
    try:
        data = json.loads(ADMIN_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return tuple(int(x) for x in data
                         if isinstance(x, (int, str)) and str(x).strip().lstrip("-").isdigit())
    except Exception:
        pass
    return ()


ADMIN_IDS: tuple[int, ...] = tuple(dict.fromkeys(_ENV_ADMIN_SEED + _load_extra_admins()))
ADMIN_ID: int = ADMIN_IDS[0]  # основной (первый в списке): ему лично шлётся меню и приветствие


def _save_admins() -> None:
    """В файл пишем только добавленных (не первичные из .env)."""
    extra = [aid for aid in ADMIN_IDS if aid not in _ENV_ADMIN_SEED]
    try:
        ADMIN_FILE.write_text(json.dumps(extra), encoding="utf-8")
    except Exception:
        pass


def add_admin_id(new_id: int) -> bool:
    """Добавить админа по ID. False — уже есть или ID мусорный."""
    global ADMIN_IDS
    new_id = int(new_id)
    if new_id <= 0 or new_id in ADMIN_IDS:
        return False
    ADMIN_IDS = tuple(dict.fromkeys(ADMIN_IDS + (new_id,)))
    _save_admins()
    return True


def remove_admin_id(target: int) -> bool:
    """Убрать админа. Первичные из .env не отзываются."""
    global ADMIN_IDS
    target = int(target)
    if target in _ENV_ADMIN_SEED or target not in ADMIN_IDS:
        return False
    ADMIN_IDS = tuple(aid for aid in ADMIN_IDS if aid != target)
    _save_admins()
    return True

WS_HOST: str = os.environ.get("WS_HOST", "0.0.0.0").strip()
try:
    WS_PORT: int = int(os.environ.get("WS_PORT", "8765"))
except ValueError:
    sys.stderr.write("[config] ОШИБКА: WS_PORT должен быть числом.\n")
    sys.exit(1)

DB_PATH: str = os.environ.get("DB_PATH", "data/modconnect.db").strip()
LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO").upper().strip()
WSS_CERT: str = os.environ.get("WSS_CERT", "").strip()
WSS_KEY: str = os.environ.get("WSS_KEY", "").strip()

# ── SOCKS-прокси для Telegram (api.telegram.org). WS-сервер его минует ──
PROXY_URL: str = os.environ.get("PROXY_URL", "").strip()
if not PROXY_URL:
    _phost = os.environ.get("PROXY_HOST", "").strip()
    _pport = os.environ.get("PROXY_PORT", "").strip()
    if _phost and _pport:
        _puser = os.environ.get("PROXY_USER", "").strip()
        _ppass = os.environ.get("PROXY_PASS", "").strip()
        _auth = ""
        if _puser or _ppass:
            try:
                from urllib.parse import quote
                _auth = f"{quote(_puser, safe='')}:{quote(_ppass, safe='')}@"
            except Exception:
                _auth = f"{_puser}:{_ppass}@"
        PROXY_URL = f"socks5://{_auth}{_phost}:{_pport}"


def proxy_display() -> str:
    """Адрес прокси для логов — без пароля."""
    if not PROXY_URL:
        return "нет"
    try:
        head, _, tail = PROXY_URL.partition("://")
        cred, _, hostport = tail.rpartition("@")
        user = cred.split(":", 1)[0] if cred else ""
        return f"{head}://{user}:***@{hostport}"
    except Exception:
        return "задан (детали скрыты)"
