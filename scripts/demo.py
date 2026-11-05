"""kb.py demo: 모의 서버 3개로 전체 흐름을 시연한다 (지시서 §7, D30·D46).

흐름: 모의 서버 3개(Jira·Confluence·LLM) 기동 → 설정(init --dev 대신 임시 config.local.yaml) →
sync → link → ingest → compile → lint → push → 결과 트리.

지키는 것(D46):
- `examples/sample-vault`를 임시 폴더(한글·공백 경로)로 복사해 그 안에서만 쓴다. 저장소의 볼트·`.state`·
  `config.local.yaml`은 쓰지 않는다. 저장소 `config.local.yaml`은 `environment` 줄만 읽고, 그 값이
  글자 그대로 company일 때만 거부한다(종료 7). 파일이 없으면 허용.
- 모든 단계는 이 프로세스 안에서 각 모듈의 `run()`으로 돌린다(하위 프로세스 없음). 모의 서버도 이 프로세스
  안에서 임시 포트(port 0)에 띄운다. `--fixed-ports`일 때만 8081~8083.
- 자격 증명은 임시 폴더의 파일 기반 가짜 keyring(tests/fake_keyring.py)에 모의 토큰만 넣는다. 실제 자격 증명
  저장소와 `DEEPINFRA_API_KEY`는 읽지 않는다(LLM 프로필은 키 참조가 없는 모의 서버 하나뿐).
- 샘플 볼트는 날짜가 고정된 데이터라 시계를 `KB_FAKE_NOW`(기본 2026-11-05 18:30 +09:00)로 고정한다.
  이미 KB_FAKE_NOW가 있으면 그 값을 쓴다. 끝나면 환경변수·keyring·로그 핸들러를 원래대로 돌린다.
- 어느 단계든 실패하면 종료 코드는 0이 아니다(첫 실패의 코드, 없으면 1). 실패해도 다음 단계는 계속 돌려
  전체 모습을 보여 준다. `--keep DIR`가 없으면 임시 폴더를 지운다.

모의 서버는 tests/에 있으므로 tests/와 examples/가 없는 배포 복사본에서는 실행할 수 없다(종료 4).
"""
from __future__ import annotations

import contextlib
import dataclasses
import io
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, TextIO

from lib import config, core

CODE_ROOT = config.CODE_ROOT
SAMPLE = CODE_ROOT / "examples" / "sample-vault"
TESTS = CODE_ROOT / "tests"
MOCK_MODULES = ("mock_jira", "mock_confluence", "mock_llm", "fake_keyring")
FIXED_PORTS = {"jira": 8081, "confluence": 8082, "llm": 8083}
DEMO_NOW = "2026-11-05T18:30:00+09:00"  # 샘플 볼트의 마지막 날 저녁
SERVICE = "work-kb-demo"  # 진짜 work-kb 자격 증명과 이름부터 다르게 둔다
TOKEN = "mock-token"  # 모의 서버용 가짜 PAT. 비밀값이 아니다
VAULT_NAME = "샘플 볼트"
WORK_PREFIX = "work-kb 데모-"

_TAGS = ("회의", "결정", "요청", "이슈", "학습", "아이디어", "개인", "참고", "일정", "리뷰")

# 모의 LLM의 작업별 고정 답(`KB-TASK: <작업>` 첫 줄로 고름). 줄 id는 요청마다 L1부터, 언급은 M1부터다.
# 고정 답이라 내용은 줄과 맞지 않을 수 있다. 그래서 '모의' 답임을 문장에 적는다.
CANNED: dict[str, Any] = {
    "tag": {"tags": {f"L{n}": _TAGS[(n - 1) % len(_TAGS)] for n in range(1, 41)}},
    "decisions": {
        "decisions": [
            {
                "title": "데모: 모의 LLM이 고른 결정 후보",
                "content": "데모용 모의 LLM의 고정 답이다. 실제 근거는 출처 줄을 본다.",
                "rationale": "모의 답이라 근거 문장은 비워 두지 않고 출처만 가리킨다.",
                "related": [],
                "source_line": "L1",
            }
        ],
        "actions": [{"text": "데모: 모의 LLM이 고른 할 일 후보", "related": [], "source_line": "L2"}],
    },
    "entity_summary": {"summary": "데모용 모의 요약이다. 최근 언급은 출처에 있다.", "sources": ["M1"]},
    "topic_proposal": {
        "proposals": [
            {"name": "캐시 설계", "why": "데모용 모의 제안: 캐시 이야기가 여러 날 나온다.", "sample_sources": ["L1"], "merge_with": None}
        ]
    },
    "image_ingest": {
        "kind": "screenshot",
        "text": "(모의 LLM) 이미지에서 읽은 글: KB-7 관련 메모",
        "summary": "데모용 모의 추출문이다.",
        "jira_keys": ["KB-7"],
        "people": [],
    },
    "contradictions": {"conflicts": []},
}

