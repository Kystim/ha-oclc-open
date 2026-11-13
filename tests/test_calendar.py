"""Calendar tests."""

from __future__ import annotations

import copy
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.oclc_open import parser
from custom_components.oclc_open.calendar import build_events
from custom_components.oclc_open.const import DOMAIN

from .conftest import load_fixture

CLIENT = "custom_components.oclc_open.api.OpenClient"
CALENDAR = "calendar.iserlohn_library"


def account() -> parser.AccountData:
    """Synthetic account: loans due 09.10. and 30.10., pickup until 13.10."""
    data = parser.parse_account(parser.soup_of(load_fixture("account_loans_synthetic.html")))
    # A second item on the first due date, to check grouping.
    extra = copy.deepcopy(data.loans[0])
    extra.title, extra.author, extra.copy_id = "Testbuch Fünf", None, "M1003"
    data.loans.append(extra)
    return data


@pytest.fixture(autouse=True)
def frozen_today(freezer: FrozenDateTimeFactory) -> None:
    """Fix 'today' to 2026-10-07."""
    freezer.move_to("2026-10-07 12:00:00+02:00")


@pytest.fixture
async def setup(hass: HomeAssistant) -> MockConfigEntry:
    """Loaded config entry."""
    await hass.config.async_set_time_zone("Europe/Berlin")
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
    with patch(f"{CLIENT}.async_fetch", AsyncMock(return_value=account())):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def get_events(hass: HomeAssistant, start: str, end: str) -> list[dict]:
    """Events via the calendar.get_events action."""
    response = await hass.services.async_call(
        "calendar",
        "get_events",
        {"entity_id": CALENDAR, "start_date_time": start, "end_date_time": end},
        blocking=True,
        return_response=True,
    )
    return response[CALENDAR]["events"]


def test_build_events_grouping_and_language() -> None:
    """Loans are grouped per due date; texts follow the language."""
    events = build_events(account(), "x", "de")
    assert [(e.start, e.summary) for e in events] == [
        (date(2026, 10, 9), "Rückgabe: 2 Medien"),
        (date(2026, 10, 13), "Abholen: Testbuch Abholung"),
        (date(2026, 10, 30), "Rückgabe: Testbuch Vier"),
        (date(2040, 12, 31), "Bibliotheksausweis läuft ab"),
    ]
    first = events[0]
    assert first.end == date(2026, 10, 10)
    assert first.description == "- Testlied Eins (Muster, Max)\n- Testbuch Fünf"
    assert first.location == "Stadtbücherei"
    assert first.uid == "x_return_2026-10-09"
    assert events[1].location == "Stadtbücherei"

    english = build_events(account(), "x", "fr")
    assert english[0].summary == "Return 2 items"
    assert english[3].summary == "Library card expires"


async def test_calendar_entity(hass: HomeAssistant, setup: MockConfigEntry) -> None:
    """State shows the next event; get_events filters by range."""
    state = hass.states.get(CALENDAR)
    assert state.state == "off"
    assert state.attributes["message"] == "Return 2 items"
    assert state.attributes["all_day"] is True
    assert state.attributes["start_time"] == "2026-10-09 00:00:00"

    events = await get_events(hass, "2026-10-01T00:00:00+02:00", "2026-10-31T00:00:00+02:00")
    assert [(e["start"], e["summary"]) for e in events] == [
        ("2026-10-09", "Return 2 items"),
        ("2026-10-13", "Pick up: Testbuch Abholung"),
        ("2026-10-30", "Return: Testbuch Vier"),
    ]
    assert await get_events(hass, "2026-10-10T00:00:00+02:00", "2026-10-12T00:00:00+02:00") == []


async def test_calendar_on_due_date(
    hass: HomeAssistant, setup: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    """On the due date the calendar is on."""
    freezer.move_to("2026-10-09 08:00:00+02:00")
    coordinator = setup.runtime_data
    with patch(f"{CLIENT}.async_fetch", AsyncMock(return_value=account())):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    state = hass.states.get(CALENDAR)
    assert state.state == "on"
    assert state.attributes["message"] == "Return 2 items"


async def test_calendar_follows_renewal(hass: HomeAssistant, setup: MockConfigEntry) -> None:
    """A changed due date moves the event."""
    data = account()
    for loan in data.loans:
        loan.due_date = date(2026, 11, 6)
    with patch(f"{CLIENT}.async_fetch", AsyncMock(return_value=data)):
        await setup.runtime_data.async_refresh()
        await hass.async_block_till_done()
    events = await get_events(hass, "2026-10-01T00:00:00+02:00", "2026-12-01T00:00:00+02:00")
    assert [(e["start"], e["summary"]) for e in events] == [
        ("2026-10-13", "Pick up: Testbuch Abholung"),
        ("2026-11-06", "Return 3 items"),
    ]
