"""읽기 전용 센서.

기기가 실제로 보고하는 속성(feature 맵의 Report)만 만든다. 아래 표에 없는
속성은 단위·의미를 모르므로 만들지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    CONCENTRATION_PARTS_PER_MILLION,
    LIGHT_LUX,
    PERCENTAGE,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ATTR_TIMER_REMAIN, DOMAIN
from .coordinator import MiroCoordinator
from .entity import MiroEntity, parse_number


@dataclass(frozen=True, kw_only=True)
class MiroSensorDescription(SensorEntityDescription):
    """미로 센서 정의. key 가 곧 API 속성명이다."""


MEASUREMENT = SensorStateClass.MEASUREMENT

SENSORS: tuple[MiroSensorDescription, ...] = (
    MiroSensorDescription(
        key="Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=MEASUREMENT,
    ),
    MiroSensorDescription(
        key="Humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=MEASUREMENT,
    ),
    MiroSensorDescription(
        key="BatteryGauge",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=MEASUREMENT,
    ),
    # 미세먼지: 모델에 따라 PM025/PM25, PM010/PM100 중 하나로 온다.
    # 실제로 보고하는 이름만 엔티티가 된다.
    MiroSensorDescription(
        key="PM025",
        translation_key="pm25",
        device_class=SensorDeviceClass.PM25,
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        state_class=MEASUREMENT,
    ),
    MiroSensorDescription(
        key="PM25",
        translation_key="pm25",
        device_class=SensorDeviceClass.PM25,
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        state_class=MEASUREMENT,
    ),
    MiroSensorDescription(
        key="PM010",
        translation_key="pm10",
        device_class=SensorDeviceClass.PM10,
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        state_class=MEASUREMENT,
    ),
    MiroSensorDescription(
        key="PM100",
        translation_key="pm10",
        device_class=SensorDeviceClass.PM10,
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        state_class=MEASUREMENT,
    ),
    MiroSensorDescription(
        key="PM10",
        translation_key="pm10",
        device_class=SensorDeviceClass.PM10,
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        state_class=MEASUREMENT,
    ),
    MiroSensorDescription(
        key="CarbonDioxide",
        device_class=SensorDeviceClass.CO2,
        native_unit_of_measurement=CONCENTRATION_PARTS_PER_MILLION,
        state_class=MEASUREMENT,
    ),
    MiroSensorDescription(
        key="Illuminance",
        device_class=SensorDeviceClass.ILLUMINANCE,
        native_unit_of_measurement=LIGHT_LUX,
        state_class=MEASUREMENT,
    ),
    # 단위가 확인되지 않은 지수값들. 숫자만 그대로 노출한다.
    MiroSensorDescription(
        key="GasMixture",
        translation_key="gas_mixture",
        state_class=MEASUREMENT,
    ),
    MiroSensorDescription(
        key="FilterLife",
        translation_key="filter_life",
        native_unit_of_measurement=PERCENTAGE,
        state_class=MEASUREMENT,
    ),
    MiroSensorDescription(
        key=ATTR_TIMER_REMAIN,
        translation_key="timer_remaining",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: MiroCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        MiroSensor(coordinator, serial, description)
        for serial in coordinator.devices
        for description in SENSORS
        if coordinator.is_reported(serial, description.key)
    )


class MiroSensor(MiroEntity, SensorEntity):
    """미로 기기의 읽기 전용 수치."""

    entity_description: MiroSensorDescription

    def __init__(
        self,
        coordinator: MiroCoordinator,
        serial: str,
        description: MiroSensorDescription,
    ) -> None:
        super().__init__(coordinator, serial)
        self.entity_description = description
        self._attr_unique_id = f"{serial}_{description.key.lower()}"

    @property
    def native_value(self) -> float | None:
        return parse_number(self.value(self.entity_description.key))

    @property
    def available(self) -> bool:
        # 오프라인이면 '--' 가 오므로 값이 없는 것으로 처리한다.
        return super().available and self.native_value is not None
