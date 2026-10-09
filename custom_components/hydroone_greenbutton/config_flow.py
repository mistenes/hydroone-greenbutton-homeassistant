"""Enter credentials locally in Home Assistant, then select the meter."""

from __future__ import annotations

import asyncio
from hashlib import sha256

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from .client import AuthenticationError, HydroOneClient, PortalError
from .const import (
    CONF_ACCOUNT, CONF_CONTRACT, CONF_HISTORY, CONF_LOOKBACK, CONF_METER, CONF_POLL,
    DEFAULT_HISTORY, DEFAULT_LOOKBACK, DEFAULT_POLL, DOMAIN,
)


def _credentials(default_username=""):
    return vol.Schema({
        vol.Required(CONF_USERNAME, default=default_username): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.EMAIL)),
        vol.Required(CONF_PASSWORD): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
    })


class HydroOneConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self):
        self.credentials = {}
        self.contracts = []
        self.reauth_entry = None

    async def _validate(self, user_input):
        async with HydroOneClient(user_input[CONF_USERNAME], user_input[CONF_PASSWORD]) as client:
            customer = await client.login()
        return client.contracts(customer)

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                self.contracts = await self._validate(user_input)
                if not self.contracts:
                    errors["base"] = "no_meters"
                else:
                    self.credentials = dict(user_input)
                    return await self.async_step_meter()
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except (PortalError, aiohttp.ClientError, asyncio.TimeoutError):
                errors["base"] = "cannot_connect"
        return self.async_show_form(step_id="user", data_schema=_credentials(), errors=errors)

    async def async_step_meter(self, user_input=None):
        if user_input is not None:
            contract = next(x for x in self.contracts if x.contract == user_input[CONF_CONTRACT])
            identity = sha256(f"{contract.account}:{contract.contract}".encode()).hexdigest()
            await self.async_set_unique_id(identity)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"Hydro One {contract.meter}",
                data={
                    **self.credentials, CONF_ACCOUNT: contract.account, CONF_CONTRACT: contract.contract,
                    CONF_METER: contract.meter, CONF_HISTORY: user_input[CONF_HISTORY],
                },
            )
        choices = {x.contract: f"Account …{x.account[-4:]} — meter {x.meter}" for x in self.contracts}
        return self.async_show_form(step_id="meter", data_schema=vol.Schema({
            vol.Required(CONF_CONTRACT, default=self.contracts[0].contract): vol.In(choices),
            vol.Required(CONF_HISTORY, default=DEFAULT_HISTORY): vol.All(vol.Coerce(int), vol.Range(min=1, max=730)),
        }))

    async def async_step_reauth(self, entry_data):
        self.reauth_entry = self._get_reauth_entry()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                contracts = await self._validate(user_input)
                if not any(x.account == self.reauth_entry.data[CONF_ACCOUNT] and
                           x.contract == self.reauth_entry.data[CONF_CONTRACT] for x in contracts):
                    errors["base"] = "wrong_account"
                else:
                    return self.async_update_reload_and_abort(self.reauth_entry, data_updates=user_input)
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except (PortalError, aiohttp.ClientError, asyncio.TimeoutError):
                errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_credentials(self.reauth_entry.data[CONF_USERNAME]), errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return HydroOneOptionsFlow()


class HydroOneOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(step_id="init", data_schema=vol.Schema({
            vol.Required(CONF_POLL, default=self.config_entry.options.get(CONF_POLL, DEFAULT_POLL)):
                vol.All(vol.Coerce(int), vol.Range(min=6, max=168)),
            vol.Required(CONF_LOOKBACK, default=self.config_entry.options.get(CONF_LOOKBACK, DEFAULT_LOOKBACK)):
                vol.All(vol.Coerce(int), vol.Range(min=2, max=60)),
        }))
