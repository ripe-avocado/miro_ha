"""참/거짓 상태 (배터리 충전, 물 없음, 필터 교체 등)."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ATTR_CONNECTIVITY, DOMAIN
from .coordinator import MiroCoordinator
from .entity import MiroEntity


@dataclass(frozen=True, kw_only=True)
class MiroBinarySensorDescription(BinarySensorEntityDescription):
    """미로 바이너리 센서 정의. key 가 곧 API 속성명이다."""

    # 기기가 오프라인이어도 의미가 있는 센서인지 (연결 상태 자체 등)
    always_available: bool = False


BINARY_SENSORS: tuple[MiroBinarySensorDescription, ...] = (
    MiroBinarySensorDescription(
        key="BatteryCharging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
    ),
    MiroBinarySensorDescription(
        key="BatteryInstallation",
        translation_key="battery_installed",
        entity_registry_enabled_default=False,
    ),
    MiroBinarySensorDescription(
        key="EmptyWater",
        translation_key="empty_water",
        device_class=BinarySensorDeviceClass.PROBLEM,
    ),
    MiroBinarySensorDescription(
        key="FilterExpired",
        translation_key="filter_expired",
        device_class=BinarySensorDeviceClass.PROBLEM,
    ),
    MiroBinarySensorDescription(
        key="DoorOpen",
        device_class=BinarySensorDeviceClass.OPENING,
    ),
    MiroBinarySensorDescription(
        key="RemoteControllerPairing",
        translation_key="remote_paired",
        entity_registry_enabled_default=False,
    ),
    MiroBinarySensorDescription(
        key=ATTR_CONNECTIVITY,
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        always_available=True,
        entity_registry_enabled_default=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: MiroCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        MiroBinarySensor(coordinator, serial, description)
        for serial in coordinator.devices
        for description in BINARY_SENSORS
        if coordinator.is_reported(serial, description.key)
    )


class MiroBinarySensor(MiroEntity, BinarySensorEntity):
    """미로 기기의 참/거짓 상태."""

    entity_description: MiroBinarySensorDescription

    def __init__(
        self,
        coordinator: MiroCoordinator,
        serial: str,
        description: MiroBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator, serial)
        self.entity_description = description
        self._attr_unique_id = f"{serial}_{description.key.lower()}"

    @property
    def is_on(self) -> bool | None:
        value = self.value(self.entity_description.key)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value == "On"
        return None

    @property
    def available(self) -> bool:
        if self.entity_description.always_available:
            # 연결 여부 자체를 보고하는 센서는 오프라인일 때도 살아 있어야 한다.
            return self.coordinator.last_update_success and bool(self._state)
        return super().available
