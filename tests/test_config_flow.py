"""Config flow tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.oclc_open.api import CannotConnect, InvalidAuth
from custom_components.oclc_open.const import (
    CONF_ALLOW_RENEWAL_FEES,
    CONF_AUTO_RENEW,
    CONF_AUTO_RENEW_DAYS,
    CONF_UPDATE_HOURS,
    DOMAIN,
)

VALIDATE = "custom_components.oclc_open.config_flow.OpenClient.async_validate"
USER_INPUT = {
    CONF_URL: "opac.iserlohn.de/",
    CONF_USERNAME: " 12345678 ",
    CONF_PASSWORD: "01.02.1990",
}


@pytest.fixture(autouse=True)
def no_setup():
    """Do not set up the entry after the flow."""
    with patch("custom_components.oclc_open.async_setup_entry", return_value=True):
        yield


async def test_user_flow(hass: HomeAssistant) -> None:
    """Happy path normalizes URL and card number."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM

    with patch(VALIDATE, AsyncMock(return_value="Iserlohn")):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Iserlohn (12345678)"
    assert result["data"] == {
        CONF_URL: "https://opac.iserlohn.de",
        CONF_USERNAME: "12345678",
        CONF_PASSWORD: "01.02.1990",
    }
    assert result["result"].unique_id == "opac.iserlohn.de_12345678"


@pytest.mark.parametrize(
    ("error", "key"),
    [(InvalidAuth("no"), "invalid_auth"), (CannotConnect("down"), "cannot_connect")],
)
async def test_user_flow_errors(hass: HomeAssistant, error: Exception, key: str) -> None:
    """Errors are shown and the flow can recover."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    with patch(VALIDATE, AsyncMock(side_effect=error)):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": key}

    with patch(VALIDATE, AsyncMock(return_value=None)):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "opac.iserlohn.de (12345678)"


async def test_duplicate(hass: HomeAssistant) -> None:
    """Same card twice is rejected."""
    MockConfigEntry(domain=DOMAIN, unique_id="opac.iserlohn.de_12345678").add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(hass: HomeAssistant) -> None:
    """New password is stored."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="opac.iserlohn.de_12345678",
        data={CONF_URL: "https://opac.iserlohn.de", CONF_USERNAME: "12345678", CONF_PASSWORD: "old"},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    with patch(VALIDATE, AsyncMock(return_value="Iserlohn")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "new"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new"


async def test_options(hass: HomeAssistant) -> None:
    """Options are stored as ints."""
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_AUTO_RENEW: True,
            CONF_AUTO_RENEW_DAYS: 2.0,
            CONF_ALLOW_RENEWAL_FEES: False,
            CONF_UPDATE_HOURS: 6.0,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        CONF_AUTO_RENEW: True,
        CONF_AUTO_RENEW_DAYS: 2,
        CONF_ALLOW_RENEWAL_FEES: False,
        CONF_UPDATE_HOURS: 6,
    }
