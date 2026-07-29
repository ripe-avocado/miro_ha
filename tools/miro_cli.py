#!/usr/bin/env python3
"""
미로 스마트홈(miroT v2) API 검증용 CLI.

HA 통합을 만들기 전에, API가 실제로 동작하는지 눈으로 확인하기 위한 도구다.
앱을 분석해 확인한 프로토콜을 그대로 구현했다.

설치:
    pip3 install requests cryptography

로그인 (토큰을 ~/.miro_cli.json 에 저장):
    python3 tools/miro_cli.py login

기기 목록:
    python3 tools/miro_cli.py devices

상태 조회 (모든 필드를 그대로 덤프):
    python3 tools/miro_cli.py status
    python3 tools/miro_cli.py status --serial ABC123 --no-sync

제어 (실기기가 즉시 반응한다. 실행 전 확인 프롬프트가 뜬다):
    python3 tools/miro_cli.py power on
    python3 tools/miro_cli.py speed 50
    python3 tools/miro_cli.py mode Manual
    python3 tools/miro_cli.py rotate 90
    python3 tools/miro_cli.py timer 4
    python3 tools/miro_cli.py raw '{"Power":"On"}' '{"FanSpeed":30}'

확인 프롬프트를 건너뛰려면 -y 를 붙인다.
"""

import argparse
import base64
import getpass
import json
import os
import sys
import unicodedata
import uuid
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("requests 가 필요합니다:  pip3 install requests")

try:
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
except ImportError:
    sys.exit("cryptography 가 필요합니다:  pip3 install cryptography")


# --- 앱에서 추출한 상수 -----------------------------------------------------

BASE_URL = "https://l5n4phtmqg.execute-api.ap-northeast-2.amazonaws.com/prod/openapi/v2/api"
OPT_AUTHORIZATION = "b3a46c61-b3d6-404b-88b9-dafa4aae0e24"
AES_KEY = b"A09E2CEC38800C8586EF83C173B141D1"
AES_IV = b"3AEA3715C6BFDB51"
APP_VERSION = "2.1.25"
UI_VERSION = "0.1.28"

# 토큰 만료 오류 코드
ERR_TOKEN_EXPIRED = (-120, -121)

STATE_PATH = Path.home() / ".miro_cli.json"


# --- 저장소 ----------------------------------------------------------------


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    os.chmod(STATE_PATH, 0o600)


def client_id(state: dict) -> str:
    """앱의 android_id 자리에 쓰는 고정 식별자. 최초 1회 생성해 재사용한다."""
    if "client_id" not in state:
        state["client_id"] = uuid.uuid4().hex[:16]
        save_state(state)
    return state["client_id"]


# --- 요청 ------------------------------------------------------------------


def aes_encode(plain: str) -> str:
    padder = padding.PKCS7(128).padder()
    data = padder.update(plain.encode("utf-8")) + padder.finalize()
    enc = Cipher(algorithms.AES(AES_KEY), modes.CBC(AES_IV)).encryptor()
    return base64.b64encode(enc.update(data) + enc.finalize()).decode().strip()


class MiroError(Exception):
    def __init__(self, code, message, payload=None):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.payload = payload


class MiroClient:
    def __init__(self, state: dict, verbose: bool = False):
        self.state = state
        self.verbose = verbose
        self.session = requests.Session()

    def call(self, api_type, sub_type, body=None, *, auth=True, opt_auth=False):
        # 서버는 "body": null 을 거부한다 (etc/ping 으로 확인). 빈 객체로 보낸다.
        if body is None:
            body = {}
        envelope = {
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
                "phone": "miro-ha-cli",
                "device_id": aes_encode(client_id(self.state)),
            },
            "body": body,
        }
        if auth:
            token = self.state.get("access_token")
            if not token:
                sys.exit("로그인이 필요합니다:  python3 tools/miro_cli.py login")
            envelope["authorization"] = token
        if opt_auth:
            envelope["optAuthorization"] = OPT_AUTHORIZATION

        if self.verbose:
            print("--> POST", BASE_URL, file=sys.stderr)
            print(json.dumps(envelope, ensure_ascii=False, indent=2), file=sys.stderr)

        resp = self.session.post(BASE_URL, json=envelope, timeout=40)
        try:
            data = resp.json()
        except ValueError:
            raise MiroError(resp.status_code, f"JSON 아님: {resp.text[:400]}")

        if self.verbose:
            print("<--", resp.status_code, file=sys.stderr)
            print(json.dumps(data, ensure_ascii=False, indent=2), file=sys.stderr)

        payload = data.get("body", data)

        if resp.status_code != 200 or (
            isinstance(payload, dict) and payload.get("result") is False
        ):
            # 오류 객체는 최상위에 올 때도 있고 body 안에 올 때도 있다.
            err = data.get("error") or (payload or {}).get("error") or {}
            code = err.get("code", resp.status_code)
            try:
                code = int(code)
            except (TypeError, ValueError):
                pass
            msg = err.get("message") or (payload or {}).get("message") or resp.text[:200]
            if code in ERR_TOKEN_EXPIRED:
                msg += "  (토큰 만료 — 다시 login 하세요)"
            raise MiroError(code, msg, payload)

        return payload

    # --- 계정 ---

    def login(self, user_id: str, password: str) -> dict:
        body = {
            "id": user_id,
            "pass": unicodedata.normalize("NFKD", password),
            "auth_type": "android",
            "auth_param": client_id(self.state),
            "auth_description": {
                "model": "miro-ha-cli",
                "osVer": "13",
                "os": "android",
            },
        }
        return self.call("account", "login", body, auth=False)

    def refresh(self) -> dict:
        return self.call(
            "account", "update_access_token", {"auth_param": client_id(self.state)}
        )

    def device_list(self) -> dict:
        return self.call("account", "device_list", None)

    # --- 기기 ---

    def query(self, serials, sync=True) -> dict:
        body = {
            "devices": list(serials),
            "target": "all",
            "ui_version": UI_VERSION,
            "temperature_unit": "C",
        }
        if sync:
            body["sync"] = True
            body["timeout"] = 5000
        return self.call("device", "query", body)

    def execute(self, serial, commands, skip_if_desired=None) -> dict:
        body = {
            "devices": {serial: commands},
            "ui_version": UI_VERSION,
            "temperature_unit": "C",
        }
        if skip_if_desired is not None:
            body["skip_if_has_desired_state"] = skip_if_desired
        return self.call("device", "execute", body)


