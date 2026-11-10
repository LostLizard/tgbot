"""Точка входа Telegram-бота «Техническое меню AROMA».

Команды:
  /start    — список разделов меню
  /menu     — то же самое
  /search   — поиск блюда по названию (пример: /search цезарь)
  /refresh  — принудительно перечитать Excel

При изменении меню (периодически проверяется) бот рассылает
уведомления всем пользователям, которые с ним общались.
"""

import asyncio
import datetime
import difflib
import html
import json
import logging
import os
import re
import zoneinfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiohttp import web

import config
from menu_loader import MenuLoader

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("aroma-bot")

router = Router()
loader = MenuLoader()

PAGE_SIZE = 10          # блюд на одной странице
MAX_BUTTON_TEXT = 40    # обрезаем длинные названия на кнопках
MAX_NOTE_LEN = 140      # обрезаем примечания в списке разделов

USERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "users.json")
os.makedirs(os.path.dirname(USERS_FILE), exist_ok=True)


def esc(s):
    """Экранирует текст для HTML-разметки."""
    return html.escape(str(s if s is not None else ""))


# ---------------------------------------------------------------------- #
# Хранилище пользователей (для рассылки)
# ---------------------------------------------------------------------- #
class UserStore:
    def __init__(self, path):
        self.path = path
        self.users = set()
        self._load()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                self.users = set(json.load(f))
        except (FileNotFoundError, ValueError, OSError):
            self.users = set()

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(sorted(self.users), f)
        except OSError:
            log.exception("Не удалось сохранить %s", self.path)

    def add(self, user_id):
        if user_id not in self.users:
            self.users.add(user_id)
            self._save()

    def remove(self, user_id):
        if user_id in self.users:
            self.users.discard(user_id)
            self._save()


users = UserStore(USERS_FILE)


# ---------------------------------------------------------------------- #
# Вспомогательные функции
# ---------------------------------------------------------------------- #
def flatten_dishes(category):
    """Плоский список (блюдо, название раздела) для категории."""
    result = []
    for section in category["sections"]:
        for dish in section["dishes"]:
            result.append((dish, section.get("title", "")))
    return result


def split_long_text(text: str, limit: int = 4000):
    """Делит текст на куски не длиннее limit по границам строк."""
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut == -1 or cut < limit // 2:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        chunks.append(text)
    return chunks


def categories_keyboard(menu):
    kb = InlineKeyboardBuilder()
    for i, cat in enumerate(menu):
        kb.button(text=cat["title"], callback_data=f"cat:{i}:0")
    kb.adjust(2)
    return kb.as_markup()


