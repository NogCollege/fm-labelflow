# fm-labelflow

Сервис для генерации макетов этикеток из текстового описания товара. Проект строит структуру этикетки, предлагает варианты компоновки, рендерит результат в `PNG` или `SVG`, поддерживает ручную доработку в редакторе и умеет работать через внешнюю авторизацию с защищёнными API.

## Что умеет сервис

- разбивает текст товара на смысловые блоки;
- генерирует несколько вариантов компоновки этикетки;
- экспортирует макеты в `PNG` и `SVG`;
- поддерживает графические символы и настройку позиций QR/иконок;
- открывает встроенный редактор макета;
- хранит историю созданных этикеток;
- умеет логиниться во внешний контур и выполнять защищённые API-запросы;
- содержит встроенный `smoke test` для быстрой технической проверки.

## Основные файлы

- `app.py` , основной Flask-сервер;
- `block_parser.py` , разбор текста на блоки;
- `layout_composer.py` , генерация вариантов компоновки;
- `label_layout.py` , рендеринг этикетки;
- `llm_client.py` , интеграция с LLM-провайдерами;
- `index.html` , основное приложение;
- `login.html` , страница авторизации;
- `labelflow-editor.html` , встроенный редактор;
- `smoke_test.py` , сценарии smoke-проверки;
- `smoke_test.html` , веб-страница запуска smoke test;
- `.env.example` , пример конфигурации окружения;
- `Dockerfile` , контейнеризация;
- `k8s/` , Kubernetes-манифесты.

## Технологии

Проект использует:
- `Python`
- `Flask`
- `Pillow`
- `requests`
- `openai`
- HTML, CSS, JavaScript

## Требования

Рекомендуемое окружение:
- Python `3.10+`
- `pip`
- `venv`

Основные зависимости из `requirements.txt`:
- `Pillow>=10.0.0`
- `requests>=2.31.0`
- `openai>=1.40.0`
- `flask>=3.0.0`
- `flask-cors>=4.0.0`

## Установка

### Linux / macOS
```bash
git clone https://github.com/NogCollege/fm-labelflow.git
cd fm-labelflow
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Windows PowerShell
```powershell
git clone https://github.com/NogCollege/fm-labelflow.git
cd fm-labelflow
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Запуск

```bash
python app.py
```

После запуска обычно доступны:
- `http://localhost:8000/` , основное приложение;
- `http://localhost:8000/login` , страница входа;
- `http://localhost:8000/labelflow-editor/` , редактор макета;
- `http://localhost:8000/smoke-test` , страница smoke test;
- `http://localhost:8000/api/status` , технический статус API.

## Основные HTTP-маршруты

### Интерфейсы
- `GET /` , основной интерфейс LabelFlow;
- `GET /login` , страница авторизации;
- `GET /labelflow-editor/` , редактор;
- `GET /smoke-test` , страница запуска smoke test.

### Базовые API
- `GET /api/status` , статус сервиса;
- `GET /api/symbols` , доступные графические символы;
- `GET /api/block-types` , типы блоков;
- `POST /api/parse-blocks` , разбор текста в блоки;
- `POST /api/get-variants` , получение вариантов компоновки;
- `POST /api/generate` , рендер результата;
- `POST /api/fix-png` , сохранение PNG с нужным DPI.

### Editor layout API
- `POST /api/editor-layouts` , сохранить layout;
- `GET /api/editor-layouts/<layout_id>` , получить layout;
- `DELETE /api/editor-layouts/<layout_id>` , удалить layout.

### История этикеток
- `GET /api/label-history` , список сохранённых записей;
- `POST /api/label-history` , сохранить запись;
- `GET /api/label-history/<entry_id>` , получить запись;
- `DELETE /api/label-history/<entry_id>` , удалить запись.

### Авторизация и внешний контур
- `GET /api/auth/config` , конфигурация авторизации;
- `GET /api/auth/status` , текущий статус сессии;
- `POST /api/auth/login` , вход во внешний сервис;
- `POST /api/auth/logout` , выход;
- `GET /api/auth/token` , текущее состояние токена;
- `PUT /api/auth/token` , вручную установить токен;
- `DELETE /api/auth/token` , очистить токен;
- `GET /api/auth/profile` , получить профиль;
- `POST /api/auth/profile/refresh` , обновить профиль;
- `DELETE /api/auth/profile` , очистить кэш профиля;
- `POST /api/external/request` , прокси-запрос во внешний защищённый API;
- `POST /api/external/test-connection` , тест соединения.

