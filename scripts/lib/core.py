"""work-kb 공통 기반. 표준 라이브러리만 쓴다.

오류 계층과 종료 코드, 표준 입출력 UTF-8 설정, 시간대, 원자적 쓰기, 전역 잠금,
파일명 정리, 상태 파일, 로그(비밀값 가림)를 담당한다.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import logging
import logging.handlers
import os
import re
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, NoReturn

# ---------------------------------------------------------------- 오류·종료 코드


class KbError(Exception):
    """사용자에게 한 줄로 보여 주고 종료 코드로 바뀌는 오류."""

    exit_code = 1


class AuthError(KbError):
    exit_code = 2


class NetworkError(KbError):
    exit_code = 3

    def __init__(self, message: str = "", *, after_send: bool = False) -> None:
        super().__init__(message)
        self.after_send = after_send  # 요청이 서버에 닿았을 수도 있음(결과 불확실)


class ConfigError(KbError):
    exit_code = 4


class PublicHostRefused(KbError):
    exit_code = 5


class LockBusy(KbError):
    exit_code = 6


class PolicyRefused(KbError):
    exit_code = 7


class FileBusy(KbError):
    """다른 프로그램이 파일을 잡고 있어 교체하지 못함."""

    exit_code = 6


class ConcurrentEdit(KbError):
    """읽고 나서 쓰기 전에 사람이 파일을 고침."""

    exit_code = 1


# ---------------------------------------------------------------- 비밀값 가림

_SECRETS: set[str] = set()


def register_secret(value: str | None) -> None:
    """로그·오류 메시지에서 가릴 값을 등록한다. 짧은 값은 오탐이 많아 무시한다."""
    if value and len(value) >= 4:
        _SECRETS.add(value)


def redact(text: str) -> str:
    for secret in sorted(_SECRETS, key=len, reverse=True):
        text = text.replace(secret, "***")
    return text


# ---------------------------------------------------------------- 진입점


def setup_stdio() -> None:
    """stdout/stderr를 UTF-8로. pythonw처럼 스트림이 None이면 건너뛴다."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def read_argv_stdin() -> list[str]:
    """stdin에서 UTF-8 JSON 문자열 배열을 읽어 argv로 돌려준다(Emacs 연동용)."""
    raw = sys.stdin.buffer.read()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"--argv-stdin: JSON 배열을 읽지 못했습니다: {exc}") from exc
    if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
        raise ConfigError("--argv-stdin: 문자열 배열이어야 합니다")
    return data


def report_error(message: str) -> None:
    """stderr가 있으면 거기에, 없으면(pythonw.exe 스케줄 작업) `.state/logs/kb.log`에 남긴다."""
    stream = getattr(sys, "stderr", None)
    if stream is not None:
        print(message, file=stream)
        return
    root = os.environ.get("KB_ROOT") or os.getcwd()
    try:
        get_logger(root).error(message)
    except Exception:  # 로그조차 못 쓰면 조용히 종료 코드만 남긴다
        pass


def run_main(main: Callable[[list[str]], int | None], argv: list[str] | None = None) -> NoReturn:
    """스크립트 진입점. KbError를 한국어 한 줄과 종료 코드로 바꾼다."""
    setup_stdio()
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if args and args[0] == "--argv-stdin":
            args = read_argv_stdin() + args[1:]
        code = main(args)
    except KbError as exc:
        message = redact(str(exc)) or exc.__class__.__name__
        report_error(f"오류: {message}")
        code = exc.exit_code
    except KeyboardInterrupt:
        report_error("중단됨")
        code = 1
    sys.exit(int(code or 0))


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:  # argparse 기본(종료 2)은 인증 오류와 겹친다
        raise ConfigError(f"{self.prog}: {message}")


def make_parser(prog: str, description: str) -> argparse.ArgumentParser:
    parser = _Parser(prog=prog, description=description, allow_abbrev=False)
    parser.add_argument("--root", default=None, help="볼트 루트 (기본: KB_ROOT 환경변수 또는 저장소 폴더)")
    return parser


