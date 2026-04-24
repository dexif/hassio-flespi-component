"""Shared paho-mqtt client pool — one TCP/TLS connection per (host, port, tls, protocol, token)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

import paho.mqtt.client as mqtt_client

from homeassistant.core import HomeAssistant

from .const import DEFAULT_PROTOCOL, DOMAIN, PROTOCOL_V31, PROTOCOL_V311, PROTOCOL_V5

_LOGGER = logging.getLogger(__name__)

_PROTOCOL_MAP: dict[str, int] = {
    PROTOCOL_V31: mqtt_client.MQTTv31,
    PROTOCOL_V311: mqtt_client.MQTTv311,
    PROTOCOL_V5: mqtt_client.MQTTv5,
}

# Pool key: host, port, use_tls, protocol, token.
ConnectionKey = tuple[str, int, bool, str, str]

TopicCallback = Callable[[str, bytes], None]


def build_direct_client(
    client_id: str,
    token: str,
    use_tls: bool,
    protocol: str = DEFAULT_PROTOCOL,
) -> mqtt_client.Client:
    """Create a paho MQTT client configured for flespi direct mode."""
    paho_protocol = _PROTOCOL_MAP[protocol]
    kwargs: dict[str, Any] = {
        "callback_api_version": mqtt_client.CallbackAPIVersion.VERSION2,
        "client_id": client_id,
        "protocol": paho_protocol,
    }
    # clean_session is only accepted for MQTTv3; MQTTv5 uses clean_start on connect.
    if paho_protocol != mqtt_client.MQTTv5:
        kwargs["clean_session"] = True
    client = mqtt_client.Client(**kwargs)
    client.username_pw_set(token, "")
    if use_tls:
        client.tls_set()
    return client


class FlespiDirectClient:
    """A paho MQTT client shared across multiple coordinators with identical creds."""

    def __init__(
        self, hass: HomeAssistant, key: ConnectionKey, client: mqtt_client.Client
    ) -> None:
        self.hass = hass
        self.key = key
        self._client = client
        # Topic filter -> list of HA-loop callbacks. Multiplexes multiple subscribers
        # on the same filter (rare in practice, but cheap insurance).
        self._topic_callbacks: dict[str, list[TopicCallback]] = {}
        self._connected = False
        self._ref_count = 0
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            _LOGGER.error(
                "Pooled flespi MQTT connect failed for %s: %s",
                self.key[0],
                reason_code,
            )
            return
        self._connected = True
        # Restore every subscription — paho drops them on disconnect.
        for topic_filter in self._topic_callbacks:
            client.subscribe(topic_filter, qos=0)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        self._connected = False
        if reason_code != 0:
            _LOGGER.warning(
                "Pooled flespi MQTT disconnected from %s: %s",
                self.key[0],
                reason_code,
            )

    async def connect(self) -> None:
        host, port = self.key[0], self.key[1]
        self._client.reconnect_delay_set(min_delay=1, max_delay=60)
        self._client.connect_async(host, port, keepalive=60)
        self._client.loop_start()

    async def disconnect(self) -> None:
        def _stop() -> None:
            self._client.disconnect()
            self._client.loop_stop()

        await self.hass.async_add_executor_job(_stop)

    def subscribe(
        self, topic_filter: str, callback: TopicCallback
    ) -> Callable[[], None]:
        """Register a callback for a topic filter. Returns an unsubscribe handle.

        `callback(topic, payload)` is invoked on the HA event loop for every
        message whose topic matches this filter.
        """
        loop = self.hass.loop

        callbacks = self._topic_callbacks.setdefault(topic_filter, [])
        first_subscriber = not callbacks
        callbacks.append(callback)

        if first_subscriber:
            # Paho dispatches matching messages to this specific filter callback.
            def _paho_cb(client, userdata, message) -> None:
                for cb in self._topic_callbacks.get(topic_filter, []):
                    loop.call_soon_threadsafe(cb, message.topic, message.payload)

            self._client.message_callback_add(topic_filter, _paho_cb)
            if self._connected:
                self._client.subscribe(topic_filter, qos=0)

        def _unsub() -> None:
            cbs = self._topic_callbacks.get(topic_filter)
            if cbs is None:
                return
            if callback in cbs:
                cbs.remove(callback)
            if not cbs:
                self._topic_callbacks.pop(topic_filter, None)
                self._client.message_callback_remove(topic_filter)
                if self._connected:
                    self._client.unsubscribe(topic_filter)

        return _unsub


class DirectClientPool:
    """Maintains one FlespiDirectClient per unique (host, port, tls, protocol, token)."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._clients: dict[ConnectionKey, FlespiDirectClient] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, key: ConnectionKey) -> FlespiDirectClient:
        """Return (and if needed, create + connect) the pooled client for `key`."""
        async with self._lock:
            pool_client = self._clients.get(key)
            if pool_client is None:
                host, _, use_tls, protocol, token = key
                # Stable per-key client_id; safe across HA restarts because clean_session=True.
                client_id = f"ha-{DOMAIN}-pool-{abs(hash(key)):x}"
                raw_client = build_direct_client(client_id, token, use_tls, protocol)
                pool_client = FlespiDirectClient(self.hass, key, raw_client)
                await pool_client.connect()
                self._clients[key] = pool_client
                _LOGGER.debug(
                    "Pool: opened connection to %s (protocol=%s, tls=%s)",
                    host,
                    protocol,
                    use_tls,
                )
            pool_client._ref_count += 1
            return pool_client

    async def release(self, pool_client: FlespiDirectClient) -> None:
        """Decrement ref count; disconnect & drop the client when it hits zero."""
        async with self._lock:
            pool_client._ref_count -= 1
            if pool_client._ref_count > 0:
                return
            self._clients.pop(pool_client.key, None)
        # Disconnect outside the lock — it awaits an executor job.
        await pool_client.disconnect()
        _LOGGER.debug("Pool: closed connection to %s", pool_client.key[0])


def get_pool(hass: HomeAssistant) -> DirectClientPool:
    """Lazily construct and return the singleton pool for this HA instance."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    pool = domain_data.get("_pool")
    if pool is None:
        pool = DirectClientPool(hass)
        domain_data["_pool"] = pool
    return pool