def dishes_keyboard(category, ci, page):
    dishes = flatten_dishes(category)
    total_pages = max(1, (len(dishes) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(max(0, page), total_pages - 1)
    start = page * PAGE_SIZE
    chunk = dishes[start : start + PAGE_SIZE]

    kb = InlineKeyboardBuilder()
    for offset, (dish, _sec) in enumerate(chunk):
        abs_index = start + offset
        label = dish["name"] if len(dish["name"]) <= MAX_BUTTON_TEXT else dish["name"][: MAX_BUTTON_TEXT - 1] + "…"
        kb.button(text=label, callback_data=f"dish:{ci}:{abs_index}:{page}")

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"cat:{ci}:{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"cat:{ci}:{page + 1}"))
    if nav:
        kb.row(*nav)
    kb.row(InlineKeyboardButton(text="⬅️ К разделам", callback_data="cats"))
    return kb.as_markup(), page, total_pages


def category_text(category, page, total_pages):
    lines = [f"📂 <b>{esc(category['title'])}</b>"]
    for section in category["sections"]:
        if section.get("title"):
            lines.append(f"\n▫️ <b>{esc(section['title'])}</b>")
        note = (section.get("note") or "").strip()
        if note:
            note = esc(note).replace("\n", " ")
            if len(note) > MAX_NOTE_LEN:
                note = note[: MAX_NOTE_LEN - 1] + "…"
            lines.append(note)
    if total_pages > 1:
        lines.append(f"\n<i>Страница {page + 1} из {total_pages}</i>")
    lines.append("\nВыберите блюдо:")
    return "\n".join(lines)


# ---------------------------------------------------------------------- #
# Сравнение меню (детект изменений)
# ---------------------------------------------------------------------- #
def flatten_map(menu):
    """{(категория, название): описание}."""
    result = {}
    for cat in menu:
        for sec in cat["sections"]:
            for dish in sec["dishes"]:
                result[(cat["title"], dish["name"])] = dish["description"]
    return result


def diff_menus(old, new):
    """Сравнивает старое и новое меню."""
    old_m = flatten_map(old)
    new_m = flatten_map(new)

    changed = []
    for key in old_m:
        if key in new_m and old_m[key] != new_m[key]:
            cat, name = key
            changed.append((cat, name, old_m[key], new_m[key]))

    added = [(cat, name) for (cat, name) in new_m if (cat, name) not in old_m]
    removed = [(cat, name) for (cat, name) in old_m if (cat, name) not in new_m]
    return {"changed": changed, "added": added, "removed": removed}


def has_changes(diff):
    return bool(diff["changed"] or diff["added"] or diff["removed"])


def build_summary(diff):
    lines = ["⚠️ <b>Внимание! Изменения в блюде!</b>"]
    if diff["changed"]:
        lines.append(f"\nИзменено блюд: {len(diff['changed'])}")
        for cat, name, _old, _new in diff["changed"]:
            lines.append(f"• <b>{esc(name)}</b>")
    if diff["added"]:
        lines.append("\n🆕 Новые блюда:")
        for cat, name in diff["added"]:
            lines.append(f"• {esc(name)} ({esc(cat)})")
    if diff["removed"]:
        lines.append("\n🗑 Убраны из меню:")
        for cat, name in diff["removed"]:
            lines.append(f"• {esc(name)} ({esc(cat)})")
    return "\n".join(lines)


def highlight_new_text(old_desc, new_desc):
    """Возвращает новый состав, где добавленные (новые) слова — жирным.

    Сравнивает старый и новый текст по словам (с сохранением пробелов и
    переносов строк) и оборачивает новые/изменённые фрагменты в <b>...</b>.
    """
    old = esc(old_desc)
    new = esc(new_desc)
    old_tokens = re.split(r"(\s+)", old)
    new_tokens = re.split(r"(\s+)", new)
    sm = difflib.SequenceMatcher(a=old_tokens, b=new_tokens, autojunk=False)
    out = []
    for op, _i1, _i2, j1, j2 in sm.get_opcodes():
        chunk = "".join(new_tokens[j1:j2])
        if op == "equal":
            out.append(chunk)
        elif op in ("insert", "replace"):
            if chunk.strip():
                out.append("<b>" + chunk + "</b>")
            else:
                out.append(chunk)
        # "delete" — старый текст, в новом его нет, пропускаем
    return "".join(out)


def build_detail(name, old_desc, new_desc):
    return (
        f"<b>{esc(name)}</b>\n\n"
        f"Состав:\n{esc(old_desc) if old_desc else '—'}\n\n"
        f"Новый состав:\n{highlight_new_text(old_desc, new_desc) if new_desc else '—'}"
    )


async def notify_users(bot: Bot, diff):
    """Рассылает уведомления всем известным пользователям."""
    summary = build_summary(diff)
    details = [
        build_detail(name, old_desc, new_desc)
        for _cat, name, old_desc, new_desc in diff["changed"]
    ]

    for uid in list(users.users):
        try:
            await bot.send_message(uid, summary, disable_web_page_preview=True)
            for detail in details:
                for chunk in split_long_text(detail):
                    await bot.send_message(uid, chunk, disable_web_page_preview=True)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if "Forbidden" in msg or "bot was blocked" in msg:
                users.remove(uid)
                log.info("Пользователь %s заблокировал бота, удалён из рассылки", uid)
            else:
                log.warning("Не удалось отправить уведомление %s: %s", uid, msg)


# ---------------------------------------------------------------------- #
# Трекинг пользователей (middleware)
# ---------------------------------------------------------------------- #
@router.message.middleware()
async def track_message(handler, event, data):
    if event.from_user:
        users.add(event.from_user.id)
    return await handler(event, data)


@router.callback_query.middleware()
async def track_callback(handler, event, data):
    if event.from_user:
        users.add(event.from_user.id)
    return await handler(event, data)


# ---------------------------------------------------------------------- #
# Команды
# ---------------------------------------------------------------------- #
@router.message(CommandStart())
async def cmd_start(message: Message):
    await show_categories(message)


@router.message(Command("menu"))
async def cmd_menu(message: Message):
    await show_categories(message)


async def show_categories(message: Message):
    try:
        menu, source = await loader.get_menu()
    except Exception as exc:  # noqa: BLE001
        log.exception("Ошибка загрузки меню")
        await message.answer(f"⚠️ Не удалось загрузить меню.\n{esc(exc)}")
        return

    text = "🍽 <b>Техническое меню AROMA</b>\n\nВыберите раздел:"
    await message.answer(text, reply_markup=categories_keyboard(menu))


@router.message(Command("refresh"))
async def cmd_refresh(message: Message):
    try:
        old = loader.cached_menu
        menu, source = await loader.get_menu(force=True)
    except Exception as exc:  # noqa: BLE001
        log.exception("Ошибка обновления меню")
        await message.answer(f"⚠️ Не удалось обновить меню.\n{esc(exc)}")
        return
    cats = len(menu)
    dishes = sum(len(flatten_dishes(c)) for c in menu)
    src = "Яндекс.Диск" if source == "yandex" else "Google Sheets"
    await message.answer(
        f"✅ Меню обновлено.\nИсточник: {src}\nРазделов: {cats}\nБлюд: {dishes}"
    )

    # если меню реально изменилось — тоже шлём уведомления
    if old is not None:
        diff = diff_menus(old, menu)
        if has_changes(diff):
            await notify_users(message.bot, diff)


@router.message(Command("notify_test"))
async def cmd_notify_test(message: Message):
    """Отправляет тестовое уведомление только этому пользователю."""
    try:
        menu, _ = await loader.get_menu()
    except Exception as exc:  # noqa: BLE001
        await message.answer(f"⚠️ Не удалось загрузить меню.\n{esc(exc)}")
        return

    # берём первое блюдо из «Пицца» как пример
    dish = None
    for cat in menu:
        if cat["title"] == "Пицца":
            for sec in cat["sections"]:
                if sec["dishes"]:
                    dish = sec["dishes"][0]
                    break
        if dish:
            break
    if dish is None:
        for cat in menu:
            for sec in cat["sections"]:
                if sec["dishes"]:
                    dish = sec["dishes"][0]
                    break
            if dish:
                break

    old_desc = dish["description"]
    new_desc = old_desc + "\n[ТЕСТ] Добавлен новый ингредиент."

    await message.answer(
        "⚠️ <b>Внимание! Изменения в блюде!</b>\n\n"
        f"Изменено блюд: 1\n• <b>{esc(dish['name'])}</b>"
    )
    for chunk in split_long_text(build_detail(dish["name"], old_desc, new_desc)):
        await message.answer(chunk)
    await message.answer("ℹ️ Это тестовое уведомление — отправлено только вам.")


@router.message(Command("search"))
async def cmd_search(message: Message, command: CommandObject):
    query = (command.args or "").strip()
    if not query:
        await message.answer("Укажите, что искать: /search <текст>")
        return
    try:
        menu, _ = await loader.get_menu()
    except Exception as exc:  # noqa: BLE001
        await message.answer(f"⚠️ Не удалось загрузить меню.\n{esc(exc)}")
        return

    query_l = query.lower()
    matches = []
    for ci, cat in enumerate(menu):
        for di, (dish, sec) in enumerate(flatten_dishes(cat)):
            hay = (dish["name"] + " " + dish["description"]).lower()
            if query_l in hay:
                matches.append((ci, di, cat["title"], sec, dish))

    if not matches:
        await message.answer(f"По запросу «{esc(query)}» ничего не найдено.")
        return

    matches = matches[:20]
    kb = InlineKeyboardBuilder()
    for ci, di, cat_title, sec, dish in matches:
        label = dish["name"] if len(dish["name"]) <= MAX_BUTTON_TEXT else dish["name"][: MAX_BUTTON_TEXT - 1] + "…"
        kb.button(text=label, callback_data=f"dish:{ci}:{di}:0")
    kb.adjust(1)

    lines = [f"🔎 Результаты по запросу «{esc(query)}»:"]
    for _, _, cat_title, sec, dish in matches[:10]:
        lines.append(f"• <b>{esc(dish['name'])}</b> — {esc(cat_title)}")
    if len(matches) > 10:
        lines.append(f"…и ещё {len(matches) - 10}")
    await message.answer("\n".join(lines), reply_markup=kb.as_markup())


# ---------------------------------------------------------------------- #
# Callback-обработчики
# ---------------------------------------------------------------------- #
@router.callback_query(F.data == "cats")
async def cb_categories(callback: CallbackQuery):
    try:
        menu, _ = await loader.get_menu()
    except Exception as exc:  # noqa: BLE001
        await callback.answer("Не удалось загрузить меню", show_alert=True)
        return
    await callback.message.edit_text(
        "🍽 <b>Техническое меню AROMA</b>\n\nВыберите раздел:",
        reply_markup=categories_keyboard(menu),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cat:"))
async def cb_category(callback: CallbackQuery):
    try:
        menu, _ = await loader.get_menu()
    except Exception as exc:  # noqa: BLE001
        await callback.answer("Не удалось загрузить меню", show_alert=True)
        return
    parts = callback.data.split(":")
    ci = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 0
    if ci >= len(menu):
        await callback.answer("Раздел не найден", show_alert=True)
        return
    category = menu[ci]
    kb, page, total_pages = dishes_keyboard(category, ci, page)
    await callback.message.edit_text(
        category_text(category, page, total_pages), reply_markup=kb
    )
    await callback.answer()


@router.callback_query(F.data.startswith("dish:"))
async def cb_dish(callback: CallbackQuery):
    try:
        menu, _ = await loader.get_menu()
    except Exception as exc:  # noqa: BLE001
        await callback.answer("Не удалось загрузить меню", show_alert=True)
        return
    parts = callback.data.split(":")
    ci = int(parts[1])
    di = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0
    category = menu[ci]
    dishes = flatten_dishes(category)
    if di >= len(dishes):
        await callback.answer("Блюдо не найдено", show_alert=True)
        return
    dish, sec = dishes[di]

    parts_text = [f"<b>{esc(dish['name'])}</b>"]
    if sec:
        parts_text.append(f"<i>{esc(sec)}</i>")
    if dish.get("description"):
        parts_text.append(esc(dish["description"]))
    body = "\n\n".join(parts_text)

    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Назад", callback_data=f"cat:{ci}:{page}")
    kb.button(text="📋 К разделам", callback_data="cats")

    chunks = split_long_text(body)
    if not chunks:
        chunks = [dish["name"]]
    await callback.message.edit_text(chunks[0], reply_markup=kb.as_markup())
    for chunk in chunks[1:]:
        await callback.message.answer(chunk, disable_web_page_preview=True)
    await callback.answer()


# ---------------------------------------------------------------------- #
# Фоновая проверка изменений (по расписанию)
# ---------------------------------------------------------------------- #
def _parse_schedule(schedule_str):
    """'23:00,06:00' -> [(23, 0), (6, 0)]"""
    times = []
    for part in schedule_str.split(","):
        part = part.strip()
        if not part:
            continue
        hh, mm = part.split(":")
        times.append((int(hh), int(mm)))
    return times


def seconds_until_next(times, tz_name):
    """Секунды до ближайшего из времён times (список (HH, MM)) в часовом поясе tz_name."""
    tz = zoneinfo.ZoneInfo(tz_name)
    now = datetime.datetime.now(tz).replace(tzinfo=None)
    today = now.date()
    candidates = []
    for hh, mm in times:
        candidates.append(datetime.datetime(today.year, today.month, today.day, hh, mm))
        candidates.append(
            datetime.datetime(today.year, today.month, today.day, hh, mm)
            + datetime.timedelta(days=1)
        )
    future = [c for c in candidates if c > now]
    nxt = min(future) if future else candidates[0]
    return max((nxt - now).total_seconds(), 1.0)


async def update_checker(bot: Bot):
    times = _parse_schedule(config.CHECK_SCHEDULE)
    while True:
        delay = seconds_until_next(times, config.CHECK_TZ)
        log.info("Следующая проверка меню через %.1f мин", delay / 60)
        await asyncio.sleep(delay)
        try:
            old = loader.cached_menu
            menu, _source = await loader.get_menu(force=True)
            if old is not None:
                diff = diff_menus(old, menu)
                if has_changes(diff):
                    log.info(
                        "Обнаружены изменения: изменено=%d, новых=%d, удалено=%d",
                        len(diff["changed"]),
                        len(diff["added"]),
                        len(diff["removed"]),
                    )
                    await notify_users(bot, diff)
        except Exception as exc:  # noqa: BLE001
            log.exception("Ошибка проверки обновлений меню: %s", exc)


# ---------------------------------------------------------------------- #
# Health-сервер (нужен для Railway/Render и подобных платформ)
# ---------------------------------------------------------------------- #
async def run_health_server():
    """Поднимает HTTP-сервер на $PORT, чтобы платформа считала сервис живым.

    Запускается только если переменная PORT задана (локально её нет —
    бот работает как раньше, без лишнего порта).
    """
    raw_port = os.environ.get("PORT", "")
    if not raw_port:
        return
    port = int(raw_port)

    app = web.Application()

    async def handler(_request):
        return web.Response(text="ok")

    app.router.add_get("/", handler)
    app.router.add_get("/health", handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info("Health-сервер запущен на порту %d", port)
    await asyncio.Event().wait()


# ---------------------------------------------------------------------- #
# Точка входа
# ---------------------------------------------------------------------- #
async def main():
    if not config.BOT_TOKEN:
        log.error("BOT_TOKEN не задан. Добавьте его в .env или переменную окружения.")
        return
    bot = Bot(
        token=config.BOT_TOKEN,
        session=AiohttpSession(proxy=config.TELEGRAM_PROXY) if config.TELEGRAM_PROXY else None,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    if config.TELEGRAM_PROXY:
        log.info("Используется прокси для Telegram: %s", config.TELEGRAM_PROXY)
    dp = Dispatcher()
    dp.include_router(router)

    # Актуальный список команд (перезаписывает команды старого бота)
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Показать меню"),
            BotCommand(command="menu", description="Разделы меню"),
            BotCommand(command="search", description="Поиск блюда"),
            BotCommand(command="refresh", description="Обновить меню"),
            BotCommand(command="notify_test", description="Тестовое уведомление"),
        ]
    )

    # Прогреваем кэш меню при старте, чтобы первый запрос не «думал» долго
    try:
        menu, source = await loader.get_menu()
        log.info("Меню загружено: источник=%s, разделов=%d", source, len(menu))
    except Exception as exc:  # noqa: BLE001
        log.warning("Не удалось прогреть кэш меню при старте: %s", exc)

    task = asyncio.create_task(update_checker(bot))
    health_task = asyncio.create_task(run_health_server())

    log.info("Бот запущен")
    try:
        await dp.start_polling(bot)
    finally:
        task.cancel()
        health_task.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Бот остановлен")
