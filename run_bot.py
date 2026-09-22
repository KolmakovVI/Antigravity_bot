import sys
import os
import asyncio
from pathlib import Path

# Fix Windows console UTF-8 encoding for emojis
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Add current dir to python path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
import log_config
from bridge import AntigravityBridge

log_config.setup_logging()

def main():
    print("=" * 65)
    print("🤖 Google Antigravity Telegram Bot Runner")
    print("=" * 65)

    # 1. Check Antigravity Connection
    print("\n🔍 Проверка подключения к Google Antigravity...")
    bridge = AntigravityBridge()
    if bridge.detect_connection():
        print(f"✅ Antigravity обнаружен!")
        print(f"   • Локальный порт: {bridge.port}")
        print(f"   • CSRF Token: {bridge.csrf_token[:8]}...")
        projects = bridge.list_projects()
        chats = bridge.list_chats()
        print(f"   • Найдено проектов: {len(projects)}")
        print(f"   • Найдено диалогов: {len(chats)}")
    else:
        print("⚠️ Antigravity не обнаружен!")
        print("   Убедитесь, что приложение Antigravity запущено на вашем ПК.")
        print("   (Бот продолжит запуск и попытается переподключиться автоматически).")

    # 2. Check Telegram Bot Token
    token = config.get_bot_token()
    if not token or token == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        print("\n" + "!" * 65)
        print("❌ Не указан TELEGRAM_BOT_TOKEN в файле .env!")
        print("!" * 65)
        print("\nИнструкция для запуска:")
        print("1. Откройте Telegram и напишите боту @BotFather: https://t.me/BotFather")
        print("2. Отправьте команду /newbot и задайте имя и username.")
        print("3. Скопируйте полученный токен (например: 123456789:ABCdefGh...)")
        print(f"4. Вставьте его в файл {config.ENV_PATH} в строчку:")
        print("   TELEGRAM_BOT_TOKEN=ваш_токен_сюда\n")
        
        # Interactive prompt if running in terminal
        try:
            user_input = input("👉 Или введите токен прямо сейчас (или Enter для выхода): ").strip()
            if user_input:
                config.set_env_variable("TELEGRAM_BOT_TOKEN", user_input)
                token = user_input
                print("✅ Токен успешно сохранён в .env!")
            else:
                return
        except (EOFError, KeyboardInterrupt):
            return

    # 3. Start Bot
    print("\n🚀 Запуск Telegram бота...")
    from bot import start_bot
    try:
        asyncio.run(start_bot())
    except (KeyboardInterrupt, SystemExit):
        print("\n🛑 Бот остановлен пользователем.")

if __name__ == "__main__":
    main()

