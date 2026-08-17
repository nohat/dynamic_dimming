"""Tests for the native Matter backend."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import patch

import aiohttp
import pytest

from homeassistant.const import CONF_URL
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dynamic_dimming.backends.matter import (
    MatterBackend,
    parse_unique_id,
    to_level,
)
from custom_components.dynamic_dimming.capability import classify
from custom_components.dynamic_dimming.const import (
    BACKEND_SIMULATED,
    DIRECTION_DOWN,
    DIRECTION_UP,
    DOMAIN,
    MATTER_COLOR_CONTROL_CLUSTER,
    MATTER_LEVEL_CONTROL_CLUSTER,
    DimmingClass,
)
from custom_components.dynamic_dimming.controller import DimmingController

from .conftest import set_light_state

URL = "ws://homeassistant.local:5580/ws"
FABRIC = "0000000012345678"
NODE = "000000000000000A"  # node_id 10


# -- fake websocket transport -------------------------------------------------------


class FakeMessage:
    """One text frame from the fake server."""

    type = aiohttp.WSMsgType.TEXT

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeWebSocket:
    """Records commands written to it and can push replies back."""

    def __init__(self):
        self.sent: list[dict] = []
        self.closed = False
        # Simulates a socket that still looks open but fails on write.
        self.fail_send = False
        self._inbox: asyncio.Queue = asyncio.Queue()

    async def send_json(self, data):
        if self.closed or self.fail_send:
            raise aiohttp.ClientError("socket is closed")
        self.sent.append(data)

    def push(self, payload):
        """Deliver a server -> client frame to the backend's reader task."""
        self._inbox.put_nowait(payload)

    async def close(self):
        self.closed = True
        self._inbox.put_nowait(None)

    def __aiter__(self):
        return self

    async def __anext__(self):
        payload = await self._inbox.get()
        if payload is None:
            raise StopAsyncIteration
        return FakeMessage(payload)

    # -- assertions helpers --

    @property
    def args(self) -> list[dict]:
        return [message["args"] for message in self.sent]

    @property
    def commands(self) -> list[str]:
        return [message["args"]["command_name"] for message in self.sent]


class FakeSession:
    """Stands in for HA's shared aiohttp session."""

    def __init__(self, error: Exception | None = None):
        self.error = error
        self.connects: list[str] = []
        self.sockets: dict[str, FakeWebSocket] = {}

    async def ws_connect(self, url, **_kwargs):
        self.connects.append(url)
        if self.error is not None:
            raise self.error
        return self.sockets.setdefault(url, FakeWebSocket())


@pytest.fixture
def session():
    """Stand in for HA's shared aiohttp session for the length of one test."""
    fake = FakeSession()
    with patch(
        "custom_components.dynamic_dimming.backends.matter.async_get_clientsession",
        return_value=fake,
    ):
        yield fake


# -- registry fixtures ---------------------------------------------------------------


def matter_light(
    hass,
    object_id="ceiling",
    *,
    node_hex=NODE,
    endpoint=1,
    url=URL,
    unique_id=None,
    loaded=True,
    brightness=100,
):
    """Register light.<object_id> as a Matter light on a Matter config entry."""
    if loaded:
        hass.config.components.add("matter")
    entry = MockConfigEntry(domain="matter", data={CONF_URL: url} if url else {})
    entry.add_to_hass(hass)
    if unique_id is None:
        # The shape HA's matter integration writes:
        # <fabric>-<node>-<postfix>-<endpoint>-<key>-<cluster>-<attribute>
        unique_id = f"{FABRIC}-{node_hex}-MatterNodeDevice-{endpoint}-MatterLight-6-0"
    er.async_get(hass).async_get_or_create(
        "light",
        "matter",
        unique_id,
        suggested_object_id=object_id,
        config_entry=entry,
    )
    entity_id = f"light.{object_id}"
    set_light_state(hass, entity_id, brightness=brightness)
    return entity_id


async def _drain(hass):
    """Let the reader task pick up anything queued for it."""
    await hass.async_block_till_done()
    await asyncio.sleep(0)
    await asyncio.sleep(0)


