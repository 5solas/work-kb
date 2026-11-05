# 문제 해결 (TROUBLESHOOTING)

형식: **증상 → 원인 → 명령**. `kb.py doctor`의 `✗ 이름: 문제 → 해결 방법` 줄 이름으로 찾는다.
모든 명령은 저장소 루트에서 `.venv\Scripts\python.exe scripts\kb.py …`로 실행한다. 아래에서는 앞부분을 줄여 `python scripts/kb.py`로 적는다.

## 연결·비밀값

### keyring 오류 (`✗ 비밀값`, `NoKeyringError`, 저장·읽기 실패)

- 증상: PAT를 저장하거나 읽지 못한다. 스케줄로 돌릴 때만 실패한다.
- 원인: Windows 자격 증명 저장소를 못 쓰는 세션(서비스 계정·비대화형 토큰)이거나 keyring 백엔드가 다르다. 스케줄 작업이 `/NP`나 SYSTEM으로 등록돼도 사용자 자격 증명을 못 읽는다.
- 명령: `python scripts/kb.py doctor`에서 `- keyring` 줄의 백엔드 이름 확인(Windows 자격 증명 백엔드여야 한다). 비밀값 다시 저장: `python scripts/kb.py init --section jira`. 스케줄은 `docs/SCHEDULING.md`의 대화형(InteractiveToken) 방식으로 다시 등록한다.

### Jira PAT 만료·거부 (`✗ Jira`, 종료 2)

- 증상: 모든 Jira 명령이 종료 2. `sync`·`act`가 인증 오류.
- 원인: PAT 만료·폐기·오타.
- 명령: Jira에서 새 PAT 생성 → `python scripts/kb.py init --section jira` → `python scripts/kb.py doctor`. 대기 중이던 쓰기는 그대로 `pending`에 있다가 `python scripts/kb.py act flush`로 나간다.

### SSL 인증서 오류 (`SSLError`, `CERTIFICATE_VERIFY_FAILED`)

- 증상: Jira·Confluence·LLM 호출이 인증서 오류(종료 3).
- 원인: 사내 인증서 체인을 Python(`requests`)이 모른다.
- 명령: 사내 루트 CA 파일(PEM) 경로를 `config.local.yaml`의 해당 연결 구역(jira, confluence, LLM 프로필)에 `ca_bundle:`으로 손으로 적는다(상대 경로는 설정 폴더 기준). `init`을 다시 실행해도 이 키는 유지된다. 프록시를 시스템 설정에서 쓰려면 같은 구역의 `trust_env`, 또는 `proxies`를 설정한다. 검증을 끄는 방법은 권하지 않는다. 다시 `python scripts/kb.py doctor`가 SSL·프록시·인증 오류를 따로 알려 준다.

### 쓰기 응답이 3xx (`failed`, "https 주소로 고치세요")

- 증상: 쓰기가 `failed`, 오류에 Location이 보인다.
- 원인: base_url이 `http://`라서 `https://`로 리다이렉트된다(쓰기는 리다이렉트를 따르지 않는다).
- 명령: `python scripts/kb.py init --section jira`에서 주소를 `https://…`로 고치고 `python scripts/kb.py act flush --retry-failed`.

### 회사에서 LLM 주소가 공개 호스트 (`✗ 공개 호스트 차단`, 종료 5)

- 증상: `doctor` ✗, `compile`·`ingest`가 종료 5로 거부.
- 원인: `environment: company`인데 LLM 프로필 주소가 `public_hosts_blocklist`(공개 클라우드)에 걸린다. 집 설정이 남았거나 오타.
- 명령: 진행하지 말 것. `python scripts/kb.py init --section llm`으로 사내 서버 주소로 고친다. 집에서 쓰던 `config.local.yaml`은 복사하지 않는다(DEPLOY 1단계).

### `py -3`이 동작하지 않는다 / 잘못된 Python

- 증상: `py -3`이 오류, 또는 Neovim `g`·스케줄이 Python을 못 찾는다. Microsoft Store 별칭(`WindowsApps\python.exe`)이 열린다.
- 원인: `py` 런처가 설치돼 있지 않거나 Store 별칭이 PATH를 가린다.
- 명령: `.venv`를 만들어 `.venv\Scripts\python.exe`를 쓴다(DEPLOY 2단계). Neovim 플러그인은 `.venv`가 있으면 자동으로 쓰고, 아니면 nvim-config의 `lua\plugins\work-kb.lua`에 있는 `opts`에 `python = { "C:/…/python.exe" }`를 더해 지정한다. Neovim 사용법: `docs/USER-GUIDE.md`.

