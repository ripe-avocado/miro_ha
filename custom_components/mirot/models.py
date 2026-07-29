"""모델별 제어 정의 표.

값 범위는 모델마다 다르다. 예를 들어 타이머는 선풍기가 0/1/2/4/8시간,
서큘레이터가 0~15시간이고, 풍량은 1~100 인 모델과 1~3단인 모델이 섞여 있다.
그래서 상수로 박지 않고 서버 UI 메타데이터에서 뽑아둔 표를 읽는다.

표 생성: tools/gen_models.py
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from .const import CATEGORY_UNKNOWN

_LOGGER = logging.getLogger(__name__)

_TABLE_PATH = Path(__file__).parent / "models.json"


@lru_cache(maxsize=1)
def _table() -> dict[str, Any]:
    try:
        return json.loads(_TABLE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _LOGGER.exception("models.json 을 읽지 못했습니다")
        return {"models": {}}


class ControlSpec:
    """어떤 속성을 어떤 값으로 제어할 수 있는지."""

    def __init__(self, attribute: str, spec: dict[str, Any]) -> None:
        self.attribute = attribute
        self.kind: str = spec.get("kind", "")
        self.values: list[Any] = spec.get("values", [])
        self.min: int | None = spec.get("min")
        self.max: int | None = spec.get("max")

    @property
    def is_toggle(self) -> bool:
        return self.kind == "toggle"

    @property
    def is_range(self) -> bool:
        return self.kind == "range"

    @property
    def is_enum(self) -> bool:
        return self.kind in ("enum", "int_enum")

    @property
    def is_usable(self) -> bool:
        """엔티티로 만들 수 있는 제어인가.

        kind="single" 은 값이 하나뿐이라 되돌릴 방법을 모르는 명령이다.
        추측해서 만들지 않는다.
        """
        return self.kind in ("toggle", "enum", "int_enum", "range")

    @property
    def options(self) -> list[str]:
        """select 엔티티에 노출할 문자열 목록."""
        return [str(v) for v in self.values]

    def to_command_value(self, option: str) -> Any:
        """select 에서 고른 문자열을 API 가 받는 타입으로 되돌린다."""
        if self.kind == "int_enum":
            return int(option)
        return option

    def __repr__(self) -> str:
        return f"<ControlSpec {self.attribute} {self.kind} {self.values or (self.min, self.max)}>"


class ModelSpec:
    """모델 하나의 제어 정의."""

    def __init__(self, code: str, spec: dict[str, Any] | None) -> None:
        self.code = code
        spec = spec or {}
        self.category: str = spec.get("category", CATEGORY_UNKNOWN)
        self.product: str | None = spec.get("product")
        self.type_label: str | None = spec.get("type_label")
        self.known = bool(spec)
        self._controls = {
            attr: ControlSpec(attr, value)
            for attr, value in (spec.get("controls") or {}).items()
        }

    def control(self, attribute: str) -> ControlSpec | None:
        return self._controls.get(attribute)

    def controls(self) -> dict[str, ControlSpec]:
        return self._controls

    def __repr__(self) -> str:
        return f"<ModelSpec {self.code} {self.category}>"


def get_model(code: str | None) -> ModelSpec:
    """모델 코드로 정의를 찾는다. 모르는 모델이면 빈 정의를 돌려준다.

    빈 정의를 받으면 각 플랫폼은 값 범위를 추측하지 않고, 기기가 보고하는
    feature 맵만으로 안전하게 만들 수 있는 엔티티(On/Off, 센서)만 만든다.
    """
    models = _table().get("models", {})
    spec = models.get(code) if code else None
    if code and spec is None:
        _LOGGER.info(
            "모델 %s 는 표에 없습니다. On/Off 와 센서만 노출합니다 "
            "(tools/gen_models.py 로 표를 갱신할 수 있습니다)",
            code,
        )
    return ModelSpec(code or "", spec)


def table_ui_version() -> str | None:
    return _table().get("ui_version")
