"""미로 스마트홈 통합 상수."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "mirot"

CONF_CLIENT_ID: Final = "client_id"
CONF_ACCESS_TOKEN: Final = "access_token"
CONF_SCAN_INTERVAL: Final = "scan_interval"

DEFAULT_SCAN_INTERVAL: Final = 5
MIN_SCAN_INTERVAL: Final = 5
MAX_SCAN_INTERVAL: Final = 300

# 기기가 전부 꺼져 있을 때 쓰는 주기(초).
#
# 꺼진 기기를 5초마다 확인해 봐야 나오는 게 없다. 서버에 보내는 요청만 늘어난다.
# 하나라도 켜지면 곧바로 설정된 주기로 돌아간다.
IDLE_SCAN_INTERVAL: Final = 20

# `sync` 를 걸어 기기를 깨우는 간격(초).
#
# 실측(2026-07-29): 기기는 전원·풍량·회전이 바뀌면 몇 초 안에 스스로 상태를
# 올린다. 그래서 평소에는 `sync` 없이 서버 캐시만 읽어도 조작이 다 잡힌다.
# 다만 변화가 없으면 아무것도 올리지 않으므로 온습도·배터리는 갱신되지 않는다.
# 그 값들을 새로 받고, 혹시 놓친 변화가 있으면 바로잡는 용도로 가끔만 건다.
SYNC_INTERVAL: Final = 60

MANUFACTURER: Final = "MIRO"

# --- API 상수 (앱에 하드코딩된 값) ------------------------------------------

API_URL: Final = (
    "https://l5n4phtmqg.execute-api.ap-northeast-2.amazonaws.com/prod/openapi/v2/api"
)
OPT_AUTHORIZATION: Final = "b3a46c61-b3d6-404b-88b9-dafa4aae0e24"
AES_KEY: Final = b"A09E2CEC38800C8586EF83C173B141D1"
AES_IV: Final = b"3AEA3715C6BFDB51"
APP_VERSION: Final = "2.1.25"
UI_VERSION: Final = "0.1.28"

# 토큰 만료를 뜻하는 서버 오류 코드
AUTH_ERROR_CODES: Final = (-120, -121)

# --- 기기 분류 --------------------------------------------------------------
# models.json 의 category 값. 어떤 주 엔티티를 만들지 결정한다.

CATEGORY_FAN: Final = "fan"
CATEGORY_AIR_PURIFIER: Final = "air_purifier"
CATEGORY_HUMIDIFIER: Final = "humidifier"
CATEGORY_DIFFUSER: Final = "diffuser"
CATEGORY_SENSOR: Final = "sensor"
CATEGORY_UNKNOWN: Final = "unknown"

# HA fan 플랫폼으로 표현하는 분류. 공기청정기도 HA 관례상 fan 이다.
FAN_CATEGORIES: Final = (CATEGORY_FAN, CATEGORY_AIR_PURIFIER)

# --- 속성 이름 --------------------------------------------------------------

ATTR_POWER: Final = "Power"
ATTR_FAN_SPEED: Final = "FanSpeed"
ATTR_OPERATION_MODE: Final = "OperationMode"
ATTR_ROTATION_RANGE: Final = "RotationRange"
ATTR_ROTATION_MODE: Final = "RotationMode"
ATTR_TIMER: Final = "Timer"
ATTR_TIMER_REMAIN: Final = "TimerRemainMinute"
ATTR_CONNECTIVITY: Final = "Connectivity"
ATTR_CURRENT_ANGLE: Final = "CurrentAngle"
ATTR_HUMIDITY: Final = "Humidity"

# 오프라인 기기는 센서값 자리에 이 문자열이 온다.
UNAVAILABLE_VALUE: Final = "--"

# 수동 조작을 뜻하는 운전 모드. 이 모드에서만 풍량을 직접 바꿀 수 있다.
PRESET_MANUAL: Final = "Manual"

# 회전을 켤 때 쓸 기본 각도. 모델이 지원하는 값 중에서 고른다.
PREFERRED_ROTATION_RANGE: Final = 90

# 주 엔티티가 직접 다루는 속성. select/switch 로 중복 노출하지 않는다.
FAN_OWNED_ATTRS: Final = (ATTR_POWER, ATTR_FAN_SPEED, ATTR_OPERATION_MODE)
HUMIDIFIER_OWNED_ATTRS: Final = (ATTR_POWER, ATTR_OPERATION_MODE)
