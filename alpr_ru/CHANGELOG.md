# Changelog

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
