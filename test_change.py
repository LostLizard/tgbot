"""Тестовый прогон детекта изменений меню.

Имитирует изменение одного блюда и показывает, какое уведомление
рассылает бот. НЕ трогает реальный Excel и НЕ отправляет сообщения —
только выводит текст уведомления.

Запуск:  python test_change.py
"""

import asyncio
import copy
import sys

# чтобы кириллица корректно выводилась в консоль Windows
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from menu_loader import MenuLoader  # noqa: E402
import bot  # noqa: E402  (функции diff/format)


# Блюдо, которое «изменяем» для теста (категория и название)
CATEGORY = "Пицца"
DISH_NAME = "Маргарита"


async def main():
    loader = MenuLoader()
    print("Загружаю меню (Яндекс/Google)...")
    menu, source = await loader.get_menu(force=True)
    print(f"Меню загружено, источник: {source}, разделов: {len(menu)}\n")

    # Имитируем изменение: копируем меню и меняем описание одного блюда
    old = copy.deepcopy(menu)
    new = copy.deepcopy(menu)

    changed = False
    for cat in new:
        if cat["title"] != CATEGORY:
            continue
        for sec in cat["sections"]:
            for dish in sec["dishes"]:
                if dish["name"] == DISH_NAME:
                    dish["description"] = (
                        dish["description"]
                        + "\n[ТЕСТ] Добавлена руккола и изменён соус."
                    )
                    changed = True
    if not changed:
        print(f"Не найдено блюдо «{DISH_NAME}» в категории «{CATEGORY}».")
        return

    diff = bot.diff_menus(old, new)

    lines = []
    lines.append("=" * 60)
    lines.append(
        f"Найдено изменений: {len(diff['changed'])}, "
        f"новых: {len(diff['added'])}, удалено: {len(diff['removed'])}"
    )
    lines.append("=" * 60)
    lines.append("")
    lines.append("СВОДКА (сообщение 1 всем пользователям):")
    lines.append(bot.build_summary(diff))
    lines.append("")
    lines.append("ДЕТАЛИ (сообщения 2..N по каждому блюду):")
    for _cat, name, old_desc, new_desc in diff["changed"]:
        lines.append("-" * 60)
        lines.append(bot.build_detail(name, old_desc, new_desc))
    lines.append("-" * 60)

    text = "\n".join(lines)
    print(text)

    with open("test_change_output.txt", "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print("\nВывод также сохранён в test_change_output.txt")


asyncio.run(main())
