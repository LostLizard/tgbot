# Телеграм-бот «Техническое меню ресторана AROMA»
#
# Бот для персонала: показывает полное техническое меню
# (состав, способ приготовления, подача, аллергены).
#
# Меню загружается из Excel:
#   1) Яндекс.Диск (первый приоритет)
#   2) Google Sheets (резерв, если Яндекс недоступен)

import os

from dotenv import load_dotenv

load_dotenv()

# --- Токен бота (получить у @BotFather) ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# --- Источники меню ---
# Яндекс.Диск: ссылка на просмотр документа (docs.yandex.ru/view/d/...)
YANDEX_URL = os.getenv(
    "YANDEX_URL",
    "https://docs.yandex.ru/view/d/EXkkK1_gh5Yidw-R4VR_KSPegnqahzm72s0qoIz-cKg6dC1YR3ljcjJhZw",
)

# Google Sheets: ID таблицы (берётся из ссылки /spreadsheets/d/<ID>/edit)
GOOGLE_SHEET_ID = os.getenv(
    "GOOGLE_SHEET_ID",
    "1JXy4B539T_hAe9Y3DUgOFKuPAWSj27zA_L0pehWgdfY",
)

# Вкладки Excel, которые нужно пропустить.
# «Утро» — это сводная/устаревшая вкладка со всем меню целиком;
# актуальные данные лежат в отдельных вкладках-категориях.
SKIP_SHEETS = [s.strip() for s in os.getenv("SKIP_SHEETS", "Утро").split(",") if s.strip()]

# Как часто перечитывать Excel (в секундах). Меню кэшируется в памяти.
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "3600"))

# Расписание проверки меню (время МСК, через запятую). Формат HH:MM.
CHECK_SCHEDULE = os.getenv("CHECK_SCHEDULE", "23:00,06:00")

# Часовой пояс для расписания.
CHECK_TZ = os.getenv("CHECK_TZ", "Europe/Moscow")

# --- Обход блокировок Telegram (РФ) ---
# HTTP/HTTPS-прокси для Bot API, например: http://127.0.0.1:8080
# (SOCKS5 тоже поддерживается, если указать socks5://127.0.0.1:1080)
TELEGRAM_PROXY = os.getenv("TELEGRAM_PROXY", "")

# Альтернативно: привязать api.telegram.org к конкретному IP
# (если часть IP Telegram доступна, а часть заблокирована).
# Пример: 149.154.167.220
TELEGRAM_API_IP = os.getenv("TELEGRAM_API_IP", "")

# Задержка (сек) между попытками загрузки в Яндексе и Гугле
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "30"))
