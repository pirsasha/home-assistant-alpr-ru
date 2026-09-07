# Changelog

## 0.1.5

- Исправлен релизный процесс Docker-образов: автоматическая сборка теперь запускается только при изменении `alpr_ru/config.yaml`, то есть при явном повышении версии.
- Исключена ситуация, когда один и тот же номер версии указывает на несколько разных Docker-образов.
- В релиз включён актуальный интерфейс с режимом `Dahua VideoMotion напрямую`, ручным IP/логином/паролем Dahua и статусом подключения.
- Обновлён cache-busting фронтенда до `0.1.5`.

## 0.1.4

- Принудительно выпущена новая версия App, чтобы Home Assistant скачал свежий Docker-образ с настройками прямого Dahua VideoMotion.
- Добавлен cache-busting для `app.js` и `app.css`, чтобы Ingress не показывал старый интерфейс после обновления.
- В настройках доступны режимы: без триггера, Home Assistant `binary_sensor.*`, Dahua VideoMotion напрямую.

## 0.1.3

- Добавлен прямой локальный триггер `Dahua VideoMotion` через `eventManager.cgi` с Digest authentication.
- ALPR-RU App теперь может работать без `binary_sensor` движения в Home Assistant: событие `VideoMotion Start` берётся напрямую с камеры Dahua.
- Используется одно постоянное локальное event-stream соединение с heartbeat, без периодического polling камеры и без лишних облачных запросов.
- Добавлен отдельный cooldown VideoMotion, чтобы серия `Start/Stop/Start` не создавала пачку ALPR-запросов.
- В настройках доступны адрес Dahua, пользователь, пароль и режим триггера; пароль хранится локально в `/data` и не возвращается в UI/API.
- В интерфейсе показывается состояние прямого подключения Dahua и время последнего события.
- Существующий триггер через Home Assistant `binary_sensor.*` сохранён как альтернативный режим.

## 0.1.2

- Исправлен сбой `UnicodeEncodeError` при ошибочно сохранённом API key с кириллицей или другими не-ASCII символами.
- API key теперь проверяется при сохранении настроек и перед отправкой запроса в ALPR-RU.
- Вместо Python traceback пользователь получает понятное сообщение о некорректном ключе.
- HTTP-запросы к ALPR-RU не используют системный proxy.

## 0.1.1

- Исправлено получение Home Assistant Supervisor token: токен больше не кэшируется при импорте приложения.
- Добавлены fallback-источники `SUPERVISOR_TOKEN` / `HASSIO_TOKEN` и s6 environment.
- Включён Supervisor API access для надёжного внутреннего подключения.
- Внутренние HTTP/WebSocket соединения не используют системный proxy.
- `icon.png` приведён к рекомендованным 128×128 и сделан контрастным для светлой и тёмной темы.

## 0.1.0

- First Home Assistant App release.
- Ingress web UI.
- Camera selection and manual recognition.
- Event-driven trigger from a selected `binary_sensor.*` using the Home Assistant WebSocket API.
- Local SQLite allowlist of vehicles.
- Gate control through `cover.*`, `switch.*` or `button.*`.
- Recognition history and last sent / detected plate images.
