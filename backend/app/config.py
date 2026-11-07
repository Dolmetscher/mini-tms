import os

SECRET_KEY = os.environ.get(
    "TMS_SECRET_KEY",
    "dev-secret-do-not-use-in-production-0123456789abcdef",
)
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("TMS_TOKEN_TTL_MINUTES", "720"))

DATABASE_URL = os.environ.get("TMS_DATABASE_URL", "sqlite:///./tms.db")

# Каталог для исходных файлов документов (§4: Документ хранит исходный файл).
DATA_DIR = os.environ.get("TMS_DATA_DIR", "./data")

FUZZY_MIN_SCORE = int(os.environ.get("TMS_FUZZY_MIN_SCORE", "60"))
FUZZY_MAX_SUGGESTIONS = 5

# Машинный перевод (FR-08): none | libretranslate | mock. Ключи только на сервере.
MT_PROVIDER = os.environ.get("TMS_MT_PROVIDER", "none")
MT_URL = os.environ.get("TMS_MT_URL", "https://libretranslate.com/translate")
MT_API_KEY = os.environ.get("TMS_MT_API_KEY", "")
MT_TIMEOUT_SECONDS = int(os.environ.get("TMS_MT_TIMEOUT", "10"))

# Напоминалки о дедлайне (§8): по умолчанию за 24 и за 2 часа.
REMINDER_OFFSETS_HOURS = (24, 2)
REMINDER_CHECK_SECONDS = 60
