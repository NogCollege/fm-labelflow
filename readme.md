# LabelFlow (vlad)

## Что это
Локальный сервер для генерации этикеток из текста:
- парсинг описания товара;
- генерация макета этикетки;
- экспорт в `PNG` и `SVG`.

## Установка
1. Перейдите в папку проекта:
```powershell
cd vlad
```
2. Создайте и активируйте виртуальное окружение (рекомендуется):
```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
```
3. Установите зависимости:
```powershell
pip install -r requirements.txt
```

## Запуск
```powershell
py -3 server.py
```

После запуска откройте:
`http://localhost:8000`

## Сборка .exe (Windows)
Сборка идёт через `pywebview` (Edge WebView2) + `PyInstaller`.

```powershell
.\build_exe.ps1
```

Готовый файл будет в `dist\LabelFlow.exe`.

Примечания:
- Нужен установленный **Microsoft Edge WebView2 Runtime** (обычно уже есть в Windows 10/11). Если окна нет — установите WebView2 Runtime.
- При запуске из exe файлы результатов сохраняются в:
`%LOCALAPPDATA%\LabelFlow\output`



## Основные API
- `GET /api/status` - статус сервера
- `POST /api/parse` - парсинг текста
- `POST /api/ai-compose` - AI-компоновка макета по человеческому запросу
- `POST /api/ai-chat` - чат с AI + применение действий к макету
- `POST /api/generate` - генерация этикетки
- `POST /api/auth/login` - вход во внешний сервис и сохранение токена/cookies на стороне Flask
- `GET /api/auth/status` - статус внешней авторизации
- `POST /api/external/request` - прокси-запрос к защищённому внешнему API через сохранённую сессию

## Внешняя авторизация
Для интеграции с ETG / защищённым API настройте переменные в `.env`:

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

Если сервис логина принимает обычную HTML-форму, переключите `AUTH_LOGIN_MODE=form`.
Если токен лежит в другом поле ответа, поправьте `AUTH_TOKEN_KEYS`.

Для ETG логика такая:
- `POST /auth/auth/token` с телом `{ "username": "...", "password": "..." }`
- `GET /auth/v1/profile` с `Authorization: Bearer <token>`

Веб-маршруты:
- `GET /login` - отдельная страница входа
- `GET /` - генератор LabelFlow, для неавторизованных пользователей редиректит на `/login`
- `GET /labelflow-editor/` - редактор, тоже защищён авторизацией

Локальные маршруты LabelFlow для этой интеграции:
- `POST /api/auth/login` - получить внешний токен и сразу подтянуть профиль
- `GET /api/auth/token` - прочитать состояние токена
- `PUT /api/auth/token` - вручную установить токен
- `DELETE /api/auth/token` - очистить токен
- `GET /api/auth/profile` - получить сохранённый профиль
- `POST /api/auth/profile/refresh` - перечитать профиль из ETG
- `DELETE /api/auth/profile` - очистить локальный кэш профиля

Пример тела запроса для генерации:
```json
{
  "text": "Ваш текст товара",
  "size_id": "46x46",
  "preview_mode": true,
  "format": "svg"
}
```

## Текст для вставки (пример)
```text
Шампунь для волос
ЭГЁН КЕРАСИС
ПАРФЮМИРОВАННАЯ
ЛИНИЯ ЭЛЕГАНС

Kerasys Classic Perfume Shampoo Elegance & Sensual

ОБЪЕМ И БЛЕСК
Парфюмированная формула с 10 цветочными экстрактами и 3 травяными маслами. Почувствуйте чувственный элегантный аромат и объем своих блестящих волос

- 10 ЦВЕТОЧНЫХ ЭКСТРАКТОВ
- 3 ТРАВЯНЫХ МАСЛА
-АМИНОКИСЛОТЫ
- 0% СИЛИКОНОВ
- СТОЙКИЙ АРОМАТ ДО 48 ЧАСОВ

При использовании шампуня и кондиционера в комплексе
Верхние ноты: яблоневый цвет, ирис, цветы персика
Средние ноты: тубероза, иланг-иланг, фиалка, гиацинт
Нижние ноты: сандал, рисовая пудра, мускус

Способ применения: Нанести на мокрые волосы небольшое количество средства, вспенить.
Оставить для воздействия на 2-3 минуты. Тщательно промыть водой.
Меры предосторожности: только для наружного применения. При попадании в глаза, промойте проточной водой. Храните в местах недоступных детям, при комнатной температуре, избегая попадания прямых лучей солнца.
Состав / Ingredients: см на упаковке

Срок годности: годен до (см. на упаковке Год/Месяц/День)
Номер партии: см. на упаковке Объем (мл/мл): см. на упаковке
Изготовитель: Aekyung Ind. Co., Ltd. 10F-13F, 2F (Aekyung Tower), 188, Yanghwa-ro, Mapo-gu, Seoul, Республика Корея
Импортер и организация, уполномоченная изготовителем на принятие претензий от потребителей: ООО «ОРИЕНТ» 690002, Россия, Приморский край, г. Владивосток, пр-т Острякова, д. 26, кв. 119. Тел: (423) 2362990, e-mail: orient@unico-gc.ru, сайт: www.unico-gc.ru

Арт.313756
```

## Примечания
- Для максимальной четкости при масштабировании используйте `SVG`.
- Для печати и растрового экспорта используйте `PNG (HD)`.
- Для LLM-режима установите переменную окружения `OPENAI_API_KEY`.
