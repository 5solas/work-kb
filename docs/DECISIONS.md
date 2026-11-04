# 결정 기록

> 이 파일은 구축 중 작업 문서입니다. 충돌 시 우선순위: 1장 → 2장 → 부록 A(원 설계) → 지시서. 부록은 Phase 6에서 한국어로 정리합니다.

## 1. 지시서와 달라지는 점

모두 `docs/DECISIONS.md`에 근거와 함께 기록한다.

| 지시서 | 변경 | 이유 |
|---|---|---|
| §2 최신 Python | `.venv`는 3.14, 3.13도 함께 테스트. 3.15는 미지원으로 안내 | PyYAML 6.0.3에 cp315 wheel이 없음(확인함). `wheels/` 경로가 깨짐 |
| §6.8 `work-kb-python` 기본 `py -3` | 기본 nil. `.venv` → `python` → `py -3` 순으로 찾음 | `py -3`이 이 PC에서 실패(확인함) |
| §6.8 utf-8 인수 전달 | 명령줄은 ASCII만, 실제 인수는 stdin JSON(`--argv-stdin`) | Emacs가 한글 인수를 깨뜨림 |
| §6.8 `clip_image.ps1` | `powershell.exe` 5.1 `-STA`로 실행 | pwsh 7에 `-Format` 없음(확인함) |
| §6.8 AHK Win+Shift+C/V, `emacsclient -n -e` | Ctrl+Alt+Shift+C/V, `-n -c -e` | PowerToys가 Win+Shift+C를 점유, 데몬에 보이는 프레임 없음(에이전트 보고, 내 재확인 없음) |
| §5.0 구역 통째 재작성 | 구역 방식 3가지: replace / append(`decisions`, `actions`) / merge(`today`, `carryover`) | 재실행 시 `[x]`와 '확정'이 지워짐 |
| §6.5 think 처리 | `reasoning`, `reasoning_content`, 여는 태그 없는 `</think>`까지 처리 | vLLM 필드명 변경, Nemotron 템플릿이 `<think>`를 미리 채움 |
| §6.11 탐지 | 200 응답은 증거로 치지 않음. 추론 흔적으로 판정하고 토큰 수는 보조 신호. 호출 10회 초과 시 중단 후 `method: none` | 서버가 모르는 파라미터를 조용히 무시함 |
| §6.11 비전 탐지 1×1 PNG | 64×64 빨강 PNG, 답에서 색을 확인 | 모델이 이미지를 무시해도 200이 옴 |
| §5.7 fallback 예시(vision false) | 탐지 결과 우선. 집은 `google/gemma-4-26B-A4B-it` | DeepInfra 태그와 예시가 어긋남, 회사 모델과 가장 가까움 |
| §5.7 `jql_mine` | `updated >= '-30d'`로 따옴표 추가, 증분도 상대 시간 사용 | Jira 프로필 시간대 문제 회피 |
| §6.7 Confluence | `mentions` 제거, `decisions.md`는 '확정' 항목만 게시 | daily 원문과 미확정 제안의 외부 유출 방지 |
| §7 demo | `init --dev` 대신 임시 설정 사용, 저장소 설정을 덮지 않음 | 실제 설정·키 보호 |
| §8 ingest·compile 스케줄 | 회사에서는 비활성으로 등록, 활성화 명령을 문서화 | §9.9와 §9.10(첫 2주는 B단계 제외) 충돌 해소 |
| §4 `.gitignore` | 앱이 생성하는 볼트 내용(`daily`, `inbox`, `raw/assets`, `jira/*.md`, 생성 wiki)을 무시 목록에 추가 | 코드 저장소와 데이터 분리. 필요하면 회사에서 지우면 됨 |
| 도구 잠금 없음 | `.state/kb.lock` + 재시도 포함 원자적 쓰기, 종료 코드 6·7 추가 | 스케줄 작업끼리 충돌, 열린 파일 교체 실패 |
| §8 완료 기준 | 사용자 확인 항목은 다음 Phase를 막지 않음. "자동 관문 통과, 사용자 확인 대기"로 표시 | 추가 질문 금지와 사용자 확인 요구의 충돌 해소 |

## 2. 원 설계 수정 사항

원문 D번호를 다음과 같이 고친다.

**구축 시작**
- D01: `UV_PYTHON_NO_REGISTRY=1`로 `uv venv --seed --managed-python`. 안 지정하면 낡은 레지스트리 항목 때문에 3.13 탐색이 오류로 끝남(확인함). 실패하면 `%APPDATA%`의 uv 3.13.0을 절대 경로로 사용.
- D02: `kb.py` 맨 앞에 표준 라이브러리만 쓰는 사전 점검을 둠. Phase 6에서 cp315 PyYAML wheel 빌드를 시도하고 결과를 기록.
- 코드 루트와 볼트 루트를 분리: `--root`/`KB_ROOT`, Emacs는 `work-kb-root` + `work-kb-vault`.
- `is_public_host`를 Phase 0의 `lib/config.py`로 옮기고 Phase 0 doctor부터 사용. LLM 요청은 `allow_redirects=False`, 호스트 끝의 점 제거.
- `kb_lock`은 프로세스 안에서 재진입 가능. `jira_act`는 네트워크 전송과 `sync_key` 전에 잠금 해제.
- `run_kb`의 하위 프로세스는 `PYTHON_KEYRING_BACKEND`로 `tmp_path`의 파일 기반 가짜 저장소 사용. 실제 저장소가 선택되지 않음을 테스트로 보장.
- 로그·outbox·설정의 토큰 가림 헬퍼 + 표식 토큰으로 검사하는 테스트. `vault` fixture 기본 경로는 한글+공백.

**연결·Jira**
- D20: `extracted` 구역은 매칭하되 정규화하지 않음. 나머지 관리 구역은 건너뜀. front matter와 위키링크 내부도 정규화 제외. inbox는 스캔만 함.
- D52: 쓰기의 401/403은 `/myself`로 재확인. 200이면 `failed`(권한 없음), 401이면 `pending` + 종료 2.
- D51: 최상위 `ORDER BY`가 있으면 보존. `jql_mine`에 있으면 설정 로드 시 거부. 프로젝트 키는 따옴표 처리. mock은 이중 ORDER BY에 400.
- D63: 검색어마다 `"term"*`로 인용, `OperationalError`면 LIKE. `KB-12`, `@김철수`, `foo.bar`, `AND`, 따옴표, 빈 입력을 테스트.
- `_views/*.md` 표 형식과 읽기 함수를 Phase 1에서 고정. Phase 2에 sync → `daily_prepare` 통합 테스트.
- `kb.py today --check-done`으로 경로 지정. `--jql`은 출력만, `--cache`는 저장까지, `--dry-run`은 `last_sync`를 옮기지 않음. `act` 옵션은 Phase 3 시작 전 고정.

**LLM**
- D35: 순서를 기준선 → 토글 후보 → (찾은 끄기 상태로) JSON·비전으로 변경. mock에 "뒤집힘 후 확인 실패" 모드 추가, POST 10회 이하 단언.
- D40: 401/403/404/400/413은 다음 프로필로. `run_task`는 프로필별 입력 한도를 받는 `messages_for(limit)`을 인수로 받음.
- D41: `method: none`이고 기준선이 추론 켜짐이면 `max_output_tokens` 하한 2048, `status`에 표시.
- D48: `/models`에 키를 보내 401/403이면 ✗. 기본 doctor는 기록된 탐지 결과를 3회 이하 호출로 재검증(저장 안 함), `--redetect`만 저장.
- `build_messages` 계약과 AGENTS.md 절 앵커 추가. T4 제안에 안정 id와 상태 부여. T3·T4 `sources`도 입력과 대조 검증.

**샘플·운영**
- W1.5(샘플 데이터)를 Phase 1 첫 묶음으로 당김. Phase 4는 공유 스텁·설정 키 → 4.1+4.2 → 4.3 → 4.4~4.7 순서.
- 맨 `kb.py init`의 담당을 명시: W2.5에서 뼈대, 이후 절마다 확장, W6.2에서 완성. 입력 함수 주입 가능, `tests/test_init_full.py`.
- W4.5가 샘플 볼트에 허구 이미지·드롭 파일·`![[...]]` 줄을 추가. demo는 §7 결과 트리 출력, 환경은 `config.local.yaml`이 명시적으로 company일 때만 거부.
- D58: 작업 이름은 `work-kb-sync` 식의 평면 이름(폴더 생성 권한 미확인). `work-kb-ingest` 추가(§8 누락분).
- D60: 복사 제외에 `jira/.cache.sqlite*`, `__pycache__`, `.pytest_cache`, `tests/` 추가.
- D03: 인터프리터는 `default-directory` 기준 상대 경로 우선. 한글 루트 ERT 추가. AHK는 `#Requires AutoHotkey v2.0`.
- 나머지 저심각도 항목(coverage 결과)은 해당 Phase 작업자에게 그대로 전달.

## 3. 실측·탐지 기록

### Phase 0 관문 (2026-11-05)

- 환경: `.venv` CPython 3.14.10, `.venv313` CPython 3.13.18 (`UV_PYTHON_NO_REGISTRY=1 uv venv --seed --managed-python`). 둘 다 SQLite 3.53.1, FTS5 사용 가능.
- pytest: 양쪽 venv 모두 337개 통과(`live`·`perf`·`demo`·`packaging` 제외).
- `tests/mock_all.py` 기동 후 `kb.py init --dev` → `doctor`: ✓ 15, ✗ 0, `-` 9. Jira(홍길동)·Confluence(honggildong)·mock LLM ✓, DeepInfra `main`(Nemotron 3.5 Super VL)·`fallback`(gemma-4-26B-A4B-it) 모델 목록 ✓. 실제 자격 증명 저장소(`WinVaultKeyring`)에 `work-kb/jira`, `work-kb/confluence` 더미값 저장. `config.local.yaml`에 토큰 문자열 없음.
- 확인된 사실: 열린 파일에 대한 `os.replace`는 PermissionError(재시도로 해결, `test_core.py`). `msvcrt.locking` 경합은 프로세스 2개로 검증. Windows PowerShell 5.1을 pwsh 7에서 띄우면 `PSModulePath`를 지워야 모듈 자동 로드 오류가 없음(doctor가 반영).
- W0.5 추가 결정: `init --dev`는 `DEEPINFRA_API_KEY`가 없거나 네트워크가 막히면 doctor ✗로 종료 1(테스트는 `KB_LLM_FALLBACK_ORDER=[mock]`과 더미 키로 0을 확인). `KB_INIT_LLM_KEY`가 있으면 main과 같은 참조를 쓰는 프로필도 `keyring:work-kb/llm`으로 바뀜. 기존 설정의 `environment`가 비었거나 잘못돼도 company로 보고 `--dev`를 거부(D44). `llm.enabled: false`여도 company 공개 호스트 ✗(종료 5)는 유지. doctor·probe는 리다이렉트를 따르지 않음. D48 보완: 키 참조가 풀리지 않으면 키 없이 `/models`를 조회하고 "(키 없이 확인)"을 표시.
- 미확인: 실제 Jira·Confluence DC, 회사 인증서(`ca_bundle`), 스케줄 작업 환경의 keyring.

### Phase 0 검토·수정 라운드 (2026-11-05)

Opus 3명 독립 검토(지시서 대조 / Windows·인코딩·안전 / 거짓 통과 테스트) → 22건 → 전부 수정. 430개 테스트 통과(양쪽 venv). 이 라운드에서 추가된 결정:

