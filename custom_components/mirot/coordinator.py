"""상태 폴링 코디네이터."""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import capabilities
from .api import MiroAuthError, MiroClient, MiroError
from .const import (
    ATTR_FAN_SPEED,
    ATTR_POWER,
    ATTR_ROTATION_MODE,
    ATTR_ROTATION_RANGE,
    CONF_ACCESS_TOKEN,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    IDLE_SCAN_INTERVAL,
)
from .models import ModelSpec, get_model, preload

_LOGGER = logging.getLogger(__name__)

# 상태 조회가 몇 번 연속 실패해야 엔티티를 '사용 안 됨' 으로 볼 것인가.
#
# sync 조회는 기기가 5초 안에 응답하기를 기다린다. 무선 기기라 한 주기쯤
# 놓치는 일이 드물지 않고, 다음 주기에는 대개 성공한다. 그때마다 엔티티를
# 전부 떨어뜨리면 화면이 깜빡이고 가용성 조건을 건 자동화가 오작동한다.
MAX_CONSECUTIVE_FAILURES = 3

# 제어 직후 서버 캐시가 새 값으로 채워지기까지 걸리는 시간(초).
#
# 조회는 기기에 보고를 지시하고 '직전' 값을 돌려준다. 새 값은 약 1초 뒤에야
# 캐시에 들어가므로, 명령 직후 곧바로 물으면 방금 바꾼 값이 아니라 옛 값이
# 온다. 화면이 되돌아간 것처럼 보이므로 조금 기다렸다 확인한다.
CONFIRM_DELAY = 1.5


def coupled_changes(command: dict[str, Any]) -> dict[str, Any]:
    """명령 하나가 기기 안에서 함께 바꾸는 다른 속성.

    화면을 먼저 맞춰 둘 때 이걸 빠뜨리면 오히려 틀린 값을 보여준다.
    예를 들어 꺼져 있는 선풍기에 풍량만 반영하면 전원이 Off 인 채라
    풍량이 0%로 표시된다. 둘 다 실기기에서 확인된 동작이다.
    """
    extra: dict[str, Any] = {}

    if ATTR_FAN_SPEED in command:
        # 풍량을 보내면 꺼져 있던 기기도 함께 켜진다.
        extra[ATTR_POWER] = "On"

    if ATTR_ROTATION_RANGE in command:
        # 회전 범위가 0이 아니면 회전이 켜지고, 0이면 꺼진다.
        value = command[ATTR_ROTATION_RANGE]
        if isinstance(value, (int, float)):
            extra[ATTR_ROTATION_MODE] = "On" if value > 0 else "Off"

    return extra


class MiroCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """기기 상태를 주기적으로 가져온다."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: MiroClient
    ) -> None:
        interval = entry.options.get(
            CONF_SCAN_INTERVAL, entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        )
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=interval),
            config_entry=entry,
        )
        self.client = client
        self.entry = entry
        # 켜져 있을 때 주기(설정값)와 다 꺼져 있을 때 주기.
        # 설정 주기가 이미 더 길면 그대로 쓴다. 느리게 하려는 게 목적이지
        # 사용자가 정한 것보다 빠르게 돌리려는 게 아니다.
        self._active_interval = interval
        self._idle_interval = max(interval, IDLE_SCAN_INTERVAL)
        # serialno -> device_list 의 기기 정보(model, nickname, feature 등)
        self.devices: dict[str, dict[str, Any]] = {}
        # serialno -> 모델 정의
        self.specs: dict[str, ModelSpec] = {}
        # 연속 실패 횟수. 성공하면 0으로 돌아간다.
        self._failures = 0
        # 제어 후 확인 조회 예약. 연달아 조작하면 마지막 것 하나만 남는다.
        self._confirm_unsub: CALLBACK_TYPE | None = None
        # serial -> (유효 시각, 낙관적으로 넣어 둔 값)
        self._optimistic: dict[str, tuple[float, dict[str, Any]]] = {}

    async def async_load_devices(self) -> None:
        """등록된 기기 목록을 읽어온다. 셋업 시 1회만 호출한다."""
        # 모델 표는 파일에서 읽으므로 이벤트 루프 밖에서 미리 채워 둔다.
        await self.hass.async_add_executor_job(preload)

        try:
            self.devices = await self.client.async_get_devices()
        except MiroAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except MiroError as err:
            raise ConfigEntryNotReady(f"기기 목록 조회 실패: {err}") from err

        self.specs = {
            serial: get_model(info.get("model"))
            for serial, info in self.devices.items()
        }
        for serial, spec in self.specs.items():
            info = self.devices[serial]
            _LOGGER.debug(
                "%s: %s (%s) -> %s",
                serial,
                info.get("model"),
                info.get("nickname"),
                spec.category,
            )
            skipped = capabilities.unrepresentable_controls(
                self.features(serial), spec
            )
            if skipped:
                # 추측해서 명령을 보내지 않으려고 일부러 비워 둔 것들이다.
                _LOGGER.info(
                    "%s (%s): 값 체계를 몰라 제어 엔티티를 만들지 않은 항목 %s",
                    info.get("nickname") or serial,
                    info.get("model"),
                    ", ".join(skipped),
                )
        self._persist_token()

    # --- 기능 판정 ---------------------------------------------------------
    #
    # 무엇을 만들지는 두 정보를 겹쳐서 정한다.
    #   1) 기기가 실제로 보고하는 feature 맵 — 이 기기에 그 기능이 있는가
    #   2) models.json 의 제어 정의        — 그 기능을 어떤 값으로 제어하는가
    # 둘 중 하나라도 없으면 추측하지 않고 만들지 않는다.

    def spec(self, serial: str) -> ModelSpec:
        return self.specs.get(serial) or get_model(None)

    def feature(self, serial: str, attribute: str) -> dict[str, Any] | None:
        features = self.devices.get(serial, {}).get("feature") or {}
        value = features.get(attribute)
        return value if isinstance(value, dict) else None

    def features(self, serial: str) -> dict[str, Any]:
        return self.devices.get(serial, {}).get("feature") or {}

    def is_reported(self, serial: str, attribute: str) -> bool:
        return capabilities.is_reported(self.features(serial), attribute)

    def is_controllable(self, serial: str, attribute: str) -> bool:
        return capabilities.is_controllable(
            self.features(serial), self.spec(serial), attribute
        )

    def main_entity_domain(self, serial: str) -> str | None:
        return capabilities.main_entity_domain(self.features(serial), self.spec(serial))

    def select_attributes(self, serial: str) -> list[str]:
        return capabilities.select_attributes(self.features(serial), self.spec(serial))

    def switch_attributes(self, serial: str) -> list[str]:
        return capabilities.switch_attributes(self.features(serial), self.spec(serial))

    # --- 폴링 -------------------------------------------------------------

    def _persist_token(self) -> None:
        """갱신된 토큰을 config entry 에 다시 저장한다."""
        token = self.client.access_token
        if token and token != self.entry.data.get(CONF_ACCESS_TOKEN):
            self.hass.config_entries.async_update_entry(
                self.entry, data={**self.entry.data, CONF_ACCESS_TOKEN: token}
            )

    async def _async_query(self, serials: list[str]) -> dict[str, dict[str, Any]]:
        """상태를 읽는다. sync 가 실패하면 서버 캐시라도 읽어 온다.

        sync 조회는 기기가 응답할 때까지 기다리므로 기기 사정에 따라 실패한다.
        그때도 서버 캐시에는 직전 값이 남아 있으니, 그걸 읽으면 조금 낡았을 뿐
        멀쩡한 상태를 돌려줄 수 있다.
        """
        try:
            return await self.client.async_query(serials, sync=True)
        except MiroAuthError:
            raise
        except MiroError as err:
            _LOGGER.debug("sync 조회 실패, 서버 캐시 조회로 대체한다: %s", err)
            return await self.client.async_query(serials, sync=False)

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        if not self.devices:
            return {}

        try:
            states = await self._async_query(list(self.devices))
        except MiroAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except MiroError as err:
            self._failures += 1
            if self.data and self._failures < MAX_CONSECUTIVE_FAILURES:
                # 일시적인 실패다. 직전 값을 유지해 엔티티를 살려 둔다.
                _LOGGER.debug(
                    "상태 조회 %d회 연속 실패, 직전 값을 유지한다: %s",
                    self._failures,
                    err,
                )
                return self.data
            raise UpdateFailed(f"상태 조회 실패: {err}") from err

        self._failures = 0
        self._persist_token()
        self._merge_optimistic(states)
        self._apply_interval(states)
        return states

    def _apply_interval(self, states: dict[str, dict[str, Any]]) -> None:
        """기기가 전부 꺼져 있으면 주기를 늦춘다.

        코디네이터는 매 갱신이 끝난 뒤 다음 실행을 예약하므로, 여기서 값을
        바꿔 두면 다음 주기부터 적용된다.
        """
        powers = [
            state.get(ATTR_POWER)
            for state in states.values()
            if state.get(ATTR_POWER) is not None
        ]
        # 전원을 아무도 보고하지 않으면 켜졌는지 알 수 없다. 빠른 쪽으로 둔다.
        active = not powers or any(power == "On" for power in powers)

        target = timedelta(
            seconds=self._active_interval if active else self._idle_interval
        )
        if self.update_interval != target:
            _LOGGER.debug(
                "폴링 주기를 %d초로 바꾼다 (켜진 기기 %s)",
                int(target.total_seconds()),
                "있음" if active else "없음",
            )
            self.update_interval = target

    # --- 제어 -------------------------------------------------------------

    def _apply_optimistic(self, serial: str, command: dict[str, Any]) -> None:
        """명령이 먹었다고 보고 화면을 먼저 바꾼다.

        기기는 명령을 받은 즉시 움직이는데 서버 캐시는 1초쯤 늦게 따라온다.
        그 사이를 비워 두면 토글을 눌러도 몇 초간 아무 반응이 없어 보인다.
        여기서 넣은 값은 곧이어 오는 실제 조회 결과로 덮인다.
        """
        values = {**command, **coupled_changes(command)}

        # 확인 조회가 오기 전에 정기 폴링이 끼어들면 아직 옛 값을 들고 온다.
        # 그게 화면을 되돌리지 않도록, 잠깐은 이 값이 이기게 해 둔다.
        expiry = time.monotonic() + CONFIRM_DELAY
        previous = self._optimistic.get(serial)
        pending = dict(previous[1]) if previous else {}
        pending.update(values)
        self._optimistic[serial] = (expiry, pending)

        state = (self.data or {}).get(serial)
        if state is None:
            return
        state.update(values)
        self.async_update_listeners()

    def _merge_optimistic(self, states: dict[str, dict[str, Any]]) -> None:
        """아직 유효한 낙관적 값을 조회 결과 위에 덮는다."""
        now = time.monotonic()
        for serial, (expiry, values) in list(self._optimistic.items()):
            if now >= expiry:
                del self._optimistic[serial]
                continue
            state = states.get(serial)
            if state is not None:
                state.update(values)

    @callback
    def _schedule_confirm(self) -> None:
        """서버 캐시가 채워질 즈음 실제 값을 한 번 읽어 확정한다."""
        if self._confirm_unsub is not None:
            self._confirm_unsub()
        self._confirm_unsub = async_call_later(
            self.hass, CONFIRM_DELAY, self._async_confirm
        )

    async def _async_confirm(self, _now: Any) -> None:
        self._confirm_unsub = None
        await self.async_request_refresh()

    async def async_shutdown(self) -> None:
        if self._confirm_unsub is not None:
            self._confirm_unsub()
            self._confirm_unsub = None
        await super().async_shutdown()

    async def async_send(self, serial: str, command: dict[str, Any]) -> None:
        """명령 하나를 보낸다."""
        try:
            await self.client.async_execute(serial, [command])
        except MiroAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except MiroError as err:
            # 서비스 호출 실패는 UI에 오류로 보여야 한다.
            raise HomeAssistantError(f"제어 실패: {err}") from err

        self._persist_token()
        self._apply_optimistic(serial, command)
        self._schedule_confirm()
