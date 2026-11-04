"""Jira 쓰기 (`kb.py act <하위 명령>`). Phase 3, 지시서 §6.3, D26·D52·D53·D57.

하위 명령:
  comment KEY --body "…" | --from-line daily/YYYY-MM-DD.md:N   댓글 (줄 지정 시 기본 본문 = 그 줄)
  transition KEY NAME [--yes]        전환 이름(대소문자 무시) → id. 후보가 여럿이거나 없으면 목록, 종료 4
  transitions KEY [--json]           지금 가능한 전환 목록만 본다(아무것도 바꾸지 않음)
  assign KEY USERNAME                담당자 지정(DC 사용자 이름)
  worklog KEY 1h30m [--comment …] [--started ISO]
  create PROJECT --summary … --type … [--description …] [--yes]
  promote --from daily/YYYY-MM-DD.md:N PROJECT [--type Task] [--yes]
                                     그 줄로 일감을 만들고 성공하면 줄 끝에 ` [[새 키]]`를 붙인다(D53)
  flush [--retry-failed] [--retry-uncertain]
  list [--json] [--all]

흐름(모든 쓰기): outbox에 pending으로 넣기(잠금 안) → 잠금 풀기 → 보내기 → 결과 기록(잠금 안) →
성공이면 `jira_sync.sync_key`로 캐시 갱신(잠금 밖, sync_key가 스스로 잠근다). 네트워크 요청을 하는
동안에는 절대 잠금을 쥐지 않는다(D26).

결과 규칙(D52):
- 2xx → done, done_at.
- 연결 실패·5xx → pending, attempts+1, 종료 3. 5번째 실패면 failed. 전환의 500은 잘못된 id라서 바로 failed.
- 429(재시도 뒤에도) → pending, 시도로 세지 않음, 종료 3.
- 응답 시간 초과·응답 전 끊김 → pending, last_error "uncertain"(보냈을 수 있음). 그냥 flush는 건너뛰고
  `--retry-uncertain`일 때만 다시 보낸다.
- 401/403 → `/myself`로 다시 확인: 200이면 권한 없음 → failed(종료 1), 401이면 토큰 문제 → pending, 종료 2.
- 그 밖의 4xx → failed(종료 1).
flush는 pending을 id 순서로 보낸다. project_keys 밖 프로젝트의 기록은 보내지 않는다(종료 4).

자동 상태 전환은 없다. `create`·`transition`·`promote`는 `--yes`가 없으면 y/N을 묻는다(EOF는 거절, 종료 1).
`KB_AGENT=1`이면 `list`·`transitions` 말고는 모두 거부(종료 7). kb.py 경로와 별개로 여기서도 확인한다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, TextIO

import jira_sync
from lib import config, core, notes, outbox
from lib import jira_client as jc
from lib.config import Config
from lib.jira_client import JiraClient

LOCK_OWNER = outbox.LOCK_OWNER
READ_ONLY = frozenset({"list", "transitions"})
KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]+-[1-9][0-9]*$")
PROJECT_RE = re.compile(r"^[A-Z][A-Z0-9_]+$")
SOURCE_RE = re.compile(r"^(daily/\d{4}-\d{2}-\d{2}\.md):([1-9][0-9]*)$")
DURATION_RE = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?$")
LIST_PREFIX = re.compile(r"^\s*[-*+]\s+(?:\[[ xX>]\]\s+)?(?:\d{1,2}:\d{2}(?:\s+|$))?")
CARRIED_RE = re.compile(r"^- \[>\] (.*) \(\d{4}-\d{2}-\d{2}로 이월\)$")
EMBED_RE = re.compile(r"!\[\[[^\]]*\]\]")
LINK_RE = re.compile(r"\[\[([^\]\|#]+)(?:#[^\]\|]*)?(?:\|([^\]]*))?\]\]")
SUMMARY_MAX = 255
YES_WORDS = frozenset({"y", "yes", "예", "네", "ㅇ"})

_input: Callable[[str], str] = input  # 테스트가 바꿔 끼운다(확인 질문)


# ---------------------------------------------------------------- 도움 함수


def _now_iso(cfg: Config) -> str:
    return core.now(cfg.tz).isoformat(timespec="seconds")


def _err(message: str) -> None:
    print(message, file=sys.stderr)


def confirm(question: str) -> bool:
    """y/N 질문. EOF·빈 답·그 밖의 답은 모두 거절."""
    try:
        answer = _input(f"{question} [y/N] ")
    except EOFError:
        print()
        return False
    return (answer or "").strip().lower() in YES_WORDS


def project_of(record: Mapping[str, Any]) -> str:
    key = str(record.get("key") or "")
    if record.get("action") in ("create", "promote"):
        return key
    return key.split("-", 1)[0]


def _check_project(cfg: Config, project: str) -> None:
    if project not in cfg.project_keys:
        allowed = ", ".join(cfg.project_keys) or "(없음)"
        raise core.ConfigError(f"프로젝트 {project}는 project_keys에 없습니다 (허용: {allowed})")


def issue_key(cfg: Config, raw: str) -> str:
    key = (raw or "").strip().upper()
    if key.startswith("[[") and key.endswith("]]"):
        key = key[2:-2].strip()
    if not KEY_RE.match(key):
        raise core.ConfigError(f"이슈 키 형식이 아닙니다: {raw!r}. 예: KB-12")
    _check_project(cfg, key.split("-", 1)[0])
    return key


def project_key(cfg: Config, raw: str) -> str:
    project = (raw or "").strip().upper()
    if not PROJECT_RE.match(project):
        raise core.ConfigError(f"프로젝트 키 형식이 아닙니다: {raw!r}. 예: KB")
    _check_project(cfg, project)
    return project


def parse_duration(text: str) -> int:
    """`1h30m`·`2h`·`45m`·`1h 30m` → 초. 그 밖(일·주 포함)은 ConfigError(인스턴스마다 하루 길이가 다르다)."""
    compact = re.sub(r"\s+", "", (text or "").lower())
    match = DURATION_RE.match(compact)
    if not compact or match is None:
        raise core.ConfigError(f"작업 시간 형식이 아닙니다: {text!r}. 예: 1h30m, 2h, 45m")
    seconds = int(match.group(1) or 0) * 3600 + int(match.group(2) or 0) * 60
    if seconds < 60:
        raise core.ConfigError(f"작업 시간은 1분 이상이어야 합니다: {text!r}")
    return seconds


def parse_started(cfg: Config, text: str | None) -> dt.datetime:
    if not text:
        return core.now(cfg.tz)
    try:
        value = dt.datetime.fromisoformat(text.strip())
    except ValueError:
        raise core.ConfigError(f"--started: ISO 8601 시각이 아닙니다: {text!r}. 예: 2026-11-05T14:00") from None
    return value if value.tzinfo is not None else value.replace(tzinfo=cfg.tz)


def line_content(text: str) -> str:
    """daily 한 줄 → Jira로 보낼 글: 목록 표시·체크박스·시각을 떼고, 위키링크는 글자로, 이미지 임베드는 뺀다."""
    body = LIST_PREFIX.sub("", text, count=1)
    body = EMBED_RE.sub("", body)
    body = LINK_RE.sub(lambda m: (m.group(2) or m.group(1)).strip(), body)
    return re.sub(r"\s+", " ", body).strip()


# ---------------------------------------------------------------- 원본 줄


@dataclass(frozen=True)
class SourceLine:
    spec: str  # daily/2026-11-05.md:14
    rel: str
    lineno: int
    path: Path
    text: str


def read_source(cfg: Config, spec: str) -> SourceLine:
    """`daily/YYYY-MM-DD.md:N`의 줄(1부터, `\\n` 기준. Emacs 줄 번호와 같다)."""
    normalized = (spec or "").strip().replace("\\", "/")
    match = SOURCE_RE.match(normalized)
    if match is None:
        raise core.ConfigError(f"줄 지정 형식이 아닙니다: {spec!r}. 예: daily/2026-11-05.md:14")
    rel, lineno = match.group(1), int(match.group(2))
    path = cfg.path(*rel.split("/"))
    if not path.is_file():
        raise core.ConfigError(f"{rel} 파일이 없습니다")
    try:
        lines = notes.split_lines(notes.decode(path.read_bytes()))
    except UnicodeDecodeError:
        raise core.ConfigError(f"{rel}: UTF-8이 아니라 읽지 못했습니다") from None
    if lineno > len(lines):
        raise core.ConfigError(f"{rel}에는 {lineno}번째 줄이 없습니다 (전체 {len(lines)}줄)")
    return SourceLine(spec=f"{rel}:{lineno}", rel=rel, lineno=lineno, path=path, text=lines[lineno - 1])


def _line_hashes(text: str) -> set[str]:
    """줄의 해시와, 이월 표시(`- [>] … (날짜로 이월)`)가 붙었으면 원래 `- [ ] …` 꼴의 해시."""
    out = {core.line_hash(text)}
    carried = CARRIED_RE.match(text.strip())
    if carried:
        out.add(core.line_hash(f"- [ ] {carried.group(1)}"))
    return out


def link_promoted(cfg: Config, record: Mapping[str, Any], new_key: str) -> tuple[bool, str]:
    """승격한 daily 줄 끝에 ` [[NEW-1]]`를 붙인다(잠금 안, expect_sha256). (붙였는가, 설명).

    기록한 줄 해시와 지금 줄이 다르면 붙이지 않는다. 같은 내용의 줄이 파일에 정확히 하나 있으면(위에
    줄이 끼어들어 번호만 밀린 경우) 그 줄에 붙인다. 이월 표시(`[>]`)가 붙은 꼴도 같은 줄로 본다.
    """
    match = SOURCE_RE.match(str(record.get("source") or ""))
    want = str((record.get("payload") or {}).get("line_hash") or "")
    if match is None or not want:
        return False, "기록에 원본 줄 정보가 없습니다"
    rel, lineno = match.group(1), int(match.group(2))
    path = cfg.path(*rel.split("/"))
    link = f"[[{new_key}]]"
    with core.kb_lock(cfg.root, LOCK_OWNER):
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return False, f"{rel} 파일이 없습니다"
        bom = notes.BOM if data.startswith(notes.BOM) else b""
        try:
            parts = notes.split_lines(notes.decode(data), keepends=True)
        except UnicodeDecodeError:
            return False, f"{rel}: UTF-8이 아닙니다"
        bodies = [part.rstrip("\r\n") for part in parts]
        index: int | None = None
        if 0 < lineno <= len(bodies) and want in _line_hashes(bodies[lineno - 1]):
            index = lineno - 1
        else:
            same = [i for i, body in enumerate(bodies) if want in _line_hashes(body)]
            if len(same) == 1:
                index = same[0]
        if index is None:
            return False, f"{rel}:{lineno} 줄이 승격할 때와 달라졌습니다"
        body = bodies[index]
        if link in body:
            return True, f"{rel}:{index + 1}에 이미 {link}가 있습니다"
        parts[index] = body.rstrip(" \t") + f" {link}" + parts[index][len(body):]
        new = bom + "".join(parts).encode("utf-8")
        try:
            core.atomic_write(path, new, expect_sha256=core.sha256_hex(data))
        except core.ConcurrentEdit:
            return False, f"{rel}을(를) 쓰는 사이에 사람이 고쳤습니다"
        except core.FileBusy as exc:
            return False, str(exc)
    return True, f"{rel}:{index + 1}에 {link}를 붙였습니다"


# ---------------------------------------------------------------- 보내기와 결과


@dataclass
class Outcome:
    code: int
    record: dict
    stop: bool = False  # flush가 여기서 멈춰야 하는가(인증·연결 문제)


def describe(record: Mapping[str, Any]) -> str:
    action = record.get("action")
    key = record.get("key")
    payload = record.get("payload") or {}
    if action == "comment":
        body = " ".join(str(payload.get("body") or "").split())
        return f"댓글 {key}: {body[:40]}{'…' if len(body) > 40 else ''}"
    if action == "transition":
        return f"전환 {key} → {payload.get('to') or payload.get('name')} (id {payload.get('transition_id')})"
    if action == "assign":
        return f"담당 {key} → {payload.get('username')}"
    if action == "worklog":
        return f"워크로그 {key} {payload.get('time_spent') or str(payload.get('seconds')) + '초'}"
    if action == "create":
        return f"새 일감 {key}: {payload.get('summary')}"
    if action == "promote":
        return f"승격 {key}: {payload.get('summary')} ({record.get('source')})"
    return f"{action} {key}"


def _send(client: JiraClient, record: Mapping[str, Any]) -> dict | None:
    """기록 하나를 Jira로 보낸다. 결과(기록에 남길 작은 dict) 또는 None."""
    action = record.get("action")
    key = str(record.get("key") or "")
    payload = record.get("payload") or {}
    if action == "comment":
        data = client.add_comment(key, str(payload.get("body") or ""))
        return {"comment_id": data.get("id")} if data.get("id") else None
    if action == "transition":
        client.do_transition(key, str(payload.get("transition_id") or ""))
        return None
    if action == "assign":
        client.assign(key, str(payload.get("username") or ""))
        return None
    if action == "worklog":
        started = jc.parse_datetime(payload.get("started"))
        if started is None:
            raise core.ConfigError(f"워크로그 시작 시각을 읽지 못했습니다: {payload.get('started')!r}")
        data = client.add_worklog(key, int(payload.get("seconds") or 0), str(payload.get("comment") or ""), started)
        return {"worklog_id": data.get("id")} if data.get("id") else None
    if action in ("create", "promote"):
        extra: dict[str, Any] = {}
        if payload.get("description"):
            extra["description"] = str(payload["description"])
        data = client.create_issue(key, str(payload.get("summary") or ""), str(payload.get("issuetype") or ""), **extra)
        return {"key": data["key"]}
    raise core.ConfigError(f"알 수 없는 outbox 동작입니다: {action!r}")


def _log(cfg: Config, record: Mapping[str, Any], note: str) -> None:
    core.get_logger(cfg.root).info(
        "act %s %s %s → %s (시도 %s) %s",
        record.get("id"), record.get("action"), record.get("key"), record.get("status"), record.get("attempts"), note,
    )


def _fail(cfg: Config, record: Mapping[str, Any], attempts: int, message: str, hint: str = "") -> Outcome:
    rec = outbox.update(
        cfg.root, record["id"], {"status": outbox.FAILED, "attempts": attempts, "last_error": core.redact(message)}
    )
    _err(f"실패: {describe(rec)} — {message}" + (f"\n  {hint}" if hint else ""))
    _log(cfg, rec, message)
    return Outcome(1, rec)


def _keep(cfg: Config, record: Mapping[str, Any], attempts: int, error: str, detail: str | None, *, stop: bool) -> Outcome:
    """pending으로 남긴다. 시도가 MAX_ATTEMPTS에 닿으면 failed."""
    if attempts >= outbox.MAX_ATTEMPTS:
        message = detail or error
        return _fail(cfg, record, attempts, f"{outbox.MAX_ATTEMPTS}번 시도했지만 보내지 못했습니다: {message}",
                     "Jira 상태를 확인한 뒤 `kb.py act flush --retry-failed`로 다시 보낼 수 있습니다")  # fmt: skip
    changes: dict[str, Any] = {"status": outbox.PENDING, "attempts": attempts, "last_error": core.redact(error)}
    if detail is not None or "error_detail" in record:
        changes["error_detail"] = core.redact(detail) if detail else None
    rec = outbox.update(cfg.root, record["id"], changes)
    if error == outbox.UNCERTAIN:
        _err(
            f"불확실: {describe(rec)} — {detail}\n"
            "  Jira에 반영됐는지 먼저 확인하세요. 반영되지 않았으면 `kb.py act flush --retry-uncertain`"
        )
    else:
        _err(f"대기: {describe(rec)} — {error}\n  outbox에 남겼습니다. 나중에 `kb.py act flush`로 보냅니다")
    _log(cfg, rec, error)
    return Outcome(3, rec, stop=stop)


def _denied(cfg: Config, client: JiraClient, record: Mapping[str, Any], attempts: int, exc: core.KbError) -> Outcome:
    """401/403: `/myself`로 토큰을 다시 확인해 권한 문제(failed)와 토큰 문제(pending, 종료 2)를 가른다(D52)."""
    try:
        client.myself()
    except core.AuthError as again:
        rec = outbox.update(cfg.root, record["id"], {"status": outbox.PENDING, "last_error": core.redact(str(again))})
        _err(
            f"인증 실패: {describe(rec)} — {again}\n"
            "  outbox에 남겼습니다. 토큰을 고친 뒤(`kb.py init --section jira`) `kb.py act flush`"
        )
        _log(cfg, rec, "auth")
        return Outcome(2, rec, stop=True)
    except core.KbError as again:
        return _keep(cfg, record, attempts, str(again), None, stop=True)
    return _fail(cfg, record, attempts, f"권한이 없습니다: {exc}", "Jira에서 이 일감에 대한 권한을 확인하세요")


def deliver(cfg: Config, client: JiraClient, record: Mapping[str, Any], out: TextIO) -> Outcome:
    """pending 기록 하나를 보내고 결과를 outbox에 적는다. 잠금은 결과를 적을 때만 잡는다."""
    attempts = int(record.get("attempts") or 0) + 1
    try:
        result = _send(client, record)
    except core.AuthError as exc:  # WriteDenied 포함
        return _denied(cfg, client, record, attempts, exc)
    except jc.RateLimited as exc:
        return _keep(cfg, record, attempts - 1, str(exc), None, stop=True)
    except jc.WriteServerError as exc:
        if record.get("action") == "transition" and exc.status == 500:
            return _fail(cfg, record, attempts, str(exc),
                         f"전환 id가 지금 상태에 맞지 않을 수 있습니다. `kb.py act transitions {record.get('key')}`로 확인하세요")  # fmt: skip
        return _keep(cfg, record, attempts, str(exc), None, stop=False)
    except core.NetworkError as exc:
        if exc.after_send:
            return _keep(cfg, record, attempts, outbox.UNCERTAIN, str(exc), stop=True)
        return _keep(cfg, record, attempts, str(exc), None, stop=True)
    except core.KbError as exc:  # WriteRejected(4xx), 형식 오류 등: 다시 보내도 같다
        return _fail(cfg, record, attempts, str(exc))
    changes: dict[str, Any] = {"status": outbox.DONE, "attempts": attempts, "last_error": None, "done_at": _now_iso(cfg)}
    if "error_detail" in record:
        changes["error_detail"] = None
    if result:
        changes["result"] = result
    try:
        rec = outbox.update(cfg.root, record["id"], changes)
    except core.KbError:  # 잠금·파일 문제: Jira에는 이미 반영됐다. 다시 보내면 중복이 된다
        _err(
            f"경고: Jira에는 보냈지만 outbox에 완료를 적지 못했습니다 ({record['id']} {describe(record)}).\n"
            f"  flush하기 전에 Jira에서 반영 여부를 확인하세요. 결과: {json.dumps(result or {}, ensure_ascii=False)}"
        )
        raise
    print(f"보냄: {describe(rec)}", file=out)
    _log(cfg, rec, "")
    _after_done(cfg, client, rec, out)
    return Outcome(0, rec)


def _after_done(cfg: Config, client: JiraClient, record: dict, out: TextIO) -> None:
    """성공 뒤: 승격이면 daily 줄에 키를 붙이고, 캐시(jira/KEY.md)를 갱신한다(잠금 밖에서 부른다)."""
    target = str(record.get("key") or "")
    if record.get("action") in ("create", "promote"):
        target = str((record.get("result") or {}).get("key") or "")
        print(f"새 일감: {target}", file=out)
        if record.get("action") == "promote":
            linked, note = link_promoted(cfg, record, target)
            outbox.update(cfg.root, record["id"], {"result": {**(record.get("result") or {}), "linked": linked}})
            if linked:
                print(note, file=out)
            else:
                _err(f"경고: 일감 {target}은(는) 만들었지만 daily 줄에 [[{target}]]를 붙이지 않았습니다: {note}\n"
                     f"  필요하면 그 줄 끝에 직접 [[{target}]]를 붙이세요")  # fmt: skip
    if not KEY_RE.match(target):
        return
    try:
        path = jira_sync.sync_key(cfg, client, target)
    except core.KbError as exc:
        _err(f"경고: Jira에는 보냈지만 캐시 갱신에 실패했습니다 ({exc}). 나중에 `kb.py sync --key {target}`")
        return
    print(f"jira/{path.name}: 동기화함", file=out)


def submit(
    cfg: Config,
    client: JiraClient,
    out: TextIO,
    *,
    action: str,
    key: str,
    payload: Mapping[str, Any],
    source: str | None = None,
) -> int:
    """outbox에 넣고(잠금 안) 잠금을 푼 뒤 바로 보낸다."""
    record = outbox.make_record(action=action, key=key, payload=payload, ts=_now_iso(cfg), source=source)
    outbox.append(cfg.root, record)
    print(f"outbox에 넣음: {record['id']} {describe(record)}", file=out)
    return deliver(cfg, client, record, out).code


# ---------------------------------------------------------------- 하위 명령


def cmd_comment(cfg: Config, args: argparse.Namespace, out: TextIO) -> int:
    key = issue_key(cfg, args.key)
    source: str | None = None
    body = args.body
    if args.from_line:
        line = read_source(cfg, args.from_line)
        source = line.spec
        if body is None:
            body = line_content(line.text)
    if body is None:
        raise core.ConfigError("댓글 내용이 필요합니다: --body \"…\" 또는 --from-line daily/YYYY-MM-DD.md:N")
    if not body.strip():
        raise core.ConfigError("댓글 내용이 비어 있습니다")
    with JiraClient.from_config(cfg) as client:
        return submit(cfg, client, out, action="comment", key=key, payload={"body": body}, source=source)


def match_transitions(options: list[dict], wanted: str) -> list[dict]:
    """이름(대소문자 무시) → 없으면 id → 없으면 도착 상태 이름 순으로 찾는다."""
    text = (wanted or "").strip().casefold()
    by_name = [t for t in options if str(t.get("name") or "").strip().casefold() == text]
    if by_name:
        return by_name
    by_id = [t for t in options if str(t.get("id") or "") == wanted.strip()]
    if by_id:
        return by_id
    return [t for t in options if str(((t.get("to") or {}).get("name")) or "").strip().casefold() == text]


def _transition_rows(options: list[dict]) -> list[dict]:
    rows = []
    for item in options:
        to = item.get("to") or {}
        category = (to.get("statusCategory") or {}).get("key") if isinstance(to, Mapping) else None
        rows.append({"id": str(item.get("id") or ""), "name": str(item.get("name") or ""),
                     "to": str(to.get("name") or "") if isinstance(to, Mapping) else "", "to_category": category or ""})  # fmt: skip
    return rows


def _print_candidates(key: str, options: list[dict], out: TextIO) -> None:
    for row in _transition_rows(options):
        print(f"  {row['id']}\t{row['name']} → {row['to']}", file=out)
    print(f"이름이 겹치면 id로 고르세요: kb.py act transition {key} <id>", file=out)


def cmd_transition(cfg: Config, args: argparse.Namespace, out: TextIO) -> int:
    key = issue_key(cfg, args.key)
    with JiraClient.from_config(cfg) as client:
        options = client.transitions(key)
        found = match_transitions(options, args.name)
        if len(found) != 1:
            if found:
                _err(f"'{args.name}'에 맞는 전환이 {len(found)}개입니다 ({key}). 하나를 고르세요")
                _print_candidates(key, found, out)
            else:
                _err(f"'{args.name}'에 맞는 전환이 없습니다 ({key}). 가능한 전환:")
                _print_candidates(key, options, out)
            return 4
        chosen = found[0]
        row = _transition_rows([chosen])[0]
        if not args.yes and not confirm(f"{key}를 '{row['name']}'(→ {row['to']})로 전환할까요?"):
            print("취소했습니다", file=out)
            return 1
        payload = {"transition_id": row["id"], "name": row["name"], "to": row["to"]}
        return submit(cfg, client, out, action="transition", key=key, payload=payload)


def cmd_transitions(cfg: Config, args: argparse.Namespace, out: TextIO) -> int:
    key = issue_key(cfg, args.key)
    with JiraClient.from_config(cfg) as client:
        rows = _transition_rows(client.transitions(key))
    if args.json:
        print(json.dumps(rows, ensure_ascii=False), file=out)
        return 0
    if not rows:
        print(f"{key}: 가능한 전환이 없습니다", file=out)
    for row in rows:
        print(f"{row['id']}\t{row['name']} → {row['to']}", file=out)
    return 0


def cmd_assign(cfg: Config, args: argparse.Namespace, out: TextIO) -> int:
    key = issue_key(cfg, args.key)
    username = (args.username or "").strip()
    if not username:
        raise core.ConfigError("담당자 사용자 이름이 비어 있습니다")
    with JiraClient.from_config(cfg) as client:
        return submit(cfg, client, out, action="assign", key=key, payload={"username": username})


def cmd_worklog(cfg: Config, args: argparse.Namespace, out: TextIO) -> int:
    key = issue_key(cfg, args.key)
    seconds = parse_duration(args.duration)
    started = parse_started(cfg, args.started)
    payload = {
        "seconds": seconds,
        "time_spent": re.sub(r"\s+", "", args.duration.lower()),
        "comment": args.comment or "",
        "started": jc.format_started(started),
    }
    with JiraClient.from_config(cfg) as client:
        return submit(cfg, client, out, action="worklog", key=key, payload=payload)


def _summary(text: str) -> str:
    summary = re.sub(r"\s+", " ", text or "").strip()
    if not summary:
        raise core.ConfigError("일감 제목이 비어 있습니다")
    if len(summary) > SUMMARY_MAX:
        summary = summary[: SUMMARY_MAX - 1] + "…"
    return summary


def cmd_create(cfg: Config, args: argparse.Namespace, out: TextIO) -> int:
    project = project_key(cfg, args.project)
    summary = _summary(args.summary)
    issuetype = (args.type or "").strip()
    if not issuetype:
        raise core.ConfigError("--type: 일감 유형이 비어 있습니다. 예: Task")
    payload: dict[str, Any] = {"summary": summary, "issuetype": issuetype}
    if args.description:
        payload["description"] = args.description
    with JiraClient.from_config(cfg) as client:
        if not args.yes and not confirm(f"{project}에 새 일감 '{summary}'({issuetype})을(를) 만들까요?"):
            print("취소했습니다", file=out)
            return 1
        return submit(cfg, client, out, action="create", key=project, payload=payload)


def cmd_promote(cfg: Config, args: argparse.Namespace, out: TextIO) -> int:
    project = project_key(cfg, args.project)
    line = read_source(cfg, args.source)
    summary = _summary(line_content(line.text))
    if re.search(rf"\[\[{re.escape(project)}-[1-9][0-9]*\]\]\s*$", line.text):
        raise core.ConfigError(f"{line.spec}: 이미 일감 키가 붙은 줄입니다")
    line_hash = core.line_hash(line.text)
    for record in outbox.records(cfg.root):
        if (
            record.get("action") == "promote"
            and record.get("status") == outbox.PENDING
            and record.get("source") == line.spec
            and (record.get("payload") or {}).get("line_hash") == line_hash
        ):
            raise core.ConfigError(f"{line.spec}: 같은 줄의 승격이 이미 대기 중입니다 ({record['id']}). `kb.py act flush`로 보내세요")
    issuetype = (args.type or "").strip() or "Task"
    payload = {"summary": summary, "issuetype": issuetype, "line_hash": line_hash}
    with JiraClient.from_config(cfg) as client:
        if not args.yes and not confirm(f"{line.spec}을(를) {project} 일감 '{summary}'({issuetype})으로 만들까요?"):
            print("취소했습니다", file=out)
            return 1
        return submit(cfg, client, out, action="promote", key=project, payload=payload, source=line.spec)


def cmd_flush(cfg: Config, args: argparse.Namespace, out: TextIO) -> int:
    targets: list[dict] = []
    uncertain = 0
    for record in sorted(outbox.records(cfg.root), key=lambda r: str(r.get("id"))):
        status = record.get("status")
        if status == outbox.PENDING:
            if record.get("last_error") == outbox.UNCERTAIN and not args.retry_uncertain:
                uncertain += 1
                continue
            targets.append(record)
        elif status == outbox.FAILED and args.retry_failed:
            targets.append(record)
    note = f" · 불확실 {uncertain}건은 건너뜀(Jira에서 확인 뒤 --retry-uncertain)" if uncertain else ""
    if not targets:
        print(f"flush: 보낼 항목이 없습니다{note}", file=out)
        return 0
    refused = [r for r in targets if project_of(r) not in cfg.project_keys]
    for record in refused:
        _err(f"거부: {describe(record)} — 프로젝트 {project_of(record)}는 project_keys에 없어 보내지 않습니다")
    sendable = [r for r in targets if r not in refused]
    codes: list[int] = []
    sent = 0
    left = 0
    if sendable:
        with JiraClient.from_config(cfg) as client:
            for index, record in enumerate(sendable):
                fresh = outbox.get(cfg.root, record["id"])
                if fresh is None or fresh.get("status") != record.get("status"):
                    continue  # 다른 실행이 먼저 처리했다
                if fresh.get("status") == outbox.FAILED:
                    fresh = outbox.update(cfg.root, fresh["id"], {"status": outbox.PENDING, "attempts": 0, "last_error": None})
                outcome = deliver(cfg, client, fresh, out)
                codes.append(outcome.code)
                sent += outcome.code == 0
                if outcome.stop:
                    left = len(sendable) - index - 1
                    if left:
                        _err(f"나머지 {left}건은 보내지 않았습니다. 문제를 고친 뒤 다시 flush하세요")
                    break
    print(f"flush: 보냄 {sent} · 실패·대기 {len(codes) - sent} · 남김 {left} · 거부 {len(refused)}{note}", file=out)
    for code in (2, 3):
        if code in codes:
            return code
    if refused:
        return 4
    return 1 if any(codes) else 0


def cmd_list(cfg: Config, args: argparse.Namespace, out: TextIO) -> int:
    items = outbox.records(cfg.root)
    shown = items if args.all else [r for r in items if r.get("status") != outbox.DONE]
    if args.json:
        print(json.dumps(shown, ensure_ascii=False), file=out)
        return 0
    for record in shown:
        error = record.get("last_error")
        tail = f" · {error}" if error else ""
        print(f"{record.get('id')} {record.get('status')} 시도 {record.get('attempts')} {describe(record)}{tail}", file=out)
    count = outbox.counts(items)
    hidden = "" if args.all or not count[outbox.DONE] else " (완료 항목은 --all로 봅니다)"
    print(f"outbox: 대기 {count[outbox.PENDING]} · 실패 {count[outbox.FAILED]} · 완료 {count[outbox.DONE]}{hidden}", file=out)
    broken = outbox.broken_lines(cfg.root)
    if broken:
        _err(f"경고: jira/outbox.jsonl에 읽지 못한 줄 {broken}개")
    return 0


HANDLERS: dict[str, Callable[[Config, argparse.Namespace, TextIO], int]] = {
    "comment": cmd_comment,
    "transition": cmd_transition,
    "transitions": cmd_transitions,
    "assign": cmd_assign,
    "worklog": cmd_worklog,
    "create": cmd_create,
    "promote": cmd_promote,
    "flush": cmd_flush,
    "list": cmd_list,
}


# ---------------------------------------------------------------- 진입점


def build_parser() -> argparse.ArgumentParser:
    parser = core.make_parser("kb.py act", "Jira 쓰기(outbox 큐 경유). 자동 상태 전환은 하지 않는다")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=argparse.SUPPRESS, help="볼트 루트")
    subs = parser.add_subparsers(dest="sub", metavar="<하위 명령>")

    def sub(name: str, help_text: str) -> argparse.ArgumentParser:
        return subs.add_parser(name, parents=[common], help=help_text, description=help_text, allow_abbrev=False)

    p = sub("comment", "댓글 달기")
    p.add_argument("key", metavar="KEY")
    p.add_argument("--body", default=None, help="댓글 내용")
    p.add_argument("--from-line", dest="from_line", default=None, metavar="daily/YYYY-MM-DD.md:N", help="이 줄을 기본 내용·출처로")

    p = sub("transition", "상태 전환(이름 → id). --yes가 없으면 확인")
    p.add_argument("key", metavar="KEY")
    p.add_argument("name", metavar="NAME", help="전환 이름(대소문자 무시) 또는 id")
    p.add_argument("--yes", action="store_true", help="확인 없이 보낸다")

    p = sub("transitions", "지금 가능한 전환 목록(아무것도 바꾸지 않음)")
    p.add_argument("key", metavar="KEY")
    p.add_argument("--json", action="store_true", help="JSON 배열 [{id, name, to, to_category}]")

    p = sub("assign", "담당자 지정")
    p.add_argument("key", metavar="KEY")
    p.add_argument("username", metavar="USERNAME", help="Jira 사용자 이름(DC name)")

    p = sub("worklog", "작업 시간 기록")
    p.add_argument("key", metavar="KEY")
    p.add_argument("duration", metavar="DURATION", help="예: 1h30m, 2h, 45m")
    p.add_argument("--comment", default="", help="워크로그 설명")
    p.add_argument("--started", default=None, help="시작 시각 ISO 8601 (기본: 지금)")

    p = sub("create", "새 일감 만들기. --yes가 없으면 확인")
    p.add_argument("project", metavar="PROJECT")
    p.add_argument("--summary", required=True, help="제목")
    p.add_argument("--type", required=True, help="일감 유형 이름. 예: Task")
    p.add_argument("--description", default=None, help="설명")
    p.add_argument("--yes", action="store_true", help="확인 없이 보낸다")

    p = sub("promote", "daily 한 줄을 일감으로 만들고 줄 끝에 [[새 키]]를 붙인다. --yes가 없으면 확인")
    p.add_argument("--from", dest="source", required=True, metavar="daily/YYYY-MM-DD.md:N", help="승격할 줄")
    p.add_argument("project", metavar="PROJECT")
    p.add_argument("--type", default="Task", help="일감 유형 이름 (기본 Task)")
    p.add_argument("--yes", action="store_true", help="확인 없이 보낸다")

    p = sub("flush", "outbox의 대기 항목을 id 순서로 보낸다")
    p.add_argument("--retry-failed", action="store_true", help="실패 항목도 다시 보낸다(시도 횟수 초기화)")
    p.add_argument("--retry-uncertain", action="store_true", help="결과를 모르는(uncertain) 항목도 다시 보낸다")

    p = sub("list", "outbox 항목 보기")
    p.add_argument("--json", action="store_true", help="JSON 배열")
    p.add_argument("--all", action="store_true", help="완료 항목도 보인다")
    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.sub:
        raise core.ConfigError(f"하위 명령이 필요합니다: {', '.join(HANDLERS)}. 예: kb.py act list")
    if args.sub not in READ_ONLY:
        core.refuse_if_agent(f"act {args.sub}")  # kb.py 경로와 별개로 직접 실행도 막는다(D57)
    cfg = config.load(args.root)
    return HANDLERS[args.sub](cfg, args, sys.stdout)


if __name__ == "__main__":
    core.run_main(main)
