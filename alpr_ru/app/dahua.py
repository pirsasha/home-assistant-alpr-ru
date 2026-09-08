from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit

import httpx


class DahuaError(RuntimeError):
    """Direct Dahua event stream error."""


def normalize_camera_url(value: str) -> str:
    """Normalize a Dahua camera base URL without embedding credentials."""
    raw = str(value or "").strip().rstrip("/")
    if not raw:
        raise DahuaError("Не задан адрес Dahua")
    if "://" not in raw:
        raw = f"http://{raw}"
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise DahuaError("Некорректный адрес Dahua")
    if parsed.username or parsed.password:
        raise DahuaError("Логин и пароль Dahua вводятся в отдельных полях")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DahuaMotionListener:
    """Listen to Dahua eventManager.cgi VideoMotion events using Digest auth."""

    def __init__(self) -> None:
        self.connected = False
        self.enabled = False
        self.last_error = ""
        self.last_event_at = ""
        self.last_action = ""
        self.last_heartbeat_at = ""
        self.last_stream_at = ""
        self.camera_url = ""
        self.reconnects = 0

    def disable(self) -> None:
        self.connected = False
        self.enabled = False
        self.last_error = ""
        self.camera_url = ""
        self.last_heartbeat_at = ""
        self.last_stream_at = ""

    def public_status(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "connected": self.connected,
            "camera_url": self.camera_url,
            "last_error": self.last_error,
            "last_event_at": self.last_event_at,
            "last_action": self.last_action,
            "last_heartbeat_at": self.last_heartbeat_at,
            "last_stream_at": self.last_stream_at,
            "reconnects": self.reconnects,
        }

    async def run(
        self,
        *,
        camera_url: str,
        username: str,
        password: str,
        on_motion_start: Callable[[str], Awaitable[None]],
    ) -> None:
        """Keep one persistent Dahua event stream open until this task is cancelled.

        Dahua normally sends a heartbeat every 5 seconds. A finite read timeout is
        intentional: if the camera/router leaves a half-open TCP connection alive
        but stops delivering the multipart stream, httpx raises ReadTimeout and we
        reconnect instead of reporting a stale connection as healthy forever.
        """
        base = normalize_camera_url(camera_url)
        user = str(username or "").strip()
        secret = str(password or "")
        if not user:
            raise DahuaError("Не задан пользователь Dahua")
        if not secret:
            raise DahuaError("Не задан пароль Dahua")

        self.enabled = True
        self.camera_url = base
        retry = 2
        # heartbeat=5 below, so 20 seconds allows several missed heartbeats before
        # considering the stream stale and reconnecting.
        timeout = httpx.Timeout(connect=10, read=20, write=10, pool=10)
        endpoint = f"{base}/cgi-bin/eventManager.cgi"
        params = {
            "action": "attach",
            "codes": "[VideoMotion]",
            "heartbeat": "5",
        }

        while True:
            try:
                auth = httpx.DigestAuth(user, secret)
                async with httpx.AsyncClient(
                    auth=auth,
                    timeout=timeout,
                    follow_redirects=True,
                    trust_env=False,
                ) as client:
                    async with client.stream("GET", endpoint, params=params) as response:
                        if response.status_code in (401, 403):
                            raise DahuaError(
                                f"Dahua отклонила логин/пароль (HTTP {response.status_code})"
                            )
                        response.raise_for_status()
                        self.connected = True
                        self.last_error = ""
                        self.last_stream_at = utc_now()
                        retry = 2

                        async for raw_line in response.aiter_lines():
                            line = raw_line.strip()
                            if not line:
                                continue

                            self.last_stream_at = utc_now()

                            if line == "Heartbeat":
                                self.last_heartbeat_at = self.last_stream_at
                                continue

                            if not line.startswith("Code=VideoMotion;"):
                                continue

                            self.last_event_at = self.last_stream_at
                            if "action=Start" in line:
                                self.last_action = "Start"
                                await on_motion_start(line)
                            elif "action=Stop" in line:
                                self.last_action = "Stop"
            except asyncio.CancelledError:
                self.connected = False
                raise
            except DahuaError as error:
                self.connected = False
                self.last_error = str(error)
            except httpx.ReadTimeout:
                self.connected = False
                self.last_error = "Dahua event stream: нет heartbeat более 20 сек, переподключение"
            except httpx.HTTPError as error:
                self.connected = False
                self.last_error = f"Dahua event stream: {error}"
            except Exception as error:
                self.connected = False
                self.last_error = f"Dahua event stream: {error}"

            self.reconnects += 1
            await asyncio.sleep(retry)
            retry = min(retry * 2, 30)
