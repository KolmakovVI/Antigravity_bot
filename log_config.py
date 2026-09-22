import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler

LOG_FILE = Path(__file__).resolve().parent / "bot.log"

def setup_logging(level=logging.INFO):
    """Configures console and file logging with UTF-8 support."""
    logger = logging.getLogger()
    logger.setLevel(level)

    # Avoid duplicate handlers if called multiple times
    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] [%(name)s]: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # File handler with UTF-8 and 5MB rotation
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    logger.addHandler(file_handler)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)
    logger.addHandler(console_handler)

    return logger

def get_recent_logs(max_lines: int = 30) -> str:
    """Read the last N lines from the log file."""
    if not LOG_FILE.exists():
        return "Лог-файл пока пуст."
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
            if not lines:
                return "Лог-файл пуст."
            recent = lines[-max_lines:]
            return "".join(recent)
    except Exception as e:
        return f"Ошибка чтения логов: {e}"
