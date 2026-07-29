"""공통 엔티티 기반 클래스."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_CONNECTIVITY,
    DOMAIN,
    MANUFACTURER,
    UNAVAILABLE_VALUE,
)
from .coordinator import MiroCoordinator


def parse_number(value: Any) -> float | None:
    """오프라인 기기는 숫자 자리에 '--' 를 보낸다. 그런 값은 None 으로."""
    if value is None or value == UNAVAILABLE_VALUE:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class MiroEntity(CoordinatorEntity[MiroCoordinator]):
    """미로 기기에 속한 엔티티의 공통 동작."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MiroCoordinator, serial: str) -> None:
        super().__init__(coordinator)
        self._serial = serial

    @property
    def _info(self) -> dict[str, Any]:
        """device_list 에서 받은 기기 정보."""
        return self.coordinator.devices.get(self._serial, {})

    @property
    def _state(self) -> dict[str, Any]:
        """마지막 폴링으로 받은 상태."""
        return (self.coordinator.data or {}).get(self._serial, {})

    def value(self, attribute: str) -> Any:
        return self._state.get(attribute)

    @property
    def device_info(self) -> DeviceInfo:
        info = self._info
        return DeviceInfo(
            identifiers={(DOMAIN, self._serial)},
            manufacturer=MANUFACTURER,
            model=info.get("model"),
            name=info.get("nickname") or f"MIRO {info.get('model', '')}".strip(),
            serial_number=self._serial,
            sw_version=info.get("version_sw"),
            hw_version=info.get("version_hw"),
        )

    @property
    def available(self) -> bool:
        if not super().available or not self._state:
            return False
        # Connectivity 를 보고하지 않는 기기는 상태가 오는 것만으로 살아있다고 본다.
        connectivity = self.value(ATTR_CONNECTIVITY)
        return connectivity is not False