### Smoke test
- `GET /api/smoke-test`
- `POST /api/smoke-test`

## Конфигурация через `.env`

Проект читает настройки из `.env` и локального env-файла пользователя. Базовый пример смотри в `.env.example`.

На практике здесь две большие группы настроек.

### 1. LLM-настройки
Примеры переменных:

```env
LLM_PROVIDER=openai
LLM_API_KEY=
LLM_MODEL=gpt-4o-mini
```

Также поддерживаются режимы вроде `yandex`, `alice` и fallback без полноценного LLM, в зависимости от конфигурации `llm_client.py`.

### 2. Внешняя авторизация и защищённый API
Примеры важных переменных:

```env
AUTH_ENABLED=true
AUTH_DEFAULT_ENV=prod
AUTH_LOGIN_MODE=json
AUTH_USERNAME_FIELD=username
AUTH_PASSWORD_FIELD=password
AUTH_TOKEN_KEYS=access_token,accessToken,token,jwt,id_token,data.access_token,data.accessToken,data.token
AUTH_PROD_LOGIN_URL=https://etg.pxt.fmlogistic.ru/auth/auth/token
AUTH_PROD_API_BASE_URL=https://etg.pxt.fmlogistic.ru/
AUTH_TEST_LOGIN_URL=https://test.pxt.fmlogistic.ru/auth/auth/token
AUTH_TEST_API_BASE_URL=https://test.pxt.fmlogistic.ru/
PRINTX_TEMPLATE_METHOD=POST
PRINTX_TEMPLATE_ENDPOINT=/wh/v1/print-templates
PRINTX_TEMPLATE_TYPE=zpl
PRINTX_TEMPLATE_CLASS=PrintTemplate
PRINTX_ACTIVITY_ID=
EXTERNAL_CONNECTION_TEST_PATH=auth/v1/profile
AUTH_PROFILE_PATH=auth/v1/profile
```

Если внешний сервис использует не JSON-логин, можно переключить режим, например:

```env
AUTH_LOGIN_MODE=form
```

## Smoke test

В проект встроен smoke-тестовый контур:
- `smoke_test.py` содержит сценарии проверки;
- `smoke_test.html` даёт веб-интерфейс для запуска;
- `/api/smoke-test` возвращает результаты проверки.

Это удобно для быстрой диагностики после изменений в:
- парсере текста;
- компоновщике;
- рендере;
- авторизации;
- прокси-запросах во внешний API.

## Сборка и деплой

### Windows / exe
В репозитории есть:
- `build_exe.ps1`
- `requirements.packaging.txt`
- `launcher.py`
- `updater.py`

Это указывает на отдельный сценарий упаковки десктопной версии.

### Docker
Есть `Dockerfile`, поэтому сервис можно контейнеризировать.

### Kubernetes
В папке `k8s/` лежат манифесты:
- `configmap.yaml`
- `deployment.yaml`
- `ingress.yaml`
- `service.yaml`

## Структура репозитория

```text
fm-labelflow/
├── app.py
├── block_parser.py
├── label_layout.py
├── layout_composer.py
├── llm_client.py
├── index.html
├── login.html
├── labelflow-editor.html
├── smoke_test.py
├── smoke_test.html
├── requirements.txt
├── requirements.packaging.txt
├── .env.example
├── Dockerfile
├── k8s/
└── readme.md
```

## Что важно знать

- Старые инструкции запуска через `server.py` для этого репозитория больше неактуальны, фактическая точка входа это `app.py`.
- Репозиторий уже не ограничивается простым генератором этикеток: в нём есть внешний auth-flow, protected API proxy и история пользовательских макетов.
- Для production-контура стоит отдельно документировать реальные значения `.env`, но не хранить секреты в репозитории.

## Для разработчиков

При изменениях важно держать README синхронным с кодом, особенно если меняются:
- точки входа;
- env-переменные;
- маршруты авторизации;
- smoke-тесты;
- деплой через Docker или Kubernetes.
