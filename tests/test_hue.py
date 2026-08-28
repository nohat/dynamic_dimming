"""Tests for the native Hue backend."""

from __future__ import annotations

import pytest

from homeassistant.const import CONF_API_VERSION
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dynamic_dimming.backends.hue import (
    HueBackend,
    to_brightness_pct,
)
from custom_components.dynamic_dimming.const import (
    DIRECTION_DOWN,
    DIRECTION_UP,
    HUE_DOMAIN,
)

from .conftest import set_light_state

LIGHT_ID = "2d4d7ecb-8bd9-4a4c-9622-24e276e0a681"
GROUP_ID = "8ab41cab-9e11-4b8f-96d3-a44530a09a49"


class FakeController:
    """Stands in for an aiohue resource controller: a set of known ids."""

    def __init__(self, ids=()):
        self._ids = set(ids)

    def get(self, resource_id, default=None):
        return {"id": resource_id} if resource_id in self._ids else default


class FakeGroups:
    def __init__(self, grouped_ids=()):
        self.grouped_light = FakeController(grouped_ids)


class FakeApi:
    """Stands in for aiohue's HueBridgeV2, recording every request."""

    def __init__(self, light_ids=(LIGHT_ID,), grouped_ids=(GROUP_ID,)):
        self.lights = FakeController(light_ids)
        self.groups = FakeGroups(grouped_ids)
        self.requests: list[tuple[str, str, dict]] = []
        self.fail = False

    async def request(self, method, path, json=None):
        if self.fail:
            raise RuntimeError("bridge is down")
        self.requests.append((method, path, json))
        return []


class FakeBridge:
    def __init__(self, api):
        self.api = api


def _hue_light(
    hass,
    object_id="lamp",
    *,
    api=None,
    resource_id=LIGHT_ID,
    api_version=2,
    with_runtime=True,
    brightness=128,
):
    """Register light.<object_id> as a Hue light on a Hue config entry."""
    entry = MockConfigEntry(domain=HUE_DOMAIN, data={CONF_API_VERSION: api_version})
    entry.add_to_hass(hass)
    if with_runtime:
        entry.runtime_data = FakeBridge(api if api is not None else FakeApi())
    er.async_get(hass).async_get_or_create(
        "light",
        HUE_DOMAIN,
        resource_id,
        suggested_object_id=object_id,
        config_entry=entry,
    )
    entity_id = f"light.{object_id}"
    set_light_state(hass, entity_id, brightness=brightness)
    return entity_id


def _only(api):
    """The single recorded request, asserting there was exactly one."""
    assert len(api.requests) == 1
    return api.requests[0]


# -- brightness mapping ------------------------------------------------------------


@pytest.mark.parametrize(
    ("brightness", "pct"), [(0, 0.0), (128, 50.2), (255, 100.0), (1000, 100.0)]
)
def test_to_brightness_pct_clamps_into_percent(brightness, pct):
    assert to_brightness_pct(brightness) == pct


# -- claiming ----------------------------------------------------------------------


async def test_claims_v2_hue_light(hass):
    assert HueBackend(hass).claims(_hue_light(hass))


async def test_claims_a_grouped_light(hass):
    entity_id = _hue_light(hass, "zone", resource_id=GROUP_ID)
    assert HueBackend(hass).claims(entity_id)


async def test_does_not_claim_a_v1_bridge(hass):
    entity_id = _hue_light(hass, api_version=1)
    assert not HueBackend(hass).claims(entity_id)


async def test_does_not_claim_without_a_runtime_client(hass):
    """A loaded-but-not-connected entry has no api to speak through."""
    entity_id = _hue_light(hass, with_runtime=False)
    assert not HueBackend(hass).claims(entity_id)


async def test_does_not_claim_an_id_the_bridge_does_not_know(hass):
    entity_id = _hue_light(hass, api=FakeApi(light_ids=(), grouped_ids=()))
    assert not HueBackend(hass).claims(entity_id)


async def test_does_not_claim_other_platform(hass):
    entry = MockConfigEntry(domain="wiz")
    entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "light", "wiz", "abc", suggested_object_id="other", config_entry=entry
    )
    set_light_state(hass, "light.other")
    assert not HueBackend(hass).claims("light.other")


async def test_a_controller_that_blows_up_reads_as_not_ours(hass):
    class ExplodingController:
        def get(self, resource_id, default=None):
            raise RuntimeError("surprise shape change")

    api = FakeApi()
    api.lights = ExplodingController()
    api.groups.grouped_light = ExplodingController()
    entity_id = _hue_light(hass, api=api)
    assert not HueBackend(hass).claims(entity_id)


# -- move / stop -------------------------------------------------------------------


async def test_move_up_puts_a_delta_to_the_top_rail(hass):
    api = FakeApi()
    entity_id = _hue_light(hass, api=api, brightness=128)  # 50.2%
    assert await HueBackend(hass).async_move(entity_id, DIRECTION_UP, "medium") is None
    method, path, payload = _only(api)
    assert method == "put"
    assert path == f"clip/v2/resource/light/{LIGHT_ID}"
    # 49.8% of travel at medium (90/255 units -> 35.29 %/s) is 1411 ms.
    assert payload == {
        "dimming_delta": {"action": "up", "brightness_delta": 49.8},
        "dynamics": {"duration": 1411},
    }


