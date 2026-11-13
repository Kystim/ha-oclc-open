"""Buttons for the OCLC OPEN library integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import OpenConfigEntry, next_due
from .entity import OpenEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpenConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up buttons."""
    coordinator = entry.runtime_data
    async_add_entities(
        [RenewAllButton(coordinator, "renew_all"), RenewNextButton(coordinator, "renew_next")]
    )


class RenewAllButton(OpenEntity, ButtonEntity):
    """Renews every loan the library allows to renew."""

    async def async_press(self) -> None:
        """Renew all loans."""
        await self.coordinator.async_renew(None)


class RenewNextButton(OpenEntity, ButtonEntity):
    """Renews only the loans with the earliest due date."""

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The due date and titles a press would renew."""
        day, loans = next_due(self.coordinator.data)
        return {
            "due_date": day.isoformat() if day else None,
            "titles": [loan.title for loan in loans],
        }

    async def async_press(self) -> None:
        """Renew the loans due next."""
        await self.coordinator.async_renew(None, next_due_only=True)
