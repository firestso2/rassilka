"""
Конфиг бота — читается из переменных окружения или .env файла.
"""

import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.environ["BOT_TOKEN"]

# Список Telegram user_id администраторов (через запятую в .env)
_admin_raw = os.environ.get("ADMIN_IDS", "")
ADMIN_IDS: list[int] = [int(x.strip()) for x in _admin_raw.split(",") if x.strip().isdigit()]

# Общий API ID/Hash для входа "по номеру телефона" (без ввода API пользователем)
# Получить на https://my.telegram.org → App configuration
SHARED_API_ID: int = int(os.environ["SHARED_API_ID"]) if os.environ.get("SHARED_API_ID") else 0
SHARED_API_HASH: str = os.environ.get("SHARED_API_HASH", "")