# -- unique_id parsing ---------------------------------------------------------------


def test_parse_unique_id_reads_node_and_endpoint():
    assert parse_unique_id(
        f"{FABRIC}-{NODE}-MatterNodeDevice-1-MatterLight-6-0"
    ) == (10, 1)


def test_parse_unique_id_handles_bridged_postfix():
    # A bridged device's postfix is its endpoint id, not "MatterNodeDevice";
    # the entity's own endpoint is still the fourth segment.
    assert parse_unique_id(f"{FABRIC}-{NODE}-3-7-MatterLight-6-0") == (10, 7)


def test_parse_unique_id_tolerates_dashes_further_right():
    # Only the leading four segments are fixed-position, which is why parsing
    # works from the left rather than the right.
    assert parse_unique_id(
        f"{FABRIC}-{NODE}-MatterNodeDevice-2-Matter-Light-Thing-6-0"
    ) == (10, 2)


@pytest.mark.parametrize(
    "unique_id",
    [
        "",
        "not-a-matter-unique-id",
        f"{FABRIC}-{NODE}-MatterNodeDevice-1",  # truncated
        f"short-{NODE}-MatterNodeDevice-1-MatterLight-6-0",
        f"{FABRIC}-nothexadecimal!!-MatterNodeDevice-1-MatterLight-6-0",
        f"{FABRIC}-{NODE}-MatterNodeDevice-x-MatterLight-6-0",
    ],
)
def test_parse_unique_id_rejects_junk(unique_id):
    assert parse_unique_id(unique_id) is None


# -- level mapping -------------------------------------------------------------------


def test_to_level_maps_and_clamps():
    assert to_level(255) == 254
    assert to_level(128) == 127
    # Matter reserves 0 for "off", reachable only via the WithOnOff variants.
    assert to_level(0) == 1
    assert to_level(1000) == 254


# -- claiming ------------------------------------------------------------------------


async def test_claims_matter_light(hass, session):
    entity_id = matter_light(hass)
    assert MatterBackend(hass).claims(entity_id)


async def test_does_not_claim_non_matter_light(hass, session):
    set_light_state(hass, "light.other", brightness=100)
    assert not MatterBackend(hass).claims("light.other")


async def test_does_not_claim_when_matter_is_not_loaded(hass, session):
    # Registry entries outlive the integration; a Matter stack that is down or
    # disabled degrades to simulation rather than to a light that does nothing.
    entity_id = matter_light(hass, loaded=False)
    assert not MatterBackend(hass).claims(entity_id)


async def test_does_not_claim_entry_without_url(hass, session):
    entity_id = matter_light(hass, url=None)
    assert not MatterBackend(hass).claims(entity_id)


async def test_does_not_claim_unparseable_unique_id(hass, session):
    entity_id = matter_light(hass, unique_id="legacy_matter_id")
    assert not MatterBackend(hass).claims(entity_id)


# -- move / stop ---------------------------------------------------------------------


async def test_move_up_sends_level_control_move(hass, session):
    entity_id = matter_light(hass)
    backend = MatterBackend(hass)

    assert await backend.async_move(entity_id, DIRECTION_UP, "medium") is None
    await _drain(hass)

    ws = session.sockets[URL]
    assert len(ws.sent) == 1
    assert ws.sent[0]["command"] == "device_command"
    assert ws.args[0] == {
        "node_id": 10,
        "endpoint_id": 1,
        "cluster_id": MATTER_LEVEL_CONTROL_CLUSTER,
        "command_name": "Move",
        "payload": {
            "moveMode": 0,
            "rate": 90,
            "optionsMask": 0,
            "optionsOverride": 0,
        },
    }
    await backend.async_unload()


async def test_move_down_flips_move_mode(hass, session):
    entity_id = matter_light(hass)
    backend = MatterBackend(hass)

    await backend.async_move(entity_id, DIRECTION_DOWN, 40)
    await _drain(hass)

    payload = session.sockets[URL].args[0]["payload"]
    assert payload["moveMode"] == 1
    assert payload["rate"] == 40
    await backend.async_unload()


