"""Data coordinator for the OCLC OPEN library integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import CannotConnect, InvalidAuth, OpenClient, OpenError, RenewalFeeError
from .const import (
    CONF_ALLOW_RENEWAL_FEES,
    CONF_AUTO_RENEW,
    CONF_AUTO_RENEW_DAYS,
    CONF_UPDATE_HOURS,
    DEFAULT_ALLOW_RENEWAL_FEES,
    DEFAULT_AUTO_RENEW,
    DEFAULT_AUTO_RENEW_DAYS,
    DEFAULT_UPDATE_HOURS,
    DOMAIN,
    MIN_UPDATE_INTERVAL,
)
from .parser import AccountData, Loan

_LOGGER = logging.getLogger(__name__)

EVENT_RENEWAL = f"{DOMAIN}_renewal"

type OpenConfigEntry = ConfigEntry[OpenCoordinator]


def next_due(data: AccountData | None) -> tuple[date | None, list[Loan]]:
    """Earliest due date and all loans due that day."""
    if data is None:
        return None, []
    dates = [loan.due_date for loan in data.loans if loan.due_date]
    if not dates:
        return None, []
    day = min(dates)
    return day, [loan for loan in data.loans if loan.due_date == day]


@dataclass(slots=True)
class RenewalResult:
    """Outcome for one item."""

    copy_id: str
    title: str
    renewed: bool
    old_due_date: date | None
    new_due_date: date | None

    def as_dict(self) -> dict[str, str | bool | None]:
        """JSON-friendly representation."""
        return {
            "copy_id": self.copy_id,
            "title": self.title,
            "renewed": self.renewed,
            "old_due_date": self.old_due_date.isoformat() if self.old_due_date else None,
            "new_due_date": self.new_due_date.isoformat() if self.new_due_date else None,
        }


class OpenCoordinator(DataUpdateCoordinator[AccountData]):
    """Polls the account and performs automatic renewals."""

    config_entry: OpenConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: OpenConfigEntry, client: OpenClient
    ) -> None:
        """Initialize."""
        hours = entry.options.get(CONF_UPDATE_HOURS, DEFAULT_UPDATE_HOURS)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=max(timedelta(hours=hours), MIN_UPDATE_INTERVAL),
        )
        self.client = client
        # (copy id, due date) pairs already tried automatically, to avoid retrying
        # a refused renewal on every poll.
        self._auto_attempted: set[tuple[str, date]] = set()

    @property
    def _allow_fees(self) -> bool:
        return self.config_entry.options.get(
            CONF_ALLOW_RENEWAL_FEES, DEFAULT_ALLOW_RENEWAL_FEES
        )

    async def _async_update_data(self) -> AccountData:
        try:
            data = await self.client.async_fetch()
        except InvalidAuth as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except OpenError as err:
            raise UpdateFailed(str(err)) from err

        if self.config_entry.options.get(CONF_AUTO_RENEW, DEFAULT_AUTO_RENEW):
            data = await self._auto_renew(data)
        return data

    def _auto_candidates(self, data: AccountData) -> list[Loan]:
        days = self.config_entry.options.get(CONF_AUTO_RENEW_DAYS, DEFAULT_AUTO_RENEW_DAYS)
        limit = dt_util.now().date() + timedelta(days=days)
        return [
            loan
            for loan in data.loans
            if loan.copy_id
            and loan.due_date
            and loan.due_date <= limit
            and loan.renewable is not False
            and (loan.copy_id, loan.due_date) not in self._auto_attempted
        ]

    async def _auto_renew(self, data: AccountData) -> AccountData:
        candidates = self._auto_candidates(data)
        if not candidates:
            return data
        for loan in candidates:
            assert loan.copy_id and loan.due_date
            self._auto_attempted.add((loan.copy_id, loan.due_date))

        error: str | None = None
        try:
            await self.client.async_renew(
                [loan.copy_id for loan in candidates if loan.copy_id], self._allow_fees
            )
        except CannotConnect as err:
            # Try again on the next poll.
            for loan in candidates:
                self._auto_attempted.discard((loan.copy_id or "", loan.due_date or date.min))
            _LOGGER.warning("Automatic renewal failed: %s", err)
            return data
        except OpenError as err:
            error = str(err)

        try:
            new_data = await self.client.async_fetch()
        except OpenError as err:
            _LOGGER.warning("Could not re-read account after renewal: %s", err)
            return data

        results = self._compare(candidates, new_data)
        self._report(results, error, automatic=True)
        return new_data

    async def async_renew(
        self, copy_ids: list[str] | None, next_due_only: bool = False
    ) -> list[RenewalResult]:
        """Renew the given copies.

        Without copy ids: all renewable loans, or only those with the earliest
        due date when next_due_only is set.
        """
        data = self.data
        if data is None:
            raise HomeAssistantError("No account data yet")
        if copy_ids is None:
            pool = next_due(data)[1] if next_due_only else data.loans
            loans = [loan for loan in pool if loan.copy_id and loan.renewable is not False]
        else:
            wanted = set(copy_ids)
            loans = [loan for loan in data.loans if loan.copy_id in wanted]
            if missing := wanted - {loan.copy_id for loan in loans}:
                raise HomeAssistantError(f"Unknown copy ids: {', '.join(sorted(missing))}")
        if not loans:
            raise HomeAssistantError("Nothing to renew")

        error: str | None = None
        try:
            await self.client.async_renew(
                [loan.copy_id for loan in loans if loan.copy_id], self._allow_fees
            )
        except RenewalFeeError as err:
            raise HomeAssistantError(
                f"Renewal would cost {err.fee} EUR; allow renewal fees in the options"
            ) from err
        except CannotConnect as err:
            raise HomeAssistantError(str(err)) from err
        except OpenError as err:
            error = str(err)

        await self.async_refresh()
        if self.last_update_success and self.data is not None:
            results = self._compare(loans, self.data)
        else:
            results = []
        self._report(results, error, automatic=False)
        if error and not any(r.renewed for r in results):
            raise HomeAssistantError(f"Renewal failed: {error}")
        return results

    @staticmethod
    def _compare(before: list[Loan], after: AccountData) -> list[RenewalResult]:
        due_after = {loan.copy_id: loan.due_date for loan in after.loans}
        results: list[RenewalResult] = []
        for loan in before:
            new_due = due_after.get(loan.copy_id)
            renewed = bool(new_due and loan.due_date and new_due > loan.due_date)
            results.append(
                RenewalResult(
                    copy_id=loan.copy_id or "",
                    title=loan.title,
                    renewed=renewed,
                    old_due_date=loan.due_date,
                    new_due_date=new_due,
                )
            )
        return results

    def _report(
        self, results: list[RenewalResult], error: str | None, automatic: bool
    ) -> None:
        for result in results:
            self.hass.bus.async_fire(
                EVENT_RENEWAL,
                {
                    "config_entry_id": self.config_entry.entry_id,
                    "automatic": automatic,
                    **result.as_dict(),
                },
            )
        failed = [r for r in results if not r.renewed]
        if not automatic or not (failed or error):
            return
        lines = [f"- {r.title} (fällig {r.old_due_date:%d.%m.%Y})" for r in failed if r.old_due_date]
        message = "Diese Medien konnten nicht automatisch verlängert werden:\n" + "\n".join(lines)
        if error:
            message += f"\n\nMeldung der Bibliothek: {error}"
        persistent_notification.async_create(
            self.hass,
            message,
            title=f"{self.config_entry.title}: Verlängerung fehlgeschlagen",
            notification_id=f"{DOMAIN}_{self.config_entry.entry_id}_renewal",
        )
