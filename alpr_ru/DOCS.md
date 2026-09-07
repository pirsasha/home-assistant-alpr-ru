# ALPR-RU

## First setup

Open the app through Home Assistant Ingress and go to **Settings**.

Configure:

- ALPR-RU API key;
- Home Assistant `camera.*` entity used to capture the JPEG;
- recognition trigger mode;
- optional gate entity (`cover.*`, `switch.*`, `button.*`);
- minimum OCR confidence;
- gate cooldown.

The app uses Home Assistant's internal Supervisor API. Camera RTSP ports are not exposed to the ALPR-RU cloud.

## Recognition flow

```text
Trigger
   ↓
Home Assistant camera.*
   ↓ current JPEG
ALPR-RU App
   ↓ HTTPS
api-alpr.pirogovx.ru/v1/recognize
   ↓
plate + confidence + crop
   ↓
local allowlist
   ↓
Home Assistant gate service (optional)
```

## Trigger modes

### No automatic trigger

Recognition starts only from the **Recognize now** button.

### Home Assistant binary_sensor

The app subscribes to Home Assistant `state_changed` events through the Supervisor WebSocket proxy. Recognition starts when the selected `binary_sensor.*` changes from a non-`on` state to `on`.

### Dahua VideoMotion direct

For Dahua cameras that generate `VideoMotion` but do not expose a useful motion entity in Home Assistant, ALPR-RU can listen to the camera directly.

Configure:

- Dahua address, for example `192.168.2.120` or `http://192.168.2.120`;
- Dahua username;
- Dahua password;
- VideoMotion cooldown.

The app opens one persistent local Digest-authenticated connection to:

```text
/cgi-bin/eventManager.cgi?action=attach&codes=[VideoMotion]&heartbeat=5
```

Recognition starts only on:

```text
Code=VideoMotion;action=Start
```

`Heartbeat` and `VideoMotion Stop` do not trigger ALPR requests. The VideoMotion cooldown suppresses repeated `Start/Stop/Start` sequences, so this mode does not periodically poll the camera or cloud API.

The Dahua credentials are stored locally in the app `/data` settings and are not sent to the ALPR-RU cloud. The actual recognition image is still obtained from the configured Home Assistant `camera.*` entity.

## Gate behavior

- `cover.*` → `cover.open_cover`
- `switch.*` → `switch.turn_on`
- `button.*` → `button.press`

The app never calls `switch.turn_off` automatically.

Auto-open requires all of the following:

1. a plate was recognized;
2. the server marked the plate format as valid;
3. confidence is at least the configured threshold;
4. the vehicle exists in the local allowlist;
5. the vehicle is enabled and `open_gate` is enabled;
6. the gate cooldown has expired.