DETECTED = {"vision": True, "json_mode": True, "reasoning_toggle": {"method": "none"}, "context_tokens": 32768}

_ENV_LINE = re.compile(r"^environment[ \t]*:[ \t]*(.*?)[ \t]*(?:#.*)?$")


# ---------------------------------------------------------------- 사전 점검


def environment_value(path: Path) -> str | None:
    """config.local.yaml의 맨 위 `environment:` 값만 읽는다(다른 키는 해석하지 않는다). 없으면 None."""
    try:
        text = path.read_bytes().decode("utf-8-sig", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        match = _ENV_LINE.match(line)
        if match:
            return match.group(1).strip().strip("'\"").strip()
    return None


def says_company(path: Path) -> bool:
    value = environment_value(path)
    return value is not None and value.lower() == "company"


def check_allowed(extra_roots: tuple[Path, ...] = ()) -> None:
    """회사 설정이면 거부(7), 샘플·모의 서버가 없으면 거부(4)."""
    for base in dict.fromkeys([CODE_ROOT, *extra_roots]):
        local = Path(base) / config.LOCAL_NAME
        if local.is_file() and says_company(local):
            raise core.PolicyRefused(
                f"{local}의 environment가 company입니다. demo는 집 개발용이라 회사 PC에서는 실행하지 않습니다"
            )
    if not SAMPLE.is_dir():
        raise core.ConfigError(f"{SAMPLE}가 없습니다. demo는 examples/가 있는 저장소 복사본에서만 실행합니다")
    missing = [name for name in MOCK_MODULES if not (TESTS / f"{name}.py").is_file()]
    if missing:
        raise core.ConfigError(
            f"모의 서버 파일이 없습니다: {', '.join(f'tests/{m}.py' for m in missing)}. "
            "demo는 tests/가 있는 저장소 복사본에서만 실행합니다"
        )


def _import_mocks() -> dict[str, Any]:
    tests = str(TESTS)
    if tests not in sys.path:
        sys.path.append(tests)  # 뒤에 붙인다: scripts/ 모듈 이름을 가리지 않게
    import importlib

    return {name: importlib.import_module(name) for name in MOCK_MODULES}


# ---------------------------------------------------------------- 모의 서버


@dataclasses.dataclass
class Mocks:
    jira: Any
    confluence: Any
    llm: Any

    def all(self) -> list[Any]:
        return [m for m in (self.jira, self.confluence, self.llm) if m is not None]

    def stop(self) -> None:
        for mock in self.all():
            try:
                mock.stop()
            except Exception:  # 멈추기 실패는 결과에 영향이 없다
                pass


def start_mocks(modules: Mapping[str, Any], *, fixed_ports: bool) -> Mocks:
    mocks = Mocks(None, None, None)
    plan = (
        ("jira", modules["mock_jira"], {"require_token": TOKEN}),
        ("confluence", modules["mock_confluence"], {"require_token": TOKEN}),
        ("llm", modules["mock_llm"], {"canned": {k: json.dumps(v, ensure_ascii=False) for k, v in CANNED.items()}}),
    )
    try:
        for name, module, modes in plan:
            port = FIXED_PORTS[name] if fixed_ports else 0
            try:
                setattr(mocks, name, module.start(port, **modes))
            except OSError as exc:
                hint = (
                    " tests/mock_all.py가 이미 떠 있으면 끄거나 --fixed-ports 없이 실행하세요"
                    if fixed_ports
                    else ""
                )
                raise core.KbError(f"모의 {name} 서버를 포트 {port or '(임시)'}에 띄우지 못했습니다 ({exc}).{hint}") from exc
    except BaseException:
        mocks.stop()
        raise
    return mocks


def chat_requests(llm_mock: Any) -> list[dict]:
    return [e["json"] for e in llm_mock.log if str(e.get("path", "")).endswith("/chat/completions") and isinstance(e.get("json"), dict)]


def has_image(body: Mapping) -> bool:
    for message in body.get("messages") or []:
        content = message.get("content") if isinstance(message, Mapping) else None
        if isinstance(content, list) and any(isinstance(p, Mapping) and p.get("type") == "image_url" for p in content):
            return True
    return False


def llm_counts(llm_mock: Any) -> dict[str, int]:
    chats = chat_requests(llm_mock)
    return {"chat": len(chats), "vision": sum(1 for body in chats if has_image(body))}


# ---------------------------------------------------------------- 격리


def _close_loggers(under: Path) -> None:
    """core.get_logger가 임시 폴더에 연 로그 파일을 닫는다(Windows는 열린 파일을 지우지 못한다)."""
    prefix = str(under)
    for name, logger in list(logging.Logger.manager.loggerDict.items()):
        if not name.startswith("work-kb.") or not isinstance(logger, logging.Logger):
            continue
        log_dir = getattr(logger, "_kb_log_dir", None)
        if isinstance(log_dir, str) and log_dir.startswith(prefix):
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
            logger._kb_log_dir = None  # type: ignore[attr-defined]


@contextlib.contextmanager
def isolated(work: Path, fake_keyring_module: Any) -> Iterator[None]:
    """가짜 keyring과 고정 시계를 걸고, 끝나면 되돌린다."""
    import keyring

    previous = keyring.get_keyring()
    old_now = os.environ.get("KB_FAKE_NOW")
    keyring.set_keyring(fake_keyring_module.FileKeyring(work / "keyring.json"))
    if old_now is None:
        os.environ["KB_FAKE_NOW"] = DEMO_NOW
    try:
        yield
    finally:
        keyring.set_keyring(previous)
        if old_now is None:
            os.environ.pop("KB_FAKE_NOW", None)
        _close_loggers(work)


def _rmtree(path: Path, attempts: int = 10) -> str | None:
    """지운다. 실패하면 이유를 돌려준다(백신·색인기가 잠깐 잡는 경우가 있어 몇 번 다시 시도)."""
    last: Exception | None = None
    for _ in range(attempts):
        if not path.exists():
            return None
        try:
            shutil.rmtree(path)
            return None
        except OSError as exc:
            last = exc
            time.sleep(0.2)
    return str(last) if path.exists() else None


# ---------------------------------------------------------------- 볼트·설정


def prepare_vault(vault: Path) -> None:
    """샘플 볼트를 복사하고 구조 설정은 저장소의 config.yaml(커밋된 구조 설정)로 맞춘다."""
    shutil.copytree(SAMPLE, vault, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", config.LOCAL_NAME))
    structure = CODE_ROOT / config.CONFIG_NAME
    if structure.is_file():
        shutil.copyfile(structure, vault / config.CONFIG_NAME)
    for sub in ("jira", "wiki", ".state"):
        (vault / sub).mkdir(exist_ok=True)


def demo_local(mocks: Mocks, space_key: str) -> dict:
    ref = lambda name: f"keyring:{SERVICE}/{name}"  # noqa: E731
    return {
        "environment": "home",
        "jira": {"base_url": mocks.jira.base_url, "project_keys": ["KB", "OPS"], "pat_ref": ref("jira"), "trust_env": False},
        "confluence": {
            "enabled": True,
            "base_url": mocks.confluence.base_url,
            "space_key": space_key,
            "parent_page_id": "",
            "pat_ref": ref("confluence"),
            "trust_env": False,
        },
        "llm": {
            "enabled": True,
            "profiles": {
                "main": {"base_url": f"{mocks.llm.base_url}/v1", "model": "mock", "trust_env": False, "detected": dict(DETECTED)},
            },
            "fallback_order": ["main"],
        },
    }


def write_config(vault: Path, mocks: Mocks) -> tuple[config.Config, list[str]]:
    """init --dev 대신: 모의 토큰을 가짜 keyring에 넣고, 개인 스페이스를 D55 순서로 찾아 임시 설정을 쓴다."""
    from lib import secrets as kb_secrets
    from lib.confluence_client import ConfluenceClient

    for name in ("jira", "confluence"):
        kb_secrets.store(SERVICE, name, TOKEN)
    with ConfluenceClient(mocks.confluence.base_url, TOKEN, conn={"trust_env": False}) as client:
        found = client.personal_space()
    if not found.own:
        raise core.ConfigError("모의 Confluence에서 개인 스페이스를 찾지 못했습니다")
    space_key = str(found.own.get("key"))
    config.save_local(vault, demo_local(mocks, space_key))
    cfg = config.load(vault, environ={})  # 사용자의 KB_* 덮어쓰기를 섞지 않는다
    if cfg.environment != "home":
        raise core.PolicyRefused("임시 설정의 environment가 home이 아닙니다")
    lines = [
        f"config.local.yaml(임시): Jira·Confluence·LLM 모두 모의 서버, 비밀값은 keyring:{SERVICE}/… (가짜 keyring 파일)",
        f"개인 스페이스(D55): 사용자 {found.username} → {space_key}",
    ]
    return cfg, lines


# ---------------------------------------------------------------- 결과 트리


def tree_files(vault: Path) -> list[str]:
    """볼트의 파일(상대 경로, `/`). `.state/logs`는 뺀다."""
    out = []
    for path in sorted(vault.rglob("*")):
        rel = path.relative_to(vault).as_posix()
        if path.is_file() and not (rel == ".state/logs" or rel.startswith(".state/logs/")):
            out.append(rel)
    return out


def render_tree(files: list[str]) -> list[str]:
    lines: list[str] = []
    shown: set[tuple[str, ...]] = set()
    for rel in files:
        parts = tuple(rel.split("/"))
        for depth in range(len(parts) - 1):
            folder = parts[: depth + 1]
            if folder not in shown:
                shown.add(folder)
                lines.append("  " * depth + folder[-1] + "/")
        lines.append("  " * (len(parts) - 1) + parts[-1])
    return lines


# ---------------------------------------------------------------- 단계


@dataclasses.dataclass
class Step:
    name: str
    ok: bool = True
    code: int = 0
    lines: list[str] = dataclasses.field(default_factory=list)


def _indent(text: str) -> list[str]:
    return [f"    {line}" for line in text.rstrip("\n").splitlines() if line.strip()]


def _run_step(name: str, fn: Callable[[TextIO], tuple[bool, list[str]]], out: TextIO, index: int, total: int) -> Step:
    """단계를 돌리고 출력(가로챈 stdout·stderr 포함)을 들여 써서 보여 준다."""
    step = Step(name)
    print(f"[{index}/{total}] {name}", file=out, flush=True)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            ok, lines = fn(buf)
        step.ok = ok
        step.lines = lines
        if not ok:
            step.code = 1
    except core.KbError as exc:
        step.ok, step.code = False, exc.exit_code
        step.lines = [f"오류: {core.redact(str(exc)) or type(exc).__name__}"]
    except Exception as exc:  # 데모는 진단 도구다: 예상 못 한 오류도 보여 주고 다음 단계로 간다
        step.ok, step.code = False, 1
        step.lines = [f"예상하지 못한 오류: {type(exc).__name__}: {core.redact(str(exc))}"]
        step.lines += _indent(core.redact(traceback.format_exc()))
    for line in _indent(buf.getvalue()):
        print(line, file=out)
    for line in step.lines:
        print(f"    {line}" if not line.startswith("    ") else line, file=out)
    print(f"    → {'성공' if step.ok else f'실패 (종료 코드 {step.code})'}", file=out, flush=True)
    return step


def _keep_dir(keep: str | os.PathLike) -> Path:
    path = Path(keep).expanduser().resolve()
    if path == CODE_ROOT or path.is_relative_to(CODE_ROOT):
        raise core.ConfigError(f"--keep {path}: 저장소 안에는 둘 수 없습니다. 저장소 밖 폴더를 지정하세요")
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise core.ConfigError(f"--keep {path}: 이미 있고 비어 있지 않습니다. 새 폴더나 빈 폴더를 지정하세요")
    return path


def run(
    *,
    keep: str | os.PathLike | None = None,
    fixed_ports: bool = False,
    out: TextIO | None = None,
    extra_roots: tuple[Path, ...] = (),
) -> dict:
    """데모 한 번. 반환: {exit_code, steps, vault, work_dir, kept, removed, llm, pages, tree, mocks}.

    `mocks`는 멈춘 모의 서버(Jira·Confluence·LLM). 로그와 저장 페이지는 그대로 남아 검사할 수 있다.
    """
    out = out or sys.stdout
    check_allowed(extra_roots)
    keep_path = _keep_dir(keep) if keep is not None else None
    modules = _import_mocks()

    work = Path(tempfile.mkdtemp(prefix=WORK_PREFIX))
    vault = keep_path if keep_path is not None else work / VAULT_NAME
    result: dict[str, Any] = {
        "exit_code": 0,
        "steps": [],
        "vault": vault,
        "work_dir": work,
        "kept": keep_path is not None,
        "removed": False,
        "llm": {"chat": 0, "vision": 0},
        "pages": [],
        "tree": [],
        "mocks": None,
    }
    mocks: Mocks | None = None
    steps: list[Step] = []
    try:
        with isolated(work, modules["fake_keyring"]):
            print("데모: 모의 서버 3개로 전체 흐름을 시연합니다 (임시 볼트, 저장소 파일은 건드리지 않음)", file=out)
            print(f"  임시 볼트: {vault}", file=out)
            print(f"  시계: {os.environ.get('KB_FAKE_NOW')} (샘플 볼트 날짜에 맞춤)", file=out, flush=True)
            prepare_vault(vault)
            mocks = start_mocks(modules, fixed_ports=fixed_ports)
            result["mocks"] = mocks
            steps = _flow(vault, mocks, out)
            result["llm"] = llm_counts(mocks.llm)
            result["pages"] = [p["title"] for p in mocks.confluence.user_pages()]
            result["tree"] = tree_files(vault)
    finally:
        if mocks is not None:
            mocks.stop()
        reason = _rmtree(work)
        result["removed"] = not work.exists()
        if reason:
            print(f"주의: 임시 폴더를 지우지 못했습니다: {work} ({reason})", file=out)

    result["steps"] = steps
    failed = [s for s in steps if not s.ok]
    result["exit_code"] = (failed[0].code or 1) if failed else 0
    _print_result(result, steps, vault, out)
    return result


def _flow(vault: Path, mocks: Mocks, out: TextIO) -> list[Step]:
    """설정 → sync → link → ingest → compile → lint → push → 검증. 실패해도 다음 단계로 간다."""
    import compile as kb_compile
    import confluence_push
    import ingest_images
    import jira_sync
    import link
    import lint
    from lib.jira_client import JiraClient

    state: dict[str, Any] = {}
    names = ["모의 서버", "설정 (init --dev 대신 임시 설정)", "sync", "link", "ingest", "compile", "lint", "push", "검증"]
    total = len(names)
    steps: list[Step] = []

    def servers(buf: TextIO) -> tuple[bool, list[str]]:
        return True, [
            f"Jira {mocks.jira.base_url} · Confluence {mocks.confluence.base_url} · LLM {mocks.llm.base_url}",
        ]

    def setup(buf: TextIO) -> tuple[bool, list[str]]:
        cfg, lines = write_config(vault, mocks)
        state["cfg"] = cfg
        return True, lines

    def need_cfg() -> config.Config:
        cfg = state.get("cfg")
        if cfg is None:
            raise core.ConfigError("설정 단계가 실패해 건너뜁니다")
        return cfg

    def sync(buf: TextIO) -> tuple[bool, list[str]]:
        cfg = need_cfg()
        with JiraClient.from_config(cfg) as client:
            code = jira_sync.run_scope(cfg, client, full=True, dry_run=False, out=buf)
        files = sorted(p.name for p in (vault / "jira").glob("*.md"))
        return code == 0 and bool(files), [f"jira/ 일감 파일 {len(files)}개"]

    def do_link(buf: TextIO) -> tuple[bool, list[str]]:
        summary = link.run(need_cfg())
        link._print_summary(summary)
        return not summary["skipped_files"], []

    def ingest(buf: TextIO) -> tuple[bool, list[str]]:
        report = ingest_images.run(need_cfg(), out=lambda line: print(line, file=buf))
        ok = report["status"] == "ok" and bool(report["processed"]) and not report["failed"]
        return ok, [] if ok else [f"상태 {report['status']}, 읽음 {len(report['processed'])}, 실패 {len(report['failed'])}"]

    def do_compile(buf: TextIO) -> tuple[bool, list[str]]:
        result = kb_compile.run(need_cfg(), out=lambda line: print(line, file=buf))
        ok = result["status"] == "ok" and result["calls"] > 0
        return ok, [] if ok else [f"상태 {result['status']}"]

    def do_lint(buf: TextIO) -> tuple[bool, list[str]]:
        result = lint.run(need_cfg(), llm=True)
        lines = lint.summary_lines(result)
        t5 = result["contradictions"].get("status")
        ok = not result.get("write_error") and t5 in ("ok", "partial")
        return ok, lines + ([] if ok else [f"모순 검사 상태 {t5}"])

    def push(buf: TextIO) -> tuple[bool, list[str]]:
        code = confluence_push.run(need_cfg(), out=buf)
        pages = mocks.confluence.user_pages()
        lines = [f"모의 Confluence 페이지 {len(pages)}개"]
        return code == 0 and bool(pages), lines

    def verify(buf: TextIO) -> tuple[bool, list[str]]:
        counts = llm_counts(mocks.llm)
        pages = mocks.confluence.user_pages()
        jira_files = list((vault / "jira").glob("*.md"))
        checks = [
            (counts["chat"] >= 1, f"모의 LLM 채팅 요청 {counts['chat']}건 (≥1)"),
            (counts["vision"] >= 1, f"그중 이미지 요청 {counts['vision']}건 (≥1)"),
            (bool(pages), f"모의 Confluence 페이지 {len(pages)}개 (≥1)"),
            (bool(jira_files), f"jira/ 일감 파일 {len(jira_files)}개 (≥1)"),
        ]
        return all(ok for ok, _ in checks), [f"{'✓' if ok else '✗'} {text}" for ok, text in checks]

    for index, (name, fn) in enumerate(
        zip(names, (servers, setup, sync, do_link, ingest, do_compile, do_lint, push, verify)), start=1
    ):
        steps.append(_run_step(name, fn, out, index, total))
    return steps


def _print_result(result: Mapping[str, Any], steps: list[Step], vault: Path, out: TextIO) -> None:
    print("", file=out)
    print(f"결과 트리 ({'남겨 둔 볼트: ' + str(vault) if result['kept'] else '임시 볼트, 지웠음'}):", file=out)
    for line in render_tree(result["tree"]):
        print(f"  {line}", file=out)
    print("", file=out)
    ok_steps = sum(1 for s in steps if s.ok)
    print(f"요약: 단계 {ok_steps}/{len(steps)} 성공", file=out)
    failed = [s.name for s in steps if not s.ok]
    if failed:
        print(f"  실패: {', '.join(failed)}", file=out)
    print(f"  모의 LLM 요청: 채팅 {result['llm']['chat']}건 (이미지 {result['llm']['vision']}건)", file=out)
    pages = result["pages"]
    print(f"  모의 Confluence 페이지 {len(pages)}개" + (f": {', '.join(pages)}" if pages else ""), file=out)
    if result["kept"]:
        print(f"  볼트를 남겼습니다: {vault} (설정의 모의 서버 주소는 이제 닿지 않습니다)", file=out)
    else:
        print(f"  임시 폴더 {'지움' if result['removed'] else '남음(지우지 못함)'}: {result['work_dir']}", file=out)
    print(f"종료 코드 {result['exit_code']}", file=out, flush=True)


def main(argv: list[str]) -> int:
    parser = core.make_parser("kb.py demo", "모의 서버 3개로 전체 흐름 시연 (임시 볼트, 저장소 설정·키를 건드리지 않음)")
    parser.add_argument("--keep", metavar="DIR", default=None, help="볼트를 이 폴더(저장소 밖, 새 폴더 또는 빈 폴더)에 만들고 남긴다")
    parser.add_argument("--fixed-ports", action="store_true", help="모의 서버를 고정 포트 8081~8083에 띄운다(기본: 임시 포트)")
    args = parser.parse_args(argv)
    core.refuse_if_agent("demo")
    extra: list[Path] = []
    for value in (args.root, os.environ.get("KB_ROOT")):
        if value:
            extra.append(Path(value).expanduser().resolve())
    return run(keep=args.keep, fixed_ports=args.fixed_ports, extra_roots=tuple(extra))["exit_code"]


if __name__ == "__main__":
    core.run_main(main)