- **공개 호스트 차단은 fail-closed**: `llm.public_hosts_blocklist`가 목록이 아니면 로드 시 `BlocklistError`(ConfigError)이고 company에서는 doctor ✗ + 종료 5. 유효 목록은 코드 ∪ `config.yaml` ∪ `config.local.yaml` ∪ 환경변수의 **합집합**이라 어떤 겹도 줄일 수 없다. `is_public_host`는 `\`, `@`, 공백, 제어 문자, 비ASCII가 든 URL, 파싱 실패, urllib3와 urllib.parse의 호스트 불일치를 모두 공개(거부)로 본다(`config.checked_host`). company에서는 공개 호스트 검사가 끝나기 전에는 LLM 호출을 하지 않는다. doctor·probe는 리다이렉트를 따르지 않고 3xx를 ✗로 본다.
- **D12 보완**: PowerShell 실행 정책은 `Get-ExecutionPolicy -List`로 읽어 MachinePolicy/UserPolicy가 Restricted/AllSigned면 ✗("F(파일) 캡처를 쓰세요"), 그 외는 `-`에 유효 정책과 범위를 표시. 자식 환경에서 `PSExecutionPolicyPreference`·`PSModulePath`를 지운다(부모 pwsh의 Process 범위 Bypass가 새어 들어오던 문제). 5.1 출력은 `oem` 코덱으로 해독.
- **D04 보완**: doctor에 `- 시간대` 줄(설정 vs OS 오프셋, tzdata 없음 표시). uv 관리 Python에는 tzdata가 없어 `Asia/Seoul`은 고정 표(+09:00)를 쓴다.
- **D16 보완**: `safe_name`은 `os.path.isreserved`(3.13+)로 `CON .txt`, `COM¹`, `CONIN$` 같은 이름도 `_` 접두.
- **kb.py `Command`**: 계약대로 `Command(module, func="main", agent_ok=True)`, `help`는 키워드 전용.
- **테스트 격리 강화**: `tests/sitecustomize.py`가 하위 프로세스에도 루프백 전용 소켓 가드를 건다(`KB_ALLOW_NETWORK=1`로 해제). `run_kb(utf8=False)`로 cp949 로캘에서 `setup_stdio`를 검증(`tests/test_cli_encoding.py`). mock Jira·Confluence에 `require_token`, mock LLM에 `redirect_to` 모드. `--argv-stdin` 테스트는 `\uXXXX` 없는 날것의 UTF-8을 보낸다. 인코딩 AST 규칙이 `io.TextIOWrapper`, `os.fdopen`, `tempfile.*`, `logging.*Handler`, `from subprocess import run` 꼴까지 잡는다.
- **managed.py**: 마커 줄 끝의 `\r\r\n`(텍스트 모드로 CRLF를 두 번 변환한 경우)도 마커로 인식.
- 남은 알려진 한계: `KB_LLM_PUBLIC_HOSTS_BLOCKLIST`에 YAML 매핑을 주면 환경변수 파서가 먼저 ConfigError를 내어 종료 1(호출은 없음, 5는 아님). fsync 생략은 테스트로 잡지 못한다.

### Phase 1 관문 (2026-11-05)

- pytest: 양쪽 venv 546개 통과 + 클립보드 2개 실패(아래). ERT 29개는 상속 콘솔·`CREATE_NO_WINDOW` 두 번, `(set-language-environment "Korean")` 아래에서 실행.
- 샘플 볼트(`examples/sample-vault` → 임시 한글 경로 복사): `link` 1회차 원천 7개·언급 43건·새 페이지 8·정규화 6줄, 2회차·3회차 쓰기 0(전체 파일 해시 diff 0). `today`(KB_FAKE_NOW=2026-11-06)가 이월 5건 복사·`[>]` 5건 표시. `status`·`search` 정상.
- perf: 1,000 daily × 20줄. 2회차(warm) 0.14초(한도 2.0초), 1회차(cold, 전부 정규화·페이지 149개 생성) 3.9~4.7초. §6.1의 2초는 warm 기준으로 해석(D: 1회차는 전체 재작성이라 성격이 다름).
- **클립보드**: `clip_image.ps1`의 `[Windows.Forms.Clipboard]::ContainsImage()`가 이 빌드 세션(Claude Code 셸, STA, session 1, UserInteractive)에서 "요청한 클립보드 작업을 수행하지 못했습니다"로 실패한다. pwsh 7 직접 호출도 같다. 테스트 2개(`test_probe_writes_nothing`, `test_save_matches_probe`)는 이 환경에서 실패 → 사용자가 일반 데스크톱 세션에서 확인해야 하는 항목으로 넘긴다(§8 Phase 1 사용자 확인에 이미 포함).
- Phase 1 추가 결정:
  - link: 원천 파일은 크기·수정시각이 캐시와 같으면 읽지 않는다(Defender 첫 열기 비용 때문에 2초 한도를 지키기 위함). 같은 크기로 고치고 mtime을 되돌리면 `--rebuild` 전까지 놓친다. 원장은 `.state/links.json`(계약 형태) + `.state/links_cache.json`(sha·렌더 캐시), 둘 다 재생성 가능. 언급 항목에서 `- `, 체크박스, `HH:MM` 접두는 제거. 다른 위키링크 안의 키(`[[KB-12-설계]]`, 이미지 임베드)는 언급이 아니다. inbox는 정규화하지 않는다. `mentions`가 비면 비운다. `--stub KEY`는 stub만 만들고 스캔하지 않는다(Emacs `o`용).
  - daily_prepare: 이전 carryover에서 옮긴 항목도 원본 줄을 `[>]`로 표시(지운 항목이 되살아나는 것 방지). 모든 이전 carryover 구역을 훑는다. 오늘 파일을 먼저 쓰고 그다음 원본에 `[>]` 표시(오늘 파일 실패 시 아무것도 잃지 않음). 막힘 항목은 `· 막힘 · 마감 MM-DD`, 두 뷰에 다 있으면 막힘 형태 하나만. carryover에는 프로젝트 키가 필요(없으면 종료 4, `--no-carryover`는 예외).
  - Emacs: 볼트 경로는 환경변수가 아니라 stdin JSON 안의 `--root`로 전달(한글 경로가 로캘 코드페이지를 타지 않게). 인터프리터는 상대 `.venv/Scripts/python.exe` + `exec-path` 앞에 루트. 캡처는 `## 기록`의 마지막 비어 있지 않은 줄 뒤에, 제목이 없으면 파일 끝에 제목을 추가. 열린 버퍼에 저장 안 된 변경이 있고 디스크도 바뀌었으면 캡처 거부. `C`는 이름과 설명을 한 번에 입력, 임시 파일은 `<코드 루트>/.state/tmp/clip-<ts>.png`. `F`는 png·jpg·jpeg·gif·webp·bmp.
  - search: 최신 daily부터, `--limit` 기본 100(0=무제한), 코드 블록·관리 구역을 걸러내지 않음, 줄 번호는 `\n` 기준(Emacs grep 버퍼와 일치). status는 아무것도 쓰지 않고 네트워크·keyring을 건드리지 않는다.
  - 샘플 볼트에는 `config.local.yaml` 대신 `config.local.example.yaml`(gitignore 때문). 사용: 임시 폴더로 복사 후 이름 변경.

### Phase 1 검토·수정 라운드 (2026-11-05)

Opus 3명 독립 검토 → 23건 → 전부 수정. 606개 통과 + 클립보드 2개 건너뜀(양쪽 venv). 추가 결정:

- **`atomic_write`**: `expect_sha256`가 있으면 `os.replace` 시도 직전마다 다시 해시를 확인한다(다른 프로그램이 잡고 있는 동안 사람이 저장한 내용을 덮지 않음). `core.MUST_NOT_EXIST`("")는 "없을 때만 생성"(하드링크로 원자적 확인). `KB_LOCK_TIMEOUT`으로 잠금 대기 시간을 바꿀 수 있다(테스트용).
- **link**: 언급 중복 제거는 (파일, 줄 해시) 기준(같은 문장이 다른 날에 있으면 각각 언급). `[[jira/KEY]]`·`[[jira/KEY.md]]`·`|별칭`·`#제목` 꼴도 키로 인식(Obsidian 절대 링크 형식). UTF-8이 아닌 원천은 stderr와 `skipped_files`에 이름을 보고하고 그 파일의 이전 언급은 그대로 둔다. 사람 페이지는 대소문자 무시로 합친다(NTFS). `--dry-run`·`search`는 `.state/logs`를 만들지 않는다(로거 지연 생성).
- **daily_prepare `today` 병합**: 키마다 첫 줄이 현재 또는 이전 렌더링과 정확히 같을 때만 도구 줄로 보고 다시 쓴다. 그 밖의 줄(같은 키의 두 번째 줄, 뷰에서 빠진 미완료 키, 사람이 덧붙인 글)은 순서 그대로 보존. 이전 렌더링은 `.state/today.json`에 기록. 사람이 쓴 줄이 있는 키에는 새 줄을 추가하지 않는다.
- **notes**: 줄 나누기는 `\n`과 홀로 있는 `\r`만(U+2028·`\x0c` 등은 줄 안의 문자). 인라인 코드는 CommonMark 규칙(같은 길이의 백틱 묶음). `core.link_safe_name`: 위키링크 안에 들어갈 이름은 `[ ] # ^ |`를 `-`로.
- **Emacs**: 자산·inbox 이름에 같은 link-safe 규칙. UTF-8이 아닌 daily에는 캡처하지 않고 파일명을 알린다. `t`는 열린 버퍼의 미저장 변경을 먼저 저장하고, 실행 뒤 변경 없는 버퍼는 조용히 되읽는다. BOM으로 시작하는 `## 기록`도 인식. ERT가 두 콘솔 모드의 `locale-coding-system`(cp65001 / cp949)을 단언.
- **status**: 미처리 이미지에 daily `## 기록`의 `![[raw/assets/…]]` 중 사이드카 없는 것도 센다.
- **clip_image.ps1**: 테스트 전용 이음매 `-FromFile <그림>`으로 저장 경로(상대 `-Out`, 폴더 생성, PNG)를 클립보드 없이 검증. `KB_REQUIRE_CLIPBOARD=1`이면 "클립보드를 열 수 없음"이 건너뛰기 대신 실패(사용자 확인용).
- 사용자 확인 대기(D31): Emacs 텍스트·클립보드·파일 캡처 각 1회, 접두 키 교체 1회. 자동 관문은 통과.

### Phase 2 구축 결정 (2026-11-05)

