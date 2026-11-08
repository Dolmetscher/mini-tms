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

# LQA-скоринг (оценка качества перевода ревьюером): штрафные баллы за ошибку
# и порог приемки. Практика LQA-методологий (memoQ LQA / MQM): minor — мелкие
# стилистические шероховатости, major — заметные ошибки, critical — искажение
# смысла/ Unsafe-ошибки. Оценка снижается на штраф×1000/слов, «зачет» — от порога.
LQA_PENALTY = {  # severity -> штрафные баллы
    "minor": float(os.environ.get("TMS_LQA_PENALTY_MINOR", "1")),
    "major": float(os.environ.get("TMS_LQA_PENALTY_MAJOR", "5")),
    "critical": float(os.environ.get("TMS_LQA_PENALTY_CRITICAL", "10")),
}
LQA_PASS_SCORE = float(os.environ.get("TMS_LQA_PASS_SCORE", "95.0"))
LQA_CATEGORIES = ("accuracy", "terminology", "fluency", "style", "locale", "consistency")
LQA_SEVERITIES = ("minor", "major", "critical")

# Прайс-листы и котировки (биллинг). Категории совпадают с анализом объема:
# ставка за слово применяется к «сырым» словам категории (практика котировок
# Trados/memoQ: скидка зашита в ставку категории, а не в объем). Дефолты —
# иллюстративные; реальный прайс задается в прайс-листе организации.
RATE_CATEGORIES = (
    "exact", "fuzzy_95_99", "fuzzy_85_94", "fuzzy_75_84",
    "fuzzy_60_74", "repetition", "no_match",
)
RATE_DEFAULT_CURRENCY = os.environ.get("TMS_RATE_CURRENCY", "EUR")
RATE_DEFAULTS: dict[str, float] = {
    "exact": 0.03,
    "fuzzy_95_99": 0.06,
    "fuzzy_85_94": 0.075,
    "fuzzy_75_84": 0.085,
    "fuzzy_60_74": 0.10,
    "repetition": 0.03,
    "no_match": 0.10,
}

# Сегментные локи («кто редактирует — тот блокирует»): TTL мягкого лока.
# Клиент продлевает лок heartbeat-ом, пока сегмент открыт; лок старше TTL
# считается просроченным (вкладка закрылась без снятия) и перехватывается.
SEGMENT_LOCK_TTL_SECONDS = int(os.environ.get("TMS_SEGMENT_LOCK_TTL", "600"))

# RX-проверки QA: ограничение длины паттерна правила (гигиена входных данных).
QA_REGEX_MAX_PATTERN = int(os.environ.get("TMS_QA_REGEX_MAX_PATTERN", "500"))
