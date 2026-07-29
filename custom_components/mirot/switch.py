"""On/Off 설정 스위치 (차일드락, 소리, 자외선 살균 등).

주 엔티티(fan/humidifier)가 다루지 않는 On/Off 속성을 스위치로 만든다.
전원을 주 엔티티가 쓰지 않는 기기(디퓨저 등)는 전원도 여기서 스위치가 된다.
"""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ATTR_POWER, DOMAIN
from .coordinator import MiroCoordinator
from .entity import MiroEntity

TRANSLATION_KEYS = {
    "DeviceLock": "device_lock",
    "Mute": "mute",
    "UltraViolet": "ultra_violet",
    "Snooze": "snooze",
    "PowerMode": "power_mode",
    "RotationMode": "rotation_mode",
    "RotationModeVertical": "rotation_mode_vertical",
}

# 부가 설정으로 볼 속성
CONFIG_ATTRS = {"DeviceLock", "Mute", "Snooze"}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: MiroCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        MiroSwitch(coordinator, serial, attribute)
        for serial in coordinator.devices
        for attribute in coordinator.switch_attributes(serial)
    )


class MiroSwitch(MiroEntity, SwitchEntity):
    """기기의 On/Off 설정 항목."""

    def __init__(
        self, coordinator: MiroCoordinator, serial: str, attribute: str
    ) -> None:
        super().__init__(coordinator, serial)
        self._attribute = attribute
        self._attr_unique_id = f"{serial}_{attribute.lower()}"

        key = TRANSLATION_KEYS.get(attribute)
        if key:
            self._attr_translation_key = key
        elif attribute == ATTR_POWER:
            self._attr_name = None  # 전원이 주 기능인 기기는 기기 이름을 그대로
        else:
            self._attr_name = attribute

        if attribute in CONFIG_ATTRS:
            self._attr_entity_category = EntityCategory.CONFIG

    @property
    def is_on(self) -> bool | None:
        value = self.value(self._attribute)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value == "On"
        return None

    async def _async_set(self, state: str) -> None:
        await self.coordinator.async_send(self._serial, {self._attribute: state})

    async def async_turn_on(self, **kwargs) -> None:
        await self._async_set("On")

    async def async_turn_off(self, **kwargs) -> None:
        await self._async_set("Off")
