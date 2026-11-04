"""연결·환경 점검 (`kb.py doctor`).

한 줄에 하나씩 세 상태로 알린다(D32).
- `✓ 이름: 설명`  정상
- `✗ 이름: 문제 → 해결 방법`  문제. 종료 코드 1 (회사 환경의 공개 LLM 호스트면 5)
- `- 이름: 참고`  선택·안내. 종료 코드에 영향 없음

점검은 `CHECKS` 목록 순서로 돈다. 이후 Phase는 목록에 함수를 더한다.
Python·의존성 점검은 외부 패키지를 가져오기 전에 한다. 외부 호출은 lib/net으로만, 짧은 시간 제한으로 한다.
LLM 점검은 `GET {base_url}/models` 한 번뿐이다(D28). llm_client는 쓰지 않는다.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from lib import core

OK, FAIL, INFO = "✓", "✗", "-"
TIMEOUT = 10
CODE_ROOT = Path(__file__).resolve().parents[1]
DEPENDENCIES = (("requests", "requests"), ("yaml", "pyyaml"), ("keyring", "keyring"), ("markdown", "markdown"), ("PIL", "pillow"))
_CORE_MODULES = ("requests", "yaml", "keyring")
_NOT_DETECTED = "탐지 안 됨 (Phase 4 kb.py init --redetect)"


# ---------------------------------------------------------------- 결과 모음


@dataclass(frozen=True)
class Check:
    state: str
    name: str
    text: str
    public_host: bool = False

    def line(self) -> str:
        return f"{self.state} {self.name}: {self.text}"


class Report:
    def __init__(self, echo: Callable[[str], None] | None = None) -> None:
        self.checks: list[Check] = []
        self._echo = echo

    def add(self, state: str, name: str, text: str, *, public_host: bool = False) -> None:
        check = Check(state, name, core.redact(text), public_host)
        self.checks.append(check)
        if self._echo is not None:
            self._echo(check.line())

    def ok(self, name: str, text: str) -> None:
        self.add(OK, name, text)

    def fail(self, name: str, text: str, *, public_host: bool = False) -> None:
        self.add(FAIL, name, text, public_host=public_host)

    def info(self, name: str, text: str) -> None:
        self.add(INFO, name, text)

    def counts(self) -> dict[str, int]:
        out = {OK: 0, FAIL: 0, INFO: 0}
        for check in self.checks:
            out[check.state] += 1
        return out

    def summary(self) -> str:
        c = self.counts()
        return f"{OK} {c[OK]} {FAIL} {c[FAIL]} {INFO} {c[INFO]}"

    def exit_code(self) -> int:
        if any(c.state == FAIL and c.public_host for c in self.checks):
            return 5
        if any(c.state == FAIL for c in self.checks):
            return 1
        return 0

    def as_list(self) -> list[dict[str, str]]:
        return [{"state": c.state, "name": c.name, "text": c.text} for c in self.checks]


@dataclass
class Context:
    root_arg: str | None
    report: Report
    deps_ok: bool = True
    cfg: Any = None  # lib.config.Config
    secrets: dict[str, str | None] = field(default_factory=dict)  # 참조 이름 → 값
    secret_errors: dict[str, str] = field(default_factory=dict)  # 참조 이름 → 해결 안내
    public_profiles: set[str] = field(default_factory=set)

    def vault_root(self) -> Path:
        if self.cfg is not None:
            return Path(self.cfg.root)
        raw = self.root_arg or os.environ.get("KB_ROOT") or str(CODE_ROOT)
        return Path(raw).expanduser()


# ---------------------------------------------------------------- 도우미


def _truthy(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(value)


def _conn(cfg: Any, section: Mapping[str, Any] | None) -> dict[str, Any]:
    """연결 설정 묶음. 상대 경로 ca_bundle은 설정 폴더 기준으로 바꾼다."""
    conn = dict(section or {})
    ca = conn.get("ca_bundle")
    if ca and not Path(str(ca)).expanduser().is_absolute():
        conn["ca_bundle"] = str(Path(cfg.config_dir) / str(ca))
    return conn


def _get(url: str, token: str | None, conn: Mapping[str, Any], *, auth_raises: bool):
    """(응답, None) 또는 (None, '문제 → 해결'). 예외를 내지 않는다."""
    import requests

    from lib import net

    try:
        session = net.session_for(conn, token)
        with session:
            resp = net.request(
                session, "GET", url, timeout=TIMEOUT, max_429=1, auth_raises=auth_raises, allow_redirects=False
            )
            resp.content  # noqa: B018 - 세션을 닫기 전에 본문을 읽어 둔다
            return resp, None
    except core.KbError as exc:
        return None, net.error_text(exc)
    except requests.RequestException as exc:
        return None, core.redact(f"{type(exc).__name__}: {exc}")


def _json(resp) -> Any:
    try:
        return resp.json()
    except ValueError:
        return None


def _profiles_to_check(cfg: Any) -> list[str]:
    order = cfg.get("llm.fallback_order") or []
    if isinstance(order, str):
        order = [x for x in order.replace(",", " ").split() if x]
    names = [str(x) for x in order] if isinstance(order, (list, tuple)) else []
    if "mock" in cfg.profiles():
        names.append("mock")
    return list(dict.fromkeys(names))


# ---------------------------------------------------------------- 점검들

Checker = Callable[[Context], None]


def check(label: str) -> Callable[[Checker], Checker]:
    def wrap(func: Checker) -> Checker:
        func.label = label  # type: ignore[attr-defined]
        return func

    return wrap


@check("Python")
def check_python(ctx: Context) -> None:
    version = ".".join(str(x) for x in sys.version_info[:3])
    if sys.version_info < (3, 13):
        ctx.report.fail("Python", f"{version} ({sys.executable}) → Python 3.13 이상으로 venv를 다시 만드세요")
    else:
        ctx.report.ok("Python", f"{version} ({sys.executable})")
    if sys.version_info >= (3, 15):
        ctx.report.info("Python 3.15", "PyYAML wheel이 아직 없습니다. 회사 배포(wheels/)에는 3.14 또는 3.13을 쓰세요")


@check("의존성")
def check_dependencies(ctx: Context) -> None:
    missing = [pkg for mod, pkg in DEPENDENCIES if importlib.util.find_spec(mod) is None]
    ctx.deps_ok = all(importlib.util.find_spec(mod) is not None for mod in _CORE_MODULES)
    if missing:
        ctx.report.fail("의존성", f"없음: {', '.join(missing)} → pip install -r requirements.txt")
    else:
        ctx.report.ok("의존성", ", ".join(pkg for _, pkg in DEPENDENCIES))


@check("UTF-8 모드")
def check_utf8(ctx: Context) -> None:
    if sys.flags.utf8_mode:
        ctx.report.ok("UTF-8 모드", "켜짐")
    else:
        ctx.report.info("UTF-8 모드", "꺼짐. 환경변수 PYTHONUTF8=1을 설정하면 더 안전합니다")


@check("config.yaml")
def check_config(ctx: Context) -> None:
    if not ctx.deps_ok:
        ctx.report.info("설정", "의존성이 없어 설정·연결 점검을 건너뜁니다")
        return
    from lib import config

    try:
        ctx.cfg = config.load(ctx.root_arg)
    except core.ConfigError as exc:
        ctx.report.fail("config.yaml", str(exc))
        return
    ctx.report.ok("config.yaml", f"읽음 ({Path(ctx.cfg.config_dir) / config.CONFIG_NAME})")


@check("config.local.yaml")
def check_local(ctx: Context) -> None:
    cfg = ctx.cfg
    if cfg is None:
        return
    if not cfg.local_exists:
        ctx.report.fail("config.local.yaml", "없음 → kb.py init 을 실행하세요")
        return
    ctx.report.ok("config.local.yaml", f"environment: {cfg.environment}")


@check("공개 호스트 차단")
def check_public_hosts(ctx: Context) -> None:
    cfg = ctx.cfg
    if cfg is None:
        return
    if cfg.environment != "company":
        ctx.report.info("공개 호스트 차단", "집 환경이라 검사하지 않습니다")
        return
    from lib import config

    blocklist = cfg.blocklist
    for name, profile in cfg.profiles().items():
        url = profile.get("base_url")
        if isinstance(url, str) and config.is_public_host(url, blocklist):
            ctx.public_profiles.add(name)
            try:
                host = urlsplit(url).hostname or url
            except ValueError:
                host = url
            ctx.report.fail(
                f"LLM {name} 주소",
                f"공개 LLM 호스트입니다 ({host}) → 회사 자료가 밖으로 나갈 수 있습니다. 사내 LLM 주소로 바꾸세요 (kb.py init --section llm)",
                public_host=True,
            )
    if not ctx.public_profiles:
        ctx.report.ok("공개 호스트 차단", "공개 LLM 주소 없음")


def _resolve(ctx: Context, label: str, ref: Any) -> None:
    from lib import secrets as kb_secrets

    try:
        value = kb_secrets.resolve(ref, ctx.cfg)
    except core.KbError as exc:
        ctx.secrets[label] = None
        ctx.secret_errors[label] = core.redact(str(exc))
        return
    ctx.secrets[label] = value
    if value is None:
        ctx.secret_errors[label] = kb_secrets.describe_missing(ref)


@check("비밀값")
def check_secrets(ctx: Context) -> None:
    cfg = ctx.cfg
    if cfg is None or not cfg.local_exists:
        return
    items: list[tuple[str, Any, bool]] = [("jira.pat_ref", cfg.get("jira.pat_ref"), True)]
    if _truthy(cfg.get("confluence.enabled"), True):
        items.append(("confluence.pat_ref", cfg.get("confluence.pat_ref"), True))
    llm_on = _truthy(cfg.get("llm.enabled"), False)
    for name, profile in cfg.profiles().items():
        if profile.get("api_key_ref") not in (None, ""):
            items.append((f"llm.profiles.{name}.api_key_ref", profile["api_key_ref"], llm_on))
    for label, ref, counts in items:
        _resolve(ctx, label, ref)
        name = f"비밀값 {label}"
        error = ctx.secret_errors.get(label)
        if error is None:
            ctx.report.ok(name, "설정됨")
        elif counts:
            ctx.report.fail(name, error)
        else:
            ctx.report.info(name, f"{error} (LLM 비활성)")


@check("keyring")
def check_keyring(ctx: Context) -> None:
    if not ctx.deps_ok:
        return
    from lib import secrets as kb_secrets

    ctx.report.info("keyring", kb_secrets.backend_name())


def _base_url(ctx: Context, section: str, name: str) -> str | None:
    url = ctx.cfg.get(f"{section}.base_url")
    if not isinstance(url, str) or not url.strip():
        ctx.report.fail(name, f"{section}.base_url이 없습니다 → kb.py init 을 실행하세요")
        return None
    return url.strip().rstrip("/")


@check("Jira")
def check_jira(ctx: Context) -> None:
    cfg = ctx.cfg
    if cfg is None or not cfg.local_exists:
        return
    base = _base_url(ctx, "jira", "Jira")
    if base is None:
        return
    token = ctx.secrets.get("jira.pat_ref")
    if not token:
        ctx.report.fail("Jira", f"PAT가 없어 확인하지 못했습니다 → {ctx.secret_errors.get('jira.pat_ref', '')}")
        return
    resp, error = _get(f"{base}/rest/api/2/myself", token, _conn(cfg, cfg.get("jira")), auth_raises=True)
    if resp is None:
        ctx.report.fail("Jira", error or "알 수 없는 오류")
        return
    from lib import net

    data = _json(resp)
    if resp.status_code == 200 and isinstance(data, Mapping):
        who = data.get("displayName") or data.get("name") or "?"
        ctx.report.ok("Jira", f"{who} 으로 연결 ({base})")
    elif resp.status_code == 200:
        ctx.report.fail("Jira", "응답이 JSON이 아닙니다 → base_url이 Jira 서버 주소인지 확인하세요")
    else:
        ctx.report.fail("Jira", f"{net.describe_status(resp)} → base_url을 확인하세요 ({base})")


@check("Confluence")
def check_confluence(ctx: Context) -> None:
    cfg = ctx.cfg
    if cfg is None or not cfg.local_exists:
        return
    if not _truthy(cfg.get("confluence.enabled"), True):
        ctx.report.info("Confluence", "비활성")
        return
    base = _base_url(ctx, "confluence", "Confluence")
    if base is None:
        return
    token = ctx.secrets.get("confluence.pat_ref")
    if not token:
        ctx.report.fail(
            "Confluence", f"PAT가 없어 확인하지 못했습니다 → {ctx.secret_errors.get('confluence.pat_ref', '')}"
        )
        return
    resp, error = _get(f"{base}/rest/api/user/current", token, _conn(cfg, cfg.get("confluence")), auth_raises=True)
    if resp is None:
        ctx.report.fail("Confluence", error or "알 수 없는 오류")
        return
    from lib import net

    data = _json(resp)
    if resp.status_code == 200 and isinstance(data, Mapping):
        if data.get("type") == "anonymous" or not data.get("username"):
            ctx.report.fail("Confluence", "익명 사용자로 응답했습니다 → PAT를 확인하세요")
            return
        shown = data["username"]
        if data.get("displayName"):
            shown = f"{shown} ({data['displayName']})"
        ctx.report.ok("Confluence", f"{shown} 으로 연결 ({base})")
    elif resp.status_code == 200:
        ctx.report.fail("Confluence", "응답이 JSON이 아닙니다 → base_url이 Confluence 서버 주소인지 확인하세요")
    else:
        ctx.report.fail("Confluence", f"{net.describe_status(resp)} → base_url을 확인하세요 ({base})")


def _model_ids(data: Any) -> list[str] | None:
    items = data.get("data") if isinstance(data, Mapping) else data
    if not isinstance(items, list):
        return None
    return [str(x.get("id")) for x in items if isinstance(x, Mapping) and x.get("id") is not None]


def _check_profile(ctx: Context, name: str, profiles: Mapping[str, dict]) -> None:
    from lib import net

    label = f"LLM {name}"
    profile = profiles.get(name)
    if profile is None:
        ctx.report.fail(label, f"프로필이 없습니다 → llm.fallback_order에서 빼거나 llm.profiles.{name}을 추가하세요")
        return
    if name in ctx.public_profiles:
        ctx.report.info(label, "공개 LLM 호스트라 호출하지 않았습니다")
        return
    base = profile.get("base_url")
    if not isinstance(base, str) or not base.strip():
        ctx.report.fail(label, f"llm.profiles.{name}.base_url이 없습니다 → kb.py init --section llm")
        return
    base = base.strip().rstrip("/")
    model = str(profile.get("model") or "")
    ref_label = f"llm.profiles.{name}.api_key_ref"
    has_ref = profile.get("api_key_ref") not in (None, "")
    key = ctx.secrets.get(ref_label) if has_ref else None
    key_missing = has_ref and not key
    resp, error = _get(f"{base}/models", key, _conn(ctx.cfg, profile), auth_raises=False)
    if resp is None:
        ctx.report.fail(label, error or "알 수 없는 오류")
        return
    status = resp.status_code
    if status in (401, 403):
        if key:
            ctx.report.fail(label, f"키 거부 ({status}) → 키를 다시 저장하세요 (kb.py init --section llm)")
        elif key_missing:
            ctx.report.fail(label, f"키가 필요합니다 ({status}) → {ctx.secret_errors.get(ref_label, '')}")
        else:
            ctx.report.fail(label, f"키가 필요합니다 ({status}) → 프로필에 api_key_ref를 넣으세요 (kb.py init --section llm)")
        return
    if 300 <= status < 400:
        ctx.report.fail(label, f"{net.describe_status(resp)} → base_url을 최종 주소로 바꾸세요")
        return
    note = " (키 없이 확인)" if key_missing else ""
    if status == 404:
        ctx.report.info(label, f"연결됨, /models 없음 (404){note}")
        return
    if not 200 <= status < 300:
        ctx.report.fail(label, f"{net.describe_status(resp)} → 서버 상태와 base_url을 확인하세요 ({base})")
        return
    ids = _model_ids(_json(resp))
    if ids is None:
        ctx.report.info(label, f"연결됨, 모델 목록을 읽지 못함{note}")
    elif model and model in ids:
        ctx.report.ok(label, f"{model} 목록에 있음 ({base}){note}")
    else:
        ctx.report.info(label, f"연결됨, 모델 목록에 없음 ({model or '모델 미설정'}){note}")


@check("LLM")
def check_llm(ctx: Context) -> None:
    cfg = ctx.cfg
    if cfg is None or not cfg.local_exists:
        return
    if not _truthy(cfg.get("llm.enabled"), False):
        ctx.report.info("LLM", "비활성")
        return
    profiles = cfg.profiles()
    names = _profiles_to_check(cfg)
    if not names:
        ctx.report.fail("LLM", "확인할 프로필이 없습니다 → kb.py init --section llm")
        return
    for name in names:
        try:
            _check_profile(ctx, name, profiles)
        except Exception as exc:  # 프로필 하나의 실패가 나머지를 막지 않게
            ctx.report.fail(f"LLM {name}", f"예상치 못한 오류: {type(exc).__name__}: {exc}")


@check("탐지")
def check_detected(ctx: Context) -> None:
    cfg = ctx.cfg
    if cfg is None or not cfg.local_exists or not _truthy(cfg.get("llm.enabled"), False):
        return
    profiles = cfg.profiles()
    for name in _profiles_to_check(cfg):
        profile = profiles.get(name)
        if profile is None:
            continue
        if profile.get("detected"):
            ctx.report.info(f"탐지 {name}", "기록됨 (재검증은 Phase 4에서 추가됩니다)")
        else:
            ctx.report.info(f"탐지 {name}", _NOT_DETECTED)


@check("쓰기 권한")
def check_write(ctx: Context) -> None:
    state = ctx.vault_root() / ".state"
    probe = state / f".doctor-{os.getpid()}.tmp"
    try:
        state.mkdir(exist_ok=True)  # 루트 자체는 만들지 않는다
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError as exc:
        ctx.report.fail("쓰기 권한", f"{state}: 쓰지 못했습니다 ({exc.strerror or exc}) → 폴더 권한과 동기화 프로그램 잠금을 확인하세요")
        return
    ctx.report.ok("쓰기 권한", str(state))


@check("볼트 경로")
def check_path(ctx: Context) -> None:
    root = ctx.vault_root()
    text = str(root)
    if text.isascii():
        ctx.report.info("볼트 경로", f"{text} (ASCII만)")
    else:
        ctx.report.info("볼트 경로", f"{text} (한글 등 ASCII 아닌 문자 포함, UTF-8로 처리)")


@check("PowerShell 실행 정책")
def check_powershell(ctx: Context) -> None:
    name = "PowerShell 실행 정책"
    system_root = os.environ.get("SystemRoot")
    builtin = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe" if system_root else None
    exe = str(builtin) if builtin is not None and builtin.is_file() else shutil.which("powershell.exe")
    if exe is None:
        ctx.report.info(name, "powershell.exe를 찾지 못했습니다. 클립보드 이미지는 F(파일)로 캡처하세요")
        return
    # pwsh 7에서 물려받은 PSModulePath가 있으면 5.1이 Security 모듈을 못 읽는다. 빼고 실행한다.
    env = {k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"}
    try:
        result = subprocess.run(
            [exe, "-NoProfile", "-NonInteractive", "-Command", "Get-ExecutionPolicy"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT,
            stdin=subprocess.DEVNULL,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        ctx.report.info(name, f"{TIMEOUT}초 안에 응답하지 않았습니다")
        return
    except OSError as exc:
        ctx.report.info(name, f"실행하지 못했습니다 ({exc.strerror or exc})")
        return
    policy = (result.stdout or "").strip().splitlines()
    if result.returncode != 0 or not policy:
        first = next((line.strip() for line in (result.stderr or "").splitlines() if line.strip()), "출력 없음")
        ctx.report.info(name, f"확인하지 못했습니다 ({first[:120]})")
        return
    value = policy[-1].strip()
    note = "클립보드 스크립트는 -ExecutionPolicy Bypass로 실행합니다"
    if value in ("Restricted", "AllSigned"):
        note += ". 그룹 정책이 막으면 F(파일)로 캡처하세요"
    ctx.report.info(name, f"{value} ({note})")


@check("편집기")
def check_editors(ctx: Context) -> None:
    emacs = shutil.which("emacs") or shutil.which("runemacs")
    if emacs:
        client = shutil.which("emacsclient")
        ctx.report.info("Emacs", emacs + (f", emacsclient {client}" if client else ", emacsclient 없음"))
    else:
        ctx.report.info("Emacs", "PATH에 없습니다. emacs/work-kb.el을 쓰려면 Emacs 30 이상을 설치하세요")
    obsidian = shutil.which("Obsidian") or shutil.which("obsidian")
    if obsidian is None:
        local = os.environ.get("LOCALAPPDATA")
        candidate = Path(local) / "Programs" / "Obsidian" / "Obsidian.exe" if local else None
        if candidate is not None and candidate.is_file():
            obsidian = str(candidate)
    if obsidian:
        ctx.report.info("Obsidian", obsidian)
    else:
        ctx.report.info("Obsidian", "찾지 못했습니다. 위키를 보려면 Obsidian을 설치하세요 (선택)")


CHECKS: list[Checker] = [
    check_python,
    check_dependencies,
    check_utf8,
    check_config,
    check_local,
    check_public_hosts,
    check_secrets,
    check_keyring,
    check_jira,
    check_confluence,
    check_llm,
    check_detected,
    check_write,
    check_path,
    check_powershell,
    check_editors,
]


def run_checks(ctx: Context, checks: list[Checker] | None = None) -> Report:
    for func in CHECKS if checks is None else checks:
        try:
            func(ctx)
        except Exception as exc:  # 점검 하나의 오류가 doctor 전체를 멈추지 않게
            label = getattr(func, "label", func.__name__)
            ctx.report.fail(label, f"예상치 못한 오류: {type(exc).__name__}: {exc}")
    return ctx.report


def main(argv: list[str]) -> int:
    parser = core.make_parser("kb.py doctor", "연결·환경 점검. ✓ 정상, ✗ 문제, - 참고")
    parser.add_argument("--json", action="store_true", help="결과를 JSON 목록으로 출력")
    parser.add_argument("--redetect", action="store_true", help="LLM 탐지 다시 하기 (Phase 4)")
    args = parser.parse_args(argv)
    if args.redetect:
        core.refuse_if_agent("doctor --redetect")
        print("LLM 탐지 다시 하기(--redetect)는 Phase 4에서 추가됩니다.")
        return 4
    report = Report(echo=None if args.json else (lambda line: print(line, flush=True)))
    ctx = Context(root_arg=args.root, report=report)
    run_checks(ctx)
    if args.json:
        print(json.dumps(report.as_list(), ensure_ascii=False, indent=1))
    else:
        print(report.summary())
    return report.exit_code()


if __name__ == "__main__":
    core.run_main(main)
