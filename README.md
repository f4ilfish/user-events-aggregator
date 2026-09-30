# User Events Aggregator

Учебный mock MVP после технического интервью: FastAPI-эндпоинт собирает события
пользователя по `customer_id` или телефону, включая объединённые аккаунты и устройства.
Все внешние сервисы заменены асинхронными in-memory реализациями. База данных,
учётные записи внешних сервисов и API-ключи не нужны.

## Быстрый старт

Нужен [uv](https://docs.astral.sh/uv/getting-started/installation/).
Команды выполняются из корня скачанного или клонированного репозитория:

```sh
uv sync --locked
uv run --locked uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`uv` создаст `.venv` и установит версии из `uv.lock`; при необходимости загрузит
Python 3.13, указанный в `.python-version`. Первый запуск требует доступа к сети.
В манифесте допускается Python 3.11+, проверка проекта выполнена на Python 3.13.
Активировать виртуальное окружение вручную не нужно.

Откройте [Swagger UI](http://127.0.0.1:8000/docs). Остановка сервера — `Ctrl+C`.
Для автоматической перезагрузки при разработке добавьте `--reload`.

## Запрос и результат

```sh
curl -i http://127.0.0.1:8000/users/collect \
  -H 'Content-Type: application/json' \
  -d '{"identifiers":[{"type":"customer_id","value":"C-100"},{"type":"phone","value":"+79990000001"}]}'
```

Принимаются 1–100 идентификаторов. `results` содержит отдельный результат для
каждого входного идентификатора в исходном порядке. Для указанного примера оба
результата имеют статус `complete`, user_ids `u-1`, `u-2`, три tracking ID и четыре
события. Повторяющиеся upstream-вызовы в рамках запроса выполняются один раз.

| Вход | Результат |
| --- | --- |
| `customer_id: C-100` или `phone: +79990000001` | Два аккаунта, три tracking ID, четыре события |
| `customer_id: C-200` или `phone: +79990000002` | Пользователь найден, один tracking ID, событий нет |
| `customer_id: C-300` | Пользователь найден, профилей нет |
| `customer_id: missing` | `not_found` |

Структура элемента результата: `identifier`, `status`, `user_ids`, `tracking_ids`,
`events`, `errors`. События отсортированы по времени, имеют `tracking_id` источника
и время в ISO 8601 UTC. Сами события не дедуплицируются: в контракте нет event_id.

| Статус | Значение |
| --- | --- |
| `complete` | Все необходимые вызовы успешны; пустые события допустимы |
| `not_found` | id-service вернул `None` |
| `failed` | Не удалось разрешить исходный идентификатор |
| `partial` | Пользователь найден, но часть профилей или событий недоступна |

Обработанные запросы возвращают HTTP 200, в том числе с `partial`/`failed`.
Невалидный вход — HTTP 422, превышение входного лимита — HTTP 429.

## Проверка ошибок внешнего сервиса

Остановите сервер и запустите его с выбранным сценарием (macOS/Linux):

```sh
MOCK_SCENARIO=upstream_error uv run --locked uvicorn app.main:app --host 127.0.0.1 --port 8000
```

В PowerShell перед обычной командой запуска задайте:

```powershell
$env:MOCK_SCENARIO = "upstream_error"
```

Затем отправьте запрос с `C-100`.

| `MOCK_SCENARIO` | Поведение `get_events(559012)` | Результат для C-100 |
| --- | --- | --- |
| `happy` (по умолчанию) | Успех | `complete`, четыре события |
| `rate_limit_then_ok` | Первые две попытки получают RateLimitError | После повторов `complete` |
| `rate_limit_always` | Постоянный RateLimitError | `partial`, ошибка `rate_limited`, attempts=3 |
| `upstream_error` | Постоянный UpstreamError | `partial`, ошибка `upstream_error`, attempts=1 |

Для двух последних сценариев сохраняются три события из доступных источников.
Счётчики mock живут до перезапуска приложения: временная ошибка не сбрасывается
для каждого HTTP-запроса. Чтобы вернуться к обычной работе, задайте `MOCK_SCENARIO=happy`.

### Использование mock-сервисов в коде

```python
import asyncio
from app.mocks import Failure, create_mock_services
from app.upstreams import RateLimitError

async def main():
    services = create_mock_services("rate_limit_then_ok", latency=0.02)
    user = await services.ids.resolve_user("customer_id", "C-100")
    print(user)
    print(await services.profiles.get_tracking_ids(user["user_id"]))

    # Простой пример повтора; в HTTP-приложении этим занимается collector.
    for attempt in range(3):
        try:
            print(await services.events.get_events(559012))
            break
        except RateLimitError as error:
            if attempt == 2:
                raise
            await asyncio.sleep(error.retry_after if error.retry_after is not None else 0.05)
    print(services.events.calls)

    # Свой сценарий: следующий вызов профилей u-2 завершится UpstreamError.
    services.profiles.failures["u-2"] = Failure("upstream", times=None)

asyncio.run(main())
```

Каждый `create_mock_services()` создаёт независимые данные и счётчики `calls`
по ID, включая неуспешные попытки. Ответы копируются: вызывающий код не меняет
fixtures. Неизвестные user ID и tracking ID дают пустые списки. Телефоны
сравниваются как строки без нормализации; время fixtures считается UTC.

`latency` задаёт задержку вызова в секундах (по умолчанию 0.02). Чтобы проверить
таймаут, задайте задержку больше таймаута вызывающего кода. Конструкторы сервисов
принимают `failures`: словарь ID → `Failure(kind, times, retry_after)`, где kind —
`"rate_limit"` или `"upstream"`. Для id-service ключ — `(id_type, value)`.
`times` задаёт число первых неуспешных вызовов этого ID; `None` означает постоянную
ошибку. Настраивайте сценарий до вызовов: счётчик попыток при смене failure не сбрасывается.

Mock генерирует RateLimitError по конкретному ID и числу попыток, а не по реальной
квоте запросов в секунду. Повторы, общий cooldown, дедлайн и частичный результат
реализует collector; mock только выдаёт ошибку и `retry_after`.


## Входной rate limit и проверка в браузере

Лимит — **3 POST-запроса за скользящие 15 секунд на IP**. В Swagger откройте
`POST /users/collect`, нажмите **Try it out**, вставьте body из примера и нажмите
**Execute четыре раза за 15 секунд**. Четвёртый запрос получит HTTP 429.

Ответ содержит `Retry-After` (целые секунды ожидания), `X-RateLimit-Limit` и
`X-RateLimit-Remaining`. После указанного ожидания запрос снова пройдёт.
Невалидные POST-запросы тоже расходуют квоту. Заблокированные запросы не продлевают
окно и не вызывают upstream. Страницы `/docs` и `/openapi.json` квоту не расходуют.

Этот лимит независим от mock RateLimitError. Для отдельной проверки входного лимита
используйте `happy`. Состояние хранится в памяти одного процесса: запускайте один
worker; перезапуск сбрасывает квоту. За прокси нужно отдельно настроить доверие к
передаваемому IP клиента. Распределённое ограничение здесь не реализовано.

## Как устроена сборка

```text
customer_id / phone → resolve_user → user_id + merged_ids
                                      ↓
                              get_tracking_ids
                                      ↓
                                tracking_ids
                                      ↓
                                  get_events
```

Три последовательных этапа, внутри каждого — конкурентные вызовы.
`CollectorSettings`: concurrency=8, call_timeout=1 с, request_timeout=5 с,
max_attempts=3, backoff=0.05 с. Общий дедлайн включает очередь и повторы.

RateLimitError повторяется с `retry_after` либо exponential backoff + jitter.
Ожидание не занимает слот семафора. Profiles и events имеют общий cooldown analytics;
id-service — отдельный. Семафор и cooldown разделяются HTTP-запросами одного процесса.
Обычные UpstreamError и таймауты не повторяются. Успешно собранные данные сохраняются.

Исходные контракты — в [note.md](note.md). Отличия: методы асинхронные, время без
зоны считается UTC, HTTP-контракт и политики отказов добавлены поверх этих контрактов.

## Команды Makefile и проверки

Нужны `uv` и `make` (macOS/Linux; на Windows можно использовать WSL).

| Команда | Действие |
| --- | --- |
| `make install` | Установка зависимостей из `uv.lock` |
| `make run` | Запуск API на 127.0.0.1:8000 |
| `make test` | Все unittest-тесты |
| `make coverage` | Тесты, покрытие строк/ветвей, HTML и JSON |
| `make mutations` | Мутационный smoke-прогон |
| `make check` | Покрытие и затем мутации |
| `make clean` | Удаление `reports/`, `dist/` и `.coverage*`; окружение сохраняется |

Например, полный прогон:

```sh
make install
make check
```

Для сценария ошибки сервиса: `MOCK_SCENARIO=upstream_error make run`.
Команды без Makefile остаются доступны через `uv run --locked`; точные вызовы
перечислены в [Makefile](Makefile).

Тесты проверяют collector, HTTP-контракт, mock-сервисы и входной rate limiter.
Backoff проверяется с управляемым временем и jitter; для каждого события
проверяется tracking ID источника. HTML-покрытие создаётся в
`reports/coverage/index.html`, JSON — в `reports/coverage.json`.

Мутационный smoke-прогон запускает 16 выбранных изменений collector во временной
копии и не меняет приложение. Сначала проверяется исходный набор тестов, затем
каждая мутация отдельно. Результаты и логи — `reports/mutation-smoke/`.
Выживший мутант или таймаут завершают команду с ошибкой. Это ограниченная выборка,
а не полный мутационный анализ. Статические сводки не хранятся: актуальный результат
получается запуском команд. Все генерируемые отчёты исключены из Git.

Автоматизированных браузерных E2E, нагрузочных тестов и интеграции с реальными
сервисами пока нет. Это демонстрационный проект, а не production-сервис.

## CI: GitHub Actions

Workflow [`.github/workflows/ci.yml`](.github/workflows/ci.yml) запускается на push,
pull request и вручную через вкладку **Actions → CI → Run workflow**.
На Ubuntu устанавливаются uv 0.9.7, Python из `.python-version` и зависимости из
`uv.lock`, затем выполняется `make check`: тесты с покрытием и мутационный smoke-прогон.
Ошибка теста, выживший мутант или таймаут мутационного прогона делают CI красным.

Сгенерированные отчёты доступны в **Actions → нужный запуск → Artifacts → test-reports**
в течение 7 дней. Загрузка выполняется и при падении проверок, если отчёты успели
создаться. В Git они не добавляются. Дополнительные секреты не требуются.
Локальный эквивалент — `make install && make check`.

Используется официальная [интеграция uv с GitHub Actions](https://docs.astral.sh/uv/guides/integration/github/).

## Структура и зависимости

```text
app/
  main.py             # HTTP, lifespan, внедрение зависимостей
  schemas.py          # Pydantic-модели входа, выхода и upstream-ответов
  collector.py        # Сборка, дедупликация, таймауты и повторы
  upstreams.py        # Protocol-интерфейсы и исключения
  mocks.py            # In-memory реализации
  fixtures.py         # Пользователи, профили и события
  rate_limit.py       # Входной лимит по IP
tests/                # unittest: collector, HTTP, mocks, rate limiter
tools/mutation_smoke.py
Makefile              # Запуск приложения, проверки и очистка
pyproject.toml        # Метаданные, зависимости, настройки coverage
uv.lock               # Точные версии, источники и хеши зависимостей
.python-version       # Python для стандартного запуска
```

`pyproject.toml` — единственный источник деклараций зависимостей; отдельные
requirements-файлы не нужны. Dev-группа устанавливается `uv sync` по умолчанию.

Для публикации включите исходники, тесты, документацию, `Makefile`, `pyproject.toml`,
`uv.lock`, `.python-version` и `.gitignore`. Окружение `.venv`, кеши, архивы и все
генерируемые отчёты (HTML, JSON, логи мутаций) исключены через `.gitignore`:
любой читатель может восстановить их командами выше.
