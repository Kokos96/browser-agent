# Browser Automation Agent

[![CI](https://github.com/Kokos96/browser-agent/actions/workflows/validate.yml/badge.svg)](https://github.com/Kokos96/browser-agent/actions/workflows/validate.yml)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![Playwright](https://img.shields.io/badge/Browser-Playwright-2EAD33)

**Browser Automation Agent** — асинхронний Python-агент для автоматизації браузерних сценаріїв. Він використовує Playwright для взаємодії зі сторінкою, Gemini для побудови структурованого плану дій і FastAPI для віддаленого запуску та перегляду стану.

Проєкт розгортається як два компоненти: вебклієнт у окремому репозиторії та API/worker у цьому репозиторії.

**Демо інтерфейсу:** [відкрити Browser Agent](https://browser-agent-web-nine.vercel.app)

> **Стан проєкту:** MVP у розробці. Базовий сценарій запуску та збереження історії реалізований; стабільність наскрізного виконання і повнота діагностики ще перевіряються. Статус завершення процесу не є доказом успішного результату цільового тесту.

## Можливості

- Керування браузером через Playwright і Chromium.
- Інспекція URL, заголовка, тексту сторінки й інтерактивних елементів.
- Автоматичне заповнення відомих полів профілю перед стартом сценарію.
- Структурований план дій від Gemini; виконуються лише дії, прив’язані до знайдених елементів.
- Опційне передавання скриншота сторінки моделі.
- FastAPI endpoint-и для запуску агента, перевірки статусу та читання історії.
- Історія за анонімним ідентифікатором пристрою; Firestore за наявності конфігурації проєкту.
- Збереження stdout-логів, стану сторінки й діагностики невдалого запуску.
- Автоматичні перевірки Python-синтаксису та unit-тести через GitHub Actions.
- Політика публічних HTTPS-адрес із DNS/IP-перевіркою та локальним egress-проксі для запитів Chromium.

## Архітектура

~~~mermaid
flowchart LR
    UI["Web client"] --> API["FastAPI<br/>api_server.py"]
    API --> Worker["Worker process<br/>main.py"]
    Worker --> Agent["BrowserAgent"]
    Agent --> Browser["Playwright / Chromium"]
    Browser --> Proxy["Public URL egress proxy"]
    Proxy --> Internet["Validated public HTTP(S) hosts"]
    Agent --> Gemini["Gemini API"]
    API --> History["Firestore / local JSON"]
    Agent --> History
~~~

Подробиці компонентів і життєвого циклу — у [docs/architecture.md](docs/architecture.md).

## Структура репозиторію

~~~text
.
├── api_server.py              # HTTP API, запуск worker, статус і історія
├── main.py                    # Точка входу окремого процесу агента
├── public_url_security.py     # URL validation and DNS-pinning egress proxy
├── src/
│   └── browser_agent/
│       ├── agent.py           # Цикл агента, дії та запис результатів
│       ├── browser.py         # Playwright controller
│       ├── config.py          # Налаштування з environment
│       ├── llm.py             # Gemini client та structured output
│       ├── models.py          # Схеми запитів і відповідей моделі
│       └── prompts.py         # Системний prompt та форматування запиту
├── tests/
│   ├── test_device_history.py # Ізоляція історії та збереження логів
│   ├── test_public_url_security.py # URL policy and proxy security
│   └── test_run_status.py     # Відповідність exit code результату агента
├── docs/
│   └── architecture.md        # Компоненти, схема та обмеження
├── .github/workflows/
│   └── validate.yml           # CI-перевірки
├── Dockerfile
├── requirements.txt
├── pyproject.toml
├── .env.example
└── DEPLOYMENT.md
~~~

Згенеровані скриншоти та файли історії належать до папок screenshots/ і results/; їх не слід комітити, оскільки вони можуть містити вміст вебсторінок.

## Як запустити локально

Потрібні Python 3.10+ і доступ до Gemini API.

~~~
git clone https://github.com/Kokos96/browser-agent.git
cd browser-agent

python -m venv .venv
source .venv/bin/activate       # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium

cp .env.example .env
~~~

Відредагуй .env: щонайменше задай GEMINI_API_KEY, потрібний TEST_URL і поля профілю. Не коміть .env та не вставляй секрети в issues, скриншоти або README.

Запуск worker напряму:

~~~
python main.py
~~~

Для запуску HTTP API локально задай окремий API_TOKEN у своєму .env, після чого виконай:

~~~
uvicorn api_server:app --host 127.0.0.1 --port 8000
~~~

## Конфігурація

| Змінна | Призначення |
| --- | --- |
| GEMINI_API_KEY | Ключ для Gemini |
| GEMINI_MODEL | Назва моделі Gemini |
| TEST_URL | Початкова URL-адреса цільової сторінки |
| USER_SURNAME, USER_NAME, USER_GROUP | Значення для початкової форми |
| MAX_STEPS | Максимальна кількість кроків агента |
| ENABLE_VISION | Чи прикріплювати скриншоти до запиту моделі |
| HEADLESS | Запуск Chromium без видимого вікна |
| MAX_SAME_ACTION | Поріг повторення однакової дії |
| TEST_LOAD_TIMEOUT | Час очікування вмісту тесту |
| AUTO_SUBMIT | Чи натискати знайдену кнопку фінального надсилання автоматично |
| API_TOKEN | Спільний токен для захисту API в поточному demo-сценарії |
| GOOGLE_CLOUD_PROJECT | Вмикає збереження історії у Firestore, якщо service account має доступ |
| FRONTEND_ORIGINS | Дозволені origin-и вебклієнта, розділені комами |
| URL_POLICY_MODE | `public_https` (default) або `strict_allowlist` |
| ALLOWED_TEST_HOSTS | Точні хости лише для явного режиму `strict_allowlist` |

Шаблон значень міститься у [.env.example](.env.example). Не вставляй реальні ключі у цей файл.

## Політика URL та мережевий захист

За замовчуванням `URL_POLICY_MODE=public_https`: API приймає синтаксично коректні публічні HTTPS-цілі без потреби додавати кожен новий домен у конфігурацію. Це дозволяє пробувати різні публічні сайти з тестами.

`URL_POLICY_MODE=strict_allowlist` вмикає сумісний суворий режим, де допускаються лише точні hostname-и з `ALLOWED_TEST_HOSTS`. Це додатковий обмежувальний режим, а не засіб захисту від SSRF сам по собі.

Перед запуском URL перевіряється на HTTPS, коректний hostname, відсутність вбудованих облікових даних, допустимий порт і публічні DNS/IP-адреси. Chromium використовує локальний loopback-only egress proxy, який перевіряє кожен outbound HTTP(S) destination та встановлює з’єднання з уже перевіреною IP-адресою. Запити до localhost, приватних, loopback, link-local, reserved та metadata адрес блокуються. Схеми, відмінні від HTTP(S), блокуються на рівні браузерного маршруту.

Публічний URL сам по собі не гарантує, що сайт можна пройти автоматично: CAPTCHA, login walls, rate limits, нестандартні форми та динамічні компоненти можуть блокувати виконання. Агент має фіксувати ці випадки як обмеження/помилки, а не заявляти успіх без фактично отриманого результату.

Жодний application-level proxy не замінює мережеву egress-політику хмарної інфраструктури. Для публічного production-сервісу слід продовжувати використовувати least-privilege service account, не надавати браузеру секрети Cloud Run, а за вимогами вищої гарантії — додати окремі egress rules на рівні інфраструктури.

## API

| Метод | Endpoint | Призначення |
| --- | --- | --- |
| GET | /api/health | Публічна перевірка доступності API |
| POST | /api/run | Запуск агента |
| GET | /api/status | Стан активного процесу й останні логи пристрою |
| GET | /api/history | Історія лише для переданого device token |
| DELETE | /api/history/{run_id} | Видалення одного запису свого пристрою |
| DELETE | /api/history | Очищення історії свого пристрою |

Захищені endpoint-и очікують заголовки X-API-Token і X-Device-Token. Не публікуй їх значення.

## Статус запуску і результат тесту

Це дві різні речі:

- **status** описує життєвий цикл worker-процесу: running, completed або error.
- **test_outcome** описує те, що агент зміг перевірити: наприклад, вміст тесту не завантажився, очікується ручне надсилання або результат сторінки зчитано.
- **score** заповнюється лише тоді, коли агент розпізнав підтримуваний формат оцінки.

Якщо вміст цільової сторінки не завантажився, run записується як помилка, а worker завершується з ненульовим кодом. Це не дозволяє звичайному завершенню Python-процесу маскувати невдалий запуск.

## Перевірки

~~~
python -m py_compile api_server.py main.py public_url_security.py src/browser_agent/*.py
python -m unittest discover -s tests -v
~~~

GitHub Actions запускає ці перевірки на push і pull request. Повний ручний тест також має перевіряти ізоляцію двох пристроїв, збереження історії після оновлення сторінки та сценарій із реально отриманим результатом.

## Безпека та відомі обмеження

- Поточний API_TOKEN — спільний demo-токен, а не повноцінна система користувацької автентифікації. Не роздавай його широкій аудиторії.
- API поки запускає лише один browser worker одночасно; другий паралельний запуск відхиляється.
- Скриншоти, логи та текст сторінки можуть містити персональні або навчальні дані. Перевіряй їх перед передаванням іншим.
- Автоматизуй лише сайти, які маєш право тестувати; не намагайся обходити CAPTCHA, paywalls, автентифікацію чи інші обмеження доступу.
- У репозиторії поки не визначено окрему LICENSE; це потрібно вирішити перед позиціонуванням як open-source проєкту.

## Розгортання

Інструкції для перевірки та контрольованого розгортання — у [DEPLOYMENT.md](DEPLOYMENT.md). Зміни з робочої гілки не варто розгортати у production, доки CI й ручний тест не пройшли.
