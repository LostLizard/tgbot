"""Загрузка и разбор технического меню из Excel.

Приоритет источников:
  1. Яндекс.Диск (через WOPI-эндпоинт просмотрщика docs.yandex.ru).
  2. Google Sheets (резерв).

Формат файла (xlsx):
  - Каждая вкладка = категория меню.
  - Колонка A = заголовки разделов и служебные примечания
    (иногда в колонке A встречаются и блюда, например «Ассорти сыров»).
  - Колонка B = блюда: первая строка — название, остальное — описание.
"""

import asyncio
import io
import json
import re
import time

import aiohttp
import openpyxl

import config

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)


class MenuError(RuntimeError):
    """Ошибка загрузки меню."""


class MenuLoader:
    """Скачивает и парсит Excel, кэширует результат в памяти."""

    def __init__(self):
        self._menu = None
        self._source = None
        self._loaded_at = 0.0
        self._lock = asyncio.Lock()

    @property
    def source(self):
        return self._source

    @property
    def cached_menu(self):
        """Текущее закэшированное меню (может быть None)."""
        return self._menu

    async def get_menu(self, force: bool = False):
        """Возвращает (menu, source). При необходимости перечитывает файл."""
        if (
            not force
            and self._menu is not None
            and (time.time() - self._loaded_at) < config.CACHE_TTL_SECONDS
        ):
            return self._menu, self._source

        async with self._lock:
            # повторная проверка на случай одновременного запроса
            if (
                not force
                and self._menu is not None
                and (time.time() - self._loaded_at) < config.CACHE_TTL_SECONDS
            ):
                return self._menu, self._source

            data, source = await self._download()
            self._menu = self._parse(data)
            self._source = source
            self._loaded_at = time.time()
            return self._menu, self._source

    async def _download(self):
        """Скачивает xlsx: сначала Яндекс, при неудаче — Google."""
        last_error = None

        try:
            data = await self._fetch_yandex()
            return data, "yandex"
        except Exception as exc:  # noqa: BLE001 — фиксируем и пробуем резерв
            last_error = exc

        try:
            data = await self._fetch_google()
            return data, "google"
        except Exception as exc:  # noqa: BLE001
            last_error = exc

        raise MenuError(
            "Не удалось загрузить меню ни из Яндекса, ни из Google. "
            f"Последняя ошибка: {last_error}"
        )

    # ------------------------------------------------------------------ #
    # Яндекс.Диск
    # ------------------------------------------------------------------ #
    async def _fetch_yandex(self) -> bytes:
        timeout = aiohttp.ClientTimeout(total=config.HTTP_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": _UA}) as s:
            # 1) HTML страницы просмотрщика содержит JSON с WOPI-токеном
            async with s.get(config.YANDEX_URL) as resp:
                if resp.status != 200:
                    raise MenuError(f"Яндекс: страница вернула HTTP {resp.status}")
                html = await resp.text()

            cfg = self._extract_client_config(html)
            office = cfg.get("officeActionData") or {}
            resource_url = office.get("resource_url")
            access_token = office.get("access_token")
            if not resource_url or not access_token:
                raise MenuError("Яндекс: не найден WOPI-токен в странице")

            # 2) WOPI GetFile — отдаёт исходный xlsx
            async with s.get(
                resource_url + "/contents",
                params={"access_token": access_token},
            ) as resp:
                if resp.status != 200:
                    raise MenuError(f"Яндекс: WOPI вернул HTTP {resp.status}")
                return await resp.read()

    @staticmethod
    def _extract_client_config(html: str) -> dict:
        m = re.search(
            r'<script[^>]*id=["\']client-config["\'][^>]*>(.*?)</script>',
            html,
            re.DOTALL,
        )
        if not m:
            raise MenuError("Яндекс: не найден блок client-config")
        raw = m.group(1).strip()
        # JSON внутри script может быть обёрнут в HTML-комментарии
        raw = raw.strip("<!-- -->").strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MenuError(f"Яндекс: ошибка разбора client-config: {exc}") from exc

    # ------------------------------------------------------------------ #
    # Google Sheets
    # ------------------------------------------------------------------ #
    async def _fetch_google(self) -> bytes:
        url = (
            "https://docs.google.com/spreadsheets/d/"
            f"{config.GOOGLE_SHEET_ID}/export?format=xlsx"
        )
        timeout = aiohttp.ClientTimeout(total=config.HTTP_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": _UA}) as s:
            async with s.get(url) as resp:
                if resp.status != 200:
                    raise MenuError(f"Google: export вернул HTTP {resp.status}")
                return await resp.read()

    # ------------------------------------------------------------------ #
    # Разбор Excel
    # ------------------------------------------------------------------ #
    @staticmethod
    def _split_dish(text: str):
        """Первая строка — название, остальное — описание."""
        parts = text.split("\n", 1)
        name = parts[0].strip()
        description = parts[1].strip() if len(parts) > 1 else ""
        return name, description

    @staticmethod
    def _classify_a_cell(text: str):
        """Определяет, что лежит в колонке A.

        Возвращает: 'dish' | 'header' | 'note'.
        """
        lines = text.split("\n")
        first = lines[0].strip()
        if len(lines) > 1 and len(first) <= 50:
            # короткое название + описание => это блюдо в колонке A
            return "dish"
        if len(first) <= 30:
            # короткая однострочная строка => заголовок раздела
            return "header"
        return "note"

    # ------------------------------------------------------------------ #
    # Разбор Excel
    # ------------------------------------------------------------------ #
    @staticmethod
    def _split_dish(text: str):
        """Первая строка — название, остальное — описание."""
        parts = text.split("\n", 1)
        name = parts[0].strip()
        description = parts[1].strip() if len(parts) > 1 else ""
        return name, description

    @staticmethod
    def _classify_a_cell(text: str):
        """Определяет, что лежит в колонке A.

        Возвращает: 'dish' | 'header' | 'note'.
        """
        lines = text.split("\n")
        first = lines[0].strip()
        if len(lines) > 1 and len(first) <= 50:
            # короткое название + описание => это блюдо в колонке A
            return "dish"
        if len(first) <= 30:
            # короткая однострочная строка => заголовок раздела
            return "header"
        return "note"

    def _parse(self, data: bytes):
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
        categories = []
        for ws in wb.worksheets:
            title = (ws.title or "").strip()
            if not title or title in config.SKIP_SHEETS:
                continue

            sections = []
            current = {"title": "", "note": "", "dishes": []}

            for row in ws.iter_rows(min_col=1, max_col=2, values_only=True):
                a = (str(row[0]).strip() if row[0] is not None else "")
                b = (str(row[1]).strip() if row[1] is not None else "")

                if a:
                    kind = self._classify_a_cell(a)
                    if kind == "dish":
                        name, desc = self._split_dish(a)
                        current["dishes"].append({"name": name, "description": desc})
                    elif kind == "header":
                        if current["title"] or current["dishes"] or current["note"]:
                            sections.append(current)
                        current = {"title": a, "note": "", "dishes": []}
                    else:  # note
                        current["note"] = (
                            (current["note"] + "\n" + a).strip()
                            if current["note"]
                            else a
                        )

                if b:
                    name, desc = self._split_dish(b)
                    current["dishes"].append({"name": name, "description": desc})

            if current["title"] or current["dishes"] or current["note"]:
                sections.append(current)

            categories.append({"title": title, "sections": sections})

        if not categories:
            raise MenuError("Excel не содержит ни одной категории меню")
        return categories