async def test_move_never_uses_the_onoff_variant(hass, session):
    # Plain Move floors at the device's minimum on-level and stays lit; the
    # WithOnOff variant would drive it off.
    entity_id = matter_light(hass)
    backend = MatterBackend(hass)

    await backend.async_move(entity_id, DIRECTION_DOWN, "fast")
    await backend.async_step(entity_id, DIRECTION_DOWN, 5.0)
    await _drain(hass)

    assert session.sockets[URL].commands == ["Move", "Step"]
    await backend.async_unload()


async def test_move_rate_is_clamped_into_the_uint8_range(hass, session):
    entity_id = matter_light(hass)
    backend = MatterBackend(hass)

    await backend.async_move(entity_id, DIRECTION_UP, 5000)
    await _drain(hass)

    assert session.sockets[URL].args[0]["payload"]["rate"] == 254
    await backend.async_unload()


async def test_move_on_unclaimed_entity_sends_nothing(hass, session):
    set_light_state(hass, "light.other", brightness=100)
    backend = MatterBackend(hass)

    await backend.async_move("light.other", DIRECTION_UP, "medium")
    await _drain(hass)

    assert session.connects == []
    await backend.async_unload()


async def test_stop_sends_level_control_stop(hass, session):
    entity_id = matter_light(hass)
    backend = MatterBackend(hass)

    await backend.async_move(entity_id, DIRECTION_UP, "medium")
    await backend.async_stop(entity_id)
    await _drain(hass)

    ws = session.sockets[URL]
    assert ws.commands == ["Move", "Stop"]
    assert ws.args[1]["cluster_id"] == MATTER_LEVEL_CONTROL_CLUSTER
    assert ws.args[1]["payload"] == {"optionsMask": 0, "optionsOverride": 0}
    await backend.async_unload()


async def test_stop_on_unclaimed_entity_is_a_noop(hass, session):
    set_light_state(hass, "light.other", brightness=100)
    backend = MatterBackend(hass)

    await backend.async_stop("light.other")
    await _drain(hass)

    assert session.connects == []
    await backend.async_unload()


# -- step ----------------------------------------------------------------------------


async def test_step_converts_pct_to_level_units(hass, session):
    entity_id = matter_light(hass)
    backend = MatterBackend(hass)

    await backend.async_step(entity_id, DIRECTION_DOWN, 5.0)
    await _drain(hass)

    payload = session.sockets[URL].args[0]["payload"]
    assert payload["stepMode"] == 1
    assert payload["stepSize"] == 13  # 5% of 254
    await backend.async_unload()


async def test_step_sends_transition_time_as_an_explicit_null(hass, session):
    # Regression: transitionTime is mandatory-but-nullable. Omitting the key
    # relies on the server supplying the default, which matter.js does not --
    # it rejects the whole command with ValidationMandatoryFieldMissingError and
    # the step never reaches the device. Verified against matter-server 1.4.0
    # (matter.js 0.17.9): omitted is refused, explicit null works.
    entity_id = matter_light(hass)
    backend = MatterBackend(hass)

    await backend.async_step(entity_id, DIRECTION_UP, 5.0)
    await _drain(hass)

    payload = session.sockets[URL].args[0]["payload"]
    assert "transitionTime" in payload
    assert payload["transitionTime"] is None
    await backend.async_unload()


async def test_step_size_never_rounds_away_to_nothing(hass, session):
    entity_id = matter_light(hass)
    backend = MatterBackend(hass)

    await backend.async_step(entity_id, DIRECTION_UP, 0.1)
    await _drain(hass)

    assert session.sockets[URL].args[0]["payload"]["stepSize"] == 1
    await backend.async_unload()


# -- fade ----------------------------------------------------------------------------


async def test_backend_claims_the_fade(hass):
    # Falling back to simulation would put 20 absolute writes a second onto a
    # Thread mesh; MoveToLevel with a transition time is one command.
    assert MatterBackend(hass).supports_fade