### Python 3.15에서 PyYAML 설치 실패 / `kb.py`가 패키지 없음을 알림 (종료 4)

- 원인: 3.15용 PyYAML wheel이 없다. 3.13 미만은 지원하지 않는다.
- 명령: 3.14 또는 3.13으로 `python -m venv .venv` 후 `pip install -r requirements.txt`.

## 한글·인코딩

### 글자가 깨진다 (`?`, `ì•ˆë…•`, `UnicodeEncodeError`, `cp949` 코덱 오류)

- 증상: 터미널 출력이 깨지거나 파일을 읽다 `UnicodeDecodeError`.
- 원인: 콘솔·편집기가 cp949(기본 ANSI)로 읽고 쓴다. 이 저장소의 파일은 모두 UTF-8이다.
- 명령: `setx PYTHONUTF8 1` 후 새 터미널. 콘솔은 `chcp 65001`. 메모장 대신 Neovim·Obsidian으로 편집(메모장 저장은 BOM·cp949를 만들 수 있다). Neovim/Neovide 사용법: `docs/USER-GUIDE.md`.

### 한글 경로에서 도구가 실패

- 증상: 경로에 한글·공백이 있으면 실패하는 외부 도구가 있다.
- 원인: 외부 프로그램이 인수를 ANSI로 받는 경우. `kb.py`는 한글 경로를 처리하지만 `doctor`는 `- 볼트 경로` 참고 줄로 알린다.
- 명령: `python scripts/kb.py doctor`. 문제가 되는 도구만 ASCII 경로로 우회한다. (Neovim 호출은 인수를 stdin JSON으로 넘겨 한글 인수에 안전하다.)

## 클립보드·PowerShell

### 클립보드 캡처(`C`)가 안 된다 (`✗`/`- PowerShell …`, 정책 오류)

- 증상: `C`에서 "클립보드를 열 수 없음"류 메시지, 또는 스크립트 실행이 막힘.
- 원인: 회사 정책(GPO)이 PowerShell 스크립트 실행을 막거나, 세션이 데스크톱이 아니다(원격·서비스). 이 스크립트는 Windows PowerShell 5.1을 `-ExecutionPolicy Bypass -STA`로 실행하는데, GPO가 정책을 덮어쓰면 소용없다.
- 명령: **`F`(파일 선택)를 쓴다.** 스크린샷은 파일로 저장하거나 `inbox/`에 끌어다 놓으면 `python scripts/kb.py ingest`가 처리한다. 확인: `python scripts/kb.py doctor`의 PowerShell 줄.

### 클립보드에 이미지가 없다

- 증상: "이미지 없음" 안내만 나온다.
- 원인: 클립보드에 텍스트·파일 복사본만 있다(파일 탐색기의 복사는 이미지가 아니다).
- 명령: 캡처 도구로 영역을 클립보드에 복사한 뒤 `C`. 파일이면 `F`.

## Neovim

### `<leader>k` 키나 `:WorkKb`가 없다

- 증상: Space k c를 눌러도 반응이 없고 `:WorkKb`가 "Not an editor command"로 나온다.
- 원인: nvim-config는 사용자 환경 변수 `WORK_KB_ROOT`가 없거나 `<값>\nvim\lua\work-kb\init.lua`가 없으면 work-kb를 아예 불러오지 않는다. 데몬은 시작할 때 환경이 고정되므로, 변수를 정하기 전에 뜬 데몬에는 값이 없다.
- 명령: 새 터미널에서 `echo $env:WORK_KB_ROOT`로 저장소 폴더가 나오는지 본다. 비었으면 저장소 폴더에서 `install.ps1`(`-Only env`)을 실행한다. 그다음 `nvd --restart`(수정한 버퍼가 있으면 먼저 `:wa`).

### 상태를 한 번에 보고 싶다

- 명령: Neovim에서 `:checkhealth work-kb`. 코드 루트, 볼트, 접두 키, Python, 상주 서버(`kb.py serve`) 상태가 나온다. Neovim 0.12 미만이면 오류로 표시된다.

