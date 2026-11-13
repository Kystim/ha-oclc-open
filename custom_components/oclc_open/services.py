"""Services for the OCLC OPEN library integration."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_COPY_IDS,
    ATTR_NEXT_DUE_ONLY,
    DOMAIN,
    SERVICE_RENEW,
)
from .coordinator import OpenConfigEntry

RENEW_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Exclusive(ATTR_COPY_IDS, "selection"): vol.All(cv.ensure_list, [cv.string]),
        vol.Exclusive(ATTR_NEXT_DUE_ONLY, "selection"): cv.boolean,
    }
)


def _entry(hass: HomeAssistant, entry_id: str | None) -> OpenConfigEntry:
    loaded = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]
    if entry_id is not None:
        for entry in loaded:
            if entry.entry_id == entry_id:
                return entry
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="entry_not_loaded"
        )
    if len(loaded) != 1:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="entry_required"
        )
    return loaded[0]


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's services."""

    async def renew(call: ServiceCall) -> ServiceResponse:
        entry = _entry(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        results = await entry.runtime_data.async_renew(
            call.data.get(ATTR_COPY_IDS), call.data.get(ATTR_NEXT_DUE_ONLY, False)
        )
        return {"results": [result.as_dict() for result in results]}

    hass.services.async_register(
        DOMAIN,
        SERVICE_RENEW,
        renew,
        schema=RENEW_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
