"""Tests for the native Shelly backend."""

from __future__ import annotations

import pytest

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dynamic_dimming.backends.shelly import (
    ShellyBackend,
    parse_component,
    to_brightness_pct,
    to_fade_rate,
)
from custom_components.dynamic_dimming.const import (
    DIRECTION_DOWN,
    DIRECTION_UP,
    SHELLY_CONF_GEN,
    SHELLY_CONF_SLEEP_PERIOD,
    SHELLY_DOMAIN,
)

from .conftest import set_light_state

MAC = "AABBCCDDEEFF"
HOST = "192.168.1.30"


def _shelly_light(
    hass,
    object_id="dimmer",
    *,
    gen=2,
    host=HOST,
    port=None,
    unique_id=None,
    sleep_period=0,
    username=None,
    password=None,
    brightness=100,
):
    """Register light.<object_id> as a Shelly light on a Shelly config entry."""
    data = {CONF_HOST: host, SHELLY_CONF_GEN: gen}
    if port is not None:
        data[CONF_PORT] = port
    if sleep_period:
        data[SHELLY_CONF_SLEEP_PERIOD] = sleep_period
    if username is not None:
        data[CONF_USERNAME] = username
    if password is not None:
        data[CONF_PASSWORD] = password
    entry = MockConfigEntry(domain=SHELLY_DOMAIN, data=data)
    entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "light",
        SHELLY_DOMAIN,
        unique_id if unique_id is not None else f"{MAC}-light:0",
        suggested_object_id=object_id,
        config_entry=entry,
    )
    entity_id = f"light.{object_id}"
    set_light_state(hass, entity_id, brightness=brightness)
    return entity_id


def _rpc_calls(aioclient_mock):
    """The (url, body) of every RPC POST the backend made."""
    return [(str(call[1]), call[2]) for call in aioclient_mock.mock_calls]


@pytest.fixture
def rpc_ok(aioclient_mock):
    """Answer every RPC POST with an empty success."""
    aioclient_mock.post(
        f"http://{HOST}:80/rpc/Light.DimUp", json={"was_on": True}
    )
    aioclient_mock.post(
        f"http://{HOST}:80/rpc/Light.DimDown", json={"was_on": True}
    )
    aioclient_mock.post(f"http://{HOST}:80/rpc/Light.DimStop", json={})
    aioclient_mock.post(f"http://{HOST}/rpc/Light.Set", json={"was_on": True})
    aioclient_mock.post(f"http://{HOST}:80/rpc/CCT.Set", json={"was_on": True})
    aioclient_mock.post(f"http://{HOST}:80/rpc/RGBCCT.Set", json={"was_on": True})
    aioclient_mock.post(f"http://{HOST}:80/rpc/CCT.DimUp", json={"was_on": True})
    return aioclient_mock


# -- unique_id parsing -------------------------------------------------------------


def test_parse_component_reads_a_light_key():
    assert parse_component(f"{MAC}-light:0") == ("Light", 0)


@pytest.mark.parametrize(
    ("key", "component"),
    [("cct:1", "CCT"), ("rgb:0", "RGB"), ("rgbw:0", "RGBW"), ("rgbcct:2", "RGBCCT")],
)
def test_parse_component_covers_every_dimmable_component(key, component):
    assert parse_component(f"{MAC}-{key}") == (component, int(key.split(":")[1]))


@pytest.mark.parametrize(
    "unique_id",
    [
        "",
        MAC,  # no key at all
        f"{MAC}-light_0",  # Gen1 shape
        f"{MAC}-switch:0",  # a relay posing as a light has nothing to dim
        f"{MAC}-light:main",  # non-numeric id
        f"{MAC}-temperature",  # a sensor suffix
    ],
)
def test_parse_component_rejects_junk(unique_id):
    assert parse_component(unique_id) is None


# -- rate / brightness mapping -----------------------------------------------------


@pytest.mark.parametrize(
    ("rate", "fade_rate"),
    [
        ("slow", 1),
        ("medium", 3),
        ("fast", 5),
        (None, 3),  # default profile is medium
        (65.0, 2),  # halfway between slow and medium interpolates
        (1.0, 1),
        (9000.0, 5),
    ],
)
def test_to_fade_rate_maps_profiles_onto_the_speed_classes(rate, fade_rate):
    assert to_fade_rate(rate) == fade_rate