async def test_move_down_measures_the_distance_to_the_floor(hass):
    api = FakeApi()
    entity_id = _hue_light(hass, api=api, brightness=128)
    await HueBackend(hass).async_move(entity_id, DIRECTION_DOWN, "medium")
    payload = _only(api)[2]
    assert payload["dimming_delta"] == {"action": "down", "brightness_delta": 50.2}
    assert payload["dynamics"] == {"duration": 1422}


async def test_move_at_the_rail_still_asks_for_a_nudge(hass):
    """HA's cache is not the authority on the level; the bridge is."""
    api = FakeApi()
    entity_id = _hue_light(hass, api=api, brightness=255)
    await HueBackend(hass).async_move(entity_id, DIRECTION_UP, "medium")
    assert _only(api)[2]["dimming_delta"]["brightness_delta"] == 1.0


async def test_move_addresses_a_grouped_light_at_its_own_path(hass):
    api = FakeApi()
    entity_id = _hue_light(hass, "zone", api=api, resource_id=GROUP_ID)
    await HueBackend(hass).async_move(entity_id, DIRECTION_UP, None)
    assert _only(api)[1] == f"clip/v2/resource/grouped_light/{GROUP_ID}"


async def test_move_on_unclaimed_entity_sends_nothing(hass):
    api = FakeApi(light_ids=(), grouped_ids=())
    entity_id = _hue_light(hass, api=api)
    await HueBackend(hass).async_move(entity_id, DIRECTION_UP, "medium")
    assert not api.requests


async def test_stop_puts_the_stop_action(hass):
    api = FakeApi()
    entity_id = _hue_light(hass, api=api)
    await HueBackend(hass).async_stop(entity_id)
    assert _only(api)[2] == {"dimming_delta": {"action": "stop"}}


async def test_a_failed_request_is_swallowed_and_warned_once(hass, caplog):
    api = FakeApi()
    api.fail = True
    entity_id = _hue_light(hass, api=api)
    backend = HueBackend(hass)
    await backend.async_move(entity_id, DIRECTION_UP, None)
    assert "Hue command" in caplog.text
    caplog.clear()
    with caplog.at_level("WARNING"):
        await backend.async_stop(entity_id)
    assert "Hue command" not in caplog.text


# -- step --------------------------------------------------------------------------


async def test_step_is_a_native_delta_without_dynamics(hass):
    api = FakeApi()
    entity_id = _hue_light(hass, api=api)
    await HueBackend(hass).async_step(entity_id, DIRECTION_DOWN, 5.0)
    payload = _only(api)[2]
    assert payload == {"dimming_delta": {"action": "down", "brightness_delta": 5.0}}


async def test_step_clamps_a_wild_percentage(hass):
    api = FakeApi()
    entity_id = _hue_light(hass, api=api)
    await HueBackend(hass).async_step(entity_id, DIRECTION_UP, 500.0)
    assert _only(api)[2]["dimming_delta"]["brightness_delta"] == 100.0


# -- fade --------------------------------------------------------------------------


def test_backend_claims_fade(hass):
    assert HueBackend(hass).supports_fade


async def test_fade_is_one_dimming_transition(hass):
    api = FakeApi()
    entity_id = _hue_light(hass, api=api)
    assert await HueBackend(hass).async_fade(entity_id, 128, 5.0) is None
    payload = _only(api)[2]
    assert payload == {
        "on": {"on": True},
        "dimming": {"brightness": 50.2},
        "dynamics": {"duration": 5000},
    }


async def test_fade_asserts_color_before_the_ramp(hass):
    api = FakeApi()
    entity_id = _hue_light(hass, api=api)
    await HueBackend(hass).async_fade(entity_id, 255, 2.0, color_temp_kelvin=2700)
    assert len(api.requests) == 2
    color, ramp = api.requests[0][2], api.requests[1][2]
    assert color == {
        "color_temperature": {"mirek": 370},
        "dynamics": {"duration": 0},
    }
    assert ramp["dimming"] == {"brightness": 100.0}


async def test_fade_clamps_the_mirek_range(hass):
    api = FakeApi()
    entity_id = _hue_light(hass, api=api)
    await HueBackend(hass).async_fade(entity_id, 255, 1.0, color_temp_kelvin=10_000)
    assert api.requests[0][2]["color_temperature"]["mirek"] == 153


async def test_fade_still_ramps_when_the_color_write_fails(hass):
    """A light with no white channel loses its color, not its fade."""

    class ColorRejectingApi(FakeApi):
        async def request(self, method, path, json=None):
            if "color_temperature" in (json or {}):
                raise RuntimeError("resource has no color_temperature")
            return await super().request(method, path, json=json)

    api = ColorRejectingApi()
    entity_id = _hue_light(hass, api=api)
    await HueBackend(hass).async_fade(entity_id, 128, 1.0, color_temp_kelvin=2700)
    assert len(api.requests) == 1
    assert "dimming" in api.requests[0][2]
