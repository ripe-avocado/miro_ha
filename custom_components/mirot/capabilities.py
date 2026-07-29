"""무엇을 만들 수 있는지 판정하는 규칙.

판정은 두 정보를 겹쳐서 한다.

1. 기기가 실제로 보고하는 `feature` 맵 — 이 기기에 그 기능이 있는가.
   `device_list` 응답에서 오며, 모델 메타데이터보다 정확하다.
2. `models.json` 의 제어 정의 — 그 기능을 어떤 값으로 제어하는가.

둘 중 하나라도 없으면 추측하지 않는다. 다만 On/Off 는 값이 뻔하므로
표에 없는 모델이라도 `feature` 가 Toggle 이라고 알려주면 다룰 수 있다.

Home Assistant 를 import 하지 않는다 — 규칙만 담아 따로 검증할 수 있게 한다.
"""

from __future__ import annotations

from typing import Any

from .const import (
    ATTR_OPERATION_MODE,
    ATTR_POWER,
    ATTR_ROTATION_MODE,
    CATEGORY_HUMIDIFIER,
    FAN_CATEGORIES,
    FAN_OWNED_ATTRS,
    HUMIDIFIER_OWNED_ATTRS,
)
from .models import ModelSpec

# main_entity_domain() 이 돌려주는 값. HA 의 Platform 값과 문자열이 같다.
DOMAIN_FAN = "fan"
DOMAIN_HUMIDIFIER = "humidifier"

# 누르는 순간에만 의미가 있어 상태로 추적할 수 없는 명령.
MOMENTARY = {"RotationDirection", "RotationMagnitude", "InvokeReport"}


def _feature(features: dict[str, Any], attribute: str) -> dict[str, Any] | None:
    value = (features or {}).get(attribute)
    return value if isinstance(value, dict) else None


def is_reported(features: dict[str, Any], attribute: str) -> bool:
    """상태로 값이 올라오는 속성인가."""
    feature = _feature(features, attribute)
    return bool(feature) and "Report" in str(feature.get("property", ""))


def is_controllable(
    features: dict[str, Any], spec: ModelSpec, attribute: str
) -> bool:
    """제어할 수 있고, 어떤 값을 보내야 하는지도 아는 속성인가."""
    feature = _feature(features, attribute)
    if not feature or "Issue" not in str(feature.get("property", "")):
        return False
    control = spec.control(attribute)
    if control is not None:
        return control.is_usable
    return feature.get("type") == "Toggle"


def main_entity_domain(features: dict[str, Any], spec: ModelSpec) -> str | None:
    """이 기기의 주 엔티티가 무엇인지. 없으면 None.

    전원을 못 켜는 기기(레이더 센서 등)나 분류를 모르는 기기는 주 엔티티 없이
    센서·스위치만 만든다.
    """
    if not is_controllable(features, spec, ATTR_POWER):
        return None
    if spec.category in FAN_CATEGORIES:
        return DOMAIN_FAN
    if spec.category == CATEGORY_HUMIDIFIER:
        return DOMAIN_HUMIDIFIER
    return None


def owned_attributes(features: dict[str, Any], spec: ModelSpec) -> set[str]:
    """주 엔티티가 직접 다루므로 select/switch 로 중복 노출하지 않을 속성."""
    domain = main_entity_domain(features, spec)
    if domain == DOMAIN_FAN:
        # 좌우 회전은 fan 의 oscillate 가 전담한다. 각도를 고를 수 있는 모델은
        # RotationRange 로, 아니면 RotationMode 로 제어하므로 둘 다 넘긴다.
        # (상하 회전 RotationModeVertical 은 별개라 스위치로 남는다)
        return set(FAN_OWNED_ATTRS) | {ATTR_ROTATION_MODE}
    if domain == DOMAIN_HUMIDIFIER:
        return set(HUMIDIFIER_OWNED_ATTRS)
    return set()


def select_attributes(features: dict[str, Any], spec: ModelSpec) -> list[str]:
    """select 로 만들 속성 — 값 목록을 아는 것만."""
    owned = owned_attributes(features, spec)
    return sorted(
        attribute
        for attribute, control in spec.controls().items()
        if attribute not in owned
        and control.is_enum
        and is_controllable(features, spec, attribute)
    )


def switch_attributes(features: dict[str, Any], spec: ModelSpec) -> list[str]:
    """switch 로 만들 속성 — 표의 토글 + feature 가 Toggle 이라고 알려주는 것."""
    owned = owned_attributes(features, spec)
    candidates = {
        attribute
        for attribute, control in spec.controls().items()
        if control.is_toggle
    }
    candidates |= {
        attribute
        for attribute, info in (features or {}).items()
        if isinstance(info, dict) and info.get("type") == "Toggle"
    }
    return sorted(
        attribute
        for attribute in candidates
        if attribute not in owned and is_controllable(features, spec, attribute)
    )


def unhandled_controls(features: dict[str, Any], spec: ModelSpec) -> list[str]:
    """제어할 수 있는데 어느 엔티티에도 안 붙은 속성. 진단용."""
    handled = (
        owned_attributes(features, spec)
        | set(select_attributes(features, spec))
        | set(switch_attributes(features, spec))
    )
    controllable = {
        attribute
        for attribute in (features or {})
        if is_controllable(features, spec, attribute)
    }
    return sorted(controllable - handled - MOMENTARY - {ATTR_OPERATION_MODE})


def unrepresentable_controls(features: dict[str, Any], spec: ModelSpec) -> list[str]:
    """기기는 제어할 수 있다는데 값 체계를 몰라 엔티티로 못 만든 속성.

    추측해서 명령을 보내지 않기 위해 일부러 비워 두는 것들이다.
    무엇이 빠졌는지 로그로 남겨 나중에 표를 보강할 수 있게 한다.
    """
    return sorted(
        attribute
        for attribute, info in (features or {}).items()
        if isinstance(info, dict)
        and "Issue" in str(info.get("property", ""))
        and attribute not in MOMENTARY
        and not is_controllable(features, spec, attribute)
    )
