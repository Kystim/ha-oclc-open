"""Calendar for the OCLC OPEN library integration."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import OpenConfigEntry, OpenCoordinator
from .entity import OpenEntity
from .parser import AccountData, Loan

PARALLEL_UPDATES = 0

# Event texts follow the Home Assistant language; Home Assistant has no
# translation mechanism for calendar event content.
TEXTS: dict[str, dict[str, str]] = {
    "en": {
        "return_one": "Return: {title}",
        "return_many": "Return {count} items",
        "pickup": "Pick up: {title}",
        "card": "Library card expires",
    },
    "de": {
        "return_one": "Rückgabe: {title}",
        "return_many": "Rückgabe: {count} Medien",
        "pickup": "Abholen: {title}",
        "card": "Bibliotheksausweis läuft ab",
    },
}


def _all_day(day: date, **kwargs: str | None) -> CalendarEvent:
    return CalendarEvent(start=day, end=day + timedelta(days=1), **kwargs)


def _loan_line(loan: Loan) -> str:
    return f"- {loan.title} ({loan.author})" if loan.author else f"- {loan.title}"


def build_events(data: AccountData, uid_prefix: str, language: str) -> list[CalendarEvent]:
    """All-day events for due dates, pickup deadlines and card expiry, sorted."""
    texts = TEXTS.get(language.split("-")[0].lower(), TEXTS["en"])
    events: list[CalendarEvent] = []

    by_due: dict[date, list[Loan]] = defaultdict(list)
    for loan in data.loans:
        if loan.due_date:
            by_due[loan.due_date].append(loan)
    for day, loans in by_due.items():
        summary = (
            texts["return_one"].format(title=loans[0].title)
            if len(loans) == 1
            else texts["return_many"].format(count=len(loans))
        )
        branches = {loan.branch for loan in loans}
        events.append(
            _all_day(
                day,
                summary=summary,
                description="\n".join(_loan_line(loan) for loan in loans),
                location=branches.pop() if len(branches) == 1 else None,
                uid=f"{uid_prefix}_return_{day.isoformat()}",
            )
        )

    for res in data.reservations:
        if res.ready and res.pickup_deadline:
            events.append(
                _all_day(
                    res.pickup_deadline,
                    summary=texts["pickup"].format(title=res.title),
                    description=res.author,
                    location=res.branch,
                    uid=f"{uid_prefix}_pickup_{res.media_id or res.title}",
                )
            )

    if data.valid_until:
        events.append(
            _all_day(data.valid_until, summary=texts["card"], uid=f"{uid_prefix}_card")
        )

    events.sort(key=lambda event: (event.start, event.summary))
    return events


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpenConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the calendar."""
    async_add_entities([OpenCalendar(entry.runtime_data)])


class OpenCalendar(OpenEntity, CalendarEntity):
    """Due dates, pickup deadlines and card expiry of one library account."""

    # The calendar is named after the device ("Iserlohn library").
    _attr_name = None

    def __init__(self, coordinator: OpenCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator, "calendar")

    def _events(self) -> list[CalendarEvent]:
        if self.coordinator.data is None:
            return []
        return build_events(
            self.coordinator.data, self.unique_id or "", self.hass.config.language
        )

    @property
    def event(self) -> CalendarEvent | None:
        """The current or next event."""
        now = dt_util.now()
        return next((ev for ev in self._events() if ev.end_datetime_local > now), None)

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Events overlapping the requested range."""
        return [
            ev
            for ev in self._events()
            if ev.start_datetime_local < end_date and ev.end_datetime_local > start_date
        ]
