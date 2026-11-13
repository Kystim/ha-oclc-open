"""Base entity for the OCLC OPEN library integration."""

from __future__ import annotations

from homeassistant.const import CONF_URL
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import OpenCoordinator


class OpenEntity(CoordinatorEntity[OpenCoordinator]):
    """Entity bound to one library account."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: OpenCoordinator, key: str) -> None:
        """Initialize."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.unique_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="OCLC",
            model="OPEN",
            configuration_url=entry.data[CONF_URL],
            entry_type=DeviceEntryType.SERVICE,
        )
