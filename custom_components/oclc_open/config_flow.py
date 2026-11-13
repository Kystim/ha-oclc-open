"""Config flow for the OCLC OPEN library integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_NAME, CONF_PASSWORD, CONF_URL, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import CannotConnect, InvalidAuth, OpenClient, OpenError, create_session
from .const import (
    CONF_ALLOW_RENEWAL_FEES,
    CONF_AUTO_RENEW,
    CONF_AUTO_RENEW_DAYS,
    CONF_UPDATE_HOURS,
    DEFAULT_ALLOW_RENEWAL_FEES,
    DEFAULT_AUTO_RENEW,
    DEFAULT_AUTO_RENEW_DAYS,
    DEFAULT_UPDATE_HOURS,
    DEFAULT_URL,
    DOMAIN,
)
from .coordinator import OpenConfigEntry

_LOGGER = logging.getLogger(__name__)

PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _normalize_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if "://" not in url:
        url = "https://" + url
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


class OpenConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1

    async def _validate(
        self, url: str, username: str, password: str
    ) -> tuple[str | None, dict[str, str]]:
        session = create_session()
        try:
            name = await OpenClient(session, url, username, password).async_validate()
        except InvalidAuth:
            return None, {"base": "invalid_auth"}
        except CannotConnect:
            return None, {"base": "cannot_connect"}
        except OpenError:
            _LOGGER.exception("Unexpected OPAC response")
            return None, {"base": "unexpected_page"}
        finally:
            await session.close()
        return name, {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for OPAC URL and library card."""
        errors: dict[str, str] = {}
        if user_input is not None:
            url = _normalize_url(user_input[CONF_URL])
            username = user_input[CONF_USERNAME].strip()
            await self.async_set_unique_id(f"{urlparse(url).netloc}_{username}".lower())
            self._abort_if_unique_id_configured()
            name, errors = await self._validate(url, username, user_input[CONF_PASSWORD])
            if not errors:
                return self.async_create_entry(
                    title=f"{name or urlparse(url).netloc} ({username})",
                    data={
                        CONF_NAME: name or urlparse(url).netloc,
                        CONF_URL: url,
                        CONF_USERNAME: username,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_URL, default=DEFAULT_URL): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.URL)
                ),
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauth when the password was rejected."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the new password."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            _, errors = await self._validate(
                entry.data[CONF_URL], entry.data[CONF_USERNAME], user_input[CONF_PASSWORD]
            )
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR}),
            description_placeholders={CONF_USERNAME: entry.data[CONF_USERNAME]},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: OpenConfigEntry) -> OpenOptionsFlow:
        """Options flow."""
        return OpenOptionsFlow()


class OpenOptionsFlow(OptionsFlowWithReload):
    """Renewal and polling options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    **user_input,
                    CONF_AUTO_RENEW_DAYS: int(user_input[CONF_AUTO_RENEW_DAYS]),
                    CONF_UPDATE_HOURS: int(user_input[CONF_UPDATE_HOURS]),
                }
            )
        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_AUTO_RENEW,
                    default=options.get(CONF_AUTO_RENEW, DEFAULT_AUTO_RENEW),
                ): BooleanSelector(),
                vol.Required(
                    CONF_AUTO_RENEW_DAYS,
                    default=options.get(CONF_AUTO_RENEW_DAYS, DEFAULT_AUTO_RENEW_DAYS),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=30, step=1, mode=NumberSelectorMode.BOX,
                        unit_of_measurement="d",
                    )
                ),
                vol.Required(
                    CONF_ALLOW_RENEWAL_FEES,
                    default=options.get(CONF_ALLOW_RENEWAL_FEES, DEFAULT_ALLOW_RENEWAL_FEES),
                ): BooleanSelector(),
                vol.Required(
                    CONF_UPDATE_HOURS,
                    default=options.get(CONF_UPDATE_HOURS, DEFAULT_UPDATE_HOURS),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1, max=24, step=1, mode=NumberSelectorMode.BOX,
                        unit_of_measurement="h",
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
