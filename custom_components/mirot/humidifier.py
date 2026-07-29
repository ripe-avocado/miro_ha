"""가습기 엔티티.

미로 가습기는 목표 습도를 지정하는 방식이 아니라 분무 세기(SteamLevel)를
고르는 방식이다. 세기 조절은 select 로 따로 노출하고, 여기서는 전원과
운전 모드만 다룬다.
"""

from __future__ import annotations

from homeassistant.components.humidifier import (
    HumidifierDeviceClass,
    HumidifierEntity,
    HumidifierEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ATTR_HUMIDITY, ATTR_OPERATION_MODE, ATTR_POWER, DOMAIN
from .coordinator import MiroCoordinator
from .entity import MiroEntity, parse_number


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: MiroCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        MiroHumidifier(coordinator, serial)
        for serial in coordinator.devices
        if coordinator.main_entity_domain(serial) == Platform.HUMIDIFIER
    )


class MiroHumidifier(MiroEntity, HumidifierEntity):
    """미로 가습기."""

    _attr_name = None
    _attr_device_class = HumidifierDeviceClass.HUMIDIFIER

    def __init__(self, coordinator: MiroCoordinator, serial: str) -> None:
        super().__init__(coordinator, serial)
        self._attr_unique_id = serial

        mode = coordinator.spec(serial).control(ATTR_OPERATION_MODE)
        if mode and coordinator.is_controllable(serial, ATTR_OPERATION_MODE):
            self._attr_supported_features = HumidifierEntityFeature.MODES
            self._attr_available_modes = list(mode.options)
        else:
            self._attr_supported_features = HumidifierEntityFeature(0)
            self._attr_available_modes = None

    @property
    def is_on(self) -> bool | None:
        power = self.value(ATTR_POWER)
        if power is None:
            return None
        return power == "On"

    @property
    def mode(self) -> str | None:
        value = self.value(ATTR_OPERATION_MODE)
        if self._attr_available_modes and value in self._attr_available_modes:
            return value
        return None

    @property
    def current_humidity(self) -> float | None:
        return parse_number(self.value(ATTR_HUMIDITY))

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_send(self._serial, {ATTR_POWER: "On"})

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_send(self._serial, {ATTR_POWER: "Off"})

    async def async_set_mode(self, mode: str) -> None:
        if not self._attr_available_modes or mode not in self._attr_available_modes:
            raise ValueError(f"지원하지 않는 모드: {mode}")
        await self.coordinator.async_send(self._serial, {ATTR_OPERATION_MODE: mode})
