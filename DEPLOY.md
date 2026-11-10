# Запуск бота на VPS (за пределами РФ)

Бот работает на любом Linux-сервере. Ниже два способа: через **Docker**
(проще) и через **systemd** (без Docker).

Сервер должен быть за пределами России (иначе `api.telegram.org` будет
заблокирован и бот не подключится). Подойдёт любой дешёвый VPS в Европе
(Hetzner, DigitalOcean, Timeweb и т.п.).

---

## Способ 1. Docker (рекомендуется)

### 1. Установите Docker
```bash
curl -fsSL https://get.docker.com | sh
```

### 2. Загрузите проект на сервер
Скопируйте папку `aroma_menu_bot` на сервер (например, через `scp` или git):
```bash
scp -r aroma_menu_bot user@ВАШ_СЕРВЕР:/opt/
```

### 3. Создайте `.env` с токеном
```bash
cd /opt/aroma_menu_bot
cp .env.example .env
nano .env   # вставьте BOT_TOKEN
```
В `.env` укажите:
```
BOT_TOKEN=ВАШ_ТОКЕН
```

### 4. Запустите
```bash
docker compose up -d --build
```

Проверить логи:
```bash
docker compose logs -f
```

Остановить/обновить:
```bash
docker compose down          # остановить
docker compose up -d --build # пересобрать и запустить после обновления кода
```

---

## Способ 2. systemd (без Docker)

```bash
# 1. Python и venv
sudo apt update && sudo apt install -y python3 python3-venv

# 2. Код в /opt/aroma-menu-bot
sudo mkdir -p /opt/aroma-menu-bot
# скопируйте файлы проекта туда (scp/git)

# 3. Виртуальное окружение и зависимости
cd /opt/aroma-menu-bot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 4. .env с токеном
cp .env.example .env && nano .env

# 5. Пользователь и служба
sudo useradd -r -s /usr/sbin/nologin aroma
sudo cp aroma-menu-bot.service /etc/systemd/system/
sudo nano /etc/systemd/system/aroma-menu-bot.service  # проверьте пути

# 6. Запуск
sudo systemctl daemon-reload
sudo systemctl enable --now aroma-menu-bot
sudo journalctl -u aroma-menu-bot -f   # логи
```

---

## Примечания

- Список пользователей для рассылки хранится в `data/users.json`
  (в Docker монтируется как том, поэтому переживает перезапуски).
- Меню берётся из Яндекса (основной) и Google (резерв) — оба источника
  доступны с любого сервера.
- Прокси (`TELEGRAM_PROXY`) на VPS обычно не нужен — он пригождается,
  только если запускать бота в РФ.