async def test_fade_sends_one_move_to_level_with_transition(hass, session):
    entity_id = matter_light(hass)
    backend = MatterBackend(hass)

    assert await backend.async_fade(entity_id, 255, 2.5) is None
    await _drain(hass)

    ws = session.sockets[URL]
    assert len(ws.sent) == 1
    assert ws.args[0]["command_name"] == "MoveToLevelWithOnOff"
    assert ws.args[0]["payload"]["level"] == 254
    assert ws.args[0]["payload"]["transitionTime"] == 25  # tenths of a second
    await backend.async_unload()


async def test_fade_color_temp_is_asserted_first_and_does_not_fade(hass, session):
    entity_id = matter_light(hass)
    backend = MatterBackend(hass)

    await backend.async_fade(entity_id, 255, 2.0, color_temp_kelvin=2700)
    await _drain(hass)

    ws = session.sockets[URL]
    assert ws.commands == ["MoveToColorTemperature", "MoveToLevelWithOnOff"]
    color = ws.args[0]
    assert color["cluster_id"] == MATTER_COLOR_CONTROL_CLUSTER
    assert color["payload"]["colorTemperatureMireds"] == 370  # 1e6 / 2700
    assert color["payload"]["transitionTime"] == 0  # asserted, not faded
    # ExecuteIfOff, so a fade up from off arrives at the right white instead of
    # flashing whatever the device last restored.
    assert color["payload"]["optionsMask"] == 1
    assert color["payload"]["optionsOverride"] == 1
    await backend.async_unload()


async def test_fade_without_color_sends_no_color_command(hass, session):
    entity_id = matter_light(hass)
    backend = MatterBackend(hass)

    await backend.async_fade(entity_id, 128, 1.0)
    await _drain(hass)

    assert session.sockets[URL].commands == ["MoveToLevelWithOnOff"]
    await backend.async_unload()


# -- connection handling -------------------------------------------------------------


async def test_one_socket_is_reused_across_commands(hass, session):
    entity_id = matter_light(hass)
    backend = MatterBackend(hass)

    await backend.async_move(entity_id, DIRECTION_UP, "medium")
    await backend.async_stop(entity_id)
    await backend.async_step(entity_id, DIRECTION_UP, 5.0)
    await _drain(hass)

    assert session.connects == [URL]
    assert len(session.sockets[URL].sent) == 3
    await backend.async_unload()


async def test_two_fabrics_get_two_sockets(hass, session):
    other_url = "ws://basement:5580/ws"
    first = matter_light(hass, "hall")
    second = matter_light(hass, "shed", node_hex="000000000000000B", url=other_url)
    backend = MatterBackend(hass)

    await backend.async_move(first, DIRECTION_UP, "medium")
    await backend.async_move(second, DIRECTION_UP, "medium")
    await _drain(hass)

    assert sorted(session.connects) == sorted([URL, other_url])
    assert session.sockets[other_url].args[0]["node_id"] == 11
    await backend.async_unload()


async def test_connect_failure_degrades_instead_of_raising(hass, session):
    # A Matter server that is down must mean "this hold does nothing", never an
    # exception out of a service call.
    session.error = aiohttp.ClientError("connection refused")
    entity_id = matter_light(hass)
    backend = MatterBackend(hass)

    await backend.async_move(entity_id, DIRECTION_UP, "medium")
    await backend.async_stop(entity_id)
    await _drain(hass)

    assert session.sockets == {}
    await backend.async_unload()


async def test_closed_socket_is_replaced_on_the_next_command(hass, session):
    entity_id = matter_light(hass)
    backend = MatterBackend(hass)

    await backend.async_move(entity_id, DIRECTION_UP, "medium")
    await _drain(hass)
    dead = session.sockets.pop(URL)
    await dead.close()  # server went away mid-hold
    await _drain(hass)

    await backend.async_stop(entity_id)
    await _drain(hass)

    assert session.connects == [URL, URL]
    assert session.sockets[URL].commands == ["Stop"]
    await backend.async_unload()


