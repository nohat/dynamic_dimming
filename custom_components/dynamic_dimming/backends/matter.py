"""Native Matter backend: Level Control Move/Stop over the Matter server's websocket.

Home Assistant's Matter integration can set a brightness, and on most devices fade
to one, but it has no notion of "start dimming and keep going until I let go". The
Level Control cluster's ``Move`` and ``Stop`` commands are not surfaced by the light
platform, by a service, or by the integration's websocket API — so the only way to
send them is to talk to the Matter server directly. This backend opens its own
websocket to the same server the Matter config entry points at and issues
``device_command`` calls itself.

That is worth a second connection because Move/Stop is the real primitive. The
device ramps its own ``CurrentLevel`` at a rate it was told exactly once, so a
two-second hold costs two commands instead of forty absolute writes — which on
Thread, where a mesh of battery-relayed hops has a fraction of Wi-Fi's headroom,
is the difference between a smooth ramp and a congested network.

Unlike the WiZ path, nothing here goes stale: the writes land on the device, the
device reports its new ``CurrentLevel``, and the Matter integration's own
subscription feeds that back into Home Assistant's state machine. There is no
resync to do.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, NamedTuple

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..const import (
    DIRECTION_UP,
    MATTER_COLOR_CONTROL_CLUSTER,
    MATTER_DOMAIN,
    MATTER_LEVEL_CONTROL_CLUSTER,
    MATTER_MAX_LEVEL,
    MATTER_MAX_TRANSITION_TENTHS,
    MATTER_MIN_LEVEL,
    MATTER_MOVE_MODE_DOWN,
    MATTER_MOVE_MODE_UP,
)
from .base import DimmingBackend
from .simulation import resolve_rate

_LOGGER = logging.getLogger(__name__)

# python-matter-server's websocket API: a command is
# {"message_id": ..., "command": ..., "args": {...}} and the server replies with
# either a "result" or an "error_code"/"details" pair carrying the same id.
_DEVICE_COMMAND = "device_command"

_CONNECT_TIMEOUT = 10.0
# Keeps a silently dropped connection (server restart, container bounce) from
# looking alive until the next hold fails.
_HEARTBEAT = 30.0
# Replies are only read to surface errors, so an id whose reply never arrives is
# harmless — but it must not accumulate for the life of the connection either.
_MAX_PENDING = 64

_MAX_BRIGHTNESS = 255
# HA builds a Matter entity's unique_id as
#   <fabric:016X>-<node:016X>-<postfix>-<endpoint>-<key>-<cluster>-<attribute>
# Only the leading four segments are fixed-position, which is all we need: the
# trailing ones can and do contain further dashes.
_OPERATIONAL_ID_HEX_LEN = 16


class _Target(NamedTuple):
    """Everything needed to address one endpoint on one Matter server."""

    url: str
    node_id: int
    endpoint_id: int


def parse_unique_id(unique_id: str) -> tuple[int, int] | None:
    """Pull ``(node_id, endpoint_id)`` out of a Matter entity's unique_id.

    Reading the registry rather than the Matter client is what keeps this
    backend independent of the Matter integration's internals: the same node and
    endpoint are already encoded in the unique_id the integration wrote, and that
    string is stable across restarts, renames and reloads.
    """
    parts = unique_id.split("-")
    if len(parts) < 5:
        return None
    fabric_hex, node_hex, _postfix, endpoint = parts[:4]
    if len(fabric_hex) != _OPERATIONAL_ID_HEX_LEN:
        return None
    if len(node_hex) != _OPERATIONAL_ID_HEX_LEN:
        return None
    try:
        return int(node_hex, 16), int(endpoint)
    except ValueError:
        return None


def to_level(brightness: float) -> int:
    """Map HA's 0-255 brightness onto Matter's 1-254 level scale."""
    scaled = round(brightness / _MAX_BRIGHTNESS * MATTER_MAX_LEVEL)
    return max(MATTER_MIN_LEVEL, min(MATTER_MAX_LEVEL, int(scaled)))


class _ServerConnection:
    """One lazily-opened websocket to one Matter server.

    Commands are written and not waited on. A Thread round-trip is hundreds of
    milliseconds and a hold-to-dim gesture must not pay it twice; the reply is
    only interesting when it is an error, which the reader task logs. Losing the
    connection is not fatal either — the next command reopens it.
    """

    def __init__(self, hass: HomeAssistant, url: str) -> None:
        self.hass = hass
        self.url = url
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._reader: asyncio.Task | None = None
        self._connect_lock = asyncio.Lock()
        self._message_id = 0
        # message_id -> human description, so an error reply can name what failed.
        self._pending: dict[str, str] = {}
        # A server that is down would otherwise log once per button press.
        self._reported_failure = False

    async def _async_ws(self) -> aiohttp.ClientWebSocketResponse | None:
        async with self._connect_lock:
            if self._ws is not None and not self._ws.closed:
                return self._ws
            session = async_get_clientsession(self.hass)
            try:
                async with asyncio.timeout(_CONNECT_TIMEOUT):
                    ws = await session.ws_connect(self.url, heartbeat=_HEARTBEAT)
            except (aiohttp.ClientError, TimeoutError, OSError) as err:
                self._log_failure("could not connect to Matter server %s: %s", err)
                return None
            self._reported_failure = False
            self._ws = ws
            self._reader = self.hass.async_create_background_task(
                self._read(ws), f"dynamic_dimming matter reader {self.url}"
            )
            return ws

    def _log_failure(self, message: str, err: object) -> None:
        """Warn on the first failure of a run, then stay quiet about it."""
        if self._reported_failure:
            _LOGGER.debug(message, self.url, err)
            return
        self._reported_failure = True
        _LOGGER.warning(message, self.url, err)

    async def _read(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """Drain replies, logging the ones that report a failure.

        The server also pushes an unsolicited ServerInfoMessage on connect; it
        carries no ``message_id`` and is simply ignored.
        """
        try:
            async for msg in ws:
                if msg.type is not aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    data = msg.json()
                except ValueError:
                    continue
                if not isinstance(data, dict):
                    continue
                description = self._pending.pop(data.get("message_id", ""), None)
                if "error_code" in data:
                    _LOGGER.warning(
                        "Matter server rejected %s: %s (error_code %s)",
                        description or "a command",
                        data.get("details"),
                        data["error_code"],
                    )
        except (aiohttp.ClientError, OSError) as err:
            _LOGGER.debug("Matter websocket %s dropped: %s", self.url, err)
        finally:
            # Only disown the socket this task was reading; a reconnect may
            # already have installed a newer one.
            if self._ws is ws:
                self._ws = None
                self._pending.clear()

    async def async_send(
        self, command: str, args: dict[str, Any], description: str
    ) -> bool:
        """Write one command. Returns whether it reached the wire."""
        ws = await self._async_ws()
        if ws is None:
            return False
        self._message_id += 1
        message_id = str(self._message_id)
        if len(self._pending) >= _MAX_PENDING:
            self._pending.pop(next(iter(self._pending)))
        self._pending[message_id] = description
        try:
            await ws.send_json(
                {"message_id": message_id, "command": command, "args": args}
            )
        except (aiohttp.ClientError, OSError) as err:
            self._pending.pop(message_id, None)
            self._log_failure("Matter send to %s failed: %s", err)
            await self.async_close()
            return False
        return True

    async def async_close(self) -> None:
        # Under the connect lock so a close racing a reconnect cannot orphan a
        # freshly opened socket (and its reader task) by nulling it out.
        async with self._connect_lock:
            ws, self._ws = self._ws, None
            reader, self._reader = self._reader, None
            self._pending.clear()
            if reader is not None:
                reader.cancel()
            if ws is not None and not ws.closed:
                await ws.close()


class MatterBackend(DimmingBackend):
    """Sends Level Control commands to Matter nodes over the Matter server API.

    Plain ``Move`` and ``Step`` are used, never the ``WithOnOff`` variants, so
    dimming down bottoms out at the device's minimum on-level and stays lit —
    the same semantics the Zigbee2MQTT backend picks for the same reason. The
    corollary, per the Level Control spec's Options handling, is that a Move on a
    light that is *off* does nothing: turn it on first.

    ``fade`` is the exception. An absolute target that a scene asked for should
    light the fixture, so it goes out as ``MoveToLevelWithOnOff`` with a
    transition time and the device runs the whole ramp itself.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry | None = None) -> None:
        self.hass = hass
        self._entry = entry
        # Matter server URL -> its connection. Keyed by URL rather than held as a
        # single socket because nothing stops a house from running two fabrics.
        self._connections: dict[str, _ServerConnection] = {}

    async def async_unload(self) -> None:
        for connection in list(self._connections.values()):
            await connection.async_close()
        self._connections.clear()

    # -- entity -> Matter node ----------------------------------------------------

    def _target(self, entity_id: str) -> _Target | None:
        """Resolve an entity to a Matter server, node and endpoint, or None.

        None means "not ours", and the entity degrades to simulation. That
        includes a house whose Matter integration is not loaded: registry
        entries outlive it, and the server behind them may well still answer,
        but a Matter stack that is down or disabled is exactly when
        ``light.turn_on`` through simulation is the honest fallback.
        """
        if MATTER_DOMAIN not in self.hass.config.components:
            return None
        entity = er.async_get(self.hass).async_get(entity_id)
        if entity is None or entity.platform != MATTER_DOMAIN:
            return None
        if entity.config_entry_id is None:
            return None
        config_entry = self.hass.config_entries.async_get_entry(entity.config_entry_id)
        if config_entry is None or config_entry.domain != MATTER_DOMAIN:
            return None
        url = config_entry.data.get(CONF_URL)
        if not url:
            return None
        parsed = parse_unique_id(entity.unique_id)
        if parsed is None:
            return None
        node_id, endpoint_id = parsed
        return _Target(url, node_id, endpoint_id)

    def claims(self, entity_id: str) -> bool:
        return self._target(entity_id) is not None

    # -- wire ---------------------------------------------------------------------

    def _connection(self, url: str) -> _ServerConnection:
        connection = self._connections.get(url)
        if connection is None:
            connection = _ServerConnection(self.hass, url)
            self._connections[url] = connection
        return connection

    async def _command(
        self, target: _Target, cluster_id: int, command_name: str, payload: dict
    ) -> None:
        await self._connection(target.url).async_send(
            _DEVICE_COMMAND,
            {
                "node_id": target.node_id,
                "endpoint_id": target.endpoint_id,
                "cluster_id": cluster_id,
                "command_name": command_name,
                "payload": payload,
            },
            f"{command_name} on node {target.node_id}/{target.endpoint_id}",
        )

    async def _level_command(
        self, target: _Target, command_name: str, payload: dict
    ) -> None:
        await self._command(
            target, MATTER_LEVEL_CONTROL_CLUSTER, command_name, payload
        )

    # -- backend interface --------------------------------------------------------

    async def async_move(
        self,
        entity_id: str,
        direction: str,
        rate: str | float | None,
        curve: str | float | None = None,
    ) -> CALLBACK_TYPE | None:
        # `curve` intentionally unused: the device runs this ramp, with whatever
        # curve its firmware applies, and this integration does not rewrite
        # device config to change that.
        target = self._target(entity_id)
        if target is None:
            return None
        # Rate is level units per second on the same 0-254 scale the shared rate
        # profiles are already expressed in, so they pass straight through. The
        # ceiling is 254 rather than the uint8 max: 255 is the spec's "use the
        # device's DefaultMoveRate", which is not what a caller asking for a
        # specific rate meant.
        units_per_second = max(1, min(MATTER_MAX_LEVEL, int(round(resolve_rate(rate)))))
        await self._level_command(
            target,
            "Move",
            {
                "moveMode": (
                    MATTER_MOVE_MODE_UP
                    if direction == DIRECTION_UP
                    else MATTER_MOVE_MODE_DOWN
                ),
                "rate": units_per_second,
                # Left at the spec defaults: ExecuteIfOff stays clear, so a Move
                # on an off light is correctly ignored rather than silently
                # winding up a level the user cannot see.
                "optionsMask": 0,
                "optionsOverride": 0,
            },
        )
        # No job handle: the device owns the ramp until Stop.
        return None

    async def async_stop(self, entity_id: str) -> None:
        target = self._target(entity_id)
        if target is None:
            return
        # Stop also terminates a MoveToLevel transition, so this is the right
        # command whether the movement came from `move` or from `fade`.
        await self._level_command(
            target, "Stop", {"optionsMask": 0, "optionsOverride": 0}
        )

    async def async_step(
        self,
        entity_id: str,
        direction: str,
        step_pct: float,
        curve: str | float | None = None,
    ) -> None:
        # `curve` intentionally unused, as for `move`. `transitionTime` is
        # omitted so it defaults to null, which tells the device to use its own
        # OnOffTransitionTime rather than snapping.
        target = self._target(entity_id)
        if target is None:
            return
        step_size = max(
            1, min(MATTER_MAX_LEVEL, int(round(step_pct / 100.0 * MATTER_MAX_LEVEL)))
        )
        await self._level_command(
            target,
            "Step",
            {
                "stepMode": (
                    MATTER_MOVE_MODE_UP
                    if direction == DIRECTION_UP
                    else MATTER_MOVE_MODE_DOWN
                ),
                "stepSize": step_size,
                "optionsMask": 0,
                "optionsOverride": 0,
            },
        )

    @property
    def supports_fade(self) -> bool:
        return True

    async def async_fade(
        self,
        entity_id: str,
        target_brightness: int,
        duration: float,
        curve: str | float | None = None,
        color_temp_kelvin: int | None = None,
    ) -> CALLBACK_TYPE | None:
        """Hand the whole fade to the device as one MoveToLevelWithOnOff.

        The alternative — falling back to simulation — would put twenty absolute
        writes a second onto a Thread mesh for the length of the fade. One
        command with a transition time gets the same result and costs one
        round-trip, so this backend claims the fade even though `curve` cannot
        be honored: the device interpolates its own level linearly, and this
        integration does not rewrite device config to change that.

        ``color_temp_kelvin`` goes out first, with a zero transition, because the
        contract is that color is asserted from the first write rather than
        faded. It carries ExecuteIfOff so that a fade *up from off* arrives at
        the right white instead of flashing whatever the device last restored.
        """
        target = self._target(entity_id)
        if target is None:
            return None

        if color_temp_kelvin is not None:
            await self._command(
                target,
                MATTER_COLOR_CONTROL_CLUSTER,
                "MoveToColorTemperature",
                {
                    "colorTemperatureMireds": max(
                        1, min(65279, int(round(1_000_000 / color_temp_kelvin)))
                    ),
                    "transitionTime": 0,
                    "optionsMask": 1,
                    "optionsOverride": 1,
                },
            )

        await self._level_command(
            target,
            "MoveToLevelWithOnOff",
            {
                "level": to_level(target_brightness),
                # Matter measures a transition in tenths of a second.
                "transitionTime": max(
                    0, min(MATTER_MAX_TRANSITION_TENTHS, int(round(duration * 10)))
                ),
                "optionsMask": 0,
                "optionsOverride": 0,
            },
        )
        # No job handle: the device owns the ramp, and `stop` reaches it.
        return None