- **Obsidian Bases 문법 확인(§6.9)**: 공식 문서 `obsidian-help` 저장소 `en/Bases/Bases syntax.md`·`Functions.md`·`Views.md`, 마지막 커밋 578f73cc(2026-10-23). 설치된 앱 1.15.0. 확인된 것: `file.inFolder()`는 하위 폴더 포함, `file.hasProperty()`, `not:`, 뷰 필터는 전역 필터와 AND, `groupBy: {property, direction}`. **미확인**: `sort` 키는 문서에 없어 앱이 저장하는 형태를 기억으로 썼음 → 사용자가 Obsidian에서 열어 확인(Phase 2 사용자 확인). `일감.base`는 프로젝트별 그룹 뷰 하나 + "막힘" 뷰(프로젝트 키를 추적 파일에 넣지 않기 위해), 키 열은 `file.name`. `엔티티.base`는 `type` 속성이 있는 wiki/projects·people·topics.
- **jira_client**: 404는 `NotFoundError`(종료 1). 읽기 5xx는 1초 뒤 1회 재시도, 다시 실패면 NetworkError. `search()`는 호출 즉시 첫 페이지를 받아 잘못된 JQL을 바로 알린다. `jira.sprint_field`가 설정에 있으면 `/field` 조회를 건너뛴다(init이 기록). mock 검색은 이슈당 댓글 3개까지만 실어 페이징 경로를 태운다(실 DC가 잘라 보내는지는 미확인).
- **jira_sync**: `synced_at`만 다른 파일은 다시 쓰지 않는다. 기존 `mentions` 바이트·EOL·BOM 보존. `.cache.sqlite`가 없으면 전체 동기화. 쓰지 못한 파일(마커 손상·동시 편집·잠김)이 있으면 `last_sync`를 옮기지 않아 다음 증분이 같은 구간을 다시 받는다. 설명의 제목은 두 단계 내려 `## 설명`·`## 댓글`과 충돌하지 않게 하고, Jira 본문에 `kb:` 마커 꼴이 있으면 앞에 공백을 넣어 구역이 생기지 않게 한다. 프로젝트 `issues` 표는 디스크의 동기화된 `jira/*.md` 전체 기준(stub·완료 제외). 뷰는 프로젝트 범위로 감싸지 않는다. 옵션 조합 `--cache` 단독, `--jql`+`--key`/`--full`, `--key`+`--full`은 종료 4.
- **wikimarkup**: 변환 실패 시 `(Jira wiki markup 변환 실패, 원문)` + 펜스 블록. 강조는 ASCII 영숫자 옆에서는 적용하지 않음(한국어 조사 옆은 적용). `-strike-`, `+underline+`, 여러 줄 표 셀은 변환하지 않음.
- **init 마법사**: 모든 경로가 저장 뒤 doctor를 실행하고 그 종료 코드를 돌려준다. confluence 블록이 없으면 `enabled: false`로 써서 Phase 5 전까지 ✗가 나지 않게 한다. 인증·네트워크 실패 시 대화형은 URL·PAT를 3회까지 다시 묻고, 비대화형은 즉시 2/3/4로 종료. PAT 입력에서 Enter는 기존 저장값 유지. `jql_mine`과 뷰는 선택 뒤 검증하되 문제가 있어도 저장은 한다.
- **search ↔ FTS**: `jira/.cache.sqlite`가 있으면 jira 범위는 캐시만 검색한다(파일 단위 결과: 경로, 줄 1, `키 · 상태 · 제목`). 캐시를 열지 못하면 jira/*.md를 직접 훑는다. daily·inbox·wiki는 항상 파일 스캔(D63).
- **init 동작 변경(Phase 0 테스트 수정)**: `kb.py init`(인수 없음)은 대화형 마법사이고 stdin이 닫혀 있으면 첫 질문에서 취소(종료 1). `--section jira`는 `config.local.yaml`이 없으면 종료 4("먼저 init으로 전체 설정").
- **Emacs `o`**: 페이지가 없거나 `stub: true`면 `sync --key`를 비동기로 돌린 뒤 연다.

### Phase 2 검토·수정 라운드 (2026-11-05)

Opus 3명 독립 검토(지시서·Jira DC API 대조 / 데이터 안전 / 거짓 통과) → 14건 → 전부 수정. 865개 통과 + 클립보드 2개 건너뜀. 추가 결정:

- **범위 밖 일감**: 전체 동기화 때 로컬에 있는 동기화된 일감(stub·완료 제외) 중 범위 검색이 돌려주지 않은 것을 `key in (…)` 50개 묶음(범위로 감싸지 않음, 400이면 `issue()`로 하나씩)으로 다시 받는다. 돌아오지 않는 것(삭제·권한 없음)은 `in_scope: false`, 키가 바뀐 것은 `moved_to: NEW` + 새 파일. `in_scope: false`인 파일은 프로젝트 표·캐시·`일감.base`(`!file.hasProperty("in_scope")`)에서 제외하고 다시 받지 않는다. 나중에 범위에 다시 들어오면 표시가 사라진다. `sync --key KB-12`가 OPS-40을 돌려주면 OPS-40.md를 쓰고 KB-12는 옮김 표시.
- **전체 동기화 조건**(기존 `--full`·캐시 없음·25일 외에): 범위 해시(정렬된 project_keys + jql_mine, `sync.json`의 `scope`)가 없거나 달라짐, `last_sync`/`last_full`이 미래, `last_full`이 `jira.full_every_hours`(기본 24)보다 오래됨. 기존 볼트는 업그레이드 뒤 첫 실행에서 전체 동기화 1회.
- **캐시 잠김**: sqlite `locked`/`busy`는 FileBusy(종료 6, "캐시를 다른 프로그램이 쓰고 있습니다"), 손상 메시지와 구분. 열 때 스키마·메타가 같으면 아무것도 쓰지 않는다.
- front matter의 모든 텍스트 필드는 한 줄(NEL·U+2028·CR·LF → 공백). DC 스프린트 문자열은 첫 값 우선(goal 안의 `,name=` 무시).
- **init PAT**: 입력한 PAT는 `/myself` 성공 전에는 저장하지 않고, 마지막 `config.local.yaml` 저장 직전에 함께 저장(같은 값이면 생략). 실패·취소는 자격 증명 저장소를 건드리지 않는다. stdin이 tty가 아니면 `getpass` 대신 한 줄을 읽고 EOF는 취소(Windows에서 `getpass`가 리다이렉트된 stdin을 무시하기 때문).
- mock 모드 추가: `move()`, `remove()`, `repeat_page_boundary`. 클라이언트는 페이지 경계에서 중복 키를 건너뛴다.
- 미처리(다음 Phase로): Emacs `o`가 `moved_to`를 따라가기; `sync --key`·`--jql --cache`로 받은 범위 밖 일감은 다음 전체 동기화 때 표시됨.
- 사용자 확인 대기(D31): Obsidian에서 `jira/_views/일감.base`·`wiki/엔티티.base` 열어 표·정렬 확인(`sort` 키 문법 미확인).

### Phase 3 구축 결정 (2026-11-05)

- **outbox 결과와 종료 코드** (`scripts/jira_act.py`, `scripts/lib/outbox.py`, §5.8 + `result`·`error_detail`·`payload` 필드):

  | 경우 | 기록 | 종료 |
  |---|---|---|
  | 연결 거부 | pending, attempts+1, flush 중단 | 3 |
  | 5xx | pending, attempts+1, flush 계속 | 3 |
  | 5번째 실패 | failed | 1 |
  | 전환 500 | 즉시 failed | 1 |
  | 429(net 재시도 뒤에도) | pending, 시도 수 안 셈 | 3 |
  | 읽기 타임아웃·전송 뒤 끊김 | pending, `last_error: uncertain`, 일반 flush는 건너뜀(`--retry-uncertain`) | 3 |
  | 401/403 → `/myself` 200 | failed(권한 없음) | 1 |
  | 401/403 → `/myself` 401 | pending, flush 중단 | 2 |
  | 그 밖의 4xx | failed | 1 |
  | project_keys 밖 프로젝트 | 보내지 않음 | 4 |
  | 전환 후보 0개·2개 이상 | 후보 목록 출력 | 4 |
  | 확인 거부·EOF | 큐에 넣지 않음 | 1 |

  `flush --retry-failed`는 attempts를 0으로. 전환 이름 매칭: 이름(대소문자 무시) → id → 대상 상태 이름. 워크로그 기간은 `XhYm`·`Xh`·`Ym`만(일·주는 Jira마다 길이가 달라 거부). `act transitions KEY [--json]`은 읽기라 `KB_AGENT=1`에서도 허용(`list`와 함께).
- **promote**: 일감 생성 뒤 원본 줄에 ` [[NEW]]`를 `expect_sha256`로 덧붙인다. 줄이 바뀌었으면 일감은 만들되 덧붙이지 않고 경고(`linked: false`). 위로 줄이 끼어들거나 `[>] … (이월)` 형태가 되어도 찾는다.
- **알려진 한계**: 전송 성공 뒤 outbox 갱신에 실패하면(잠금 60초 초과 등) 기록이 pending으로 남아 다음 flush가 두 번 보낼 수 있다(경고 출력).
- **mock**: `down` 모드는 소켓을 실제로 닫는다(`go_down()`/`come_up()`). 끊긴 연결은 "불확실"로 보이기 때문. 추가 모드 `write_500_times`, `slow_write_sec`, `transition_500`, `assign_denied`(401), `worklog_denied`(403), `on_write` 훅.
- **`today --check-done`**: `## 오늘`의 `- [x] [[KEY]]`만 읽어 `jira/KEY.md` 상태가 done이 아니면 전환 제안 명령을 출력(`jira.done_transition_names`, 기본 `[Done, 완료]`). `moved_to`를 최대 5단계 따라간다. 캐시 없음·stub이면 동기화 안내. 쓰지 않고 Jira를 부르지 않으며 항상 종료 0. `--json` 지원.
- **Emacs `j`**: transient에 v c t a w n p q S f d. 확인이 필요한 명령(create·transition·promote)은 `y-or-n-p` 뒤 `--yes`로 보낸다(`--argv-stdin`에서는 y/N 프롬프트가 EOF).
- Phase 3 관문(mock): 댓글 즉시 전송 → done → `sync --key`로 캐시의 `## 댓글`에 반영; 다운 → pending(종료 3) → 복구 → flush. 970개 테스트 통과(양쪽 venv). 실패 시 stub이 있으면 그대로 열고, 없으면 `link --stub` 후 연다. 같은 키의 동기화가 진행 중이면 다시 시작하지 않는다.

## 부록 A. 원 설계 (D01~D65, 모듈 계약, Phase 표)

Repo root is `D:\work\work-kb`. Paths in sections 1 to 4 are repo-relative. Nothing was created or changed in this stage.

## 0. Evidence and limits

Basis tags used below:
- **[V]** I re-ran it in this stage.
- **[R]** An investigator reported it and I did not re-run it.
- **[I]** Inferred.

**Re-run in this stage [V]**
- **Interpreters:**
  - `py -3` fails with "Unable to create process ... 3.13.3\python.exe". `python` is 3.15.2.
  - `uv python list --only-installed` shows 3.15.2, CPython 3.12.15 and PyPy.
  - CPython 3.14.10 and 3.13.18 are download-only.
- **Wheels:**
  - `pip index versions pyyaml --only-binary :all:` on 3.15 gives "No matching distribution".
  - With `--python-version 3.14` or `3.13` (`--abi cp314`/`cp313`, `--platform win_amd64`) it resolves 6.0.3. Pillow 13.0.0 resolves for cp314.
- **Timezone:** `zoneinfo.ZoneInfo('Asia/Seoul')` raises `ZoneInfoNotFoundError` on 3.15.2. `utf8_mode` is 1 there.
- **Emacs stdin:** in `emacs -Q --batch`, `make-process` with `:coding '(utf-8-dos . utf-8-unix)` and `json-serialize` sent over stdin round-tripped Hangul and U+1F600 exactly. This held under both the inherited console (`locale=cp65001`) and `CREATE_NO_WINDOW` (`locale=cp949`).
- **Emacs defaults under `-Q --batch`:**
  - `buffer-file-coding-system` is `utf-8-dos` until `(set-language-environment "Korean")`.
  - After that it is `korean-iso-8bit-dos`, and `<f9>` becomes `hangul-to-hanja-conversion`.
  - Before that call, `<f7>`, `<f8>`, `<f9>`, `C-c k` and `C-c c` are all nil. `transient` and `json-serialize` are present.
- **Clipboard:** `Get-Clipboard` has `-Format` only in `powershell.exe` 5.1, not in pwsh 7.
- **Scheduler:** `schtasks /Create /?` lists `/XML`, `/IT` and `/NP`.
- **DeepInfra GET:**
  - 173 models are listed.
  - `nvidia/NVIDIA-Nemotron-3.5-Super-VL`: context 262144, tags include vision and reasoning, price 0.15/0.5.
  - `google/gemma-4-26B-A4B-it`: context 262144, tags include vision and reasoning, price 0.07/0.34.
  - `/models` returns 200 with no key.
- **Git:** global identity is set and `init.defaultBranch` is unset.

**Where I overruled the investigations**
- "Batch cannot reproduce the cp949 locale" is wrong. `CREATE_NO_WINDOW` gives `locale=cp949` in batch, so the encoding test can be automated. It must also call `set-language-environment "Korean"` to reproduce the daemon's file coding.
- "Try 3.15 first" is rejected. No cp315 wheel means no `wheels/` path. 3.14 also defaults to the locale encoding (cp949 here [I, PEP 686 applies from 3.15]), so it exposes missing `encoding=` without needing `-X utf8=0`.
- A generic toggle record is unnecessary. The §5.7 `param` form with dict values covers candidates 1, 2 and 4.
- Base64 path passing is unnecessary. The clipboard script gets an ASCII relative temp path, and Emacs renames the file afterwards.
- Timezone conversion for incremental sync is replaced by relative JQL.
- The live T2 budget of 2048 tokens is rejected, because reasoning shares `max_tokens` [R].

**Not verifiable at this stage**
- Any DeepInfra POST: toggle behaviour, reasoning field name, JSON plus reasoning, image acceptance.
- Real Jira or Confluence DC behaviour.
- A `.base` file rendering.
- Keyring behaviour under a scheduled task.
- `msvcrt` and `os.replace` under contention.
- Task registration.
- AutoHotkey.
- A real clipboard image.
- Whether the uv 3.14 and 3.13 builds include FTS5 (the §6.2 LIKE fallback covers this). The uv download itself is also untested.
- Anything on the company PC.

## 1. Resolved decisions (paste into `docs/DECISIONS.md`)

Principles are the §3 numbers.

**Runtime and platform**

| ID | Decision | Basis |
|---|---|---|
| D01 | Build and test on `.venv` (CPython 3.14). Second gate on `.venv313` (3.13), so the §2 floor is tested. Create both with `uv venv --seed --python X`, so pip exists and matches the DEPLOY path. If the uv download fails, try 3.15.2 with the PyYAML sdist. If that also fails, stop and report. | [V] §2, §9 |
| D02 | Company path: `python -m venv .venv`, then `pip install -r requirements.txt` (or `--no-index --find-links wheels`). `wheels/` holds cp313 and cp314 sets from `pip download --only-binary=:all: --platform win_amd64 --implementation cp --python-version X --abi cpX`. `doctor` flags 3.15 as "no PyYAML wheel". `kb.py` catches `ImportError` and prints the venv command. | [V] §9.2 |
| D03 | `work-kb-python` defaults to nil and deviates from §6.8. Resolve in order: `<root>/.venv/Scripts/python.exe`, then `python` (reject paths containing `WindowsApps`), then `py -3` if it runs. Always pass `-X utf8`. | [V] `py -3` fails; [R] Store alias |
| D04 | No `tzdata`, since §2 limits dependencies. `core.resolve_tz`: `ZoneInfo(name)`, then a fixed table `{Asia/Seoul: +09:00}`, then OS local. `doctor` shows `-` if OS offset differs from config. | [V] §5.7 |
| D05 | Encoding enforcement has two parts: an AST test over `scripts/` and `tests/` that flags missing `encoding=`, and CLI tests run on 3.14 with Korean content. Entry point: `reconfigure` guarded for `None` (pythonw). `doctor` reports `sys.flags.utf8_mode` as `-`, not the env var. | [V] §2, §10; [R] pythonw |
| D06 | Import only as `from lib import secrets as kb_secrets`. Never put `scripts/lib` on `sys.path`. | [I] §4 |
| D07 | `kb_root` resolves against the config folder. Root order: argument, `KB_ROOT`, `parents[2]` of `config.py`. `prompts/` and `AGENTS.md` resolve from code. | [I] §5.7 |
| D08 | Additions to the §4 tree: `scripts/lib/{core,net,notes}.py`, `scripts/{status,search,demo}.py`, `tests/{conftest,fake_keyring,mock_all}.py`, `tests/emacs/test-work-kb.el`, `pytest.ini`, `requirements-dev.txt`, `.gitattributes`, `wiki/엔티티.base`. | §4, §6.9, §6.10 |
| D09 | Dev dependency is pytest only. No `truststore`. Per-connection `ca_bundle` and `trust_env` keys in `config.local.yaml` cover company TLS and proxy. `doctor` separates SSL, proxy and auth failures. | [I] §5.7 |

**Emacs and clipboard**

| ID | Decision | Basis |
|---|---|---|
| D10 | All Emacs-to-Python calls run `python -X utf8 scripts/kb.py --argv-stdin` with `default-directory` set to root. Real arguments go as a JSON array on stdin. Command lines stay ASCII. This replaces the §6.8 utf-8 argv assumption. | [V] round trip; [R] argv loss |
| D11 | `work-kb.el` binds `utf-8-unix` for new files and registers `<root>/.*\.md` in `modify-coding-system-alist` as `utf-8`. Existing files keep their EOL. Scanners report a non-UTF-8 file by name and continue. Enables auto-revert for vault buffers. | [V] Korean default is EUC-KR |
| D12 | `clip_image.ps1` runs via `%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -STA -File`. The script is ASCII only. It takes an ASCII relative path `.state/tmp/clip-<ts>.png` and exits 0 (saved), 2 (no image), 3 (error). Emacs renames and owns all Korean messages. If GPO blocks it: `doctor` ✗, and the hint points to `F`. No extra fallback code. | [V] §6.8; [R] RemoteSigned |
| D13 | AHK uses `emacsclient -n -c -e`, because the daemon has only an invisible frame [R]. Default hotkeys are Ctrl+Alt+Shift+C and V (PowerToys holds Win+Shift+C [R]). README shows `<f8>` instead of `<f9>`, with a "check with `C-h k`" note. | §6.8 |
| D14 | Search chain: `consult-ripgrep`, then `rg` in a grep buffer with the pattern via `-f -`, then `kb.py search`. Never `rgrep` unless GNU find is detected. `markdown-mode` and `consult` are soft dependencies. Custom sentinel so status text stays out of `*work-kb*`. | [R] no grep on PATH |
| D15 | Capture text starting `[ ] ` writes `- [ ] HH:MM 내용`. Parsers treat time as optional. If `daily_prepare` fails, Emacs still creates `## 기록` and captures. | P1, §5.1 |
| D16 | Prompts accept empty input: inbox title becomes the first line (40 chars) or `메모`, and image name becomes `capture`. `safe_name` strips forbidden chars and escapes reserved names via `os.path.isreserved`. Collisions take `-2`, `-3`, never overwrite. | P1, P7 |

**Data and regions**

| ID | Decision | Basis |
|---|---|---|
| D17 | Region modes: replace (`mentions`, `issues`, `summary`, `index`, `extracted`, `proposals`, `lint`), append (`decisions`, `actions`, existing bytes never altered), merge (`today`, `carryover`, done in `daily_prepare`, keeps human state keyed by Jira key or line text). | P6; §5.0 vs §5.5 |
| D18 | Authoritative daily-write allowlist in AGENTS.md: capture append, `daily_prepare` skeleton/regions/`[>]`, link normalisation, promote key append. `compile` and `ingest` stay byte-immutable. `[>]` form keeps the text: `- [>] 개인: 회의실 예약 (2026-11-06로 이월)`. | §5.1, §10 |
| D19 | Carryover source: all earlier dailies' `## 기록` `- [ ]` lines with no key, plus unchecked items in the latest earlier carryover. `[>]` makes reruns idempotent, with no ledger. Skipped days are covered. | §5.1 |
| D20 | Link: each region is a pure function of its sources, sorted deterministically. The ledger is a rebuildable cache. One `core.line_hash()` over normalised text. Skip managed regions, code blocks and URLs for matching and normalising. Truncate before an open `[[`, strip embed `!`, omit a missing time, inbox sources link by filename. `@` needs no word char before it. | P6, §6.1 |
| D21 | Sidecars all go to `inbox/<image-stem>.md` with `source_image`. Under `extracted`, render `jira_keys` in `project_keys` as `[[KEY]]` and people as `@이름`. Images stay in inbox until processed. Duplicate-hash drops go to `_trash/`. | §5.2, §6.5 |
| D22 | Topics: link matches explicit links and literal names or aliases of 2+ chars. `proposals accept` creates `wiki/topics/<name>.md` with `aliases`, and `merge_with` appends an alias. Tags stay in the ledger, and link renders counts into `index` when present. | §5.0, §6.5 |
| D23 | `jira/*.md` is regenerated whole except `mentions` (explicit exception to §5.0). `updated` is rewritten only when a region changed. `decisions.md` and `actions.md` templates carry title and markers. Stubs have `stub: true`, and `o` treats them as no cache. | §5.3, §5.4 |
| D24 | Malformed markers (start without end, duplicates): refuse that file, warn, and show it in lint. Missing headings: `daily_prepare` appends the skeleton below existing text. End of `## 기록` is the line before the next `## ` or EOF, ignoring trailing blanks. | P6 |
| D25 | All managed I/O is bytes. New text uses the file's dominant EOL, BOM is tolerated. Scanners skip `.#*`, `#*#`, `*~`, `.*.tmp`. | §5.0, §7 |
| D26 | One lock `.state/kb.lock` (`msvcrt.locking`, 60s, then exit 6) around write sections only. LLM and network calls run outside it, and sources are reread inside. `core.atomic_write`: same-dir temp, fsync, `os.replace`, retry on winerror 5/32/33 with backoff. `expect_sha256` detects a human edit, and that file is skipped and reported. Emacs capture edits the live buffer. | §8 collisions; [I] |
| D27 | Front matter parses with `yaml.BaseLoader` (all strings), so `on`, `NO` and timestamps survive. `set_fields` edits only named lines. Config uses SafeLoader, normalises reasoning from bool or str, and casts project keys to validated strings. `config.yaml` quotes `"on"` and `"off"`. | §5.7 |

**Phase allocation and gates**

| ID | Decision | Basis |
|---|---|---|
| D28 | Thin stubs are not preemption. Phase 0 `kb.py` has only `init` and `doctor`, and its LLM check is `GET /models` only. Phase 1 `o` opens or creates a stub, with `--key` sync added in Phase 2. `daily_prepare` treats missing `_views` as an empty `today` plus one note line. | §10 vs §8 |
| D29 | Features no Phase claims: DECISIONS and status skeleton P0-P1; `i`, `?`, `kb.py search`, sample vault P1; `init --section jira`, setup.md start P2; `init --section llm`, `--redetect`, `g`, `a`, `proposals accept` P4; `init --section confluence` P5; `--from-env` grows per section; schedule step, `capture.ahk`, setup.md final P6. | §8 |
| D30 | `scripts/demo.py` is built in Phase 5, because the Phase 5 criterion and §6.7 need the flow then. Phase 6 only proves it on a copy. | §8 conflict |
| D31 | User-confirm items (P1 captures, P2 Bases) do not block later Phases. Those Phases are labelled "automated gates passed, user confirmation pending" in DECISIONS and at the top of the report, never "done". | Header vs §8, §10 |
| D32 | `doctor` is three-state: ✓, ✗, `-` (optional or informational, no exit effect). "전부 ✓" means no ✗. `-` covers PYTHONUTF8 unset, no AHK, LLM disabled, `method: none`, no `rg`. Exit 0, 1, or 5 on a public-host ✗. | §6.11, §9.5 |
| D33 | Live is required for Phase 4 here. `KB_REQUIRE_LIVE=1` turns any skip into a failure. Assert schema and capability, never wording. Input is fixtures only. Refuse if environment is company. | [V] key present; §7 vs §8 |
| D34 | Phase 6 done means: full suite on both venvs; `kb.py demo` clean; DEPLOY rehearsed on a temp copy under a Hangul-and-space path from `wheels/` with `--no-index`, with `environment=company` proving public-host ✗; final tree reported. | §8 lacks a criterion |

**LLM**

| ID | Decision | Basis |
|---|---|---|
| D35 | Detection counts POSTs only, cap 10, asserted by a test. Sequence: `json_mode` 1, `vision` 1, baseline 1. Then for each candidate, one call in the direction opposite the baseline. On a flip, one confirming call of the other direction. Worst case 8 calls. 200 is not evidence, and 4xx rejects a candidate. | §6.11; [R] servers ignore params |
| D36 | Distinguishing signal is the trace (`reasoning`, `reasoning_content`, or `</think>` in content). The token rule is a tiebreak only: no trace in either, usage present, on ≥ 2×off, gap ≥ 30 (my starting value, not sourced). `/think` is accepted on trace only. An off call must yield non-empty content. Probe `max_tokens` is 1024. | [R] template has no `/think` |
| D37 | Candidate 4 tests only `reasoning:{enabled}`. `max_reasoning_tokens`, `reasoning_budget_tokens` and `medium_effort` are manual hand-edits, documented in TROUBLESHOOTING. Record shape stays §5.7 `param` (`on_value`/`off_value` may be dicts), plus `system_prefix` and `none`, plus `source` and `evidence`. | §5.7 |
| D38 | Vision probe uses an embedded 64×64 solid red PNG and requires the colour in the answer (deviates from 1×1). JSON probe requires `json.loads` to succeed. Runtime: if reasoning-on plus `response_format` gives 400 or empty content, retry once with prompt-forced JSON and keep that for the run. | [I] §6.11 |
| D39 | `normalize_message`: read `reasoning` or `reasoning_content`. If `</think>` is in content, keep the text after the last one, with or without an opening tag. Empty or None content, or `finish_reason=length`, counts as a schema failure. | [R] vLLM, template |
| D40 | Retry order: `task.profile`, then the rest of `fallback_order`. Connection, timeout, 429 or 5xx moves to the next profile. Schema failure retries once on the same profile (length failure doubles the budget, cap 16384), then moves on. Exhausted gives `skipped`, and after 3 runs `gave_up`, shown by lint and status. Exit 0. | §6.5 |
| D41 | Add `max_output_tokens` per task: tag 64, decisions 8192, entity_summary 2048, topic_proposal 8192, image_ingest 2048, contradictions 8192. Input limit is `min(max_input_tokens, 0.8×context_tokens)`, images excluded. Split by date then line. T1 and T3 keep newest. T4 and T5 merge partials. Context lookup: `metadata.context_length`, `max_model_len`, `context_length`, else 262144 with `source: assumed`. Keep temperature 0. | [V] metadata; §6.5 |
| D42 | Images: convert to RGB and send PNG or JPEG, one image per request, long side 1600. | [R] HF card, §6.5 |
| D43 | Home fallback is `google/gemma-4-26B-A4B-it`. It is the closest to "gemma4-26b 급", has the same context as main and has vision. Detection wins over the §5.7 example. Selection filters `embed` and non-chat. Fallback behaviour at home does not carry over to the company's thinking-less Gemma. | [V] §11 |
| D44 | Company guard lives in `LlmClient.__init__`, the only path to LLM HTTP, covering detection, doctor, ingest, compile and lint. Case-insensitive suffix match at a label boundary. All profiles are checked, and any hit refuses. Effective blocklist is code default ∪ config, so overrides only add. Missing or invalid environment means company. `KB_ENVIRONMENT` is ignored. `env:` refs are ✗ at company, and at home only `DEEPINFRA_API_KEY` is allowed. | §2 |
| D45 | `init --dev` reads the home block of `config.local.example.yaml`, so code holds no DeepInfra URL except the blocklist. It refuses (exit 7) if existing config says company. Stores `mock-token` as the dummy PAT. URL overrides via `KB_INIT_*`. | §6.11, §10 |
| D46 | `demo` copies the sample vault to temp, writes a temp config with `main` pointing at mock_llm, never reads the repo's config, `.state` or `DEEPINFRA_API_KEY`, and refuses at company or when `examples/` is absent. The test asserts mock_llm received over 0 chat and vision requests. | §7 |
| D47 | T2 dedupes decisions and actions on (file, line_hash) and writes `<!-- src:hash -->` per entry, so state can be rebuilt. Whole day is context, consumed lines are marked. Drop output whose `source_line` does not resolve. Reject keys outside `project_keys`. Checkbox and `[>]` lines are not actions. `--dry-run` calls the LLM and writes nothing. | §5.5, §6.5 |
| D48 | Doctor from Phase 4 makes one minimal chat call per profile. `detected` is re-tested only with `--redetect`. `/models` cannot prove a key [V]. | §6.11 |

**Jira, Confluence, operations**

| ID | Decision | Basis |
|---|---|---|
| D49 | `jql_mine` becomes `updated >= '-30d'` (quoted). Sync runs `project in (...) AND (jql_mine)`; `--key` and `--jql` are not wrapped. Each view runs independently: a 400 logs Jira's message, keeps the old file, and exits 4 after other work. `init` and `doctor` validate views with `maxResults=0`. Views can be overridden in `config.local.yaml`. | [R] §5.7, §6.2 |
| D50 | Incremental uses relative JQL `updated >= '-Nm'`, N = minutes since `last_sync` (taken at run start) plus 5, avoiding the profile-timezone problem. `last_sync` older than 25 days means full. `--full` means `jql_mine` scope. | [R] §6.2 |
| D51 | `max_results` is page size. Advance `startAt` by `len(issues)`, append `ORDER BY key ASC`. Page comments when `total` exceeds returned. Sprint field is found from `/field` by `schema.custom == com.pyxis.greenhopper.jira:gh-sprint` and stored as `jira.sprint_field`. Parser accepts string and object forms. | [R] §5.3 |
| D52 | Outbox handling: 401/403 stays pending, exit 2. Other 4xx and transition 500 become `failed` immediately. 5xx and connection refused stay pending, attempts+1. 429 consumes no attempt. Read timeout stays pending with `last_error: uncertain`, skipped unless `--retry-uncertain`. `flush --retry-failed` exists. Worklog `started` serialises as `yyyy-MM-dd'T'HH:mm:ss.SSSZ`. `flush` refuses projects not in `project_keys`. | [R] §6.3 |
| D53 | Promote stores the line hash, refuses on mismatch, and appends ` [[NEW-1]]` to the source line, so carryover stops treating it as personal. Keep `assign`, add transient key `a`. Emacs asks `y-or-n-p` and passes `--yes`. | §6.3, §6.8 |
| D54 | `net.request` is the one retry helper for Jira and Confluence: honour `retry-after`, else exponential backoff with a ceiling. | [R] rate limits |
| D55 | Confluence personal space order is reversed: `/user/current` (stop if anonymous), then `/rest/api/space?spaceKey=~<username>`, then list `type=personal` and require a numbered choice. Empty parent means space homepage. | [R] §6.11 |
| D56 | Push removes `mentions` regions and all markers, and publishes only `상태: 확정` entries of `decisions.md`. Output is XML-checked in the mock. Forbidden folders are refused in code (exit 7). Always `expand=version`. | §6.7 intent |
| D57 | `KB_AGENT=1` refuses external writes and config: `act` writes, `flush`, `push`, `init`, `proposals accept`, `doctor --redetect`. Checked in the scripts too, so direct runs are covered. Tool-run `sync`, `link`, `compile`, `ingest`, `lint` stay allowed. Agents never hand-edit. | §5.6 vs §6.10 |
| D58 | Tasks are created from generated XML (`schtasks /Create /TN work-kb\<name> /XML`): InteractiveToken, WorkingDirectory, StartWhenAvailable, IgnoreNew, `.venv\Scripts\pythonw.exe -X utf8`. Never `/NP` or SYSTEM. Logs in `.state/logs/`. Names: sync, prepare, link, compile, lint. Link 12:30/18:30, compile 12:32/18:32 so failures stay separate. Compile runs T4 when 7+ days have passed. | [V] options; [R] CredWrite |
| D59 | "Confluence from the start" means connected and `enabled: true`. Pushing stays manual. `work-kb\compile` is created disabled at company, enable command in SCHEDULING.md, which resolves §9.9 vs §9.10. | §9, §11 |
| D60 | DEPLOY step 1 adds exclusions: `.venv*`, `.git`, `jira/*.md`, `jira/_views/*.md`, `jira/outbox.jsonl`, generated `wiki/` pages, `inbox/`, `raw/assets/`. | §9.1 |
| D61 | Keyring: strict ref parsing, `len(utf-16-le) <= 2560`, read back after store, reject the `fail` backend, catch all backend errors and diagnose in Korean. No fallback to file or env. | [R] §2, §11 |
| D62 | Env overrides: `KB_<SECTION>_<KEY>`, two levels only, YAML scalars. Reserved: `KB_AGENT`, `KB_ROOT`, `KB_REQUIRE_LIVE`, `KB_SKIP_EMACS`, `KB_FAKE_NOW`, `KB_INIT_*`. `KB_INIT_{ENVIRONMENT,JIRA_URL,JIRA_PAT,JIRA_PROJECTS,CONFLUENCE_URL,CONFLUENCE_PAT,CONFLUENCE_SPACE,LLM_URL,LLM_MODEL,LLM_KEY}`. Secrets go to keyring. | §5.7, §7 |
| D63 | FTS5 with `unicode61`, each term auto-prefixed `term*`, LIKE on zero hits. `kb.py search` adds a pure-Python scan of daily, inbox and wiki. | [R] Korean miss |
| D64 | Bases filter with `file.inFolder`, exclude `_views`, require `file.hasProperty("status_category")` so stubs drop out, and `status_category != "done"`. Syntax checked against obsidian-help commit 578f73cc and app 1.15.0 [R]. `setup.md`: enable Bases, absolute link format, exclude `examples/ tests/ scripts/ emacs/ docs/ prompts/ obsidian/ _trash/ wheels/`. T6 parser resolves bare names in `raw/assets`. | §6.9 |
| D65 | Test design: ports 8081-8083 are for demo and manual use only. pytest uses port 0, `allow_reuse_address=False`. Autouse: remove key, loopback-only sockets, fake keyring. Subprocess env adds a dead `HTTPS_PROXY` and `NO_PROXY=127.0.0.1,localhost`. ERT is added to the tree and run under both console locales. | [V], [R] |

## 2. Shared contracts

These are frozen at each phase start. Changes go through the lead only.

**Exit codes** (`core`)

| Code | Meaning | Status |
|---|---|---|
| 0 | OK, including LLM disabled and skipped units | §6.5 |
| 1 | `doctor` has ✗, user declined, or unexpected error | new |
| 2 | auth | §6.2 |
| 3 | network, including "left pending" | §6.2 |
| 4 | config, usage, JQL 400, missing dependency | §6.2 |
| 5 | company public host | §6.5 |
| 6 | lock busy | new |
| 7 | policy refusal: `KB_AGENT`, forbidden path, demo or `--dev` at company | new |

`clip_image.ps1` has its own 0/2/3, read only by Emacs. `make_parser` overrides argparse so a usage error exits 4 instead of colliding with auth 2.

```python
# lib/core.py — stdlib only
class KbError(Exception): exit_code = 1
class AuthError(KbError): exit_code = 2
class NetworkError(KbError): exit_code = 3   # .after_send: bool = False
class ConfigError(KbError): exit_code = 4
class PublicHostRefused(KbError): exit_code = 5
class LockBusy(KbError): exit_code = 6
class PolicyRefused(KbError): exit_code = 7
class FileBusy(KbError): ...
class ConcurrentEdit(KbError): ...
def setup_stdio() -> None
def run_main(main: Callable[[list[str]], int], argv: list[str] | None = None) -> NoReturn
def make_parser(prog: str, description: str) -> argparse.ArgumentParser
def refuse_if_agent(action: str) -> None
def resolve_tz(name: str) -> datetime.tzinfo
def now(tz: datetime.tzinfo) -> datetime.datetime      # honours KB_FAKE_NOW
def sha256_hex(data: bytes) -> str
def line_hash(text: str) -> str
def atomic_write(path: Path, data: bytes, *, expect_sha256: str | None = None) -> None
def kb_lock(root: Path, owner: str, timeout: float = 60.0) -> ContextManager[None]
def safe_name(name: str, max_len: int = 80) -> str
def unique_path(path: Path) -> Path
def is_scannable(path: Path) -> bool
def load_state(root: Path, name: str) -> dict
def save_state(root: Path, name: str, data: Mapping) -> None   # .state/<name>.json
def get_logger(root: Path) -> logging.Logger                   # .state/logs/, rotating
```
```python
# lib/config.py
CODE_BLOCKLIST: frozenset[str]
@dataclass(frozen=True)
class Config:
    root: Path
    data: Mapping[str, Any]
    environment: Literal["home", "company"]
    def get(self, dotted: str, default: Any = None) -> Any
    def path(self, *parts: str) -> Path
    def code_path(self, *parts: str) -> Path
    @property
    def tz(self) -> datetime.tzinfo
    @property
    def project_keys(self) -> list[str]
    @property
    def blocklist(self) -> frozenset[str]
    def profiles(self) -> dict[str, dict]
    def profile(self, name: str) -> dict
def load(root: Path | None = None, *, environ: Mapping[str, str] | None = None) -> Config
def parse_env_overrides(environ: Mapping[str, str]) -> dict
def save_local(root: Path, data: Mapping[str, Any]) -> None    # refuses secret-looking non-*_ref keys
```
```python
# lib/secrets.py
@dataclass(frozen=True)
class Ref:
    scheme: Literal["keyring", "env"]
    service: str
    name: str
def parse_ref(ref: str) -> Ref
def resolve(ref: str | None, cfg: Config) -> str | None
def store(service: str, username: str, value: str) -> None     # size check, read-back
def backend_name() -> str
def describe_missing(ref: str | None) -> str
```
```python
# lib/frontmatter.py
def split(data: bytes) -> tuple[bytes, bytes]
def parse(data: bytes) -> dict[str, Any]                       # BaseLoader
def render(fields: Mapping[str, Any], eol: bytes = b"\n") -> bytes
def set_fields(data: bytes, updates: Mapping[str, Any]) -> bytes

# lib/managed.py — bytes only
class MarkerError(ValueError): ...
def detect_eol(data: bytes) -> bytes
def spans(data: bytes) -> list[tuple[str, int, int]]           # raises MarkerError
def read_region(data: bytes, name: str) -> bytes | None
def replace_region(data: bytes, name: str, body: str) -> bytes # creates at EOF if absent
def append_to_region(data: bytes, name: str, entries: Iterable[str]) -> bytes
def strip_markers(data: bytes, *, drop_regions: Iterable[str] = ()) -> bytes

# lib/net.py
def session_for(conn: Mapping[str, Any], token: str | None) -> requests.Session
def request(session, method: str, url: str, *, timeout: float = 30, max_429: int = 5, **kw) -> requests.Response
    # 401/403 -> AuthError; connect/read failure -> NetworkError(after_send=...); other statuses returned

# lib/notes.py
@dataclass(frozen=True)
class NoteLine:
    rel: str; lineno: int; text: str; day: str | None
    time: str | None; hash: str; region: str | None; in_code: bool
def iter_lines(rel: str, data: bytes) -> Iterator[NoteLine]
def section_span(data: bytes, heading: str) -> tuple[int, int] | None
def record_lines(rel: str, data: bytes) -> list[NoteLine]
def daily_path(cfg: Config, day: datetime.date) -> Path
def iter_sources(cfg: Config) -> Iterator[tuple[Path, bytes]]  # logs and skips undecodable files
def daily_skeleton(day: datetime.date) -> bytes
def ensure_page(cfg: Config, kind: str, name: str | None = None, *, title: str | None = None) -> Path
```
```python
# lib/jira_client.py
class JqlError(ConfigError): ...
def parse_sprint(value: Any) -> str | None
class JiraClient:
    def __init__(self, base_url: str, pat: str, *, conn: Mapping | None = None, page_size: int = 500)
    @classmethod
    def from_config(cls, cfg: Config) -> "JiraClient"
    def myself(self) -> dict
    def projects(self) -> list[dict]
    def fields(self) -> list[dict]
    def server_info(self) -> dict
    def sprint_field_id(self) -> str | None
    def search(self, jql: str, *, fields: list[str]) -> Iterator[dict]
    def validate_jql(self, jql: str) -> str | None             # error text or None
    def issue(self, key: str, *, fields: list[str] | None = None) -> dict
    def comments(self, key: str) -> list[dict]
    # added in Phase 3
    def add_comment(self, key: str, body: str) -> dict
    def transitions(self, key: str) -> list[dict]
    def do_transition(self, key: str, transition_id: str) -> None
    def assign(self, key: str, username: str) -> None
    def add_worklog(self, key: str, seconds: int, comment: str, started: datetime.datetime) -> dict
    def create_issue(self, project_key: str, summary: str, issuetype: str, **fields) -> dict

# lib/wikimarkup.py
class WikimarkupError(ValueError): ...
def to_markdown(text: str) -> str
def convert_or_raw(text: str) -> str                           # failure -> fenced original
```
```python
# lib/llm_client.py
@dataclass(frozen=True)
class ChatResult:
    content: str; reasoning: str | None; finish_reason: str | None
    usage: dict | None; profile: str; model: str
@dataclass(frozen=True)
class TaskOutcome:
    status: Literal["ok", "skipped", "disabled"]
    data: dict | None; profile: str | None; attempts: list[dict]
class ChatHttpError(KbError):
    status: int; body: str
def is_public_host(url: str, blocklist: Iterable[str]) -> bool
def assert_host_allowed(cfg: Config, url: str) -> None         # used by init before any call
def normalize_message(message: Mapping) -> tuple[str, str | None]
def estimate_tokens(text: str) -> int                          # len/2.5
def split_by_date(items: Sequence[tuple[str, str]], limit: int) -> list[list[tuple[str, str]]]
class LlmClient:
    def __init__(self, cfg: Config)                            # raises PublicHostRefused
    @property
    def enabled(self) -> bool
    def connected_profiles(self) -> list[str]
    def list_models(self, profile: str) -> list[dict] | None
    def chat(self, profile: str, messages: list[dict], *, reasoning: bool | None = None,
             json_mode: bool = False, body_extra: Mapping | None = None,
             max_tokens: int = 1024, timeout: float | None = None) -> ChatResult
    def run_task(self, task: str, messages: list[dict], *, validate: Callable[[dict], None],
                 needs_vision: bool = False) -> TaskOutcome
    def input_limit(self, task: str, profile: str | None = None) -> int

# lib/llm_detect.py
MAX_POSTS = 10
def has_trace(result: ChatResult) -> bool
def detect_context(client: LlmClient, profile: str) -> dict
def detect_json(client: LlmClient, profile: str, budget: "Budget") -> dict
def detect_vision(client: LlmClient, profile: str, budget: "Budget") -> dict
def detect_reasoning(client: LlmClient, profile: str, budget: "Budget") -> dict
def detect(client: LlmClient, profile: str) -> dict            # the `detected` block
```

**`scripts/kb.py`**
- `COMMANDS: dict[str, Command]`, where `Command(module, func="main", agent_ok=True)`, imported lazily.
- Every script exposes `main(argv: list[str]) -> int` and runs directly via `core.run_main`.
- `--argv-stdin` as the first argument reads `sys.stdin.buffer` as a UTF-8 JSON array and uses it as argv.

| Command | Target | Phase | Under `KB_AGENT=1` |
|---|---|---|---|
| `init` | `init.main` | 0 | refused |
| `doctor` | `doctor.main` | 0 | `--redetect` refused |
| `today` | `daily_prepare.today_main` | 1 | allowed |
| `link` | `link.main` | 1 | allowed |
| `status` | `status.main` | 1 | allowed |
| `search` | `search.main` | 1 | allowed |
| `sync`, `jira_sync` | `jira_sync.main` | 2 | allowed |
| `act` | `jira_act.main` | 3 | only `list` allowed |
| `compile` | `compile.main` | 4 | allowed |
| `ingest` | `ingest_images.main` | 4 | allowed |
| `proposals` | `compile.proposals_main` | 4 | `accept` refused |
| `lint` | `lint.main` | 5 | allowed |
| `push` | `confluence_push.main` | 5 | refused |
| `demo` | `demo.main` | 5 | refused |

**Cross-script functions**
- `link.run(cfg, *, dry_run=False) -> dict`
- `daily_prepare.prepare(cfg, day) -> Path`
- `jira_sync.sync_key(cfg, client, key) -> Path`
- `compile.SCHEMAS: dict[str, Callable[[dict], None]]`

**Mocks** (`tests/mock_*.py`)
- `start(port: int = 0, **modes) -> RunningMock`, with `.base_url`, `.port`, `.log`, `.modes`, `.stop()`.
- `__main__` binds the §7 fixed port.
- `tests/mock_all.py` starts all three.

**`tests/conftest.py`** (lead-owned) provides `vault`, `mock_jira`, `mock_confluence`, `mock_llm`, `run_kb`, `fake_now`.

**`emacs/work-kb.el`** internal contract
- `(work-kb--run ARGS &key on-exit buffer)`: the sole process entry point. ARGS is a list of strings, sent via D10.
- `(work-kb--python-command)`: resolves the interpreter per D03.
- `work-kb--now-function`: time seam for tests.

## 3. Phases

Conventions:
- **L** = lead step, sequential, before workers.
- **W** = concurrent workstream. W-groups run in the order shown.
- **I** = sequential integration, after workers.
- Workers run only their own test files and never touch git.

**Gate shorthand**
- `G` means:
  ```
  .\.venv\Scripts\python -m pytest -q
  .\.venv313\Scripts\python -m pytest -q
  ```
- The default `addopts` excludes `live`, `perf`, `demo` and `packaging`.
- ERT runs inside the default suite via `tests/test_emacs_ert.py`, using both inherited console and `CREATE_NO_WINDOW`, with stdin set to `DEVNULL`.

### Phase 0: skeleton and config

**L**
- `git init -b main`.
- Both venvs.
- `.gitignore`, `.gitattributes`, `pytest.ini`, `requirements.txt`, `requirements-dev.txt`.
- `.gitkeep` in empty dirs.
- `docs/DECISIONS.md`.
- `scripts/lib/__init__.py`, `scripts/lib/core.py`.
- `tests/conftest.py`, `tests/fake_keyring.py`, `tests/test_core.py`, `tests/test_encoding_rule.py`.

`test_core.py` covers held-handle replace retry and lock contention, which replaces what could not be checked in this stage.

| W | Owned files | Tests |
|---|---|---|
| 0.1 config | `config.yaml`, `config.local.example.yaml`, `scripts/lib/config.py`, `scripts/lib/secrets.py` | `tests/test_config.py`, `tests/test_secrets.py` |
| 0.2 files | `scripts/lib/managed.py`, `scripts/lib/frontmatter.py` | `tests/test_managed.py`, `tests/test_frontmatter.py` |
| 0.3 mocks | `tests/mock_jira.py`, `tests/mock_confluence.py`, `tests/mock_llm.py`, `tests/mock_all.py` | `tests/test_mocks.py` |
| 0.4 agent docs | `AGENTS.md`, `CLAUDE.md` | none |
| then 0.5 connect | `scripts/lib/net.py`, `scripts/init.py` (`--dev`), `scripts/doctor.py` | `tests/test_init_dev.py`, `tests/test_doctor.py` |

**I:** `scripts/kb.py` (init, doctor, `--argv-stdin`), `tests/test_kb_dispatch.py`.

**Gate**
- `G`.
- Then, with `python tests/mock_all.py` running, `.\.venv\Scripts\python scripts\kb.py init --dev` and `doctor`. Expect mock ×3 ✓ and main ✓ (key resolves, reachable, model listed, per D28). Save the transcript in the report.
- This writes only `mock-token` dummies to the real Credential Manager under service `work-kb`.

**User:** nothing.

### Phase 1: capture and link

**L:** `scripts/lib/notes.py`, `tests/test_notes.py`.

| W | Owned files | Tests |
|---|---|---|
| 1.1 link | `scripts/link.py` | `tests/test_link.py`, `tests/test_link_perf.py` (`perf`) |
| 1.2 daily | `scripts/daily_prepare.py` | `tests/test_daily_prepare.py` |
| 1.3 emacs | `emacs/work-kb.el` (c, C, F, i, t, s, o, l, ?, prefix rebind) | `tests/emacs/test-work-kb.el`, `tests/test_emacs_ert.py` |
| 1.4 clipboard | `scripts/clip_image.ps1` | `tests/test_clip_image.py` |
| 1.5 samples and CLI | `examples/sample-vault/**`, `tests/fixtures/notes/**`, `scripts/search.py`, `scripts/status.py` | `tests/test_search.py`, `tests/test_status.py` |

**I:** `scripts/kb.py` (today, link, status, search).

**Gate**
- `G`, including sample-vault link second-run with zero writes.
- `.\.venv\Scripts\python -m pytest -m perf -q`. The warm run must be ≤ 2.0 s (1,000 dailies × 20 lines). Report the cold number and do not relax it automatically.

**User** (recorded as pending per D31)
1. Text capture: `C-c k c`, enter `테스트 [[KB-12]] @김철수`, `C-c C-c`. Check the line ends `## 기록` in UTF-8.
2. Clipboard image: Win+Shift+S, `C-c k C`, enter a name. Check the PNG and the `![[...]]` line. Then copy plain text, press `C-c k C`, and check you get a message with no new file.
3. File image: `C-c k F` from dired.
4. Prefix: set `work-kb-prefix-key` to `C-c c`, reload, check, then revert.

### Phase 2: Jira read

| W | Owned files | Tests |
|---|---|---|
| 2.1 client | `scripts/lib/jira_client.py`, `tests/mock_jira.py`, `tests/fixtures/jira/**` | `tests/test_jira_client.py` |
| 2.2 markup | `scripts/lib/wikimarkup.py` | `tests/test_wikimarkup.py` |
| 2.3 obsidian | `jira/_views/일감.base`, `wiki/엔티티.base`, `obsidian/setup.md` | `tests/test_bases.py` |
| then 2.4 sync | `scripts/jira_sync.py` | `tests/test_jira_sync.py` |
| 2.5 wizard | `scripts/init.py` (`--section jira`, `--from-env`), `scripts/doctor.py` (Jira, JQL, sprint) | `tests/test_init_jira.py` |
| 2.6 emacs | `emacs/work-kb.el` (`o` sync), `tests/emacs/test-work-kb.el` | same |

**I:** `scripts/kb.py` (sync), `scripts/search.py` (FTS), `scripts/status.py` (last sync).

**Gate:** `G`, covering full, incremental, mentions byte-preservation and `--jql`.

**User:** open the sample vault in Obsidian after sync, open both `.base` files, and confirm columns, no Done issue, grouping by KB and OPS, and that `updated` sorts as a date. Note the app version.

### Phase 3: Jira write

| W | Owned files | Tests |
|---|---|---|
| 3.1 act | `scripts/jira_act.py`, `scripts/lib/jira_client.py`, `tests/mock_jira.py` | `tests/test_jira_act.py`, `tests/test_jira_client_write.py` |
| 3.2 check-done | `scripts/daily_prepare.py` | `tests/test_check_done.py` |
| 3.3 emacs | `emacs/work-kb.el` (transient), `tests/emacs/test-work-kb.el` | same |

**I:** `scripts/kb.py` (act), `scripts/status.py` (outbox).

**Gate:** `G`, including refused connection on a kept port, then pending, recovery, flush, done, cache updated.

**User:** optional, `C-c k j` against mocks to see the menu render. Non-blocking.

### Phase 4: compile

| W | Owned files | Tests |
|---|---|---|
| 4.1 mock | `tests/mock_llm.py` (all §7 modes plus accept-and-ignore per candidate, `reasoning` field, orphan `</think>`, `content: null`, system-prefix, reasoning-object, json reject and json ignored, image ignored, no `/models`, no usage, always reasoning, call counter, connection and 5xx) | `tests/test_mock_llm.py` |
| 4.2 client | `scripts/lib/llm_client.py` | `tests/test_llm_client.py`, `tests/test_company_guard.py` |
| 4.3 detect | `scripts/lib/llm_detect.py` | `tests/test_llm_detect.py` |
| then 4.4 compile | `scripts/compile.py`, `prompts/tag.md`, `prompts/decisions.md`, `prompts/entity_summary.md`, `prompts/topic_proposal.md`, `tests/fixtures/llm/**` | `tests/test_compile.py` |
| 4.5 images | `scripts/ingest_images.py`, `prompts/image_ingest.md`, `tests/fixtures/images/**`, `tests/fixtures/make_images.py` | `tests/test_ingest_images.py` |
| 4.6 wizard | `scripts/init.py` (`--section llm`, `--redetect`), `scripts/doctor.py` (LLM) | `tests/test_init_llm.py` |
| 4.7 emacs | `emacs/work-kb.el` (g, a), `tests/emacs/test-work-kb.el` | same |

**I**
- `scripts/kb.py` (compile, ingest, proposals).
- `scripts/status.py` (images, proposals, detection).
- `scripts/link.py` (tag counts in `index`).
- `config.yaml` (`max_output_tokens`).
- `AGENTS.md` (schemas).
- `tests/test_live.py`.

**Gate**
- `G`, including all candidates, ≤10 POSTs worst case, `enabled: false`, company refusal for every caller, and daily and raw byte immutability.
- Then `$env:KB_REQUIRE_LIVE='1'; .\.venv\Scripts\python -m pytest -m live -q`.
- Fictional fixtures only, session cap 20 calls. Cost is estimated under one US cent [R], with image tokens unknown.
- Record the toggle, reasoning field and fallback result in DECISIONS.

**User:** view the 3 fixture images once to confirm they are fictional.

### Phase 5: lint and share

| W | Owned files | Tests |
|---|---|---|
| 5.1 lint | `scripts/lint.py`, `prompts/contradictions.md` | `tests/test_lint.py` |
| 5.2 push | `scripts/confluence_push.py`, `tests/mock_confluence.py` | `tests/test_confluence_push.py` |
| 5.3 wizard | `scripts/init.py` (`--section confluence`), `scripts/doctor.py` (Confluence) | `tests/test_init_confluence.py` |
| then 5.4 demo | `scripts/demo.py` | `tests/test_demo.py` (`demo`) |

**I:** `scripts/kb.py` (lint, push, demo), `scripts/status.py`.

**Gate:** `G`, then `.\.venv\Scripts\python -m pytest -m demo -q`.

**User:** nothing.

### Phase 6: packaging

| W | Owned files | Tests |
|---|---|---|
| 6.1 docs | `docs/README.md`, `docs/DEPLOY.md`, `docs/TROUBLESHOOTING.md` | `tests/test_docs.py` |
| 6.2 schedule | `docs/SCHEDULING.md`, `scripts/init.py` (schedule step, XML) | `tests/test_schedule.py` (parses output, never registers) |
| 6.3 packaging | `.gitignore`, `tests/test_packaging.py` (`packaging`) | same |
| 6.4 finish | `emacs/capture.ahk`, `obsidian/setup.md`, `AGENTS.md` | `tests/test_repo_tree.py` (§4 paths, no secrets, fixture URLs limited) |

**I:** `docs/DECISIONS.md` final, including the pending-user list.

**Gate**
- `G`, then `-m "live"` with `KB_REQUIRE_LIVE=1`, then `-m perf`, `-m demo`, `-m packaging`.
- Then the D34 rehearsal under a temp path such as `<temp>\한글 경로 (테스트)\work-kb`.

**User**
- Run one generated `/Create` on approval, then `/Run`, `/Query` and `/Delete`.
- Optional: AHK.
- Everything in §9, at the company.

## 4. Repo setup

- **Init:** the lead runs `git init -b main` as the first act of Phase 0 and commits the brief alone first. Identity is already configured [V]. No remote, no push.
- **`.gitattributes`:** `* -text`, because system `core.autocrlf=true` [R] would otherwise break CRLF byte tests. CRLF cases are generated in test code.
- **`.gitignore`:**
  - §4 list: `config.local.yaml`, `.state/`, `jira/.cache.sqlite`, `.env`, `_trash/`, `wheels/`.
  - Tooling: `.venv*/`, `__pycache__/`, `.pytest_cache/`, `.obsidian/`.
  - Runtime vault content: `/daily/*.md`, `/inbox/*`, `/raw/assets/*`, `/jira/*.md`, `/jira/_views/*.md`, `/jira/outbox.jsonl`, and generated pages under `/wiki/`.
  - Keep the `.base` files and `.gitkeep` tracked.
- **Cadence:**
  - Only the lead commits, using explicit paths.
  - One commit per finished W-group, one per integration step.
  - Annotated tag `phase-N` only after that phase's gate passes.
  - Hooks are not skipped.
- **Worktrees:** I agree they are unnecessary. Ownership is disjoint within a phase, shared files change only in L or I steps, and one committer avoids `index.lock` races. A worker's half-written file can fail someone else's run, which is why workers run only their own tests.
- **AGENTS.md scope:** it must state that it governs the runtime editor. The builder follows the brief, even though a build session will read it through `CLAUDE.md`.

## 5. Top risks

| # | Risk | Mitigation |
|---|---|---|
| 1 | Machine rewrites destroy human state (`[x]`, 확정, unticked actions) | D17, D24, D26, with byte-level tests per region mode and rerun tests |
| 2 | Company data reaches a public LLM | D44 choke point, fail-closed environment, additive blocklist, tests for every caller. D46 keeps demo out |
| 3 | False-green tests (encoding, silent skips, batch locale, mocks accepting everything) | 3.14 and 3.13 gates, AST rule, ERT under both locales with Korean environment set, `KB_REQUIRE_LIVE`, demo asserts mock hits, accept-and-ignore modes |
| 4 | Detection misjudges the company server, which cannot be tested at home | D35 to D39, raw evidence stored in `detected`, hand-editable, `--redetect`, first-run items in DEPLOY |
| 5 | Real Jira or Confluence differs (status names, sprint, statusCategory keys, error shapes, TLS) | D09, D49, D51, D55. `doctor` validates views. §9.6 `--dry-run` before full sync. Results come back as written notes only (§9.11) |
| 6 | Windows concurrency, the §8 schedule colliding with open editors | D26, IgnoreNew, tests holding real handles. Contention behaviour is unverified until Phase 0 |
| 7 | Keyring unavailable in scheduled context or blocked by GPO | D58 InteractiveToken, `doctor` ✗ with a hint, no insecure fallback, user-run registration test |
| 8 | Unmet prerequisite: uv download, PyYAML or FTS5 on the chosen build | D01 stop rule on first step, LIKE fallback, wheel test in Phase 6 |
| 9 | Pending GUI confirmations hide a Phase 1 defect until late | Report them immediately after Phase 1 and keep the pending list at the top of every report |
| 10 | Parallel workers drift from the contracts | Frozen section 2, lead-only changes, disjoint files, wave order in Phases 0, 2, 4, 5 |

### Critical Files for Implementation
- D:\work\work-kb\scripts\lib\core.py
- D:\work\work-kb\scripts\lib\managed.py
- D:\work\work-kb\scripts\lib\llm_client.py
- D:\work\work-kb\scripts\kb.py
- D:\work\work-kb\tests\conftest.py

## 부록 B. 적대적 검토 결과

- [blocker] D01: `uv venv --seed --python 3.13` is blocked by the stale registry entry; '3.13 is download-only' is also wrong
  - 조치: Use `uv venv --seed --managed-python --python 3.13 .venv313` for both venvs, or set `UV_PYTHON_NO_REGISTRY=1`. Test this first in Phase 0. Fallback: `uv venv --seed --python <absolute path to the existing 3.13.0>`. Record the exact command in DECISIONS. Do not edit the user's registry.
- [high] D20 contradicts D21 and §5.1: link skips the very region that holds image-extracted text
  - 조치: Change D20: skip `today`, `carryover` and other generated regions, but match inside `extracted`, and never normalise there. Add an end-to-end test: ingest a fixture image, run link, assert the mention exists on jira/KB-12.md, then run link again and assert zero writes.
- [high] D35 probes JSON and vision before the reasoning state is known
  - 조치: Reorder: baseline, then toggle candidates, then JSON and vision using the found off-state (no parameter if `none`). Judge content after `normalize_message`, and also accept JSON in the reasoning field as a distinct `json_with_reasoning` note. Vision prompt must fix the answer language or accept both red and 빨강. Add a mock mode for default-on with content null.
- [high] D35 'worst case 8 calls' is false; the true worst case is 11 and the test would not see it
  - 조치: Define budget exhaustion: stop, record `method: none` with `evidence: budget_exhausted`, and warn. Add a mock mode 'flip then no flip' for every candidate and assert <=10 POSTs and that exhaustion path. State 11 as the unbudgeted worst case in DECISIONS, or allow only one failed confirmation.
- [high] D52 and `net.request`: Jira permission denials become AuthError and stay pending forever
  - 조치: Do not map 401/403 inside `net.request` for writes. In `jira_act`, on 401/403 call GET /rest/api/2/myself: if it returns 200, mark the item `failed` with Jira's message and carry on; if it returns 401, keep it pending and exit 2. Add mock modes for assign 401 and worklog 403 with a valid token.
- [high] D63: auto-prefixing `term*` raises an FTS5 syntax error on Jira keys and other common input
  - 조치: Always quote each term: `'"' + t.replace('"','""') + '"*'`, and also catch `sqlite3.OperationalError` and fall back to LIKE. Add these inputs as test parameters: `KB-12`, `@김철수`, `foo.bar`, `AND`, a term with a quote, and an empty query.
- [high] D02: `doctor` cannot flag 3.15, and `wheels/` plus 'latest stable' Python leaves the company path broken
  - 조치: Add a stdlib-only preflight at the top of kb.py: if `import yaml` fails and the version is >=3.15, say to install Python 3.14 (or build the wheel at home) and exit 4. Remove the doctor flag or make it `-`. In DEPLOY step 2, state the supported interpreters. In Phase 6, try building a cp315 PyYAML wheel into `wheels/` and record the result.
- [high] D40 and the `run_task` signature cannot handle ordinary 4xx or a smaller fallback context
  - 조치: Define it: 401/403/404 on a profile moves to the next profile and is reported by status; 400 or 413 moves on, recording the body. Change `run_task` to accept a builder, `messages_for(limit: int) -> list[dict]`, called per profile, or skip a profile whose `input_limit` is below the built size. Add mock modes for 400 context-exceeded on main and a small-context fallback.
- [medium] D51 appends `ORDER BY key ASC` unconditionally, and D49's wrapper can produce invalid JQL
  - 조치: Add a helper in `jira_client` that detects a top-level ORDER BY outside quotes and keeps it, appending `key ASC` only when absent or as a tiebreak. Reject ORDER BY inside `jql_mine` at config load. Quote project keys in the wrapper. Have mock_jira parse enough to return 400 on double ORDER BY.
- [medium] `kb_lock` has no re-entrancy rule, while cross-script calls nest
  - 조치: Make `kb_lock` re-entrant per process with a depth counter, and assert in `test_core.py` that nesting does not wait. State that `jira_act` releases the lock before any network send and before `sync_key`.
- [medium] Phase 2 user step and the code/vault root split cannot work as written
  - 조치: Add `--root`/`KB_ROOT` semantics to the contract: code root (from `__file__`) and vault root are separate. `work-kb-root` is the code root plus an optional `work-kb-vault`. Phase 2 user step: run `kb.py demo --keep <dir>` or copy the sample vault to temp, seed the .base templates from one location, sync, and open that temp vault. Keep .base files in `obsidian/templates/` or copy them during seeding.
- [medium] Phase 4 wave order: 4.2 and 4.3 run concurrently but depend on each other, and `max_output_tokens` lands only at the I step
  - 조치: Add a Phase 4 L step for the `config.yaml` keys and the `ChatResult`/`chat` stubs. Split into waves: 4.1 and 4.2, then 4.3, then 4.4 to 4.7. Apply the same check to `tests/test_link.py`, which reads the sample vault owned by concurrent W1.5.
- [medium] D44 'only path to LLM HTTP' is false in Phases 0-3, and the guard ignores redirects
  - 조치: Move `is_public_host` to `lib/config.py` (Phase 0, next to `CODE_BLOCKLIST`), and have Phase 0 doctor use it. Add an AST test: only `llm_client` may call `net` with an LLM profile URL. Send LLM requests with `allow_redirects=False` and treat 3xx as an error. Strip the trailing dot before matching.
- [medium] D46 contradicts itself and D44: demo refuses on a fresh copy
  - 조치: Specify: demo reads only the `environment` key from the repo's config.local.yaml if the file exists, refuses only when it literally says company, and treats a missing file as allowed. Use a fake keyring file inside the temp dir. Record the §7 deviation in DECISIONS.
- [medium] D65: fake keyring does not reach subprocesses, so `run_kb` can write to the real Credential Manager
  - 조치: `run_kb` sets `PYTHON_KEYRING_BACKEND` to a file-backed fake under `tmp_path` and adds `tests/` to PYTHONPATH. Add a guard test that the real backend is never selected in a subprocess (`kb.py` prints `backend_name()` through a test-only check).
- [medium] D58 `work-kb\<name>` task folder: non-elevated creation is unverified, with no fallback
  - 조치: Use flat names (`work-kb-sync`, `work-kb-prepare`, ...), which avoids the question entirely. Otherwise, generate a fallback and have Phase 6's user step run non-elevated.
- [medium] D60 still leaves fictional cache and fixtures in the company vault
  - 조치: Add `jira/.cache.sqlite` and `.pytest_cache`, `__pycache__` to D60. Add `tests/` to the exclusions or ensure fixture notes are not named like Jira keys. Make `tests/test_repo_tree.py` check that the DEPLOY exclusion list covers every runtime artefact from §4.
- [medium] D41 budgets fail when reasoning is always on (`method: none`)
  - 조치: Record in `detected` whether the baseline is reasoning-on. When `method: none` and baseline on, use a floor such as 2048 for all tasks and mark tasks with `reasoning: off` as not honoured in `status`. Add the mock mode 'always reasoning with truncation' to the compile tests, not only detection tests.
- [low] D48 and D0's '/models cannot prove a key' is wrong for a sent key
  - 조치: Correct the D48 basis. Phase 0: send the key on `/models` and treat 401/403 as ✗, with key-absent as ✗ or `-` per the ref. Keep the chat call only if the goal is to prove that the model serves, and say that is the reason; consider moving it behind `doctor --deep` to avoid spending on every run.
- [low] D03 absolute interpreter path under a Hangul root is untested, and D10's 'command lines stay ASCII' overlooks it
  - 조치: Use a relative command with `default-directory`, such as `.venv/Scripts/python.exe`, if Emacs accepts it, or test both forms. Add one ERT case that runs under a Hangul root with a real venv, in the D34 rehearsal, under `set-language-environment "Korean"`.

## 부록 C. 지시서 대조 누락 항목

- [high] Phase 1 and 4: D20 stops link from reading sidecar text
  - 조치: Amend D20: match inside `extracted` but never normalise there, and keep skipping all other regions. Add a Phase 4 integration test: ingest a fixture, run link, and assert the mention appears in `jira/KEY.md` and `people/이름.md`. Assign the link.py change in Phase 4 I.
- [high] Phase 6: `ingest` missing from the schedule
  - 조치: Add a `work-kb\ingest` task, for example between link and compile, or state that the compile task runs ingest first. Extend D59's disabled-at-company rule to it. Have test_schedule assert all six scheduled actions and times.
- [high] Phase 1 and 2: no `_views/*.md` format contract, no populated `today` test
  - 조치: Freeze the view table columns and a `core`/`notes` read function in Section 2 at Phase 1. Decide how status text is rendered (raw Jira status or `status_category` mapping, since company status names are unknown). Add a Phase 2 integration test running sync then `daily_prepare` with an assertion on `today` content.
- [high] Phase 0-6: no owner for the bare `kb.py init` flow
  - 조치: Assign the sequence explicitly: skeleton with environment step in W2.5, extended in each later wizard, final in W6.2. Define the prompt seam (injectable input and getpass). Add `tests/test_init_full.py` for the home path and the company path, covering re-run defaults, partial abort, and `--non-interactive --from-env`.
- [high] Phase 4 and 5: nobody adds images to the sample vault
  - 조치: Add `examples/sample-vault/**` (images, sidecar-free inbox drop, and a daily `![[raw/assets/...]]` line) to W4.5 or W5.4, using only fictional fixture images. Also state that demo prints the result tree required by §7, which D46 omits.
- [high] Phase 1 and 2: sample vault config and `.base` location unspecified
  - 조치: Define the sample-vault setup: fixture config, how it runs (`KB_ROOT` or a copy to temp), and an exact command sequence for the user step. Either include the `.base` files in the vault or have the user open the repo root after a mock sync.
- [medium] Phase 0 and 4: public-host check placed too late
  - 조치: Move `is_public_host` to `lib/config.py` or `core` in Phase 0 and test doctor ✗ then. Define the non-raising path for doctor and status. State the `enabled: false` plus blocked-host outcome and test it.
- [medium] Phase 4: D35's call count is wrong
  - 조치: Define the failed-confirmation behaviour and the budget-exhausted result, recording untested candidates in `evidence`. Correct the worst-case number and add a mock mode where every candidate flips but never confirms.
- [medium] Phase 4: doctor does not re-verify detection without `--redetect`
  - 조치: Either add a cheap read-only check per recorded capability within a stated call limit, or log D48 explicitly as a deviation from §6.11 and have doctor print that detection was not re-tested.
- [medium] Phase 4: unclear how home `detected` gets real values
  - 조치: State that the example's `detected` block is not copied, and that `--dev` from Phase 4 runs detection or marks it pending. State where the Gemma id lives. Define how a two-environment example file is laid out so that a 'home block' can be parsed.
- [medium] Phase 1, 3: no command route for `--check-done`, and `today` undefined
  - 조치: Add the route to the Section 2 table, either `kb.py daily_prepare` or `kb.py today --check-done`, and define what `today` does and prints.
- [medium] Phase 3: transient interface under-specified
  - 조치: Before Phase 3, freeze `act` subcommand options: a `--list`/JSON output for transitions and create metadata, the time format, exit code for multiple candidates, and EOF treated as declined (exit 1). Give each transient key one line.
- [medium] Phase 2: `--cache` option absent; `--jql` semantics undefined
  - 조치: Define these: `--jql` prints only, `--cache` also writes files and sqlite, `--dry-run` writes nothing and does not move `last_sync`. Skip the ORDER BY append or strip the user's clause, with a test.
- [medium] Phase 1 and 6: Hangul path has no pytest item
  - 조치: Make `vault` default to a Hangul-and-space path, or add a dedicated test in Phase 1 for link, daily_prepare and search. Run ERT under the same root and launch the interpreter from it. Make the CRLF and Hangul filename cases explicit test items.
- [medium] Phase 0, 6: doctor's check list unallocated and D02 cannot work as written
  - 조치: List all checks with phase and ✓/✗/`-` state. Run Python and dependency checks before any third-party import. Decide whether a GPO-blocked clipboard is ✗ or `-`.
- [medium] Phase 4: AGENTS.md section and prompt loading has no contract
  - 조치: Add `llm_client.build_messages(task, ...)` or similar to Section 2, plus heading anchors in AGENTS.md set in W0.4, and a test that the system message contains the required rules. Assign the T5 schema's location.
- [medium] Phase 4: T4 proposal state undefined under replace mode
  - 조치: Give each proposal a stable id and state in `.state/compile.json`, render the region from that state, merge new proposals, and test accept after a re-run.
- [medium] Phase 4: source requirement enforced only for T2
  - 조치: Add `compile.SCHEMAS` rules that T3 and T4 sources must resolve to supplied inputs, treat failure as a schema failure under D40, and define how T3 sources appear in the region.
- [medium] Phase 1: normalisation scope gaps
  - 조치: State the inbox decision in D18. Add front matter and all wikilink interiors to D20's exclusions, along with a key word-boundary rule. Add byte-level tests for each.
- [medium] Phase 6: D60 omits `jira/.cache.sqlite`
  - 조치: Add `jira/.cache.sqlite*` to D60 and the `.gitignore` list. Have the D34 rehearsal assert no cache file arrives in the copy.
- [medium] Phase 6: company on Python 3.15 and DEPLOY interpreter
  - 조치: Add a DEPLOY branch for 3.15 (install 3.14 or 3.13, or try the sdist). Name the rehearsal's base interpreter. Record the §9.2 wording change as a decision, and have `test_docs` assert the 11 steps are present.
- [medium] Phase 5: demo deviates from §7 without a logged deviation, and has no token source
  - 조치: Log the deviation. Use a test-only keyring backend or demo-only ref. State that demo runs before exclusions or on a copy that keeps `examples/`. Define the error when ports are taken, and print the §7 result tree.
- [medium] Phase 0-6: no log and error redaction rule
  - 조치: Add a redaction helper in `core`/`net` and a test that runs with a sentinel token and scans logs, outbox and config for it. Limit `evidence` to named fields.
- [medium] Phase 5: push options and link rules not addressed
  - 조치: List these items in W5.2's test scope. Strip all HTML comments on push, and define skip behaviour for an empty decisions page.
- [low] Phase 4: `--dry-run` shows no diff
  - 조치: Specify a unified diff to stdout, no ledger or file changes, and identical behaviour for ingest. Decide whether a task filter exists.
- [low] Phase 3: promote pending or after carryover
  - 조치: Append at flush success, accept the `[>]` form as a match, and report rather than fail. Exclude lines with a pending promote from carryover. Add a test.
- [low] Phase 1, 3, 6: small Emacs gaps
  - 조치: Add these to W1.3's list. Make `work-kb-root` a defcustom with a `:set` that re-registers the coding alist. Scope 'sole entry' to Python calls. Create stubs via a `kb.py` command or via link. Test prefix rebind and reload in ERT.
- [low] Phase 2: Dataview example and `setup.md` contents
  - 조치: Add the §6.9 checklist to `test_bases.py` or `test_docs.py`, and define the 엔티티.base filter and columns.
- [low] Phase 1: warm-only performance gate
  - 조치: Log the interpretation in DECISIONS, or also gate the cold run. Add it to the top of the Phase 1 report.
- [low] Phase 2-6: minor data wording and small under-specifications
  - 조치: Add one line for each: limit the D23 `updated` rule to wiki pages, set a cache age threshold, wire or document config keys, state the top-level override rule, store `sprint_field` in config.local, and create state folders on demand with a test from a clean copy.
- [low] Phase 6: SCHEDULING.md usability
  - 조치: Define the XML location and encoding, add a `kb.py init --section schedule` or print command, and test parsing with a Hangul root path. Task registration itself remains unverified until the user runs it.
- [low] Test scope: fixtures, live autouse, later-phase callers
  - 조치: Add a fixture-spec assertion to `test_repo_tree`, state the `live` marker's exemption, and add the Phase 5 caller cases to the guard test.
- [low] Section 0 limits: unverifiable floors and AHK version
  - 조치: Add these to the unverified list and to the report. Declare `#Requires AutoHotkey v2.0` with a user-run pending item. State that clip tests save and restore the clipboard or use only the no-image path through a seam.
