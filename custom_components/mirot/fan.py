"""선풍기·서큘레이터·공기청정기 엔티티.

공기청정기도 HA 관례상 fan 플랫폼으로 표현한다.
"""

from __future__ import annotations

import math
from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.percentage import (
    ordered_list_item_to_percentage,
    percentage_to_ordered_list_item,
    percentage_to_ranged_value,
    ranged_value_to_percentage,
)

from .const import (
    ATTR_CURRENT_ANGLE,
    ATTR_FAN_SPEED,
    ATTR_OPERATION_MODE,
    ATTR_POWER,
    ATTR_ROTATION_MODE,
    ATTR_ROTATION_RANGE,
    ATTR_TIMER_REMAIN,
    DOMAIN,
    PREFERRED_ROTATION_RANGE,
    PRESET_MANUAL,
)
from .coordinator import MiroCoordinator
from .entity import MiroEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: MiroCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        MiroFan(coordinator, serial)
        for serial in coordinator.devices
        if coordinator.main_entity_domain(serial) == Platform.FAN
    )


class MiroFan(MiroEntity, FanEntity):
    """바람을 내보내는 미로 기기."""

    _attr_name = None  # 기기 이름을 그대로 쓴다

    def __init__(self, coordinator: MiroCoordinator, serial: str) -> None:
        super().__init__(coordinator, serial)
        self._attr_unique_id = serial

        spec = coordinator.spec(serial)
        self._speed = spec.control(ATTR_FAN_SPEED)
        self._mode = spec.control(ATTR_OPERATION_MODE)
        self._rotation_range = spec.control(ATTR_ROTATION_RANGE)
        self._rotation_toggle = spec.control(ATTR_ROTATION_MODE)

        features = FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF

        # 풍량은 모델에 따라 1~100 연속이기도 하고 1/2/3 단이기도 하다.
        if self._speed and coordinator.is_controllable(serial, ATTR_FAN_SPEED):
            features |= FanEntityFeature.SET_SPEED
            if self._speed.is_range:
                self._speed_range = (self._speed.min or 1, self._speed.max or 100)
                self._speed_steps = None
                self._attr_speed_count = self._speed_range[1] - self._speed_range[0] + 1
            else:
                self._speed_range = None
                self._speed_steps = [int(v) for v in self._speed.values]
                self._attr_speed_count = len(self._speed_steps)
        else:
            self._speed_range = None
            self._speed_steps = None

        if self._mode and coordinator.is_controllable(serial, ATTR_OPERATION_MODE):
            features |= FanEntityFeature.PRESET_MODE
            self._attr_preset_modes = list(self._mode.options)

        # 회전은 각도를 고르는 모델(RotationRange)과 On/Off 뿐인 모델(RotationMode)이 있다.
        if self._rotation_range and coordinator.is_controllable(
            serial, ATTR_ROTATION_RANGE
        ):
            features |= FanEntityFeature.OSCILLATE
            angles = [int(v) for v in self._rotation_range.values if int(v) > 0]
            self._oscillate_angle = (
                PREFERRED_ROTATION_RANGE
                if PREFERRED_ROTATION_RANGE in angles
                else (max(angles) if angles else 0)
            )
        elif self._rotation_toggle and coordinator.is_controllable(
            serial, ATTR_ROTATION_MODE
        ):
            features |= FanEntityFeature.OSCILLATE
            self._oscillate_angle = None
        else:
            self._oscillate_angle = None

        self._attr_supported_features = features

    # --- 상태 --------------------------------------------------------------

    @property
    def is_on(self) -> bool | None:
        power = self.value(ATTR_POWER)
        if power is None:
            return None
        return power == "On"

    @property
    def percentage(self) -> int | None:
        """전원이 꺼져 있으면 0."""
        if not self.is_on:
            return 0
        speed = self.value(ATTR_FAN_SPEED)
        if not isinstance(speed, (int, float)):
            return None
        speed = int(speed)
        if self._speed_range:
            return ranged_value_to_percentage(self._speed_range, speed)
        if self._speed_steps:
            if speed not in self._speed_steps:
                return None
            return ordered_list_item_to_percentage(self._speed_steps, speed)
        return None

    @property
    def preset_mode(self) -> str | None:
        mode = self.value(ATTR_OPERATION_MODE)
        if self._attr_preset_modes and mode in self._attr_preset_modes:
            return mode
        return None

    @property
    def oscillating(self) -> bool | None:
        mode = self.value(ATTR_ROTATION_MODE)
        if isinstance(mode, str):
            return mode == "On"
        rotation_range = self.value(ATTR_ROTATION_RANGE)
        if isinstance(rotation_range, (int, float)):
            return rotation_range > 0
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        for key, attribute in (
            ("rotation_range", ATTR_ROTATION_RANGE),
            ("current_angle", ATTR_CURRENT_ANGLE),
            ("timer_remain_minute", ATTR_TIMER_REMAIN),
        ):
            value = self.value(attribute)
            if value is not None:
                attrs[key] = value
        return attrs

    # --- 제어 --------------------------------------------------------------

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        await self.coordinator.async_send(self._serial, {ATTR_POWER: "On"})
        if preset_mode is not None:
            await self.async_set_preset_mode(preset_mode)
        if percentage:
            await self.async_set_percentage(percentage)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_send(self._serial, {ATTR_POWER: "Off"})

    async def async_set_percentage(self, percentage: int) -> None:
        if percentage <= 0:
            await self.async_turn_off()
            return

        # 자동·자연·수면 모드에서는 풍량을 직접 못 바꾼다. 먼저 수동으로 돌린다.
        if (
            self._attr_preset_modes
            and PRESET_MANUAL in self._attr_preset_modes
            and self.preset_mode not in (None, PRESET_MANUAL)
        ):
            await self.coordinator.async_send(
                self._serial, {ATTR_OPERATION_MODE: PRESET_MANUAL}
            )

        if self._speed_range:
            low, high = self._speed_range
            speed = math.ceil(percentage_to_ranged_value(self._speed_range, percentage))
            speed = max(low, min(high, speed))
        elif self._speed_steps:
            speed = percentage_to_ordered_list_item(self._speed_steps, percentage)
        else:
            return

        # 풍량을 보내면 꺼져 있던 기기도 함께 켜진다.
        await self.coordinator.async_send(self._serial, {ATTR_FAN_SPEED: speed})

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        if not self._attr_preset_modes or preset_mode not in self._attr_preset_modes:
            raise ValueError(f"지원하지 않는 모드: {preset_mode}")
        await self.coordinator.async_send(
            self._serial, {ATTR_OPERATION_MODE: preset_mode}
        )

    async def async_oscillate(self, oscillating: bool) -> None:
        """회전 On/Off.

        각도를 고르는 모델은 RotationRange 로 제어한다. 0이 아닌 값을 주면
        RotationMode 가 따라서 켜지고, 0을 주면 꺼진다 (기기에서 연동됨).
        """
        if self._oscillate_angle is not None:
            target = self._oscillate_angle if oscillating else 0
            await self.coordinator.async_send(
                self._serial, {ATTR_ROTATION_RANGE: target}
            )
            return

        await self.coordinator.async_send(
            self._serial, {ATTR_ROTATION_MODE: "On" if oscillating else "Off"}
        )
