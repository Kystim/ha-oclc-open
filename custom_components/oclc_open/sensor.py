"""Sensors for the OCLC OPEN library integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import OpenConfigEntry, OpenCoordinator
from .entity import OpenEntity
from .parser import AccountData, Loan

PARALLEL_UPDATES = 0


def _next_due(data: AccountData) -> date | None:
    dates = [loan.due_date for loan in data.loans if loan.due_date]
    return min(dates) if dates else None


def _due_on(data: AccountData, day: date | None) -> list[Loan]:
    return [loan for loan in data.loans if day and loan.due_date == day]


def _days_until_due(data: AccountData) -> int | None:
    if (day := _next_due(data)) is None:
        return None
    return (day - dt_util.now().date()).days


def _loans_attrs(data: AccountData) -> dict[str, Any]:
    today = dt_util.now().date()
    return {
        "loans": [loan.as_dict() for loan in sorted(data.loans, key=_sort_key)],
        "overdue": sum(1 for loan in data.loans if loan.due_date and loan.due_date < today),
        "renewable": sum(1 for loan in data.loans if loan.renewable),
        "warning": data.warning,
    }


def _sort_key(loan: Loan) -> tuple[date, str]:
    return (loan.due_date or date.max, loan.title)


def _next_due_attrs(data: AccountData) -> dict[str, Any]:
    return {"titles": [loan.title for loan in _due_on(data, _next_due(data))]}


def _reservations_attrs(data: AccountData) -> dict[str, Any]:
    return {"reservations": [r.as_dict() for r in data.reservations if not r.ready]}


def _ready_attrs(data: AccountData) -> dict[str, Any]:
    return {"reservations": [r.as_dict() for r in data.reservations if r.ready]}


def _fees_attrs(data: AccountData) -> dict[str, Any]:
    def num(value: Decimal | None) -> float | None:
        return float(value) if value is not None else None

    return {
        "fees_total": num(data.fees_open),
        "paid_total": num(data.fees_paid),
        "balance": num(data.balance),
        "fees": [fee.as_dict() for fee in data.fees],
    }


@dataclass(frozen=True, kw_only=True)
class OpenSensorDescription(SensorEntityDescription):
    """Describes an OPEN sensor."""

    value_fn: Callable[[AccountData], Any]
    attrs_fn: Callable[[AccountData], dict[str, Any]] | None = None


SENSORS: tuple[OpenSensorDescription, ...] = (
    OpenSensorDescription(
        key="loans",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: len(data.loans),
        attrs_fn=_loans_attrs,
    ),
    OpenSensorDescription(
        key="next_due_date",
        device_class=SensorDeviceClass.DATE,
        value_fn=_next_due,
        attrs_fn=_next_due_attrs,
    ),
    OpenSensorDescription(
        key="days_until_due",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.DAYS,
        value_fn=_days_until_due,
    ),
    OpenSensorDescription(
        key="reservations",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: sum(1 for r in data.reservations if not r.ready),
        attrs_fn=_reservations_attrs,
    ),
    OpenSensorDescription(
        key="ready_for_pickup",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: sum(1 for r in data.reservations if r.ready),
        attrs_fn=_ready_attrs,
    ),
    OpenSensorDescription(
        key="fees",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="EUR",
        suggested_display_precision=2,
        value_fn=lambda data: data.amount_owed,
        attrs_fn=_fees_attrs,
    ),
    OpenSensorDescription(
        key="card_valid_until",
        device_class=SensorDeviceClass.DATE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.valid_until,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpenConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors."""
    coordinator = entry.runtime_data
    async_add_entities(OpenSensor(coordinator, description) for description in SENSORS)


class OpenSensor(OpenEntity, SensorEntity):
    """A library account sensor."""

    entity_description: OpenSensorDescription

    def __init__(
        self, coordinator: OpenCoordinator, description: OpenSensorDescription
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """Return the state."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return item details."""
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)
