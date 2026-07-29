"""상태 폴링 코디네이터."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import capabilities
from .api import MiroAuthError, MiroClient, MiroError
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .models import ModelSpec, get_model

_LOGGER = logging.getLogger(__name__)


class MiroCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """기기 상태를 주기적으로 가져온다."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: MiroClient
    ) -> None:
        interval = entry.options.get(
            CONF_SCAN_INTERVAL, entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        )
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=interval),
            config_entry=entry,
        )
        self.client = client
        self.entry = entry
        # serialno -> device_list 의 기기 정보(model, nickname, feature 등)
        self.devices: dict[str, dict[str, Any]] = {}
        # serialno -> 모델 정의
        self.specs: dict[str, ModelSpec] = {}

    async def async_load_devices(self) -> None:
        """등록된 기기 목록을 읽어온다. 셋업 시 1회만 호출한다."""
        try:
            self.devices = await self.client.async_get_devices()
        except MiroAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except MiroError as err:
            raise ConfigEntryNotReady(f"기기 목록 조회 실패: {err}") from err

        self.specs = {
            serial: get_model(info.get("model"))
            for serial, info in self.devices.items()
        }
        for serial, spec in self.specs.items():
            info = self.devices[serial]
            _LOGGER.debug(
                "%s: %s (%s) -> %s",
                serial,
                info.get("model"),
                info.get("nickname"),
                spec.category,
            )
            skipped = capabilities.unrepresentable_controls(
                self.features(serial), spec
            )
            if skipped:
                # 추측해서 명령을 보내지 않으려고 일부러 비워 둔 것들이다.
                _LOGGER.info(
                    "%s (%s): 값 체계를 몰라 제어 엔티티를 만들지 않은 항목 %s",
                    info.get("nickname") or serial,
                    info.get("model"),
                    ", ".join(skipped),
                )
        self._persist_token()

    # --- 기능 판정 ---------------------------------------------------------
    #
    # 무엇을 만들지는 두 정보를 겹쳐서 정한다.
    #   1) 기기가 실제로 보고하는 feature 맵 — 이 기기에 그 기능이 있는가
    #   2) models.json 의 제어 정의        — 그 기능을 어떤 값으로 제어하는가
    # 둘 중 하나라도 없으면 추측하지 않고 만들지 않는다.

    def spec(self, serial: str) -> ModelSpec:
        return self.specs.get(serial) or get_model(None)

    def feature(self, serial: str, attribute: str) -> dict[str, Any] | None:
        features = self.devices.get(serial, {}).get("feature") or {}
        value = features.get(attribute)
        return value if isinstance(value, dict) else None

    def features(self, serial: str) -> dict[str, Any]:
        return self.devices.get(serial, {}).get("feature") or {}

    def is_reported(self, serial: str, attribute: str) -> bool:
        return capabilities.is_reported(self.features(serial), attribute)

    def is_controllable(self, serial: str, attribute: str) -> bool:
        return capabilities.is_controllable(
            self.features(serial), self.spec(serial), attribute
        )

    def main_entity_domain(self, serial: str) -> str | None:
        return capabilities.main_entity_domain(self.features(serial), self.spec(serial))

    def select_attributes(self, serial: str) -> list[str]:
        return capabilities.select_attributes(self.features(serial), self.spec(serial))

    def switch_attributes(self, serial: str) -> list[str]:
        return capabilities.switch_attributes(self.features(serial), self.spec(serial))

    # --- 폴링 -------------------------------------------------------------

    def _persist_token(self) -> None:
        """갱신된 토큰을 config entry 에 다시 저장한다."""
        token = self.client.access_token
        if token and token != self.entry.data.get(CONF_ACCESS_TOKEN):
            self.hass.config_entries.async_update_entry(
                self.entry, data={**self.entry.data, CONF_ACCESS_TOKEN: token}
            )

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        if not self.devices:
            return {}

        try:
            states = await self.client.async_query(list(self.devices), sync=True)
        except MiroAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except MiroError as err:
            raise UpdateFailed(f"상태 조회 실패: {err}") from err

        self._persist_token()
        return states

    async def async_send(self, serial: str, command: dict[str, Any]) -> None:
        """명령 하나를 보내고 곧바로 상태를 다시 읽는다."""
        try:
            await self.client.async_execute(serial, [command])
        except MiroAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except MiroError as err:
            # 서비스 호출 실패는 UI에 오류로 보여야 한다.
            raise HomeAssistantError(f"제어 실패: {err}") from err

        self._persist_token()
        # 반영에 1초 안팎이 걸린다. 다음 폴링을 앞당겨 UI 지연을 줄인다.
        await self.async_request_refresh()
