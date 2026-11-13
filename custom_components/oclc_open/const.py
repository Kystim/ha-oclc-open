"""Constants for the OCLC OPEN library integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "oclc_open"

DEFAULT_URL: Final = "https://opac.iserlohn.de"
ACCOUNT_PATH: Final = "/Mein-Konto"

CONF_AUTO_RENEW: Final = "auto_renew"
CONF_AUTO_RENEW_DAYS: Final = "auto_renew_days"
CONF_ALLOW_RENEWAL_FEES: Final = "allow_renewal_fees"
CONF_UPDATE_HOURS: Final = "update_hours"

DEFAULT_AUTO_RENEW: Final = False
DEFAULT_AUTO_RENEW_DAYS: Final = 3
DEFAULT_ALLOW_RENEWAL_FEES: Final = False
DEFAULT_UPDATE_HOURS: Final = 3

MIN_UPDATE_INTERVAL: Final = timedelta(minutes=30)

SERVICE_RENEW: Final = "renew"
ATTR_COPY_IDS: Final = "copy_ids"
ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"
ATTR_NEXT_DUE_ONLY: Final = "next_due_only"