### `nvd`가 "daemon is not responding"

- 원인: 데몬이 질문(스왑 파일 등)에서 입력을 기다린다. 개발 중에는 gitsigns 플러그인이 `git`을 시작하는 순간 헤드리스 Neovim이 멈춘 적도 있다(원인 미확인).
- 명령: `nvd`로 창을 열어 질문에 답한다. 안 되면 `nvd --stop --force`(저장하지 않은 수정은 사라진다) 후 `nvd`. 자세한 설명은 nvim-config의 `docs\USER_GUIDE.md` 7절.

### Python을 찾지 못한다 (`Python을 찾지 못했습니다`)

- 원인: 저장소 폴더에 `.venv`가 없고 PATH의 `python`도 쓸 수 없다.
- 명령: 저장소 폴더에서 `install.ps1`(`-Only venv`)로 `.venv`를 만든다. 위 "`py -3`이 동작하지 않는다" 항목도 본다.

## 잠금·캐시

### 잠금 중 (종료 6, "다른 프로세스가 잡고 있음")

- 증상: `link`·`sync`·`act`가 종료 6.
- 원인: 같은 볼트에서 다른 명령(스케줄 작업, Neovim 호출)이 실행 중이거나, Obsidian·백신·동기화 프로그램이 파일을 잡고 있다.
- 명령: 잠시(수십 초) 뒤 다시 실행. 계속되면 `python scripts/kb.py status`로 상태를 보고 실행 중인 `pythonw.exe`/`python.exe`를 작업 관리자에서 확인한다. 스케줄 작업은 겹치면 새 실행을 건너뛴다(IgnoreNew).

### 캐시 잠김 (`jira/.cache.sqlite` 사용 중)

- 증상: `sync`·`search`가 "database is locked" 또는 종료 6.
- 원인: 다른 `sync`가 쓰는 중이거나 동기화 프로그램이 sqlite 파일을 잡고 있다.
- 명령: 다른 작업이 끝난 뒤 `python scripts/kb.py sync`. 여전히 안 되면 `jira/.cache.sqlite*`는 캐시일 뿐이므로 `_trash/`로 옮기고 `python scripts/kb.py sync --full`로 다시 만든다(삭제하지 말고 옮긴다).

## outbox

### `pending`이 쌓인다

- 원인: 네트워크 다운·5xx·429. 시도 횟수가 5번을 넘기 전에는 `pending`이다.
- 명령: `python scripts/kb.py act list` → `python scripts/kb.py act flush`. 인증(종료 2)이면 PAT부터 갱신.

### `uncertain` (결과를 모름)

- 증상: `act list`에 `uncertain`. 일반 flush는 건너뛴다.
- 원인: 요청이 서버에 닿은 뒤 응답을 못 받았거나(타임아웃·끊김), 응답 형태가 이상했거나(HTML, id 없음), 전송 중 프로세스가 중단됐다(`sending`이 오래 남음). 이미 올라갔을 수 있다.
- 명령: **먼저 Jira에서 그 댓글·전환이 실제로 있는지 확인한다.** 없으면 `python scripts/kb.py act flush --retry-uncertain <id>`. 있으면 그대로 두고 `python scripts/kb.py sync --key KEY`로 캐시를 갱신한다.

### `failed`

- 원인: 권한 없음(403), 잘못된 요청(4xx), 전환 500, 5번 실패. `act list --json`의 `last_error`에 이유가 있다.
- 명령: 원인을 고친 뒤 `python scripts/kb.py act flush --retry-failed <id>`.

### 전환 후보가 0개이거나 여러 개 (종료 4)

- 명령: `python scripts/kb.py act transitions KEY`로 `이름 → 대상 (id)` 목록을 보고 이름 또는 id로 다시 지정한다.

## LLM 탐지

### `reasoning_toggle`이 `method: none`

