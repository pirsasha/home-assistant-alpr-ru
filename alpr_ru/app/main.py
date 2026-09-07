from __future__ import annotations

import asyncio
import logging
import shutil
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .alpr import (
    AlprError,
    crop_result_from_bbox,
    download_result_image,
    recognize,
    validate_api_key,
)
from .config import (
    APP_VERSION,
    DATA_DIR,
    HISTORY_DIR,
    HISTORY_LIMIT,
    LAST_RESULT_PATH,
    LAST_SENT_PATH,
    load_settings,
    save_settings,
)
from .dahua import DahuaMotionListener
from .db import (
    add_event,
    allowed_vehicle,
    delete_vehicle,
    init_db,
    list_events,
    list_vehicles,
    normalize_plate,
    prune_events,
    upsert_vehicle,
)
from .ha import HomeAssistantClient

LOGGER = logging.getLogger("alpr_ru")
APP_DIR = Path(__file__).resolve().parent
MOSCOW = ZoneInfo("Europe/Moscow")
HA = HomeAssistantClient()
DAHUA = DahuaMotionListener()
recognition_lock = asyncio.Lock()
stop_event = asyncio.Event()
ha_listener_task: asyncio.Task[None] | None = None
dahua_listener_task: asyncio.Task[None] | None = None
last_gate_open_monotonic = 0.0
last_dahua_trigger_monotonic = 0.0
last_result: dict[str, Any] = {}


class SettingsInput(BaseModel):
    api_url: str = "https://api-alpr.pirogovx.ru"
    api_key: str = ""
    camera_entity: str = ""
    trigger_mode: str = "none"
    trigger_entity: str = ""
    dahua_url: str = ""
    dahua_username: str = "admin"
    dahua_password: str = ""
    dahua_motion_cooldown: int = Field(default=10, ge=0, le=3600)
    plate_type: str = "auto"
    gate_entity: str = ""
    min_confidence: float = Field(default=0.85, ge=0, le=1)
    gate_cooldown: int = Field(default=30, ge=0, le=3600)


class VehicleInput(BaseModel):
    plate: str
    name: str = ""
    note: str = ""
    enabled: bool = True
    open_gate: bool = True


def public_settings(settings: dict[str, Any]) -> dict[str, Any]:
    result = dict(settings)
    result["api_key_set"] = bool(result.get("api_key"))
    result["api_key"] = ""
    result["dahua_password_set"] = bool(result.get("dahua_password"))
    result["dahua_password"] = ""
    return result


def _event_media_dir(event_id: int) -> Path:
    return HISTORY_DIR / str(int(event_id))


def _remove_event_media(event_ids: list[int]) -> None:
    for event_id in event_ids:
        shutil.rmtree(_event_media_dir(event_id), ignore_errors=True)


def _prune_history_storage() -> None:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    deleted_ids = prune_events(HISTORY_LIMIT)
    _remove_event_media(deleted_ids)

    keep_ids = {int(row["id"]) for row in list_events(HISTORY_LIMIT)}
    for child in HISTORY_DIR.iterdir():
        if child.is_dir() and child.name.isdigit() and int(child.name) not in keep_ids:
            shutil.rmtree(child, ignore_errors=True)


def _record_event(
    event: dict[str, Any],
    image: bytes | None = None,
    crop: tuple[bytes, str] | None = None,
) -> int:
    event_id = add_event(event)
    event_dir = _event_media_dir(event_id)
    event_dir.mkdir(parents=True, exist_ok=True)
    if image:
        (event_dir / "frame.jpg").write_bytes(image)
    if crop and crop[0]:
        (event_dir / "crop.jpg").write_bytes(crop[0])
    _prune_history_storage()
    return event_id