async def test_send_failure_drops_the_socket_so_the_next_command_reconnects(hass, session):
    # A socket that still reports itself open but throws on write must not be
    # kept: releasing the button has to reach the device.
    entity_id = matter_light(hass)
    backend = MatterBackend(hass)

    await backend.async_move(entity_id, DIRECTION_UP, "medium")
    await _drain(hass)
    broken = session.sockets.pop(URL)
    broken.fail_send = True
    await backend.async_move(entity_id, DIRECTION_DOWN, "medium")
    await _drain(hass)

    assert broken.closed  # dropped rather than reused
    await backend.async_stop(entity_id)
    await _drain(hass)

    assert session.connects == [URL, URL]
    assert session.sockets[URL].commands == ["Stop"]
    await backend.async_unload()


async def test_error_reply_is_logged_against_its_command(hass, session, caplog):
    entity_id = matter_light(hass)
    backend = MatterBackend(hass)

    await backend.async_move(entity_id, DIRECTION_UP, "medium")
    await _drain(hass)
    with caplog.at_level(logging.WARNING):
        session.sockets[URL].push(
            {"message_id": "1", "error_code": 1, "details": "Node not found"}
        )
        await _drain(hass)

    assert "Move on node 10/1" in caplog.text
    assert "Node not found" in caplog.text
    await backend.async_unload()


async def test_server_info_greeting_is_ignored(hass, session):
    # python-matter-server pushes an unsolicited ServerInfoMessage on connect.
    entity_id = matter_light(hass)
    backend = MatterBackend(hass)

    await backend.async_move(entity_id, DIRECTION_UP, "medium")
    await _drain(hass)
    session.sockets[URL].push({"fabric_id": 1, "schema_version": 11})
    await _drain(hass)

    await backend.async_stop(entity_id)
    await _drain(hass)
    assert session.sockets[URL].commands == ["Move", "Stop"]
    await backend.async_unload()


async def test_unload_closes_every_socket(hass, session):
    entity_id = matter_light(hass)
    backend = MatterBackend(hass)

    await backend.async_move(entity_id, DIRECTION_UP, "medium")
    await _drain(hass)
    await backend.async_unload()
    await _drain(hass)

    assert session.sockets[URL].closed


# -- controller routing ---------------------------------------------------------------


def _controller(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    return DimmingController(hass, entry)


async def test_matter_light_classifies_as_native(hass, session):
    entity_id = matter_light(hass)
    controller = _controller(hass)
    assert (
        classify(hass, entity_id, controller.native_backends) is DimmingClass.NATIVE
    )
    await controller.async_unload()


async def test_controller_routes_move_to_the_matter_backend(hass, session):
    entity_id = matter_light(hass)
    controller = _controller(hass)

    await controller.async_move(entity_id, DIRECTION_UP, "medium")
    await _drain(hass)

    assert session.sockets[URL].commands == ["Move"]
    # The device owns the ramp, so there is no tick job to cancel.
    assert entity_id not in controller._jobs
    await controller.async_unload()


async def test_controller_fade_stays_native_instead_of_ticking(hass, session):
    entity_id = matter_light(hass)
    controller = _controller(hass)

    await controller.async_fade(entity_id, 255, 3.0)
    await _drain(hass)

    assert session.sockets[URL].commands == ["MoveToLevelWithOnOff"]
    assert entity_id not in controller._jobs
    await controller.async_unload()


async def test_simulated_override_still_works_on_a_matter_light(hass, session):
    entity_id = matter_light(hass)
    controller = _controller(hass)

    await controller.async_move(entity_id, DIRECTION_UP, "medium", BACKEND_SIMULATED)
    await _drain(hass)

    assert session.sockets == {}
    assert entity_id in controller._jobs
    await controller.async_unload()


async def test_controller_unload_closes_the_matter_socket(hass, session):
    entity_id = matter_light(hass)
    controller = _controller(hass)

    await controller.async_move(entity_id, DIRECTION_UP, "medium")
    await _drain(hass)
    await controller.async_unload()
    await _drain(hass)

    assert session.sockets[URL].closed
