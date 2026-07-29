"""미로(miroT v2) 클라우드 API 클라이언트.

요청 봉투를 AES-256-CBC 로 암호화해 단일 엔드포인트에 POST 한다.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import unicodedata
import uuid
from typing import Any

import aiohttp
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .const import (
    AES_IV,
    AES_KEY,
    API_URL,
    APP_VERSION,
    AUTH_ERROR_CODES,
    OPT_AUTHORIZATION,
    UI_VERSION,
)

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)


class MiroError(Exception):
    """미로 API 일반 오류."""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class MiroAuthError(MiroError):
    """인증 실패 — 토큰 만료 또는 자격증명 오류."""


def _aes_encode(plain: str) -> str:
    """앱과 동일한 방식으로 기기 식별자를 암호화한다."""
    padder = padding.PKCS7(128).padder()
    data = padder.update(plain.encode()) + padder.finalize()
    encryptor = Cipher(algorithms.AES(AES_KEY), modes.CBC(AES_IV)).encryptor()
    return base64.b64encode(encryptor.update(data) + encryptor.finalize()).decode().strip()


class MiroClient:
    """미로 클라우드와 통신한다. 토큰 만료 시 스스로 갱신한다."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        client_id: str,
        access_token: str | None = None,
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._client_id = client_id
        self._access_token = access_token
        self._auth_lock = asyncio.Lock()

    @property
    def access_token(self) -> str | None:
        """현재 토큰. 갱신되면 config entry 에 다시 저장해야 한다."""
        return self._access_token

    # --- 저수준 호출 -------------------------------------------------------

    def _envelope(
        self,
        api_type: str,
        sub_type: str,
        body: dict[str, Any] | None,
        *,
        auth: bool,
        opt_auth: bool,
    ) -> dict[str, Any]:
        envelope: dict[str, Any] = {
            "uuid": str(uuid.uuid4()),
            "type": api_type,
            "sub_type": sub_type,
            "version": "v2",
            "opt": {
                "lang": "ko",
                "os": "android",
                "osVer": "13",
                "appVer": APP_VERSION,
                "timezone": "Asia/Seoul",
                "phone": "home-assistant",
                "device_id": _aes_encode(self._client_id),
            },
            # 서버는 body: null 을 거부한다.
            "body": body if body is not None else {},
        }
        if auth and self._access_token:
            envelope["authorization"] = self._access_token
        if opt_auth:
            envelope["optAuthorization"] = OPT_AUTHORIZATION
        return envelope

    async def _post(
        self,
        api_type: str,
        sub_type: str,
        body: dict[str, Any] | None,
        *,
        auth: bool = True,
        opt_auth: bool = False,
    ) -> dict[str, Any]:
        payload = self._envelope(api_type, sub_type, body, auth=auth, opt_auth=opt_auth)
        try:
            async with self._session.post(
                API_URL, json=payload, timeout=REQUEST_TIMEOUT
            ) as resp:
                data = await resp.json(content_type=None)
                status = resp.status
        except aiohttp.ClientError as err:
            raise MiroError(f"통신 실패: {err}") from err
        except asyncio.TimeoutError as err:
            raise MiroError("서버 응답 시간 초과") from err

        if not isinstance(data, dict):
            raise MiroError(f"예상치 못한 응답: {data!r}")

        result = data.get("body") if isinstance(data.get("body"), dict) else data

        failed = status != 200 or result.get("result") is False
        if not failed:
            return result

        # 오류 객체는 최상위에 올 때도, body 안에 올 때도 있다.
        error = data.get("error") or result.get("error") or {}
        code: int | None
        try:
            code = int(error.get("code"))
        except (TypeError, ValueError):
            code = None
        message = error.get("message") or result.get("message") or f"HTTP {status}"

        if code in AUTH_ERROR_CODES:
            raise MiroAuthError(message, code)
        raise MiroError(message, code)

    # --- 인증 -------------------------------------------------------------

    async def async_login(self) -> str:
        """아이디/비밀번호로 로그인하고 새 토큰을 반환한다."""
        body = {
            "id": self._username,
            "pass": unicodedata.normalize("NFKD", self._password),
            "auth_type": "android",
            "auth_param": self._client_id,
            "auth_description": {
                "model": "home-assistant",
                "osVer": "13",
                "os": "android",
            },
        }
        try:
            result = await self._post("account", "login", body, auth=False)
        except MiroError as err:
            # 로그인 자체가 실패하면 자격증명 문제로 본다.
            raise MiroAuthError(str(err), err.code) from err

        token = result.get("access_token")
        if not token:
            raise MiroAuthError("로그인 응답에 access_token 이 없습니다")
        self._access_token = token
        return token

    async def _async_reauth(self) -> None:
        """토큰 갱신을 시도하고, 안 되면 재로그인한다."""
        if self._access_token:
            try:
                result = await self._post(
                    "account",
                    "update_access_token",
                    {"auth_param": self._client_id},
                )
            except MiroError as err:
                _LOGGER.debug("토큰 갱신 실패, 재로그인 시도: %s", err)
            else:
                token = result.get("access_token")
                if token:
                    self._access_token = token
                    return
        await self.async_login()

    async def _call(
        self, api_type: str, sub_type: str, body: dict[str, Any] | None
    ) -> dict[str, Any]:
        """인증이 필요한 호출. 토큰이 만료됐으면 한 번만 갱신 후 재시도한다."""
        if not self._access_token:
            async with self._auth_lock:
                if not self._access_token:
                    await self.async_login()

        try:
            return await self._post(api_type, sub_type, body)
        except MiroAuthError:
            async with self._auth_lock:
                await self._async_reauth()
            return await self._post(api_type, sub_type, body)

    # --- 기능 -------------------------------------------------------------

    async def async_get_devices(self) -> dict[str, dict[str, Any]]:
        """등록된 기기 목록. serialno 를 키로 하는 맵."""
        result = await self._call("account", "device_list", None)
        devices = result.get("devices")
        return devices if isinstance(devices, dict) else {}

    async def async_query(
        self, serials: list[str], *, sync: bool = True
    ) -> dict[str, dict[str, Any]]:
        """상태 조회.

        sync=True 는 기기에 "상태를 보고하라"고 지시만 하고, 응답으로는 갱신 전
        값을 돌려준다. 새 값은 약 1초 뒤 캐시에 반영되므로, 매 주기 sync 로
        호출하면 항상 폴링 간격 이하만큼만 낡은 값을 받게 된다.
        """
        body: dict[str, Any] = {
            "devices": serials,
            "target": "all",
            "ui_version": UI_VERSION,
            "temperature_unit": "C",
        }
        if sync:
            body["sync"] = True
            body["timeout"] = 5000
        result = await self._call("device", "query", body)
        devices = result.get("devices")
        return devices if isinstance(devices, dict) else {}

    async def async_execute(
        self, serial: str, commands: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """제어 명령 전송.

        명령 배열은 순차 적용되므로, 상태 추적을 위해 호출부에서 한 번에
        하나씩 보내는 것을 권한다.
        """
        body = {
            "devices": {serial: commands},
            "ui_version": UI_VERSION,
            "temperature_unit": "C",
        }
        return await self._call("device", "execute", body)