- 증상: `doctor`/`init`이 "추론 켜기·끄기를 구분하지 못함"으로 알린다. 모든 작업이 서버 기본 상태로 돈다.
- 원인: 서버가 지원하는 토글이 없거나 후보 방식이 모두 구분되지 않았다.
- 명령: 먼저 `python scripts/kb.py doctor --redetect`로 한 번 더. 그래도 같으면 `config.local.yaml`의 해당 LLM 프로필 `detected.reasoning_toggle` 블록을 **손으로 고친다**. 서버 방식을 알면 `method`(`param`/`system_prefix`/`none`)와 켜기·끄기 값(`on_value`/`off_value`, 사전도 가능)을 직접 쓴다. 자동 탐지가 시험하지 않는 `max_reasoning_tokens`·`reasoning_budget_tokens`·`medium_effort` 방식도 이렇게 손으로만 넣는다. 저장 뒤 `python scripts/kb.py doctor`로 재검증한다. 구조는 `docs/LIVE.md`의 실제 기록(`method: param`, `param_name`, `on_value`, `off_value`)을 따른다.

### `detected: pending` / 탐지 대기

- 원인: `--dev`에서 키가 없었거나 아직 탐지를 안 했다.
- 명령: `python scripts/kb.py init --redetect`.

### 비전 프로필이 없어 이미지가 "미처리"

- 증상: `kb.py status`에 "미처리 이미지 N개".
- 원인: `detected.vision`이 참인 프로필이 없다.
- 명령: 비전 가능한 프로필을 추가(`python scripts/kb.py init --section llm`)하고 `python scripts/kb.py ingest`.

### 포기한 편찬 단위 / 이미지 (`gave_up`)

- 증상: `kb.py status`에 "포기한 편찬 단위 N개", ingest가 "포기한 이미지"를 건너뜀.
- 원인: 같은 단위가 3번 실행에서 모두 건너뛰어졌다(`skipped`: 스키마 불일치, 너무 긴 입력, 서버 오류).
- 명령: 원인(서버 상태, 입력 크기)을 고친 뒤 `python scripts/kb.py compile --force` 또는 이미지는 `python scripts/kb.py ingest --force`. 먼저 `--dry-run`으로 본다.

## 캐시 내용

### 일감이 이동(moved)되었다

- 증상: `jira/OLD-1.md`의 front matter에 `in_scope: false`와 `moved_to: NEW-KEY`가 붙었다(본문은 옛 내용 그대로). 이 일감은 `일감.base` 표에서 더 이상 보이지 않는다.
- 원인: Jira에서 일감이 다른 프로젝트로 옮겨졌다(키가 바뀜).
- 명령: `python scripts/kb.py sync --key NEW-KEY`. 새 키의 프로젝트가 `project_keys`에 없으면 `python scripts/kb.py init --section jira`에서 추가한다. `today --check-done`은 `moved_to`를 따라간다.

### 일감이 사라졌거나 오래되었다

- 명령: `python scripts/kb.py sync --full`, 개별은 `python scripts/kb.py sync --key KEY`. 오래된 캐시는 `python scripts/kb.py lint` 보고(`wiki/_lint.md`)에 나온다.

## Obsidian

### Bases가 표시되지 않는다

- 증상: `.base` 파일이 빈 화면·오류, 또는 표가 보이지 않는다.
- 원인: Obsidian이 Bases를 지원하지 않는 오래된 버전이거나 코어 플러그인이 꺼져 있다. 정렬(`sort`) 문법은 앱 버전에 따라 다를 수 있다(DECISIONS: 사용자 확인 대기).
- 명령: Obsidian을 업데이트하고 설정 → 코어 플러그인에서 Bases 켜기. `.base` 파일은 코드가 만들지 않는다. 파일이 없으면(볼트를 저장소와 따로 둔 경우 등) 저장소의 `jira/_views/일감.base`와 `wiki/엔티티.base` 두 파일을 볼트의 같은 경로로 복사한다. 파일은 있는데 표가 비어 있으면 `python scripts/kb.py sync`·`python scripts/kb.py link`를 실행한다. 자세한 점검 목록은 `obsidian/setup.md`. 안 되면 Bases 없이도 `jira/_views/*.md` 표로 같은 정보를 볼 수 있다.

## 스케줄

### 작업이 돌지 않는다

- 명령: `schtasks /Query /TN work-kb-sync /V /FO LIST`로 마지막 실행 결과를 보고 `.state/logs/`의 로그를 확인한다. 대화형 토큰(로그온 상태)이어야 keyring을 읽는다. 절차는 `docs/SCHEDULING.md`.
