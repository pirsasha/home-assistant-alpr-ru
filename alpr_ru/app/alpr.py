from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import httpx


class AlprError(RuntimeError):
    pass


def validate_api_key(value: str) -> str:
    """Validate that the API key is safe to send as an HTTP header."""
    api_key = str(value or "").strip()
    if not api_key:
        raise AlprError("Не задан API key ALPR-RU")
    try:
        api_key.encode("ascii")
    except UnicodeEncodeError as error:
        raise AlprError(
            "API key содержит кириллицу или другие недопустимые символы. "
            "Скопируйте ключ заново без изменений."
        ) from error
    if any(ord(char) < 33 or ord(char) > 126 for char in api_key):
        raise AlprError(
            "API key содержит пробелы или управляющие символы. "
            "Скопируйте ключ заново без изменений."
        )
    return api_key


async def recognize(
    *,
    api_url: str,
    api_key: str,
    image: bytes,
    content_type: str,
    plate_type: str = "auto",
) -> dict[str, Any]:
    api_key = validate_api_key(api_key)
    base = api_url.rstrip("/") + "/"
    url = urljoin(base, "v1/recognize")
    files = {"file": ("home_assistant_camera.jpg", image, content_type or "image/jpeg")}
    data = {
        "plate_type": plate_type,
        "use_rectifier": "true",
        "include_debug_urls": "true",
    }
    try:
        async with httpx.AsyncClient(
            timeout=45,
            follow_redirects=True,
            trust_env=False,
        ) as client:
            response = await client.post(
                url,
                headers={"X-API-Key": api_key},
                files=files,
                data=data,
            )
    except httpx.HTTPError as error:
        raise AlprError(str(error)) from error
    try:
        payload = response.json()
    except ValueError as error:
        raise AlprError(f"ALPR-RU вернул не JSON (HTTP {response.status_code})") from error
    if response.status_code in (401, 403):
        raise AlprError("Неверный API key ALPR-RU")
    if response.status_code >= 400:
        detail = payload.get("error") if isinstance(payload, dict) else None
        raise AlprError(str(detail or f"HTTP {response.status_code}"))
    return payload if isinstance(payload, dict) else {"data": payload}


async def download_result_image(api_url: str, result: dict[str, Any]) -> tuple[bytes, str] | None:
    path = result.get("rectified_crop_url") or result.get("crop_url")
    if not path:
        return None
    url = str(path)
    if url.startswith("/"):
        url = api_url.rstrip("/") + url
    try:
        async with httpx.AsyncClient(
            timeout=20,
            follow_redirects=True,
            trust_env=False,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            if not response.content:
                return None
            return response.content, response.headers.get("content-type", "image/jpeg").split(";", 1)[0]
    except httpx.HTTPError:
        return None
