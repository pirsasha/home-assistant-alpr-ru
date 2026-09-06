# ALPR-RU for Home Assistant

Home Assistant App (add-on) for cloud license-plate recognition through ALPR-RU.

The app runs in its own container, opens through Home Assistant Ingress and uses the Supervisor proxy to:

- read available `camera.*` and trigger entities;
- capture a current JPEG from any Home Assistant camera that supports snapshots;
- send the frame to `https://api-alpr.pirogovx.ru/v1/recognize`;
- keep a local allowlist of vehicles in SQLite;
- open a selected `cover.*`, `switch.*` or `button.*` when an allowed plate is recognized;
- keep recognition history and the last sent / last detected plate images.

## Install

1. Home Assistant → Settings → Apps → App store.
2. Add repository: `https://github.com/pirsasha/home-assistant-alpr-ru`
3. Install **ALPR-RU**.
4. Start the app and enable **Show in sidebar**.
5. Open ALPR-RU and configure the API key, camera and optional trigger/gate entity.

No HACS integration is required.
