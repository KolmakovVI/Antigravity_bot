import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"

# Antigravity paths
HOME_DIR = Path.home()
GEMINI_DIR = HOME_DIR / ".gemini"
ANTIGRAVITY_APP_DATA = GEMINI_DIR / "antigravity"
ANTIGRAVITY_CONFIG_DIR = GEMINI_DIR / "config"
ANTIGRAVITY_PROJECTS_DIR = ANTIGRAVITY_CONFIG_DIR / "projects"
ANTIGRAVITY_CONVERSATIONS_DIR = ANTIGRAVITY_APP_DATA / "conversations"
ANTIGRAVITY_BRAIN_DIR = ANTIGRAVITY_APP_DATA / "brain"
ANTIGRAVITY_LOGS_DIR = HOME_DIR / "AppData" / "Roaming" / "Antigravity" / "logs"

def reload_env():
    if ENV_PATH.exists():
        load_dotenv(dotenv_path=ENV_PATH, override=True)
    else:
        load_dotenv(override=True)

reload_env()

def get_api_id() -> int | None:
    val = os.getenv("TELEGRAM_API_ID")
    if not val:
        reload_env()
        val = os.getenv("TELEGRAM_API_ID")
    if val and val.strip().isdigit():
        return int(val.strip())
    return None

def get_api_hash() -> str:
    val = os.getenv("TELEGRAM_API_HASH")
    if not val:
        reload_env()
        val = os.getenv("TELEGRAM_API_HASH")
    return (val or "").strip()

def get_session_path() -> str:
    path_val = os.getenv("TELEGRAM_SESSION_PATH")
    if not path_val:
        reload_env()
        path_val = os.getenv("TELEGRAM_SESSION_PATH")
    if path_val and path_val.strip():
        return str(Path(path_val.strip()).resolve())
    return str(BASE_DIR / "telegram.session")

def get_phone() -> str:
    val = os.getenv("TELEGRAM_PHONE")
    if not val:
        reload_env()
        val = os.getenv("TELEGRAM_PHONE")
    return (val or "").strip()

def get_bot_token() -> str:
    val = os.getenv("TELEGRAM_BOT_TOKEN")
    if not val:
        reload_env()
        val = os.getenv("TELEGRAM_BOT_TOKEN")
    return (val or "").strip()

def set_env_variable(key: str, value: str):
    """Write or update a key in .env file safely."""
    lines = []
    found = False
    if ENV_PATH.exists():
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
    
    new_lines = []
    for line in lines:
        if line.strip().startswith(f"{key}="):
            new_lines.append(f"{key}={value}\n")
            found = True
        else:
            new_lines.append(line)
    if not found:
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines.append("\n")
        new_lines.append(f"{key}={value}\n")
    
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(new_lines)
    os.environ[key] = value

def get_allowed_user_ids() -> list[int]:
    val = os.getenv("ALLOWED_TELEGRAM_USER_IDS", "")
    if not val:
        reload_env()
        val = os.getenv("ALLOWED_TELEGRAM_USER_IDS", "")
    if not val.strip():
        return []
    res = []
    for item in val.split(","):
        item = item.strip()
        if item.isdigit() or (item.startswith("-") and item[1:].isdigit()):
            res.append(int(item))
    return res

def add_allowed_user_id(user_id: int):
    ids = get_allowed_user_ids()
    if user_id not in ids:
        ids.append(user_id)
        set_env_variable("ALLOWED_TELEGRAM_USER_IDS", ",".join(str(i) for i in ids))

def is_configured() -> bool:
    api_id = get_api_id()
    api_hash = get_api_hash()
    return bool(api_id and api_hash)
