"""
Конфиг бота — читается из переменных окружения или .env файла.
"""

import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.environ["8692746932:AAGwi8DTOm3EopPNTrdHthk4tC-A7VsS0ek"]

# Список Telegram user_id администраторов (через запятую в .env)
_admin_raw = os.environ.get("8416449434", "")
ADMIN_IDS: list[int] = [int(x.strip()) for x in _admin_raw.split(",") if x.strip().isdigit()]
