"""새 일감 양식의 후보 (`kb.py meta`). Jira를 읽기만 한다. 설계 D-NV11.

하위 명령:
  create PROJECT [--refresh]                    유형과 유형별 항목(필수 여부·고를 값)
  users (--project P | --issue KEY) [--query 글]   담당으로 지정할 수 있는 사용자(나 포함)

`create`는 `.state/createmeta-<프로젝트>.json`에 하루 캐시한다. 오래됐으면 그대로 내고 `stale: true`(새로 받기는
부르는 쪽이 --refresh로 한다). 새로 받지 못하면 전의 캐시를 `stale: true`와 `error`로 내고 캐시는 덮지 않는다.
캐시도 없고 연결도 안 되면 `fallback: true`(유형만, 항목 없음). 다른 base_url의 캐시나 깨진 캐시는 쓰지 않는다.
사용자 목록은 저장하지 않는다(개인 정보).

항목 종류(kind)는 Jira의 schema로 정한다. 값 변환(`convert`)도 여기서 하고 `act create --field`가 쓴다.
편집기는 id·글만 보낸다: option → "id", options → ["id"], user → "사용자명", labels → ["글"], 그 밖 → "글".
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
from collections.abc import Iterable, Mapping
from typing import Any

from lib import config, core
from lib import jira_client as jc
from lib.config import Config
from lib.jira_client import JiraClient

MAX_AGE = dt.timedelta(hours=24)
FALLBACK_TYPES = ("Task", "Bug", "Story")
# 양식이 따로 다루는 항목(--field로 덮지 못한다)과 만들 때 쓰지 않는 항목
RESERVED = frozenset({"project", "issuetype", "summary", "description"})
HIDDEN = RESERVED | {"attachment", "issuelinks"}
EPIC_LINK = "com.pyxis.greenhopper.jira:gh-epic-link"
KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]+-[1-9][0-9]*$")
OPTION_TYPES = frozenset({"option", "priority", "version", "component", "resolution", "securitylevel"})
ARRAY_OPTION_TYPES = frozenset({"option", "version", "component"})
UNSUPPORTED = "unsupported"


# ---------------------------------------------------------------- 항목 종류


def kind_of(schema: Any) -> str:
    schema = schema if isinstance(schema, Mapping) else {}
    kind, items = str(schema.get("type") or ""), str(schema.get("items") or "")
    custom, system = str(schema.get("custom") or ""), str(schema.get("system") or "")
    if custom == EPIC_LINK:
        return "epic"
    if kind == "string":
        return "textarea" if custom.endswith(":textarea") or system == "environment" else "text"
    if kind in ("number", "date", "datetime", "user"):
        return kind
    if kind in OPTION_TYPES:
        return "option"
    if kind == "array":
        if items in ARRAY_OPTION_TYPES:
            return "options"
        if items == "user":
            return "users"
        if items == "string" and (system == "labels" or custom.endswith(":labels")):
            return "labels"
    return UNSUPPORTED


def _allowed(values: Any) -> list[dict[str, str]]:
    out = []
    for item in values if isinstance(values, list) else ():
        if isinstance(item, Mapping) and item.get("id") is not None and not item.get("disabled") and not item.get("archived"):
            name = item.get("name") or item.get("value") or item["id"]
            out.append({"id": str(item["id"]), "name": str(name)})
    return out


def _field(raw: Mapping[str, Any]) -> dict[str, Any]:
    kind = kind_of(raw.get("schema"))
    allowed = _allowed(raw.get("allowedValues"))
    if kind in ("option", "options") and not allowed:
        kind = UNSUPPORTED  # 고를 값이 없다
    return {
        "id": str(raw.get("fieldId") or raw.get("key") or ""),
        "name": str(raw.get("name") or raw.get("fieldId") or ""),
        "required": raw.get("required") is True,
        "has_default": raw.get("hasDefaultValue") is True,
        "kind": kind,
        "allowed": allowed if kind in ("option", "options") else [],
    }


def build_types(raw: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    types = []
    for item in raw:
        fields = [f for f in item.get("fields") or () if isinstance(f, Mapping)]
        by_id = {str(f.get("fieldId") or f.get("key") or ""): f for f in fields}
        desc = by_id.get("description")
        types.append({
            "id": str(item.get("id") or ""),
            "name": str(item.get("name") or ""),
            "description": {"available": desc is not None, "required": bool(desc and desc.get("required") is True and desc.get("hasDefaultValue") is not True)},
            "fields": [_field(f) for fid, f in by_id.items() if fid and fid not in HIDDEN],
        })  # fmt: skip
    return types


# ---------------------------------------------------------------- 캐시


def _state_name(project: str) -> str:
    return f"createmeta-{project}"


def _base_url(cfg: Config) -> str:
    return str(cfg.get("jira.base_url") or "").rstrip("/")


def load_cache(cfg: Config, project: str) -> dict[str, Any] | None:
    """쓸 수 있는 캐시. 없거나 깨졌거나 다른 서버의 것이면 None."""
    try:
        data = core.load_state(cfg.root, _state_name(project))
        dt.datetime.fromisoformat(data["fetched_at"])
        if data["base_url"] != _base_url(cfg) or not data["issuetypes"]:
            return None
        for item in data["issuetypes"]:
            for field in item["fields"]:
                field["id"], field["kind"], field["allowed"], field["required"], field["has_default"], field["name"]
            item["name"], item["description"]["required"]
    except (core.KbError, OSError, ValueError, KeyError, TypeError):
        return None
    return data


def _is_stale(cfg: Config, cache: Mapping[str, Any]) -> bool:
    fetched = dt.datetime.fromisoformat(cache["fetched_at"])
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=cfg.tz)
    age = core.now(cfg.tz) - fetched
    return age > MAX_AGE or age < -dt.timedelta(minutes=5)


def fetch(cfg: Config, project: str) -> dict[str, Any]:
    with JiraClient.from_config(cfg) as client:
        types = build_types(client.create_meta(project))
    if not types:
        raise core.KbError(f"프로젝트 {project}에 일감을 만들 권한이 없거나 유형이 없습니다")
    data = {"base_url": _base_url(cfg), "fetched_at": core.now(cfg.tz).isoformat(timespec="seconds"), "issuetypes": types}
    with core.kb_lock(cfg.root, "jira_meta"):
        core.save_state(cfg.root, _state_name(project), data)
    return data


def create_meta(cfg: Config, project: str, *, refresh: bool = False) -> dict[str, Any]:
    cache = load_cache(cfg, project)
    error: str | None = None
    stale = cache is not None and _is_stale(cfg, cache)
    if cache is None or refresh:
        try:
            cache, stale = fetch(cfg, project), False
        except core.KbError as exc:
            if cache is None and not isinstance(exc, core.NetworkError):
                raise
            error, stale = str(exc), True
    if cache is None:
        types = [{"id": "", "name": n, "description": {"available": True, "required": False}, "fields": []} for n in FALLBACK_TYPES]
        return {"project": project, "fetched_at": None, "stale": True, "fallback": True, "error": error, "issuetypes": types}
    return {"project": project, "fetched_at": cache["fetched_at"], "stale": stale, "fallback": False, "error": error, "issuetypes": cache["issuetypes"]}


# ---------------------------------------------------------------- 값 변환 (act create --field)


def _bad(field: Mapping[str, Any], why: str) -> core.ConfigError:
    return core.ConfigError(f"--field {field['id']}({field['name']}): {why}")


def _string(field: Mapping[str, Any], value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _bad(field, "비어 있지 않은 글이어야 합니다")
    return value


def _list(field: Mapping[str, Any], value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        raise _bad(field, "비어 있지 않은 목록이어야 합니다")
    return [_string(field, v).strip() for v in value]


def _choice(field: Mapping[str, Any], value: Any) -> dict[str, str]:
    chosen = _string(field, value)
    if chosen not in {a["id"] for a in field["allowed"]}:
        raise _bad(field, f"고를 수 없는 값입니다: {chosen}")
    return {"id": chosen}


def convert(cfg: Config, field: Mapping[str, Any], value: Any) -> Any:
    """편집기가 보낸 값(글·글 목록) → Jira가 받는 모양. 맞지 않으면 ConfigError(종료 4)."""
    kind = field["kind"]
    if kind in ("text", "textarea"):
        return _string(field, value)
    if kind == "option":
        return _choice(field, value)
    if kind == "options":
        return [_choice(field, v) for v in _list(field, value)]
    if kind == "user":
        return {"name": _string(field, value).strip()}
    if kind == "users":
        return [{"name": v} for v in _list(field, value)]
    if kind == "labels":
        labels = _list(field, value)
        if any(re.search(r"\s", v) for v in labels):
            raise _bad(field, "레이블에는 공백을 쓸 수 없습니다")
        return labels
    text = _string(field, value).strip()
    if kind == "epic":
        key = text.upper()
        if not KEY_RE.match(key):
            raise _bad(field, f"일감 키가 아닙니다: {text}")
        return key
    try:
        if kind == "number":
            number = float(text)
            if not math.isfinite(number):
                raise ValueError
            return int(number) if number.is_integer() else number
        if kind == "date":
            return dt.date.fromisoformat(text).isoformat()
        if kind == "datetime":
            moment = dt.datetime.fromisoformat(text)
            return jc.format_started(moment if moment.tzinfo else moment.replace(tzinfo=cfg.tz))
    except ValueError:
        example = {"number": "1.5", "date": "2026-11-30", "datetime": "2026-11-30T09:00"}[kind]
        raise _bad(field, f"형식이 맞지 않습니다: {text} (예: {example})") from None
    raise _bad(field, "이 항목은 Jira 웹에서만 채울 수 있습니다")


def resolve_fields(cfg: Config, project: str, issuetype: str, pairs: Iterable[str]) -> dict[str, Any]:
    """`ID=JSON` 목록 → Jira 항목. 캐시에 없는 항목은 받지 않는다."""
    pairs = list(pairs)
    if not pairs:
        return {}
    cache = load_cache(cfg, project)
    if cache is None:
        raise core.ConfigError(f"--field를 쓰려면 항목 정보가 필요합니다. 먼저 `kb.py meta create {project}`")
    found = next((t for t in cache["issuetypes"] if t["name"] == issuetype), None)
    by_id = {f["id"]: f for f in found["fields"]} if found else {}
    out: dict[str, Any] = {}
    for pair in pairs:
        fid, sep, raw = pair.partition("=")
        if not sep or fid in out:
            raise core.ConfigError(f"--field 형식이 아니거나 겹칩니다: {fid} (예: --field 'priority=\"2\"')")
        if fid in RESERVED or fid not in by_id:
            raise core.ConfigError(f"--field {fid}: {project} {issuetype}에서 쓸 수 없는 항목입니다")
        try:
            value = json.loads(raw)
        except ValueError:
            raise core.ConfigError(f"--field {fid}: 값이 JSON이 아닙니다") from None
        out[fid] = convert(cfg, by_id[fid], value)
    return out


# ---------------------------------------------------------------- 사용자


def _user(item: Any) -> dict[str, str] | None:
    if not isinstance(item, Mapping) or not item.get("name") or item.get("active") is False:
        return None
    return {"name": str(item["name"]), "display": str(item.get("displayName") or item["name"])}


def users(cfg: Config, *, project: str | None, issue: str | None, query: str) -> dict[str, Any]:
    with JiraClient.from_config(cfg) as client:
        found = [u for u in map(_user, client.assignable_users(project=project, issue=issue, query=query)) if u]
        try:
            me = _user(client.myself())
        except core.KbError:
            me = None
    return {"me": me, "users": found}


# ---------------------------------------------------------------- 진입점


def _cmd_create(cfg: Config, args: argparse.Namespace) -> int:
    from jira_act import project_key

    data = create_meta(cfg, project_key(cfg, args.project), refresh=args.refresh)
    if args.json:
        print(core.redact(json.dumps(data, ensure_ascii=False)))
        return 0
    if data["error"]:
        print(core.redact(f"경고: {data['error']}"))
    for item in data["issuetypes"]:
        print(item["name"])
        for field in item["fields"]:
            mark = "*" if field["required"] and not field["has_default"] else " "
            print(core.redact(f"  {mark} {field['id']}\t{field['name']}\t{field['kind']}"))
    return 0


def _cmd_users(cfg: Config, args: argparse.Namespace) -> int:
    from jira_act import issue_key, project_key

    if bool(args.project) == bool(args.issue):
        raise core.ConfigError("--project와 --issue 가운데 하나만 주세요")
    project = project_key(cfg, args.project) if args.project else None
    issue = issue_key(cfg, args.issue) if args.issue else None
    data = users(cfg, project=project, issue=issue, query=args.query.strip())
    if args.json:
        print(core.redact(json.dumps(data, ensure_ascii=False)))
    else:
        for user in data["users"]:
            print(f"{user['name']}\t{user['display']}")
    return 0


def main(argv: list[str]) -> int:
    parser = core.make_parser("kb.py meta", "새 일감 양식의 후보(Jira 읽기만)")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=argparse.SUPPRESS, help="볼트 루트")
    common.add_argument("--json", action="store_true", help="JSON 객체로 출력")
    parser.epilog = "create: --json --refresh · users: --json --project --issue --query"
    subs = parser.add_subparsers(dest="sub", metavar="<하위 명령>")
    p = subs.add_parser("create", parents=[common], help="유형과 항목", allow_abbrev=False)
    p.add_argument("project", metavar="PROJECT")
    p.add_argument("--refresh", action="store_true", help="캐시를 두고 Jira에서 새로 받는다")
    p = subs.add_parser("users", parents=[common], help="담당으로 지정할 수 있는 사용자", allow_abbrev=False)
    p.add_argument("--project", default=None, metavar="PROJECT", help="새 일감용")
    p.add_argument("--issue", default=None, metavar="KEY", help="있는 일감용")
    p.add_argument("--query", default="", help="이름·사용자명 일부")
    args = parser.parse_args(argv)
    if not args.sub:
        raise core.ConfigError("하위 명령이 필요합니다: create, users")
    cfg = config.load(args.root)
    return _cmd_create(cfg, args) if args.sub == "create" else _cmd_users(cfg, args)


if __name__ == "__main__":
    core.run_main(main)
