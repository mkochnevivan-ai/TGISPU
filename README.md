# Бот расписания ИГЭУ

Telegram-бот, который показывает расписание **любой группы ИГЭУ** с сайта
[schedule.ispu.ru](http://schedule.ispu.ru/).

## Возможности

- 🎓 каждый пользователь выбирает свою группу: кнопками (факультет → курс → группа)
  или просто написав название, например `1-12а`;
- 👥 выбор подгруппы («х», «хх» и т.п.), если группа на них делится;
- 📅 расписание на сегодня / завтра / любую дату (`/day 15.10`);
- 🗓 расписание на текущую и следующую неделю;
- автоматически определяет номер недели (1-я / 2-я) по дате начала, указанной на сайте;
- сам выбирает нужное расписание (лекционное в начале семестра, затем постоянное)
  по датам действия, указанным на сайте;
- 🔔 ежедневная утренняя рассылка (`/subscribe`), по умолчанию в 07:00 МСК;
- кэширует расписание на 30 минут; если сайт недоступен — отдаёт последнюю копию.

## Быстрый запуск на своём компьютере

1. Создайте бота у [@BotFather](https://t.me/BotFather) (`/newbot`) и получите токен.
2. Установите Python 3.10+ и зависимости:

   ```bash
   pip install -r requirements.txt
   ```

3. Создайте файл `.env` (можно скопировать `.env.example`) и впишите токен:

   ```
   BOT_TOKEN=123456789:AAH...
   ```

4. Запустите:

   ```bash
   python -m ispu_bot
   ```

Бот работает, пока запущена программа. Чтобы он отвечал круглосуточно — см. ниже.

## Круглосуточная работа (сервер)

Боту нужен компьютер, который включён всегда. Проще всего арендовать самый дешёвый
VPS с Ubuntu (1 ядро, 512 МБ–1 ГБ памяти более чем достаточно). Подойдёт и
домашний компьютер / Raspberry Pi, который не выключается.

### Вариант A: systemd (без Docker)

Подключитесь к серверу по SSH и выполните:

```bash
sudo apt update && sudo apt install -y git python3-venv
sudo useradd -r -m -d /opt/ispu-bot ispubot
sudo -u ispubot git clone -b claude/telegram-bot-pome72 https://github.com/mkochnevivan-ai/TGISPU.git /opt/ispu-bot
cd /opt/ispu-bot
sudo -u ispubot python3 -m venv .venv
sudo -u ispubot .venv/bin/pip install -r requirements.txt
echo 'BOT_TOKEN=ваш_токен' | sudo -u ispubot tee .env
sudo cp deploy/ispu-bot.service /etc/systemd/system/
sudo systemctl enable --now ispu-bot
```

Полезные команды:

```bash
sudo systemctl status ispu-bot      # работает ли
sudo journalctl -u ispu-bot -f      # логи
sudo systemctl restart ispu-bot     # перезапуск
# обновление кода:
cd /opt/ispu-bot && sudo -u ispubot git pull && sudo systemctl restart ispu-bot
```

### Вариант Б: Docker Compose

```bash
git clone -b claude/telegram-bot-pome72 https://github.com/mkochnevivan-ai/TGISPU.git ispu-bot
cd ispu-bot
echo 'BOT_TOKEN=ваш_токен' > .env
docker compose up -d --build
```

> Если репозиторий приватный, для `git clone` понадобится
> [токен доступа GitHub](https://github.com/settings/tokens) вместо пароля.

## Настройки

Все параметры задаются переменными окружения (или в `.env`):

| Переменная    | По умолчанию      | Описание                            |
|---------------|-------------------|-------------------------------------|
| `BOT_TOKEN`   | —                 | токен бота (обязательно)            |
| `NOTIFY_TIME` | `07:00`           | время ежедневной рассылки           |
| `TZ_NAME`     | `Europe/Moscow`   | часовой пояс                        |
| `DATA_FILE`   | `data/users.json` | где хранить настройки пользователей |

## Как это работает

Сайт расписания сделан на ASP.NET WebForms: выбор факультета, группы и подгруппы
происходит postback-запросами. Бот повторяет эти запросы (`ispu_bot/scraper.py`),
разбирает таблицу `#sheduleTable` с учётом объединённых ячеек (`rowspan`)
и форматирует результат (`ispu_bot/formatting.py`). Список всех групп загружается
при старте и обновляется раз в 6 часов.

> Сайт по HTTPS работает только со старыми версиями TLS, поэтому запросы идут по HTTP.

## Тесты

```bash
pip install pytest
python -m pytest
```
