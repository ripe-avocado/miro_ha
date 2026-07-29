#!/usr/bin/env python3
"""서버 UI 메타데이터에서 통합이 쓸 모델 표(models.json)를 뽑아낸다.

미로 앱은 화면을 서버 메타데이터로 그린다. 그 안에 모델별로 어떤 속성을
어떤 값으로 제어할 수 있는지가 전부 들어 있으므로, 값 범위를 추측하지 않고
그대로 옮겨 쓴다.

사용법:
    python3 tools/gen_models.py                    # reference/ 의 사본에서 생성
    python3 tools/gen_models.py --download         # 서버에서 최신본을 받아 생성

출력: custom_components/mirot/models.json
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_META = REPO / "reference" / "miro_meta.json"
DEFAULT_STRINGS = REPO / "reference" / "string_ko.json"
OUTPUT = REPO / "custom_components" / "mirot" / "models.json"

# 미로 기기 분류 → 이 통합이 만들 주 엔티티 종류
CATEGORY_BY_TYPE = {
    "SID_DEVICETYPE_FAN": "fan",
    "SID_DEVICETYPE_CIRCULATOR": "fan",
    "SID_DEVICETYPE_AIRPURIFIER": "air_purifier",
    "SID_DEVICETYPE_HUMIDIFIER": "humidifier",
    "SID_DEVICETYPE_DIFFUSER": "diffuser",
    "SID_DEVICETYPE_SENSOR": "sensor",
}
CATEGORY_BY_LABEL = {
    "선풍기": "fan",
    "서큘레이터": "fan",
    "공기청정기": "air_purifier",
    "가습기": "humidifier",
    "디퓨저": "diffuser",
    "센서": "sensor",
}

# 순간 동작(누르는 동안만 의미 있는 명령)이라 상태로 추적할 수 없는 속성.
MOMENTARY = {"RotationDirection", "RotationMagnitude"}


def download_meta() -> tuple[dict, dict]:
    """서버에서 최신 메타데이터 zip 을 받아 파싱한다."""
    sys.path.insert(0, str(REPO / "tools"))
    import requests  # noqa: PLC0415
    from miro_cli import APP_VERSION, MiroClient, load_state  # noqa: PLC0415

    client = MiroClient(load_state())
    version = client.call(
        "phone",
        "version_app",
        {"os": "android", "version": APP_VERSION, "timezone": "Asia/Seoul"},
        auth=False,
        opt_auth=True,
    )["version_ui_latest"]
    info = client.call(
        "phone",
        "get_ui",
        {"os": "android", "ui_version": version, "timezone": "Asia/Seoul"},
        auth=False,
        opt_auth=True,
    )
    print(f"ui_version {info['ui_version']} 내려받는 중…", file=sys.stderr)
    blob = requests.get(info["url"], timeout=180).content
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        meta = json.loads(zf.read("miro_meta.json"))
        strings = json.loads(zf.read("string_ko.json"))
    return meta, strings


def extract_controls(screen: dict) -> dict[str, dict]:
    """ui_controls 에서 (속성 → 허용 값) 을 뽑는다."""
    controls: dict[str, dict] = {}

    for control in screen.get("ui_controls", []):
        for mode in control.get("mode", []):
            for command in mode.get("control_commands") or []:
                for attr, value in command.items():
                    if attr in MOMENTARY:
                        continue
                    entry = controls.setdefault(attr, {"values": []})
                    if str(value) == "{##?##}":
                        # 슬라이더: 실제 범위는 value_range 에 있다
                        value_range = mode.get("value_range")
                        if value_range and len(value_range) == 2:
                            entry["min"] = int(value_range[0])
                            entry["max"] = int(value_range[1])
                    elif value not in entry["values"]:
                        entry["values"].append(value)

    result: dict[str, dict] = {}
    for attr, entry in controls.items():
        if "min" in entry:
            result[attr] = {"kind": "range", "min": entry["min"], "max": entry["max"]}
            continue
        values = entry["values"]
        if not values:
            continue
        as_text = {str(v) for v in values}
        if all(isinstance(v, bool) for v in values) or as_text == {"On", "Off"}:
            result[attr] = {"kind": "toggle"}
        elif len(values) == 1:
            # 값이 하나뿐인 명령. 예를 들어 NR08 은 조명 슬라이더 0 칸에서만
            # LightMode:Off 를 보내고, 켤 때는 Brightness 로 보낸다. 되돌리는
            # 값을 모르므로 토글로 만들지 않고 표시만 해 둔다.
            result[attr] = {"kind": "single", "values": [values[0]]}
        elif all(isinstance(v, int) for v in values):
            result[attr] = {"kind": "int_enum", "values": sorted(values)}
        else:
            result[attr] = {"kind": "enum", "values": [str(v) for v in values]}
    return result


def build(meta: dict, strings: dict) -> dict:
    resolve = lambda key: strings.get(key, key) if isinstance(key, str) else key
    models: dict[str, dict] = {}

    for code, device in meta["devices"].items():
        type_key = device.get("display_name_type")
        category = CATEGORY_BY_TYPE.get(type_key) or CATEGORY_BY_LABEL.get(
            resolve(type_key)
        )
        if category is None:
            print(f"  ! {code}: 분류를 알 수 없음 ({resolve(type_key)})", file=sys.stderr)
            category = "unknown"

        controls = extract_controls(device.get("screen_device_control", {}))
        models[code] = {
            "category": category,
            "type_label": resolve(type_key),
            "product": resolve(device.get("display_name_productname")),
            "model_number": resolve(device.get("display_name_modelnumber")),
            "controls": controls,
        }

    return {
        "ui_version": meta.get("version"),
        "_comment": (
            "tools/gen_models.py 로 서버 UI 메타데이터에서 생성. 직접 고치지 말 것."
        ),
        "models": dict(sorted(models.items())),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--download", action="store_true", help="서버에서 최신 메타데이터를 받아 생성"
    )
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    has_local = DEFAULT_META.exists() and DEFAULT_STRINGS.exists()
    if args.download or not has_local:
        if not args.download:
            print("reference/ 사본이 없어 서버에서 내려받습니다.", file=sys.stderr)
        meta, strings = download_meta()
    else:
        meta = json.loads(DEFAULT_META.read_text())
        strings = json.loads(DEFAULT_STRINGS.read_text())

    table = build(meta, strings)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(table, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )

    by_category: dict[str, int] = {}
    for model in table["models"].values():
        by_category[model["category"]] = by_category.get(model["category"], 0) + 1
    print(f"{args.output} 생성 — 모델 {len(table['models'])}종 {by_category}")


if __name__ == "__main__":
    main()
