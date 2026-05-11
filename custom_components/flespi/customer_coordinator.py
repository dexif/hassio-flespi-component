"""Coordinator for flespi platform customer counters (Master-token-only)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .const import (
    CONF_HOST,
    CONF_PORT,
    CONF_PROTOCOL,
    CONF_TOKEN,
    CONF_USE_TLS,
    DEFAULT_HOST,
    DEFAULT_PORT_TLS,
    DEFAULT_PROTOCOL,
)
from .pool import ConnectionKey, get_pool

_LOGGER = logging.getLogger(__name__)

_COUNTERS_TOPIC_PREFIX = "flespi/state/platform/customer/counters/"
_COUNTERS_TOPIC_FILTER = "flespi/state/platform/customer/counters/#"
_ACCESS_TYPE_MASTER = 1


class FlespiCustomerCoordinator:
    """Subscribe to flespi customer counters on Master-token connections.

    Non-Master tokens silently skip -- flespi only publishes
    ``flespi/state/platform/*`` to Master tokens.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.cid: int | None = None
        self.data: dict[str, Any] = {}
        self._listeners: list[Callable[[], None]] = []
        self._new_key_callbacks: list[Callable[[str], None]] = []
        self._teardown: Callable[[], Awaitable[None]] | None = None
        self._got_data = asyncio.Event()

    async def async_start(self) -> bool:
        """Acquire pool client, check identity, subscribe if Master.

        Returns True if subscribed, False if silently skipped.
        """
        data = self.entry.data
        key: ConnectionKey = (
            data.get(CONF_HOST, DEFAULT_HOST),
            data.get(CONF_PORT, DEFAULT_PORT_TLS),
            data.get(CONF_USE_TLS, True),
            data.get(CONF_PROTOCOL, DEFAULT_PROTOCOL),
            data[CONF_TOKEN],
        )
        pool = get_pool(self.hass)
        pool_client = await pool.acquire(key)

        try:
            await pool_client.wait_identity()
        except Exception:
            await pool.release(pool_client)
            raise

        if pool_client.access_type != _ACCESS_TYPE_MASTER:
            await pool.release(pool_client)
            return False

        self.cid = pool_client.cid

        # Pass cid as an MQTT v5 User Property on the SUBSCRIBE packet
        # so the broker scopes delivery to this customer.
        props: Properties | None = None
        if self.cid is not None:
            props = Properties(PacketTypes.SUBSCRIBE)
            props.UserProperty = [("cid", str(self.cid))]

        unsub = pool_client.subscribe(
            _COUNTERS_TOPIC_FILTER,
            self._process_counter,
            properties=props,
        )

        async def teardown() -> None:
            unsub()
            await pool.release(pool_client)

        self._teardown = teardown

        # Retained messages arrive in a burst after subscribe; wait so
        # build_customer_sensors sees the full counter set.
        try:
            await self._wait_initial_data()
        except Exception:
            await self.async_stop()
            raise

        return True

    async def async_stop(self) -> None:
        if self._teardown is not None:
            await self._teardown()
            self._teardown = None

    async def _wait_initial_data(self, timeout: float = 5.0) -> None:
        """Wait for the first retained counter, then drain queued callbacks."""
        try:
            await asyncio.wait_for(self._got_data.wait(), timeout)
        except asyncio.TimeoutError:
            _LOGGER.debug(
                "No customer counters received within %.1fs -- "
                "sensors will populate as messages arrive",
                timeout,
            )
            return
        # Retained messages arrive as a burst of call_soon_threadsafe
        # callbacks queued on the HA loop.  The waiter resumes only after
        # all callbacks that were already queued at set()-time have run
        # (asyncio.Event.set schedules via call_soon, which lands after
        # the remaining threadsafe callbacks).  Two extra yields catch
        # any that arrive during processing.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    @callback
    def _process_counter(self, topic: str, payload: bytes) -> None:
        if not topic.startswith(_COUNTERS_TOPIC_PREFIX):
            return
        counter_key = topic[len(_COUNTERS_TOPIC_PREFIX):]
        if not counter_key:
            return

        try:
            value = json.loads(payload)
        except (ValueError, TypeError):
            _LOGGER.debug("Unparseable counter payload on %s: %r", topic, payload)
            return

        # Only scalar numbers are meaningful counters; per-device JSON
        # blobs (devices/{id}) and other non-numeric payloads are skipped.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return

        is_new = counter_key not in self.data
        self.data[counter_key] = value

        if not self._got_data.is_set():
            self._got_data.set()

        if is_new:
            for cb in self._new_key_callbacks:
                cb(counter_key)

        self._notify()

    def _notify(self) -> None:
        for cb in self._listeners:
            cb()

    @callback
    def async_add_listener(self, update_callback: Callable[[], None]) -> None:
        """Register a listener for value updates on existing counters."""
        self._listeners.append(update_callback)

    @callback
    def on_new_key(self, new_key_callback: Callable[[str], None]) -> None:
        """Register a callback fired when a previously unseen counter key arrives."""
        self._new_key_callbacks.append(new_key_callback)