# --- 출력 도우미 -----------------------------------------------------------


def dump(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def describe(d) -> str:
    return (f"  {d.get('serialno','?'):10s} {d.get('model','?'):8s} "
            f"{d.get('type',''):12s} {d.get('nickname') or d.get('alias') or ''}")


def resolve_serials(state, args, client, *, single):
    """--serial 값을 실제 시리얼로 푼다. 시리얼 대신 별명 일부도 받는다.

    single=False 이고 --serial 이 없으면 등록된 기기 전부를 대상으로 한다.
    """
    devices = extract_devices(client.device_list())
    if not devices:
        sys.exit("등록된 기기가 없습니다.")

    wanted = getattr(args, "serial", None)
    if wanted:
        exact = [d for d in devices if d.get("serialno") == wanted]
        if exact:
            return [wanted]
        matched = [d for d in devices
                   if wanted.lower() in str(d.get("nickname") or
                                            d.get("alias") or "").lower()]
        if len(matched) == 1:
            return [matched[0]["serialno"]]
        if not matched:
            print(f"'{wanted}' 에 해당하는 기기가 없습니다. 등록된 기기:", file=sys.stderr)
        else:
            print(f"'{wanted}' 가 여러 기기에 걸립니다:", file=sys.stderr)
        for d in devices:
            print(describe(d), file=sys.stderr)
        sys.exit(1)

    if not single:
        return [d["serialno"] for d in devices]

    if len(devices) == 1:
        return [devices[0]["serialno"]]

    print("기기가 여러 대입니다. --serial 로 지정하세요 (시리얼 또는 별명 일부):",
          file=sys.stderr)
    for d in devices:
        print(describe(d), file=sys.stderr)
    sys.exit(1)


def extract_devices(payload) -> list:
    """device_list 응답에서 기기 배열을 최대한 관대하게 뽑아낸다.

    실제 응답 스키마가 아직 미확인이라 몇 가지 형태를 모두 받아준다.
    """
    if not isinstance(payload, dict):
        return []
    for key in ("devices", "device_list", "list"):
        node = payload.get(key)
        if isinstance(node, list):
            return [d for d in node if isinstance(d, dict)]
        if isinstance(node, dict):
            out = []
            for serial, val in node.items():
                item = dict(val) if isinstance(val, dict) else {"data": val}
                item.setdefault("serialno", serial)
                out.append(item)
            return out
    return []


def confirm(serial, commands, assume_yes) -> None:
    print(f"\n실기기에 명령을 보냅니다.  serial={serial}")
    print("  " + json.dumps(commands, ensure_ascii=False))
    if assume_yes:
        return
    if input("진행할까요? [y/N] ").strip().lower() not in ("y", "yes"):
        sys.exit("취소했습니다.")


def send(client, state, args, commands, skip_if_desired=None):
    serial = resolve_serials(state, args, client, single=True)[0]
    confirm(serial, commands, args.yes)
    dump(client.execute(serial, commands, skip_if_desired))


# --- 명령 ------------------------------------------------------------------


def main():
    p = argparse.ArgumentParser(
        description="미로 스마트홈 API 검증 CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("-v", "--verbose", action="store_true", help="요청/응답 전문 출력")

    # 서브명령 뒤에도 쓸 수 있도록 공통 인자로 뺀다.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--serial", help="대상 기기 — 시리얼 또는 별명 일부")
    common.add_argument("-y", "--yes", action="store_true",
                        help="제어 확인 프롬프트 건너뛰기")

    sub = p.add_subparsers(dest="cmd", required=True, parser_class=(
        lambda **kw: argparse.ArgumentParser(parents=[common], **kw)))

    sub.add_parser("login", help="로그인하고 토큰 저장")
    sub.add_parser("refresh", help="access_token 갱신")
    s = sub.add_parser("devices", help="기기 목록 조회")
    s.add_argument("--raw-json", action="store_true", help="응답 원문 그대로 출력")
    sub.add_parser("ping", help="서버 헬스체크 (인증 불필요)")
    sub.add_parser("version", help="앱/메타데이터 버전 조회 (인증 불필요)")

    s = sub.add_parser("status",
                       help="상태 조회 — 기본은 전체 기기, --serial 로 한 대만")
    s.add_argument("--no-sync", action="store_true",
                   help="기기 실시간 질의 없이 캐시된 상태만 조회")

    s = sub.add_parser("power", help="전원")
    s.add_argument("value", choices=["on", "off"])

    s = sub.add_parser("speed", help="풍량 1~100")
    s.add_argument("value", type=int)

    s = sub.add_parser("mode", help="운전 모드")
    s.add_argument("value", help="Manual / Auto / Natural / Sleep")

    s = sub.add_parser("rotate", help="회전 범위 (도)")
    s.add_argument("value", type=int, choices=[0, 30, 60, 90, 120])

    s = sub.add_parser("timer", help="타이머 (시간)")
    s.add_argument("value", type=int, choices=[0, 1, 2, 4, 8])

    s = sub.add_parser("aim", help="바람 방향 미세조정")
    s.add_argument("direction", choices=["Left", "Right"])
    s.add_argument("magnitude", type=int, nargs="?", default=5, help="5 또는 120")

    s = sub.add_parser("raw", help="임의 제어 명령 직접 전송")
    s.add_argument("commands", nargs="+", help='예: \'{"Power":"On"}\'')

    args = p.parse_args()
    state = load_state()
    client = MiroClient(state, verbose=args.verbose)

    try:
        run(args, state, client)
    except MiroError as e:
        sys.exit(f"실패: {e}")


def run(args, state, client):
    cmd = args.cmd

    if cmd == "ping":
        dump(client.call("etc", "ping", None, auth=False))

    elif cmd == "version":
        dump(client.call(
            "phone", "version_app",
            {"os": "android", "version": APP_VERSION, "timezone": "Asia/Seoul"},
            auth=False, opt_auth=True))

    elif cmd == "login":
        user_id = input("아이디: ").strip()
        password = getpass.getpass("비밀번호: ")
        res = client.login(user_id, password)
        token = res.get("access_token")
        if not token:
            dump(res)
            sys.exit("응답에 access_token 이 없습니다.")
        state["access_token"] = token
        if res.get("refresh_token"):
            state["refresh_token"] = res["refresh_token"]
        state["id"] = user_id
        save_state(state)
        print(f"로그인 성공. 토큰을 {STATE_PATH} 에 저장했습니다.")
        print(f"응답에 포함된 키: {sorted(res.keys())}")

    elif cmd == "refresh":
        res = client.refresh()
        if res.get("access_token"):
            state["access_token"] = res["access_token"]
            save_state(state)
            print("토큰을 갱신했습니다.")
        dump(res)

    elif cmd == "devices":
        res = client.device_list()
        if args.raw_json:
            dump(res)
        else:
            for d in extract_devices(res):
                print(describe(d))
                feats = d.get("feature") or {}
                if feats:
                    ctl = [k for k, v in feats.items()
                           if "Issue" in str(v.get("property"))]
                    ro = [k for k, v in feats.items()
                          if v.get("property") == "Report"]
                    print(f"    제어 가능: {', '.join(sorted(ctl))}")
                    print(f"    읽기 전용: {', '.join(sorted(ro))}")

    elif cmd == "status":
        serials = resolve_serials(state, args, client, single=False)
        dump(client.query(serials, sync=not args.no_sync))

    elif cmd == "power":
        send(client, state, args, [{"Power": args.value.capitalize()}])

    elif cmd == "speed":
        if not 1 <= args.value <= 100:
            sys.exit("풍량은 1~100 입니다.")
        send(client, state, args, [{"FanSpeed": args.value}])

    elif cmd == "mode":
        send(client, state, args, [{"OperationMode": args.value}])

    elif cmd == "rotate":
        send(client, state, args, [{"RotationRange": args.value}])

    elif cmd == "timer":
        send(client, state, args, [{"Timer": args.value}])

    elif cmd == "aim":
        send(client, state, args,
             [{"RotationDirection": args.direction,
               "RotationMagnitude": args.magnitude}],
             skip_if_desired=False)

    elif cmd == "raw":
        try:
            commands = [json.loads(c) for c in args.commands]
        except json.JSONDecodeError as e:
            sys.exit(f"JSON 파싱 실패: {e}")
        send(client, state, args, commands)


if __name__ == "__main__":
    main()
