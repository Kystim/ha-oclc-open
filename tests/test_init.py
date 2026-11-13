"""Setup, sensor and renewal tests."""

from __future__ import annotations

import copy
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.oclc_open import parser
from custom_components.oclc_open.api import InvalidAuth, RenewError, RenewOutcome
from custom_components.oclc_open.const import (
    CONF_AUTO_RENEW,
    CONF_AUTO_RENEW_DAYS,
    DOMAIN,
    SERVICE_RENEW,
)
from custom_components.oclc_open.coordinator import EVENT_RENEWAL

from .conftest import load_fixture

CLIENT = "custom_components.oclc_open.api.OpenClient"


def account() -> parser.AccountData:
    """Synthetic account with two loans."""
    data = parser.parse_account(parser.soup_of(load_fixture("account_loans_synthetic.html")))
    data.loans[0].renewable = True
    data.loans[1].renewable = False
    return data


def renewed(data: parser.AccountData) -> parser.AccountData:
    """Same account after renewing the first loan."""
    new = copy.deepcopy(data)
    new.loans[0].due_date = date(2026, 11, 6)
    return new


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    """Config entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Iserlohn (12345678)",
        unique_id="opac.iserlohn.de_12345678",
        data={
            CONF_URL: "https://opac.iserlohn.de",
            CONF_USERNAME: "12345678",
            CONF_PASSWORD: "pw",
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture(autouse=True)
def frozen_today(freezer: FrozenDateTimeFactory) -> None:
    """Fix 'today' to 2026-10-07."""
    freezer.move_to("2026-10-07 12:00:00+02:00")


async def test_setup_and_sensors(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Sensors reflect the parsed account."""
    with patch(f"{CLIENT}.async_fetch", AsyncMock(return_value=account())):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    loans = hass.states.get("sensor.iserlohn_12345678_loans")
    assert loans.state == "2"
    assert loans.attributes["renewable"] == 1
    assert loans.attributes["loans"][0]["copy_id"] == "M1001"
    assert hass.states.get("sensor.iserlohn_12345678_next_due_date").state == "2026-10-09"
    assert hass.states.get("sensor.iserlohn_12345678_days_until_due").state == "2"
    assert hass.states.get("sensor.iserlohn_12345678_reservations").state == "1"
    assert hass.states.get("sensor.iserlohn_12345678_ready_for_pickup").state == "1"
    assert hass.states.get("sensor.iserlohn_12345678_fees").state == "2.00"
    assert hass.states.get("sensor.iserlohn_12345678_card_valid_until").state == "2040-12-31"
    assert hass.states.get("button.iserlohn_12345678_renew_all") is not None

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_auth_failure_starts_reauth(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Rejected password leads to reauth."""
    with patch(f"{CLIENT}.async_fetch", AsyncMock(side_effect=InvalidAuth("no"))):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert flows and flows[0]["context"]["source"] == "reauth"


async def test_renew_service(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Service renews and reports per item."""
    before = account()
    fetch = AsyncMock(side_effect=[before, renewed(before)])
    renew = AsyncMock(return_value=RenewOutcome("ok", []))
    events = async_capture_events(hass, EVENT_RENEWAL)
    with patch(f"{CLIENT}.async_fetch", fetch), patch(f"{CLIENT}.async_renew", renew):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        response = await hass.services.async_call(
            DOMAIN, SERVICE_RENEW, {"copy_ids": ["M1001"]}, blocking=True, return_response=True
        )

    renew.assert_awaited_once_with(["M1001"], False)
    assert response == {
        "results": [
            {
                "copy_id": "M1001",
                "title": "Testlied Eins",
                "renewed": True,
                "old_due_date": "2026-10-09",
                "new_due_date": "2026-11-06",
            }
        ]
    }
    assert len(events) == 1 and events[0].data["automatic"] is False
    assert hass.states.get("sensor.iserlohn_12345678_next_due_date").state == "2026-10-30"


async def test_renew_all_button_refused(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Library refusal surfaces as an error."""
    data = account()
    fetch = AsyncMock(return_value=data)
    renew = AsyncMock(side_effect=RenewError("Medium ist vorbestellt"))
    with patch(f"{CLIENT}.async_fetch", fetch), patch(f"{CLIENT}.async_renew", renew):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        with pytest.raises(HomeAssistantError, match="vorbestellt"):
            await hass.services.async_call(
                "button",
                "press",
                {"entity_id": "button.iserlohn_12345678_renew_all"},
                blocking=True,
            )
    # Only the renewable loan is requested.
    renew.assert_awaited_once_with(["M1001"], False)


async def test_auto_renew(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Loans due within the window are renewed once; refusals notify."""
    hass.config_entries.async_update_entry(
        entry, options={CONF_AUTO_RENEW: True, CONF_AUTO_RENEW_DAYS: 3}
    )
    before = account()
    before.loans[1].renewable = None  # due 30.10., outside the window
    fetch = AsyncMock(side_effect=[before, renewed(before)])
    renew = AsyncMock(return_value=RenewOutcome("ok", []))
    events = async_capture_events(hass, EVENT_RENEWAL)
    with patch(f"{CLIENT}.async_fetch", fetch), patch(f"{CLIENT}.async_renew", renew):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    renew.assert_awaited_once_with(["M1001"], False)
    assert [e.data["renewed"] for e in events] == [True]
    assert events[0].data["automatic"] is True
    assert hass.states.get("sensor.iserlohn_12345678_next_due_date").state == "2026-10-30"


async def test_auto_renew_refused_notifies_once(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """A refused automatic renewal creates a notification and is not retried."""
    hass.config_entries.async_update_entry(
        entry, options={CONF_AUTO_RENEW: True, CONF_AUTO_RENEW_DAYS: 3}
    )
    data = account()
    fetch = AsyncMock(return_value=data)
    renew = AsyncMock(side_effect=RenewError("Medium ist vorbestellt"))
    with (
        patch(f"{CLIENT}.async_fetch", fetch),
        patch(f"{CLIENT}.async_renew", renew),
        patch(
            "custom_components.oclc_open.coordinator.persistent_notification.async_create"
        ) as notify,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await entry.runtime_data.async_refresh()

    assert renew.await_count == 1
    notify.assert_called_once()
    assert "Testlied Eins" in notify.call_args.args[1]
    assert "vorbestellt" in notify.call_args.args[1]