def _event_media_info(row: dict[str, Any]) -> dict[str, Any]:
    event_id = int(row["id"])
    event_dir = _event_media_dir(event_id)
    frame = event_dir / "frame.jpg"
    crop = event_dir / "crop.jpg"
    result = dict(row)
    result.update(
        {
            "frame_available": frame.exists(),
            "crop_available": crop.exists(),
            "frame_url": f"media/history/{event_id}/frame.jpg" if frame.exists() else "",
            "crop_url": f"media/history/{event_id}/crop.jpg" if crop.exists() else "",
        }
    )
    return result


def _moscow_date_key(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(MOSCOW).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return ""


async def recognize_once(trigger_entity: str = "") -> dict[str, Any]:
    global last_gate_open_monotonic, last_result
    if recognition_lock.locked():
        return {"ok": False, "skipped": True, "reason": "recognition_in_progress"}
    async with recognition_lock:
        settings = load_settings()
        camera = str(settings.get("camera_entity") or "")
        if not camera:
            raise HTTPException(status_code=422, detail="Сначала выберите камеру")

        event: dict[str, Any] = {
            "occurred_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "camera_entity": camera,
            "trigger_entity": trigger_entity,
            "plate": "",
            "confidence": None,
            "detector_confidence": None,
            "valid_format": None,
            "allowed": False,
            "gate_opened": False,
            "error": "",
        }
        image: bytes = b""
        crop: tuple[bytes, str] | None = None
        try:
            image, content_type = await HA.camera_image(camera)
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            LAST_SENT_PATH.write_bytes(image)
            if LAST_RESULT_PATH.exists():
                LAST_RESULT_PATH.unlink(missing_ok=True)

            result = await recognize(
                api_url=str(
                    settings.get("api_url") or "https://api-alpr.pirogovx.ru"
                ),
                api_key=str(settings.get("api_key") or ""),
                image=image,
                content_type=content_type,
                plate_type=str(settings.get("plate_type") or "auto"),
            )
            plate = normalize_plate(str(result.get("plate") or ""))
            confidence_raw = result.get("confidence")
            try:
                confidence = (
                    float(confidence_raw) if confidence_raw is not None else 0.0
                )
            except (TypeError, ValueError):
                confidence = 0.0
            vehicle = allowed_vehicle(plate) if plate else None
            allowed = bool(
                result.get("ok")
                and plate
                and bool(result.get("valid_format"))
                and confidence >= float(settings.get("min_confidence", 0.85))
                and vehicle
            )
            gate_opened = False
            gate_action = ""
            gate_entity = str(settings.get("gate_entity") or "")
            if allowed and vehicle and bool(vehicle.get("open_gate")) and gate_entity:
                cooldown = int(settings.get("gate_cooldown", 30))
                if monotonic() - last_gate_open_monotonic >= cooldown:
                    gate_action = await HA.open_gate(gate_entity)
                    last_gate_open_monotonic = monotonic()
                    gate_opened = True

            api_url = str(settings.get("api_url") or "")
            api_key = str(settings.get("api_key") or "")
            crop = await download_result_image(api_url, api_key, result)
            crop_source = "server_debug" if crop else ""
            if not crop:
                crop = crop_result_from_bbox(image, result)
                if crop:
                    crop_source = "local_bbox"
            if crop:
                LAST_RESULT_PATH.write_bytes(crop[0])

            event.update(
                {
                    "plate": plate,
                    "confidence": confidence if confidence_raw is not None else None,
                    "detector_confidence": result.get("detector_confidence"),
                    "valid_format": result.get("valid_format"),
                    "allowed": allowed,
                    "gate_opened": gate_opened,
                }
            )
            event_id = _record_event(event, image=image, crop=crop)
            last_result = {
                **result,
                **event,
                "event_id": event_id,
                "vehicle": vehicle,
                "gate_action": gate_action,
                "result_image_source": crop_source,
                "sent_image_available": LAST_SENT_PATH.exists(),
                "result_image_available": LAST_RESULT_PATH.exists(),
            }
            return last_result
        except HTTPException:
            raise
        except Exception as error:
            event["error"] = str(error)
            event_id = _record_event(event, image=image or None, crop=crop)
            last_result = {**event, "event_id": event_id, "ok": False}
            LOGGER.exception("Recognition failed")
            raise HTTPException(status_code=502, detail=str(error)) from error


async def on_state_changed(data: dict[str, Any]) -> None:
    settings = load_settings()
    if str(settings.get("trigger_mode") or "none") != "ha":
        return
    trigger = str(settings.get("trigger_entity") or "")
    if not trigger or str(data.get("entity_id") or "") != trigger:
        return
    old_state = (data.get("old_state") or {}).get("state")
    new_state = (data.get("new_state") or {}).get("state")
    if new_state == "on" and old_state != "on":
        try:
            await recognize_once(trigger_entity=trigger)
        except HTTPException as error:
            LOGGER.warning("HA trigger recognition failed: %s", error.detail)


async def on_dahua_motion_start(_raw_event: str) -> None:
    global last_dahua_trigger_monotonic
    settings = load_settings()
    if str(settings.get("trigger_mode") or "none") != "dahua":
        return
    cooldown = int(settings.get("dahua_motion_cooldown", 10))
    now = monotonic()
    if now - last_dahua_trigger_monotonic < cooldown:
        return
    last_dahua_trigger_monotonic = now
    try:
        await recognize_once(trigger_entity="dahua:VideoMotion")
    except HTTPException as error:
        LOGGER.warning("Dahua VideoMotion recognition failed: %s", error.detail)


async def _run_dahua_listener(settings: dict[str, Any]) -> None:
    try:
        await DAHUA.run(
            camera_url=str(settings.get("dahua_url") or ""),
            username=str(settings.get("dahua_username") or ""),
            password=str(settings.get("dahua_password") or ""),
            on_motion_start=on_dahua_motion_start,
        )
    except asyncio.CancelledError:
        raise
    except Exception as error:
        DAHUA.enabled = True
        DAHUA.connected = False
        DAHUA.last_error = str(error)
        LOGGER.warning("Dahua listener stopped: %s", error)


async def restart_dahua_listener() -> None:
    global dahua_listener_task
    if dahua_listener_task:
        dahua_listener_task.cancel()
        try:
            await dahua_listener_task
        except asyncio.CancelledError:
            pass
        dahua_listener_task = None

    settings = load_settings()
    if str(settings.get("trigger_mode") or "none") != "dahua":
        DAHUA.disable()
        return

    DAHUA.enabled = True
    DAHUA.connected = False
    DAHUA.last_error = "Подключение..."
    dahua_listener_task = asyncio.create_task(_run_dahua_listener(settings))


@asynccontextmanager
async def lifespan(_: FastAPI):
    global ha_listener_task
    init_db()
    _prune_history_storage()
    stop_event.clear()
    ha_listener_task = asyncio.create_task(
        HA.subscribe_state_changes(on_state_changed, stop_event)
    )
    await restart_dahua_listener()
    try:
        yield
    finally:
        stop_event.set()
        if ha_listener_task:
            ha_listener_task.cancel()
            try:
                await ha_listener_task
            except asyncio.CancelledError:
                pass
        if dahua_listener_task:
            dahua_listener_task.cancel()
            try:
                await dahua_listener_task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="ALPR-RU", version=APP_VERSION, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "version": APP_VERSION}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(
        APP_DIR / "static" / "index.html",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/api/status")
def status() -> dict[str, Any]:
    return {
        "version": APP_VERSION,
        "settings": public_settings(load_settings()),
        "last_result": last_result,
        "history_limit": HISTORY_LIMIT,
        "images": {
            "sent": LAST_SENT_PATH.exists(),
            "result": LAST_RESULT_PATH.exists(),
        },
        "dahua": DAHUA.public_status(),
    }


@app.get("/api/ha/entities")
async def ha_entities() -> dict[str, list[dict[str, str]]]:
    try:
        return await HA.entities()
    except Exception as error:
        raise HTTPException(
            status_code=503, detail=f"Home Assistant API: {error}"
        ) from error


@app.put("/api/settings")
async def update_settings(payload: SettingsInput) -> dict[str, Any]:
    current = load_settings()
    incoming = payload.model_dump()
    if incoming.get("api_key"):
        try:
            incoming["api_key"] = validate_api_key(str(incoming["api_key"]))
        except AlprError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
    else:
        incoming["api_key"] = current.get("api_key", "")

    if incoming.get("dahua_password"):
        incoming["dahua_password"] = str(incoming["dahua_password"])
    else:
        incoming["dahua_password"] = current.get("dahua_password", "")

    if incoming.get("plate_type") not in {"auto", "single_line", "two_line"}:
        raise HTTPException(status_code=422, detail="Некорректный тип номера")
    if incoming.get("trigger_mode") not in {"none", "ha", "dahua"}:
        raise HTTPException(status_code=422, detail="Некорректный режим триггера")

    saved = save_settings(incoming)
    await restart_dahua_listener()
    return public_settings(saved)


@app.post("/api/recognize")
async def manual_recognize() -> dict[str, Any]:
    return await recognize_once()


@app.get("/api/vehicles")
def vehicles() -> list[dict[str, Any]]:
    return list_vehicles()


@app.post("/api/vehicles")
def save_vehicle(payload: VehicleInput) -> dict[str, Any]:
    try:
        return upsert_vehicle(payload.model_dump())
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.delete("/api/vehicles/{vehicle_id}")
def remove_vehicle(vehicle_id: int) -> JSONResponse:
    delete_vehicle(vehicle_id)
    return JSONResponse({"ok": True})


@app.get("/api/events")
def events(
    date: str = Query(default="", max_length=10),
    plate: str = Query(default="", max_length=32),
    q: str = Query(default="", max_length=120),
    with_plate: bool = Query(default=False),
) -> list[dict[str, Any]]:
    rows = list_events(HISTORY_LIMIT)
    date_filter = date.strip()
    plate_filter = normalize_plate(plate)
    query = q.strip().casefold()
    normalized_query = normalize_plate(q) if q.strip() else ""
    filtered: list[dict[str, Any]] = []

    for row in rows:
        row_plate = normalize_plate(str(row.get("plate") or ""))
        if date_filter and _moscow_date_key(str(row.get("occurred_at") or "")) != date_filter:
            continue
        if plate_filter and plate_filter not in row_plate:
            continue
        if with_plate and not row_plate:
            continue
        if query:
            haystack = " ".join(
                str(row.get(key) or "")
                for key in (
                    "plate",
                    "vehicle_name",
                    "vehicle_note",
                    "camera_entity",
                    "trigger_entity",
                    "error",
                )
            ).casefold()
            plate_match = bool(normalized_query and normalized_query in row_plate)
            if query not in haystack and not plate_match:
                continue
        filtered.append(_event_media_info(row))

    return filtered


@app.get("/media/last_sent.jpg")
def last_sent() -> FileResponse:
    if not LAST_SENT_PATH.exists():
        raise HTTPException(status_code=404, detail="Кадр ещё не получен")
    return FileResponse(
        LAST_SENT_PATH, media_type="image/jpeg", headers={"Cache-Control": "no-store"}
    )


@app.get("/media/last_result.jpg")
def last_crop() -> FileResponse:
    if not LAST_RESULT_PATH.exists():
        raise HTTPException(status_code=404, detail="Crop номера ещё не получен")
    return FileResponse(
        LAST_RESULT_PATH,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/media/history/{event_id}/frame.jpg")
def history_frame(event_id: int) -> FileResponse:
    path = _event_media_dir(event_id) / "frame.jpg"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Кадр события не найден")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/media/history/{event_id}/crop.jpg")
def history_crop(event_id: int) -> FileResponse:
    path = _event_media_dir(event_id) / "crop.jpg"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Crop события не найден")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
