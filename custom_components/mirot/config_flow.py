"""설정 흐름 — 계정 로그인과 폴링 주기 옵션."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import MiroAuthError, MiroClient, MiroError
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_CLIENT_ID,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class MiroConfigFlow(ConfigFlow, domain=DOMAIN):
    """미로 계정으로 통합을 설정한다."""

    VERSION = 1

    def __init__(self) -> None:
        self._reauth_entry: ConfigEntry | None = None

    async def _async_validate(
        self, username: str, password: str, client_id: str
    ) -> tuple[str | None, dict[str, str]]:
        """로그인을 시도해 토큰을 받아온다. (토큰, 오류) 를 돌려준다."""
        client = MiroClient(
            async_get_clientsession(self.hass), username, password, client_id
        )
        try:
            token = await client.async_login()
        except MiroAuthError:
            return None, {"base": "invalid_auth"}
        except MiroError:
            return None, {"base": "cannot_connect"}
        return token, {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME]
            await self.async_set_unique_id(username.lower())
            self._abort_if_unique_id_configured()

            client_id = uuid.uuid4().hex[:16]
            token, errors = await self._async_validate(
                username, user_input[CONF_PASSWORD], client_id
            )
            if token:
                return self.async_create_entry(
                    title=username,
                    data={
                        CONF_USERNAME: username,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_CLIENT_ID: client_id,
                        CONF_ACCESS_TOKEN: token,
                    },
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._reauth_entry is not None
        errors: dict[str, str] = {}

        if user_input is not None:
            data = self._reauth_entry.data
            client_id = data.get(CONF_CLIENT_ID) or uuid.uuid4().hex[:16]
            token, errors = await self._async_validate(
                data[CONF_USERNAME], user_input[CONF_PASSWORD], client_id
            )
            if token:
                return self.async_update_reload_and_abort(
                    self._reauth_entry,
                    data={
                        **data,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_CLIENT_ID: client_id,
                        CONF_ACCESS_TOKEN: token,
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={
                CONF_USERNAME: self._reauth_entry.data[CONF_USERNAME]
            },
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> MiroOptionsFlow:
        return MiroOptionsFlow()


class MiroOptionsFlow(OptionsFlow):
    """폴링 주기를 조정한다."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = self.config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_INTERVAL, default=current): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                    )
                }
            ),
        )