@pytest.mark.parametrize(
    ("brightness", "pct"), [(0, 0), (128, 50), (255, 100), (1000, 100)]
)
def test_to_brightness_pct_clamps_into_percent(brightness, pct):
    assert to_brightness_pct(brightness) == pct


# -- claiming ----------------------------------------------------------------------


async def test_claims_gen2_shelly_light(hass):
    assert ShellyBackend(hass).claims(_shelly_light(hass))


async def test_claims_cct_light(hass):
    entity_id = _shelly_light(hass, "cct", unique_id=f"{MAC}-cct:0")
    assert ShellyBackend(hass).claims(entity_id)


async def test_does_not_claim_gen1(hass):
    entity_id = _shelly_light(hass, gen=1, unique_id=f"{MAC}-light_0")
    assert not ShellyBackend(hass).claims(entity_id)


async def test_does_not_claim_a_sleepy_device(hass):
    entity_id = _shelly_light(hass, sleep_period=3600)
    assert not ShellyBackend(hass).claims(entity_id)


async def test_does_not_claim_a_relay_as_light(hass):
    entity_id = _shelly_light(hass, "relay", unique_id=f"{MAC}-switch:0")
    assert not ShellyBackend(hass).claims(entity_id)


async def test_does_not_claim_without_a_host(hass):
    entity_id = _shelly_light(hass, host="")
    assert not ShellyBackend(hass).claims(entity_id)


async def test_does_not_claim_other_platform(hass):
    entry = MockConfigEntry(domain="hue")
    entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "light", "hue", "abc", suggested_object_id="other", config_entry=entry
    )
    set_light_state(hass, "light.other")
    assert not ShellyBackend(hass).claims("light.other")


# -- move / stop -------------------------------------------------------------------


async def test_move_up_posts_dim_up_with_the_mapped_rate(hass, rpc_ok):
    entity_id = _shelly_light(hass)
    backend = ShellyBackend(hass)
    assert await backend.async_move(entity_id, DIRECTION_UP, "medium") is None
    assert _rpc_calls(rpc_ok) == [
        (f"http://{HOST}/rpc/Light.DimUp", {"id": 0, "fade_rate": 3})
    ]


async def test_move_down_posts_dim_down(hass, rpc_ok):
    entity_id = _shelly_light(hass)
    await ShellyBackend(hass).async_move(entity_id, DIRECTION_DOWN, "slow")
    assert _rpc_calls(rpc_ok) == [
        (f"http://{HOST}/rpc/Light.DimDown", {"id": 0, "fade_rate": 1})
    ]


async def test_move_addresses_the_component_and_id_from_the_unique_id(
    hass, aioclient_mock
):
    aioclient_mock.post(f"http://{HOST}:80/rpc/CCT.DimUp", json={})
    entity_id = _shelly_light(hass, "cct2", unique_id=f"{MAC}-cct:1")
    await ShellyBackend(hass).async_move(entity_id, DIRECTION_UP, None)
    assert _rpc_calls(aioclient_mock) == [
        (f"http://{HOST}/rpc/CCT.DimUp", {"id": 1, "fade_rate": 3})
    ]


async def test_move_honors_a_custom_port(hass, aioclient_mock):
    aioclient_mock.post(f"http://{HOST}:8080/rpc/Light.DimUp", json={})
    entity_id = _shelly_light(hass, port=8080)
    await ShellyBackend(hass).async_move(entity_id, DIRECTION_UP, None)
    assert len(aioclient_mock.mock_calls) == 1


async def test_move_on_unclaimed_entity_sends_nothing(hass, rpc_ok):
    entity_id = _shelly_light(hass, gen=1, unique_id=f"{MAC}-light_0")
    await ShellyBackend(hass).async_move(entity_id, DIRECTION_UP, "medium")
    assert not rpc_ok.mock_calls


async def test_stop_posts_dim_stop(hass, rpc_ok):
    entity_id = _shelly_light(hass)
    await ShellyBackend(hass).async_stop(entity_id)
    assert _rpc_calls(rpc_ok) == [(f"http://{HOST}/rpc/Light.DimStop", {"id": 0})]


