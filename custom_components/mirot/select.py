"""여러 값 중 하나를 고르는 설정 (타이머, 회전 범위, 분무 세기 등).

모델마다 고를 수 있는 값이 달라서, 목록은 models.json 에서 읽는다.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ATTR_ROTATION_RANGE, ATTR_TIMER, DOMAIN
from .coordinator import MiroCoordinator
from .entity import MiroEntity
from .models import ControlSpec

# 번역 키가 있는 속성. 없는 속성은 속성명을 그대로 쓴다.
TRANSLATION_KEYS = {
    ATTR_TIMER: "timer",
    ATTR_ROTATION_RANGE: "rotation_range",
    "SteamLevel": "steam_level",
    "OperationMode": "operation_mode",
    "LightMode": "light_mode",
    "LightColor": "light_color",
    "Brightness": "brightness",
    "PTCLevel": "ptc_level",
    "Stage": "stage",
    "Fragrance": "fragrance",
    "VentilationMode": "ventilation_mode",
}

# 주 기능이 아니라 부가 설정으로 볼 속성 (UI에서 설정 영역에 모인다).
CONFIG_ATTRS = {"LightMode", "LightColor", "Brightness", "PTCLevel", "VentilationMode"}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: MiroCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[MiroSelect] = []
    for serial in coordinator.devices:
        spec = coordinator.spec(serial)
        for attribute in coordinator.select_attributes(serial):
            control = spec.control(attribute)
            if control is not None:
                entities.append(MiroSelect(coordinator, serial, control))

    async_add_entities(entities)


class MiroSelect(MiroEntity, SelectEntity):
    """정해진 값 중 하나를 고르는 설정."""

    def __init__(
        self, coordinator: MiroCoordinator, serial: str, control: ControlSpec
    ) -> None:
        super().__init__(coordinator, serial)
        self._control = control
        attribute = control.attribute
        self._attr_unique_id = f"{serial}_{attribute.lower()}"
        self._attr_options = control.options

        key = TRANSLATION_KEYS.get(attribute)
        if key:
            self._attr_translation_key = key
        else:
            # 표에는 있으나 번역을 준비하지 못한 속성. 이름만이라도 보이게 한다.
            self._attr_name = attribute

        if attribute in CONFIG_ATTRS:
            self._attr_entity_category = EntityCategory.CONFIG

    @property
    def current_option(self) -> str | None:
        value = self.value(self._control.attribute)
        if value is None:
            return None
        if isinstance(value, float):
            value = int(value)
        option = str(value)
        return option if option in self._attr_options else None

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_send(
            self._serial,
            {self._control.attribute: self._control.to_command_value(option)},
        )