def refuse_if_agent(action: str) -> None:
    """KB_AGENT=1 세션에서는 외부 쓰기·설정 변경을 거부한다."""
    if os.environ.get("KB_AGENT") == "1":
        raise PolicyRefused(f"KB_AGENT=1 세션에서는 '{action}'을(를) 할 수 없습니다")


# ---------------------------------------------------------------- 시간

_FIXED_OFFSETS = {"Asia/Seoul": 9, "UTC": 0}


def resolve_tz(name: str) -> dt.tzinfo:
    """zoneinfo → 고정 표 → OS 로컬 순으로 시간대를 고른다(tzdata 의존성 없음)."""
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # ZoneInfoNotFoundError 포함, tzdata 없는 Windows
        pass
    if name in _FIXED_OFFSETS:
        return dt.timezone(dt.timedelta(hours=_FIXED_OFFSETS[name]), name)
    return _os_local_tz()


def _os_local_tz() -> dt.tzinfo:
    """OS가 알려 주는 현재 로컬 시간대(마지막 대안)."""
    local = dt.datetime.now().astimezone().tzinfo
    return local if local is not None else dt.timezone.utc


def now(tz: dt.tzinfo) -> dt.datetime:
    """현재 시각. 테스트는 KB_FAKE_NOW(ISO 8601)로 고정한다."""
    fake = os.environ.get("KB_FAKE_NOW")
    if fake:
        value = dt.datetime.fromisoformat(fake)
        if value.tzinfo is None:
            value = value.replace(tzinfo=tz)
        return value.astimezone(tz)
    return dt.datetime.now(tz)


# ---------------------------------------------------------------- 해시

_WS = re.compile(r"\s+")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_line(text: str) -> str:
    return _WS.sub(" ", unicodedata.normalize("NFC", text)).strip()


def line_hash(text: str) -> str:
    """공백·유니코드 정규화 뒤의 줄 해시(16자). 원장 키로 쓴다."""
    return sha256_hex(normalize_line(text).encode("utf-8"))[:16]


# ---------------------------------------------------------------- 원자적 쓰기

_RETRY_WINERRORS = {5, 32, 33}  # ACCESS_DENIED, SHARING_VIOLATION, LOCK_VIOLATION


MUST_NOT_EXIST = ""
"""atomic_write(expect_sha256=MUST_NOT_EXIST): 파일이 없을 때만 만든다(있으면 ConcurrentEdit)."""


class _Unreadable(Exception):
    """대상 파일을 지금 읽을 수 없음(다른 프로그램이 잡고 있음)."""


def _current_sha(path: Path) -> str | None:
    """대상의 현재 sha256. 없으면 None. 공유 위반으로 못 읽으면 _Unreadable."""
    try:
        return sha256_hex(path.read_bytes())
    except FileNotFoundError:
        return None
    except PermissionError as exc:
        if getattr(exc, "winerror", None) in _RETRY_WINERRORS:
            raise _Unreadable(str(exc)) from exc
        raise


def _check_expected(path: Path, expect_sha256: str) -> None:
    current = _current_sha(path)
    if expect_sha256 == MUST_NOT_EXIST:
        if current is not None:
            raise ConcurrentEdit(f"{path}: 읽은 뒤에 파일이 생겨 쓰지 않았습니다")
    elif current != expect_sha256:
        raise ConcurrentEdit(f"{path}: 읽은 뒤에 파일이 바뀌어 쓰지 않았습니다")


def _create_exclusive(tmp: Path, path: Path) -> bool:
    """임시 파일을 대상 이름으로 하드 링크해 '없을 때만 만들기'를 원자적으로 한다.

    True면 만들었다. 하드 링크를 못 쓰는 파일 시스템이면 False(호출자가 확인 후 os.replace).
    """
    try:
        os.link(tmp, path)
        return True
    except FileExistsError as exc:
        raise ConcurrentEdit(f"{path}: 읽은 뒤에 파일이 생겨 쓰지 않았습니다") from exc
    except (OSError, NotImplementedError, AttributeError):
        return False


