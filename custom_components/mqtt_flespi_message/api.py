"""Thin REST client for the flespi HTTP API."""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import quote

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import FLESPI_REST_BASE_URL

_LOGGER = logging.getLogger(__name__)


class FlespiApiError(Exception):
    """Raised when the flespi REST API returns an error."""


class FlespiRestClient:
    """Minimal async client for flespi REST calls used by the integration."""

    def __init__(
        self,
        hass: HomeAssistant,
        token: str,
        base_url: str = FLESPI_REST_BASE_URL,
    ) -> None:
        self._session: aiohttp.ClientSession = async_get_clientsession(hass)
        self._headers = {"Authorization": f"FlespiToken {token}"}
        self._base_url = base_url.rstrip("/")

    async def _get(self, path: str, **params: Any) -> list[dict[str, Any]]:
        url = f"{self._base_url}{path}"
        try:
            async with self._session.get(
                url,
                headers=self._headers,
                params=params,
                timeout=aiohttp.ClientTimeout(total=8),
            ) as resp:
                text = await resp.text()
        except aiohttp.ClientError as err:
            raise FlespiApiError(f"flespi GET {path} network error: {err}") from err

        # HTTP errors may carry plain-text bodies (e.g. `404 Not Found` from a
        # proxy) that aren't valid JSON — surface the status + body directly
        # without pretending the server returned malformed JSON.
        if resp.status >= 400:
            preview = text[:300] if text else "<empty>"
            raise FlespiApiError(
                f"flespi GET {path} -> HTTP {resp.status}: {preview}"
            )

        try:
            body = json.loads(text)
        except (ValueError, TypeError) as err:
            preview = text[:300] if text else "<empty>"
            raise FlespiApiError(
                f"flespi GET {path} -> malformed JSON ({err}); body preview: {preview!r}"
            ) from err

        result = body.get("result") if isinstance(body, dict) else None
        if not isinstance(result, list):
            raise FlespiApiError(
                f"Unexpected response shape from {path}: {str(body)[:300]}"
            )
        return result

    async def get_device_telemetry(self, device_id: int) -> dict[str, Any]:
        """Return the latest telemetry snapshot for a device.

        The flespi sub-resource requires a field-selector after `/telemetry/`;
        `/all` returns every parameter. Response result is a list with a single
        object whose `telemetry` field maps parameter names to {value, ts} pairs.
        """
        result = await self._get(f"/gw/devices/{device_id}/telemetry/all")
        if not result:
            return {}
        telemetry = result[0].get("telemetry") or {}
        return telemetry if isinstance(telemetry, dict) else {}

    async def get_message_parameters(self) -> dict[str, dict[str, Any]]:
        """Return the message-parameter dictionary keyed by parameter name.

        flespi exposes `name`, `units`, and `type` on this endpoint. There is
        no `description` field, so human-readable sensor names fall back to
        slug-derived labels via entity._derive_name.
        """
        result = await self._get(
            "/gw/message-parameters/all",
            fields="name,units,type",
        )
        return {item["name"]: item for item in result if "name" in item}

    async def search_devices(
        self, query: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Server-side search by name or ident substring.

        Uses a flespi expression selector so the match runs on flespi's side —
        this scales to accounts with tens of thousands of devices.
        Returns an empty list for empty/whitespace queries.
        """
        safe_q = query.replace('"', "").replace("\\", "").strip()
        if not safe_q:
            return []
        expr = f'{{name~"*{safe_q}*" || configuration.ident~"*{safe_q}*"}}'
        path = f"/gw/devices/{quote(expr, safe='')}"
        return await self._get(
            path,
            fields="id,name,configuration.ident",
            limit=limit,
        )
