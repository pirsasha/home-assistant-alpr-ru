from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .alpr import AlprError, download_result_image, recognize, validate_api_key
from .config import APP_VERSION, DATA_DIR, LAST_RESULT_PATH, LAST_SENT_PATH, load_settings, save_settings
from .db import add_event, allowed_vehicle, delete_vehicle, init_db, list_events, list_vehicles, normalize_plate, upsert_vehicle
from .ha import HomeAssistantClient

LOGGER = logging.getLogger("alpr_ru")
APP_DIR = Path(__file__).resolve().parent
HA = HomeAssistantClient()
recognition_lock = asyncio.Lock()
stop_event = asyncio.Event()
listener_task: asyncio.Task[None] | None = None
last_gate_open_monotonic = 0.0
last_result: dict[str, Any] = {}


class SettingsInput(BaseModel):
    api_url: str = "https://api-alpr.pirogovx.ru"
    api_key: str = ""
    camera_entity: str = ""
    trigger_entity: str = ""
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
    return result


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
        try:
            image, content_type = await HA.camera_image(camera)
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            LAST_SENT_PATH.write_bytes(image)
            if LAST_RESULT_PATH.exists():
                LAST_RESULT_PATH.unlink(missing_ok=True)

            result = await recognize(
                api_url=str(settings.get("api_url") or "https://api-alpr.pirogovx.ru"),
                api_key=str(settings.get("api_key") or ""),
                image=image,
                content_type=content_type,
                plate_type=str(settings.get("plate_type") or "auto"),
            )
            plate = normalize_plate(str(result.get("plate") or ""))
            confidence_raw = result.get("confidence")
            try:
                confidence = float(confidence_raw) if confidence_raw is not None else 0.0
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

            crop = await download_result_image(str(settings.get("api_url") or ""), result)
            if crop:
                LAST_RESULT_PATH.write_bytes(crop[0])

            event.update({
                "plate": plate,
                "confidence": confidence if confidence_raw is not None else None,
                "detector_confidence": result.get("detector_confidence"),
                "valid_format": result.get("valid_format"),
                "allowed": allowed,
                "gate_opened": gate_opened,
            })
            add_event(event)
            last_result = {
                **result,
                **event,
                "vehicle": vehicle,
                "gate_action": gate_action,
                "sent_image_available": LAST_SENT_PATH.exists(),
                "result_image_available": LAST_RESULT_PATH.exists(),
            }
            return last_result
        except HTTPException:
            raise
        except Exception as error:
            event["error"] = str(error)
            add_event(event)
            last_result = {**event, "ok": False}
            LOGGER.exception("Recognition failed")
            raise HTTPException(status_code=502, detail=str(error)) from error


async def on_state_changed(data: dict[str, Any]) -> None:
    settings = load_settings()
    trigger = str(settings.get("trigger_entity") or "")
    if not trigger or str(data.get("entity_id") or "") != trigger:
        return
    old_state = (data.get("old_state") or {}).get("state")
    new_state = (data.get("new_state") or {}).get("state")
    if new_state == "on" and old_state != "on":
        try:
            await recognize_once(trigger_entity=trigger)
        except HTTPException as error:
            LOGGER.warning("Trigger recognition failed: %s", error.detail)


@asynccontextmanager
async def lifespan(_: FastAPI):
    global listener_task
    init_db()
    stop_event.clear()
    listener_task = asyncio.create_task(HA.subscribe_state_changes(on_state_changed, stop_event))
    try:
        yield
    finally:
        stop_event.set()
        if listener_task:
            listener_task.cancel()
            try:
                await listener_task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="ALPR-RU", version=APP_VERSION, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "version": APP_VERSION}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(APP_DIR / "static" / "index.html")


@app.get("/api/status")
def status() -> dict[str, Any]:
    return {
        "version": APP_VERSION,
        "settings": public_settings(load_settings()),
        "last_result": last_result,
        "images": {"sent": LAST_SENT_PATH.exists(), "result": LAST_RESULT_PATH.exists()},
    }


@app.get("/api/ha/entities")
async def ha_entities() -> dict[str, list[dict[str, str]]]:
    try:
        return await HA.entities()
    except Exception as error:
        raise HTTPException(status_code=503, detail=f"Home Assistant API: {error}") from error


@app.put("/api/settings")
def update_settings(payload: SettingsInput) -> dict[str, Any]:
    current = load_settings()
    incoming = payload.model_dump()
    if incoming.get("api_key"):
        try:
            incoming["api_key"] = validate_api_key(str(incoming["api_key"]))
        except AlprError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
    else:
        incoming["api_key"] = current.get("api_key", "")
    if incoming.get("plate_type") not in {"auto", "single_line", "two_line"}:
        raise HTTPException(status_code=422, detail="Некорректный тип номера")
    return public_settings(save_settings(incoming))


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
def events(limit: int = Query(default=50, ge=1, le=500)) -> list[dict[str, Any]]:
    return list_events(limit)


@app.get("/media/last_sent.jpg")
def last_sent() -> FileResponse:
    if not LAST_SENT_PATH.exists():
        raise HTTPException(status_code=404, detail="Кадр ещё не получен")
    return FileResponse(LAST_SENT_PATH, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/media/last_result.jpg")
def last_crop() -> FileResponse:
    if not LAST_RESULT_PATH.exists():
        raise HTTPException(status_code=404, detail="Crop номера ещё не получен")
    return FileResponse(LAST_RESULT_PATH, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
