"""Buttons for the OCLC OPEN library integration."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import OpenConfigEntry
from .entity import OpenEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpenConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up buttons."""
    async_add_entities([RenewAllButton(entry.runtime_data, "renew_all")])


class RenewAllButton(OpenEntity, ButtonEntity):
    """Renews every loan the library allows to renew."""

    async def async_press(self) -> None:
        """Renew all loans."""
        await self.coordinator.async_renew(None)
