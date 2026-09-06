from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote

import httpx
import websockets

HA_API = "http://supervisor/core/api"
HA_WS = "ws://supervisor/core/websocket"


class HomeAssistantError(RuntimeError):
    pass


class HomeAssistantClient:
    def __init__(self) -> None:
        self.token = os.environ.get("SUPERVISOR_TOKEN", "")

    @property
    def headers(self) -> dict[str, str]:
        if not self.token:
            raise HomeAssistantError("SUPERVISOR_TOKEN недоступен")
        return {"Authorization": f"Bearer {self.token}"}

    async def states(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(f"{HA_API}/states", headers=self.headers)
            response.raise_for_status()
            data = response.json()
        return data if isinstance(data, list) else []

    async def entities(self) -> dict[str, list[dict[str, str]]]:
        groups: dict[str, list[dict[str, str]]] = {
            "cameras": [], "triggers": [], "gates": []
        }
        for state in await self.states():
            entity_id = str(state.get("entity_id") or "")
            attributes = state.get("attributes") or {}
            name = str(attributes.get("friendly_name") or entity_id)
            item = {"entity_id": entity_id, "name": name}
            if entity_id.startswith("camera."):
                groups["cameras"].append(item)
            elif entity_id.startswith("binary_sensor."):
                groups["triggers"].append(item)
            elif entity_id.split(".", 1)[0] in {"cover", "switch", "button"}:
                groups["gates"].append(item)
        for values in groups.values():
            values.sort(key=lambda item: item["name"].lower())
        return groups

    async def camera_image(self, entity_id: str) -> tuple[bytes, str]:
        if not entity_id.startswith("camera."):
            raise HomeAssistantError("Выбрана некорректная camera.* сущность")
        safe_entity = quote(entity_id, safe="._-")
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"{HA_API}/camera_proxy/{safe_entity}", headers=self.headers
            )
            response.raise_for_status()
            content_type = response.headers.get("content-type", "image/jpeg").split(";", 1)[0]
            if not response.content:
                raise HomeAssistantError("Home Assistant вернул пустой кадр")
            return response.content, content_type

    async def open_gate(self, entity_id: str) -> str:
        if not entity_id or "." not in entity_id:
            raise HomeAssistantError("Не выбрано устройство ворот")
        domain = entity_id.split(".", 1)[0]
        service = {
            "cover": "open_cover",
            "switch": "turn_on",
            "button": "press",
        }.get(domain)
        if not service:
            raise HomeAssistantError("Поддерживаются только cover.*, switch.* и button.*")
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"{HA_API}/services/{domain}/{service}",
                headers={**self.headers, "Content-Type": "application/json"},
                json={"entity_id": entity_id},
            )
            response.raise_for_status()
        return f"{domain}.{service}"

    async def subscribe_state_changes(
        self,
        callback: Callable[[dict[str, Any]], Awaitable[None]],
        stop_event: asyncio.Event,
    ) -> None:
        if not self.token:
            return
        retry = 2
        while not stop_event.is_set():
            try:
                async with websockets.connect(HA_WS, open_timeout=15, ping_interval=30) as websocket:
                    hello = json.loads(await websocket.recv())
                    if hello.get("type") != "auth_required":
                        raise HomeAssistantError("Неожиданный ответ Home Assistant WebSocket")
                    await websocket.send(json.dumps({"type": "auth", "access_token": self.token}))
                    auth = json.loads(await websocket.recv())
                    if auth.get("type") != "auth_ok":
                        raise HomeAssistantError("Home Assistant WebSocket: авторизация отклонена")
                    await websocket.send(json.dumps({"id": 1, "type": "subscribe_events", "event_type": "state_changed"}))
                    subscribed = json.loads(await websocket.recv())
                    if not subscribed.get("success"):
                        raise HomeAssistantError("Не удалось подписаться на state_changed")
                    retry = 2
                    while not stop_event.is_set():
                        try:
                            raw = await asyncio.wait_for(websocket.recv(), timeout=60)
                        except asyncio.TimeoutError:
                            continue
                        message = json.loads(raw)
                        if message.get("type") != "event":
                            continue
                        event = message.get("event") or {}
                        if event.get("event_type") == "state_changed":
                            await callback(event.get("data") or {})
            except asyncio.CancelledError:
                raise
            except Exception:
                if stop_event.is_set():
                    break
                await asyncio.sleep(retry)
                retry = min(retry * 2, 30)
