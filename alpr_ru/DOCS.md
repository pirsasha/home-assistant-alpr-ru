# ALPR-RU

## First setup

Open the app through Home Assistant Ingress and go to **Settings**.

Configure:

- ALPR-RU API key;
- Home Assistant camera;
- optional `binary_sensor.*` trigger;
- optional gate entity (`cover.*`, `switch.*`, `button.*`);
- minimum OCR confidence;
- gate cooldown.

The app uses Home Assistant's internal Supervisor API. Camera credentials and RTSP ports are not exposed to the ALPR-RU cloud.

## Recognition flow

```text
Home Assistant camera
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

## Trigger mode

When a trigger entity is selected, the app subscribes to Home Assistant `state_changed` events over the Supervisor WebSocket proxy. It does not periodically poll the cloud API.

Recognition starts only when the selected trigger changes from a non-`on` state to `on`.

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