def atomic_write(path: Path | str, data: bytes, *, expect_sha256: str | None = None, retries: int = 8) -> None:
    """같은 폴더의 임시 파일에 쓰고 os.replace. 열린 파일이면 잠시 기다렸다 다시 시도한다.

    expect_sha256가 주어지면 교체를 시도할 때마다 바로 앞에서 현재 파일 해시를 다시 확인하고,
    다르면 ConcurrentEdit를 낸다(사람 편집 보호: 편집기가 저장하느라 파일을 잡고 있던 사이에
    바뀐 내용을 덮지 않는다). `MUST_NOT_EXIST`("")면 파일이 없을 때만 만든다.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if expect_sha256 is not None:
        with contextlib.suppress(_Unreadable):  # 지금 못 읽으면 교체 직전에 다시 확인한다
            _check_expected(path, expect_sha256)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if expect_sha256 == MUST_NOT_EXIST and _create_exclusive(tmp, path):
            return
        delay = 0.05
        for attempt in range(retries + 1):
            try:
                if expect_sha256 is not None:
                    _check_expected(path, expect_sha256)
                os.replace(tmp, path)
                return
            except _Unreadable as exc:
                if attempt == retries:
                    raise FileBusy(f"{path}: 다른 프로그램이 파일을 잡고 있어 쓰지 못했습니다 ({exc})") from exc
            except PermissionError as exc:
                if getattr(exc, "winerror", None) not in _RETRY_WINERRORS or attempt == retries:
                    raise FileBusy(f"{path}: 다른 프로그램이 파일을 잡고 있어 쓰지 못했습니다 ({exc})") from exc
            time.sleep(delay)
            delay = min(delay * 2, 1.0)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)


# ---------------------------------------------------------------- 전역 잠금

_HELD: dict[str, dict[str, Any]] = {}  # 잠금 파일 → {"fd", "depth"} (프로세스 안 재진입)


def _try_lock(fd: int) -> bool:
    if sys.platform == "win32":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        with contextlib.suppress(OSError):
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    with contextlib.suppress(OSError):
        fcntl.flock(fd, fcntl.LOCK_UN)


def _read_owner(lock_path: Path) -> str:
    """잠긴 바이트 0을 피해 16바이트 뒤의 소유자 정보만 읽는다."""
    try:
        fd = os.open(lock_path, os.O_RDONLY)
        try:
            os.lseek(fd, 16, os.SEEK_SET)
            raw = os.read(fd, 4096)
        finally:
            os.close(fd)
        info = json.loads(raw.decode("utf-8")) if raw.strip() else {}
        return f"{info.get('owner', '?')} (pid {info.get('pid', '?')}, {info.get('since', '?')})"
    except (OSError, ValueError):
        return "알 수 없음"


@contextlib.contextmanager
def kb_lock(root: Path | str, owner: str, timeout: float = 60.0) -> Iterator[None]:
    """`.state/kb.lock`에 대한 프로세스 간 배타 잠금. 같은 프로세스에서는 재진입된다."""
    lock_path = Path(root) / ".state" / "kb.lock"
    key = os.path.normcase(os.path.abspath(lock_path))
    held = _HELD.get(key)
    if held is not None:
        held["depth"] += 1
        try:
            yield
        finally:
            held["depth"] -= 1
        return

    override = os.environ.get("KB_LOCK_TIMEOUT")  # 테스트용: 기다리는 시간을 줄인다
    if override:
        with contextlib.suppress(ValueError):
            timeout = min(timeout, float(override))
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT)
    try:
        deadline = time.monotonic() + timeout
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                raise LockBusy(
                    f"다른 작업이 실행 중입니다: {_read_owner(lock_path)}. {timeout:g}초 기다렸습니다"
                )
            time.sleep(0.1)
        info = {"owner": owner, "pid": os.getpid(), "since": dt.datetime.now().isoformat(timespec="seconds")}
        with contextlib.suppress(OSError):
            os.ftruncate(fd, 16)
            os.lseek(fd, 16, os.SEEK_SET)
            os.write(fd, json.dumps(info, ensure_ascii=False).encode("utf-8"))
        _HELD[key] = {"fd": fd, "depth": 1}
        try:
            yield
        finally:
            _HELD.pop(key, None)
            _unlock(fd)
    finally:
        os.close(fd)


# ---------------------------------------------------------------- 파일명·경로

_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# os.path.isreserved(Windows 전용)가 없을 때의 대안. ntpath와 같은 규칙: 첫 '.' 앞, 끝 공백 무시.
_RESERVED = {
    "CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
    *(f"COM{c}" for c in "123456789¹²³"),
    *(f"LPT{c}" for c in "123456789¹²³"),
}  # fmt: skip


def _is_reserved(text: str) -> bool:
    isreserved = getattr(os.path, "isreserved", None)
    if isreserved is not None and isreserved(text):
        return True
    return text.partition(".")[0].rstrip(" ").upper() in _RESERVED


def safe_name(name: str, max_len: int = 80) -> str:
    """Windows에서 쓸 수 있는 파일명 조각으로 다듬는다. 비면 '_'. 예약 이름이면 앞에 '_'(D16)."""
    text = unicodedata.normalize("NFC", name or "")
    text = _FORBIDDEN.sub("", text)
    text = _WS.sub(" ", text).strip(" .")
    text = text[:max_len].rstrip(" .")
    if not text:
        return "_"
    if _is_reserved(text):
        text = "_" + text
    return text


_LINK_UNSAFE = re.compile(r"[\[\]#^|]")


def link_safe_name(name: str, max_len: int = 80) -> str:
    """위키링크 `[[...]]` 안에 들어갈 파일명. safe_name 뒤에 `[ ] # ^ |`를 `-`로 바꾼다.

    Obsidian은 이 문자들을 링크 문법(제목·블록·별칭)으로 읽는다. Emacs 쪽도 같은 규칙을 따로 구현한다.
    """
    return _LINK_UNSAFE.sub("-", safe_name(name, max_len))


def unique_path(path: Path | str) -> Path:
    """이미 있으면 `이름-2.ext`, `이름-3.ext`… 절대 덮어쓰지 않는다."""
    path = Path(path)
    if not path.exists():
        return path
    for i in range(2, 10_000):
        candidate = path.with_name(f"{path.stem}-{i}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise KbError(f"{path}: 빈 이름을 찾지 못했습니다")


_SKIP_NAMES = (
    re.compile(r"^\.#"),  # Emacs 잠금 링크
    re.compile(r"^#.*#$"),  # Emacs 자동 저장
    re.compile(r"~$"),  # 백업
    re.compile(r"^\..*\.tmp$"),  # atomic_write 임시
)


def is_scannable(path: Path | str) -> bool:
    name = Path(path).name
    return not any(pattern.search(name) for pattern in _SKIP_NAMES)


# ---------------------------------------------------------------- 상태 파일


def state_dir(root: Path | str) -> Path:
    return Path(root) / ".state"


def load_state(root: Path | str, name: str) -> dict:
    path = state_dir(root) / f"{name}.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: 상태 파일이 손상되었습니다 ({exc}). 지우면 다시 만들어집니다") from exc


def save_state(root: Path | str, name: str, data: Mapping) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    atomic_write(state_dir(root) / f"{name}.json", payload.encode("utf-8"))


# ---------------------------------------------------------------- 로그


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


class _LazyRotatingHandler(logging.handlers.RotatingFileHandler):
    """첫 기록 때에야 폴더와 파일을 만든다(delay=True). 읽기 전용 명령이 로그 폴더를 만들지 않게."""

    def _open(self):  # type: ignore[override]
        os.makedirs(os.path.dirname(self.baseFilename), exist_ok=True)
        return super()._open()


def get_logger(root: Path | str, name: str = "kb") -> logging.Logger:
    """`.state/logs/kb.log`에 회전 저장. 비밀값은 가려진다. 루트가 바뀌면 핸들러를 바꾼다.

    파일과 폴더는 처음 기록할 때 만든다. 로거를 얻기만 해서는 아무것도 생기지 않는다.
    """
    logger = logging.getLogger(f"work-kb.{name}")
    log_dir = state_dir(root) / "logs"
    if getattr(logger, "_kb_log_dir", None) != str(log_dir):
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
        handler = _LazyRotatingHandler(
            log_dir / f"{name}.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8", delay=True
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        handler.addFilter(_RedactFilter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger._kb_log_dir = str(log_dir)  # type: ignore[attr-defined]
    return logger
