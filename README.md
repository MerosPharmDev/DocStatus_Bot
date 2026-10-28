# Telegram-бот контроля заявки

Бот по номеру заявки одновременно обращается к WMS, бухгалтерии и TMS. Разные серверы не мешают: компьютер, на котором запущен бот, должен видеть все три HTTP-адреса и иметь исходящий доступ к `api.telegram.org:443`.

## Установка на Windows

1. Установите Python 3.11 или новее с python.org. При установке отметьте `Add Python to PATH`.
2. Скопируйте `config.example.json` в `config.json`.
3. Создайте бота через `@BotFather`, возьмите токен и вставьте его в `telegram_bot_token`.
4. Заполните URL, логины и пароли WMS, бухгалтерии и TMS.
5. Запустите `start_bot.bat`.
6. Напишите боту `/chatid`. Он покажет ID личного чата или группы.
7. Впишите этот ID в `allowed_chat_ids`, например: `[-1001234567890]`, и перезапустите бота.
8. Отправьте боту номер заявки: `4381879` или `/check 4381879`.

Дополнительные Python-библиотеки не нужны.

Бот обрабатывает разных пользователей параллельно, но сохраняет порядок сообщений
каждого пользователя. Число одновременно обслуживаемых пользователей задаётся
параметром `update_workers` в `config.json` (по умолчанию `4`). Параметр
`batch_workers` отдельно ограничивает число одновременно проверяемых заявок в одном
пакете.

## Проверка связи с базами

Выполняйте команды именно на компьютере, где будет работать бот:

```powershell
curl.exe -u Web:ПАРОЛЬ "http://WMS-SERVER/SkladTash/hs/ObmenSUT/proverkadoc/?doc_number=4381879"
curl.exe -u Web:ПАРОЛЬ "http://BUH-SERVER/БАЗА/hs/imzo_bot/proverkabuh/?doc_number=4381879"
curl.exe -u Web:ПАРОЛЬ "http://TMS-SERVER/БАЗА/hs/СЕРВИС/МЕТОД/?doc_number=4381879"
```

Все три команды должны вернуть HTTP 200 и JSON. Если браузер открывает адрес, а компьютер бота нет, проблема в маршрутизации, DNS или firewall, а не в коде бота.

## Ожидаемый JSON

WMS:

```json
{
  "order_found": true,
  "assembly_found": true,
  "assembly_finished": true,
  "basket_quantity": 0,
  "basket_has_items": false,
  "assembly_items": [
    {"product_code": "A13581", "product_name": "Товар", "series": "34827282", "quantity": 50}
  ],
  "unassembled_items": [],
  "basket_items": []
}
```

Бухгалтерия:

```json
{
  "document_found": true,
  "document_posted": true,
  "invoice_status": "Подписан клиентом",
  "invoice_sent": true,
  "accounting_completed": true,
  "contract_check_required": false,
  "correspondent_filled": false,
  "document_contract_filled": false,
  "active_contract_found": false,
  "contract_matches": false,
  "document_contract": "",
  "active_contract": "",
  "error": ""
}
```

Поле `invoice_sent` сохранено для совместимости и означает, что ЭСФ подписана.
Бухгалтерия считается завершённой только при одновременно проведённом документе
и подписанной ЭСФ. Если ЭСФ не подписана, сервис устанавливает
`contract_check_required: true` и возвращает результат проверки договора.
В этом случае бот показывает текущий статус ЭСФ, договор документа и активный
договор. При отсутствии или несовпадении договора бот направляет пользователя в
бухгалтерию.

TMS:

```json
{"order_found": true, "driver": "Иванов И.И.", "ttn_sent": true}
```

Для полной проверки расхождений WMS должна передавать `assembly_items`. При
несовпадении бот отправляет отдельную полную таблицу: товар, код, серия,
количество в Smartup, количество в WMS и причина расхождения. Длинная таблица
автоматически разбивается на несколько сообщений.

## Язык интерфейса

Выбранный при регистрации язык сохраняется у пользователя. Для `uz` на
узбекский переключаются клавиатура, подсказки, результаты проверки, таблицы,
состояние интеграций и отчёты. Повторно выбрать язык можно командой `/register`.

## Где запускать

Самый простой вариант — постоянно включённый Windows-компьютер или сервер внутри сети компании. Домен, IIS, внешний IP и входящий порт не требуются. Если базы доступны только из разных изолированных сетей, между ними понадобится VPN или правила маршрутизации/firewall.

## Режимы работы: development и production

Режим задаётся переменной `APP_ENV` в `.env` (шаблон — `.env.example`):

| `APP_ENV` | Где хранятся зарегистрированные пользователи |
|---|---|
| `development` (по умолчанию) | файл `access.json` (путь можно сменить переменной `ACCESS_PATH`) |
| `production` | таблица `users` в PostgreSQL |

