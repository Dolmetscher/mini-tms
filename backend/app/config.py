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
# Ручной конкорданс (поиск вхождения слова/фразы): список длиннее автоподсказок.
CONCORDANCE_MAX_RESULTS = 20

# Анализ объема (котировка): доля «оплачиваемых» слов по категориям совпадений.
# Веса — распространенная industry-практика (аналог банд Trados/memoQ);
# настраиваются env-переменными при другом прайсе.
ANALYSIS_EXACT_WEIGHT = float(os.environ.get("TMS_ANALYSIS_EXACT_WEIGHT", "0.30"))
ANALYSIS_REPETITION_WEIGHT = float(os.environ.get("TMS_ANALYSIS_REPETITION_WEIGHT", "0.30"))
ANALYSIS_BANDS = (  # (нижняя граница %, верхняя граница %, вес)
    (95, 99, 0.60),
    (85, 94, 0.75),
    (75, 84, 0.85),
    (60, 74, 1.00),
)

# Машинный перевод (FR-08): none | libretranslate | mock. Ключи только на сервере.
MT_PROVIDER = os.environ.get("TMS_MT_PROVIDER", "none")
MT_URL = os.environ.get("TMS_MT_URL", "https://libretranslate.com/translate")
MT_API_KEY = os.environ.get("TMS_MT_API_KEY", "")
MT_TIMEOUT_SECONDS = int(os.environ.get("TMS_MT_TIMEOUT", "10"))

# Напоминалки о дедлайне (§8): по умолчанию за 24 и за 2 часа.
REMINDER_OFFSETS_HOURS = (24, 2)
REMINDER_CHECK_SECONDS = 60