async def test_http_failure_is_swallowed_and_logged(hass, aioclient_mock, caplog):
    """A device that has dropped off the network costs a warning, not a crash."""
    aioclient_mock.post(
        f"http://{HOST}:80/rpc/Light.DimUp", exc=OSError("no route to host")
    )
    entity_id = _shelly_light(hass)
    backend = ShellyBackend(hass)
    await backend.async_move(entity_id, DIRECTION_UP, None)
    assert "Shelly device" in caplog.text
    # The second failure is demoted to debug so an outage does not spam.
    caplog.clear()
    with caplog.at_level("WARNING"):
        await backend.async_move(entity_id, DIRECTION_UP, None)
    assert "Shelly device" not in caplog.text


async def test_http_error_status_is_swallowed_and_logged(hass, aioclient_mock, caplog):
    aioclient_mock.post(
        f"http://{HOST}:80/rpc/Light.DimUp",
        status=401,
        text="Unauthorized",
    )
    entity_id = _shelly_light(hass)
    await ShellyBackend(hass).async_move(entity_id, DIRECTION_UP, None)
    assert "401" in caplog.text


async def test_authenticated_device_still_sends(hass, rpc_ok):
    """A stored password rides along as digest middleware, not a refusal."""
    entity_id = _shelly_light(hass, username="admin", password="secret")
    await ShellyBackend(hass).async_move(entity_id, DIRECTION_UP, None)
    assert len(rpc_ok.mock_calls) == 1


# -- step --------------------------------------------------------------------------


def test_backend_declines_native_step(hass):
    """No relative step in the RPC; the controller simulates it instead."""
    assert not ShellyBackend(hass).supports_step


async def test_step_is_a_noop_if_called_directly(hass, rpc_ok):
    entity_id = _shelly_light(hass)
    await ShellyBackend(hass).async_step(entity_id, DIRECTION_UP, 5.0)
    assert not rpc_ok.mock_calls


# -- fade --------------------------------------------------------------------------


def test_backend_claims_fade(hass):
    assert ShellyBackend(hass).supports_fade


async def test_fade_posts_one_set_with_a_transition(hass, rpc_ok):
    entity_id = _shelly_light(hass)
    assert await ShellyBackend(hass).async_fade(entity_id, 128, 5.0) is None
    assert _rpc_calls(rpc_ok) == [
        (
            f"http://{HOST}/rpc/Light.Set",
            {"id": 0, "on": True, "brightness": 50, "transition_duration": 5.0},
        )
    ]


async def test_fade_clamps_a_snap_to_the_firmware_minimum(hass, rpc_ok):
    entity_id = _shelly_light(hass)
    await ShellyBackend(hass).async_fade(entity_id, 255, 0.1)
    assert _rpc_calls(rpc_ok)[0][1]["transition_duration"] == 0.5


async def test_fade_ignores_color_on_a_white_only_component(hass, rpc_ok):
    """The plain Light component has no ct channel to point a color at."""
    entity_id = _shelly_light(hass)
    await ShellyBackend(hass).async_fade(entity_id, 128, 1.0, color_temp_kelvin=2700)
    assert "ct" not in _rpc_calls(rpc_ok)[0][1]


async def test_fade_carries_ct_on_a_cct_component(hass, rpc_ok):
    entity_id = _shelly_light(hass, "cct", unique_id=f"{MAC}-cct:0")
    await ShellyBackend(hass).async_fade(entity_id, 128, 1.0, color_temp_kelvin=2700)
    url, body = _rpc_calls(rpc_ok)[0]
    assert url == f"http://{HOST}/rpc/CCT.Set"
    assert body["ct"] == 2700
    assert "mode" not in body


async def test_fade_names_the_mode_on_an_rgbcct_component(hass, rpc_ok):
    entity_id = _shelly_light(hass, "strip", unique_id=f"{MAC}-rgbcct:0")
    await ShellyBackend(hass).async_fade(entity_id, 128, 1.0, color_temp_kelvin=2700)
    body = _rpc_calls(rpc_ok)[0][1]
    assert body["ct"] == 2700
    assert body["mode"] == "cct"