Бот сам читает `.env` при запуске (`python bot.py`), а в Docker переменные
передаёт `docker-compose.yml`. Таблица `users` создаётся автоматически при
первом запуске. При старте бот пишет в лог, какое хранилище используется.

В режиме `development` без Docker никаких библиотек ставить не нужно.
Драйвер PostgreSQL (`psycopg`) нужен только в `production` и уже установлен в образе.

## Запуск в Docker

`docker-compose.yml` поднимает два контейнера: бот и PostgreSQL 18.

1. Скопируйте `config.sample.json` в `config.json` и заполните реальными данными:

```bash
cp config.sample.json config.json
```

2. Создайте `.env` из шаблона, укажите режим и задайте пароль базы:

```bash
cp .env.example .env
sed -i "s/^UID=.*/UID=$(id -u)/; s/^GID=.*/GID=$(id -g)/" .env
sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(openssl rand -hex 24)/" .env
sed -i "s/^APP_ENV=.*/APP_ENV=production/" .env   # на сервере
mkdir -p data
```

3. Соберите и запустите:

```bash
docker compose up -d --build
```

Бот стартует только после того, как PostgreSQL пройдёт healthcheck.

4. Полезные команды:

```bash
docker compose logs -f docstatus-bot   # смотреть логи бота
docker compose restart docstatus-bot   # перезапустить (например, после правки config.json)
docker compose down                    # остановить и удалить контейнеры (данные базы сохраняются)
docker compose up -d --build           # применить изменения кода
```

### PostgreSQL

Данные базы лежат в именованном томе `postgres-data` и переживают
`docker compose down` и пересборку. Удаляет их только `docker compose down -v`.

Порт базы наружу не открыт — к ней подключается только бот по внутренней сети
Docker. Посмотреть пользователей:

```bash
docker compose exec postgres psql -U docstatus -d docstatus -c "SELECT * FROM users"
```

Резервная копия и восстановление:

```bash
docker compose exec -T postgres pg_dump -U docstatus docstatus > backup.sql
docker compose exec -T postgres psql -U docstatus -d docstatus < backup.sql
```

Вместо `POSTGRES_*` можно задать строку подключения `DATABASE_URL`
(например, для внешнего сервера PostgreSQL).

### Режим development в Docker

При `APP_ENV=development` пользователи хранятся в `data/access.json`.
Каталог смонтирован с хоста, поэтому файл выживает при пересборке контейнера
и не попадает в репозиторий (`access.json` и `data/` в `.gitignore`).

Монтируется именно **каталог**, а не одиночный файл: бот сохраняет файл
через атомарный `rename`, а при монтировании файла это даёт ошибку
`Errno 16 Resource busy`.

Контейнер работает от UID/GID из `.env`, чтобы иметь право записи в `data/`.
При ошибке `Permission denied` для `data/access.json` выполните
`chown -R "$(id -u):$(id -g)" data` и перезапустите контейнер.

### Логи

Бот пишет технические логи и структурированные события `check_event` в stdout —
их собирает и ротирует Docker. Событие содержит пользователя, номер заявки,
стадию, итог, длительность, склад, причины проблем и признак использования
кеша. Пароли и токен в события не попадают.

```bash
docker compose logs -f                  # поток логов
docker compose logs --tail 100          # последние 100 строк
docker compose logs --since 1h          # за последний час
```

Ротация настроена в `docker-compose.yml` (`max-size: 10m`, `max-file: 3`) — логи не разрастутся на диске. При необходимости их можно перенаправить в systemd/journald или во внешний сборщик (Loki, ELK), сменив `driver` в секции `logging`.

Кроме stdout ведётся постоянный журнал проверок:

- `development` — `data/check_events.jsonl` (путь меняется через `AUDIT_PATH`);
- `production` — таблица PostgreSQL `check_events`, создаётся автоматически.

Администратор из `admin_chat_ids` может получить сводку прямо в Telegram:

```text
/report       # последние 7 дней
/report 30    # последние 30 дней
```

Сводка показывает количество проверок, уникальных пользователей и заявок,
распределение итогов и стадий, а также наиболее активных пользователей. Срок
отчёта — от 1 до 365 дней.

`config.json` монтируется в контейнер только для чтения. После его изменения достаточно `docker compose restart docstatus-bot`.

Ограничения ресурсов бота заданы в `docker-compose.yml`: 0.5 CPU и 128 МБ RAM. Контейнер бота запускается от непривилегированного пользователя (UID/GID из `.env`), оба контейнера поднимаются автоматически после перезагрузки сервера (`restart: unless-stopped`).

## Безопасность

- Не отправляйте `config.json` и `.env` другим людям и не публикуйте их в GitHub.
- Для HTTP-сервисов 1С создайте отдельного пользователя только с правом чтения необходимых данных.
- Если запросы выходят через интернет, используйте HTTPS. Basic Auth по обычному HTTP не защищает пароль.
- Ограничьте доступ через `allowed_chat_ids`.
