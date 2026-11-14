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

### Jira 403 (`권한이 없습니다 (403, …)`, 종료 2 또는 `in_scope: false`)

- 증상: `sync`가 `"권한이 없습니다 (403, …)"`(종료 2)로 멈춘다. `act` 쓰기가 `failed`가 된다. 또는 `sync --full` 뒤 기존 일감 파일에 `in_scope: false`가 붙어 `일감.base`에서 사라진다.
- 원인: 그 프로젝트·일감에 권한이 없거나, 로그인 실패가 이어져 Jira가 웹 CAPTCHA를 요구한다. `sync --full`은 범위 밖 기존 일감의 403을 `in_scope: false`로 넘기고 계속한다. 단 토큰이 정상일 때만이다(무효면 종료 2).
- 명령: `python scripts/kb.py doctor`로 `Jira`가 ✓인지 본다. ✓인데 403이면 브라우저로 Jira에 한 번 로그인해 CAPTCHA를 풀고 권한을 확인한다. 일감 하나는 `python scripts/kb.py sync --key KEY`로 다시 받는다.

### SSL 인증서 오류 (`SSLError`, `CERTIFICATE_VERIFY_FAILED`)

- 증상: Jira·Confluence·LLM 호출이 인증서 오류(종료 3).
- 원인: 사내 인증서 체인을 Python(`requests`)이 모른다.
- 명령: 사내 루트 CA 파일(PEM) 경로를 `config.local.yaml`의 해당 구역(jira, confluence, LLM 프로필)에 `ca_bundle:`으로 손으로 적는다.
  - 상대 경로는 설정 폴더 기준이다. `init`을 다시 해도 이 키는 남는다.
  - 프록시는 같은 구역의 `trust_env`(시스템 설정 사용)나 `proxies`로 정한다.
  - 검증을 끄는 방법은 권하지 않는다.
  - 고친 뒤 `python scripts/kb.py doctor`. SSL·프록시·인증 오류를 따로 알려 준다.

### 쓰기 응답이 3xx (`failed`, "https 주소로 고치세요")

- 증상: 쓰기가 `failed`, 오류에 Location이 보인다.
- 원인: base_url이 `http://`라서 `https://`로 리다이렉트된다(쓰기는 리다이렉트를 따르지 않는다).
- 명령: `python scripts/kb.py init --section jira`에서 주소를 `https://…`로 고치고 `python scripts/kb.py act flush --retry-failed`.

### 회사에서 LLM 주소가 공개 호스트 (`✗ 공개 호스트 차단`, 종료 5)

- 증상: `doctor` ✗, `compile`·`ingest`가 종료 5로 거부.
- 원인: `environment: company`인데 LLM 프로필 주소가 다음 중 하나다. 집 설정이 남았거나 오타다.
  - `public_hosts_blocklist`(공개 클라우드)에 걸리는 호스트
  - 공개 IP 주소 (사설·루프백 IP는 차단 목록에 직접 넣지 않는 한 허용)
  - 호스트를 하나로 정할 수 없는 주소 (공백·`\`·`@`·ASCII 아닌 문자 포함)
- 명령: 진행하지 말 것. `python scripts/kb.py init --section llm`으로 사내 서버 주소로 고친다. 집에서 쓰던 `config.local.yaml`은 복사하지 않는다(DEPLOY 1단계).

### `py -3`이 동작하지 않는다 / 잘못된 Python

- 증상: `py -3`이 오류, 또는 Neovim 플러그인·스케줄이 Python을 못 찾는다. Microsoft Store 별칭(`WindowsApps\python.exe`)이 열린다.
- 원인: `py` 런처가 설치돼 있지 않거나 Store 별칭이 PATH를 가린다.
- 명령: `.venv`를 만들어 `.venv\Scripts\python.exe`를 쓴다(DEPLOY 2단계). Neovim 플러그인은 `.venv`가 있으면 자동으로 쓰고, 아니면 nvim-config의 `lua\plugins\work-kb.lua`에 있는 `opts`에 `python = { "C:/…/python.exe" }`를 더해 지정한다. Neovim 사용법: `docs/USER-GUIDE.md`.

### Python 3.14가 아닌 버전 / PyYAML 설치 실패 / 패키지 없음 (종료 4)

- 증상: 다음 중 하나가 나온다.
  - `kb.py`: `"오류: Python 3.14가 필요합니다"` 또는 `"오류: 패키지가 없습니다"`(종료 4)
  - `doctor`: `✗ Python`
  - `install.ps1`: `.venv is Python …, not 3.14`
  - 3.15에서 오프라인 설치(`--no-index --find-links wheels`) 실패
- 원인: 지원하는 Python은 3.14 하나다. 3.14 미만은 `kb.py`와 `doctor`가 거부한다. 3.15용 PyYAML wheel은 없다(`kb.py wheels`도 3.14만 받는다).
- 명령: 3.14로 `py -3.14 -m venv .venv`(또는 3.14인 `python -m venv .venv`) 후 `pip install -r requirements.txt`. 저장소 폴더에서 `install.ps1 -Only venv -Yes`도 된다. 3.14가 아닌 `.venv`는 이름을 바꿔 두고 새로 만든다.

### venv에 `pip`가 없다 (`pip가 없습니다`, `ensurepip`)

- 증상: `kb.py wheels`가 `"… pip가 없습니다. … -m ensurepip 후 다시 실행하세요"`(종료 4)로 실패한다. `install.ps1`이 `pip install failed`로 끝난다.
- 원인: venv를 pip 없이 만들었다(`--without-pip`, 또는 `uv venv`에 `--seed`를 빠뜨림).
- 명령: `.venv\Scripts\python.exe -m ensurepip --upgrade`. 개발 환경은 `uv venv --seed --python 3.14 .venv` 후 `uv pip install --python .venv\Scripts\python.exe -r requirements-dev.txt`.

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

### `install.ps1` / `uninstall.ps1`에서 `WORK_KB_ROOT`가 다른 클론을 가리킨다

- 증상: `install.ps1`이 `WORK_KB_ROOT is …; rerun with -Yes`로 멈추거나, `uninstall.ps1`이 `"WORK_KB_ROOT는 다른 클론(…)이 설정한 값이라 그대로 둡니다"`로 건너뛴다.
- 원인: 매니페스트(`%LOCALAPPDATA%\work-kb\install-manifest.json`)는 클론끼리 공유한다. `WORK_KB_ROOT`는 그 값을 정한 클론만 되돌린다.
- 명령: 이 클론으로 바꾸려면 `install.ps1 -Only env -Yes` 후 새 터미널에서 `nvd --restart`. 되돌리려면 값을 정한 클론에서 `uninstall.ps1`.

### 상태를 한 번에 보고 싶다

- 명령: Neovim에서 `:checkhealth work-kb`. 코드 루트, 볼트, 접두 키, Python, 상주 서버(`kb.py serve`) 상태가 나온다.
- Neovim 0.12 미만이면 오류다(`install.ps1` 점검과 `kb.py doctor`도 같은 기준).
- 상주 서버가 "놀고 있음"이면 정상이다. 다음 요청 때 뜬다.

### 명령이 "시간 초과"로 끝난다

- 증상: `"work-kb: … 시간 초과. 다시 실행하지 않았습니다. *work-kb* 버퍼를 보세요"` 알림. 버퍼에는 `"90초 안에 답이 없어 서버를 멈췄습니다"`.
- 원인: 상주 서버의 요청 하나가 90초를 넘었다(다른 명령이 잠금을 오래 잡는 등). 서버는 멈춘다.
  - 읽기 명령(`status`·`search`·`proposals list`·`act list`)은 한 번 실행으로 다시 돌린다.
  - 쓰기 명령(`today`·`link`)은 두 번 쓰지 않도록 다시 돌리지 않는다. 위 알림은 이때 뜬다.
- 명령: 잠시 뒤 같은 키를 다시 누른다. 서버는 다음 요청 때 바로 다시 뜬다. 계속되면 아래 "잠금 중 (종료 6)"을 본다.

### `nvd`가 "daemon is not responding"

- 원인: 데몬이 질문(스왑 파일 등)에서 입력을 기다린다. gitsigns가 `git`을 띄울 때 헤드리스 Neovim이 멈춘 적도 있다(원인 미확인).
- 명령: `nvd`로 창을 열어 질문에 답한다. 안 되면 `nvd --stop --force`(저장하지 않은 수정은 사라진다) 후 `nvd`. 자세한 설명은 nvim-config의 `docs\USER_GUIDE.md` 7절.

### Python을 찾지 못한다 (`Python을 찾지 못했습니다`)

- 원인: 저장소 폴더에 `.venv`가 없고 PATH의 `python`도 쓸 수 없다.
- 명령: 저장소 폴더에서 `install.ps1`(`-Only venv`)로 `.venv`를 만든다. 위 "`py -3`이 동작하지 않는다" 항목도 본다.

## 잠금·캐시

### 잠금 중 (종료 6, "다른 프로세스가 잡고 있음")

- 증상: `link`·`sync`·`act`가 종료 6.
- 원인: 같은 볼트에서 다른 명령(스케줄 작업, Neovim 호출)이 실행 중이거나, Obsidian·백신·동기화 프로그램이 파일을 잡고 있다.
- 명령: 잠시(수십 초) 뒤 다시 실행. 계속되면 `python scripts/kb.py status`로 상태를 보고 실행 중인 `pythonw.exe`/`python.exe`를 작업 관리자에서 확인한다. 스케줄 작업은 겹치면 새 실행을 건너뛴다(IgnoreNew).

### 원자적 쓰기 재시도 실패 / 동시 편집 보호 (`파일을 바꾸지 못했습니다`, `읽은 뒤 바뀌었습니다`)

- 증상:
  - Neovim 캡처: `"파일을 바꾸지 못했습니다"`, `"다른 프로그램이 잡고 있어 파일을 읽지 못했습니다. 쓰지 않았습니다"`, `"파일이 읽은 뒤 바뀌었습니다. 다시 시도하세요"`
  - `kb.py`: `"다른 프로그램이 파일을 잡고 있어 쓰지 못했습니다"`(종료 6), `"읽은 뒤에 파일이 바뀌어 쓰지 않았습니다"`(종료 1)
- 원인: 캡처와 `kb.py`는 임시 파일에 쓴 뒤 바꿔 넣는다. 파일이 잡혀 있으면 몇 번 다시 시도한다.
  - 백신·동기화·편집기가 파일을 계속 잡고 있으면 끝내 실패한다.
  - 읽은 뒤 내용이 바뀌었으면 사람 편집을 덮지 않도록 쓰지 않는다.
- 명령: 파일을 잡은 프로그램과 열린 편집기의 저장 상태를 확인하고 잠시 뒤 다시 한다. 남은 `.tmp` 임시 파일은 자동으로 지워진다.

### 캐시 잠김 (`jira/.cache.sqlite` 사용 중)

- 증상: `sync`·`search`가 `"캐시를 다른 프로그램이 쓰고 있습니다"`(종료 6).
- 원인: 다른 `sync`가 쓰는 중이거나, DB 도구·동기화 프로그램이 sqlite 파일을 잡고 있다.
- 명령: 다른 작업이 끝난 뒤 `python scripts/kb.py sync`. 여전히 안 되면 `jira/.cache.sqlite*`는 캐시일 뿐이므로 `_trash/`로 옮기고 `python scripts/kb.py sync --full`로 다시 만든다(삭제하지 말고 옮긴다).

## outbox

### `pending`이 쌓인다

- 원인: 네트워크 다운·5xx·429. 시도 횟수가 5번을 넘기 전에는 `pending`이다.
- 명령: `python scripts/kb.py act list` → `python scripts/kb.py act flush`. 인증(종료 2)이면 PAT부터 갱신.

### `uncertain` (결과를 모름)

- 증상: `kb.py status`에 `불확실 N개`, `act list`에 `uncertain`. 일반 flush는 건너뛴다.
- 원인: 요청이 서버에 닿았는데 결과를 모른다. 이미 올라갔을 수 있다.
  - 응답을 못 받았다(타임아웃·끊김).
  - 응답 형태가 이상했다(HTML, id 없음).
  - 보내던 프로세스가 죽었거나 `sending`이 오래 남았다.
- `--retry-uncertain`으로 다시 보내다 429·5xx·네트워크 오류가 나면 횟수와 상관없이 `uncertain`으로 남는다(`failed`가 되지 않는다).
- 명령: **먼저 Jira에서 그 댓글·전환이 실제로 있는지 확인한다.** 없으면 `python scripts/kb.py act flush --retry-uncertain <id>`. 있으면 그대로 두고 `python scripts/kb.py sync --key KEY`로 캐시를 갱신한다.

### `failed`

- 원인: 권한 없음(403), 잘못된 요청(4xx), 전환 500, 5번 실패. `act list --json`의 `last_error`에 이유가 있다.
- 명령: 원인을 고친 뒤 `python scripts/kb.py act flush --retry-failed <id>`.

### 전환 후보가 0개이거나 여러 개 / 오프라인 전환 (종료 4·3)

- 원인: 이름에 맞는 전환이 없거나 여럿이다(종료 4). 또는 오프라인이라 전환 목록을 받지 못했다(종료 3).
- 명령: `python scripts/kb.py act transitions KEY`로 `이름 → 대상 (id)` 목록을 보고 이름이나 id로 다시 지정한다.
- 오프라인: 숫자 id로 `python scripts/kb.py act transition KEY <id>`를 주면 outbox에 `pending`으로 넣는다(종료 3). 나중에 `act flush`로 나간다. 이름으로는 오프라인 전환이 안 된다.

## LLM 탐지

### `reasoning_toggle`이 `method: none`

- 증상: `doctor`/`init`이 "추론 켜기·끄기를 구분하지 못함"으로 알린다. 모든 작업이 서버 기본 상태로 돈다.
- 원인: 서버가 지원하는 토글이 없거나 후보 방식이 모두 구분되지 않았다.
- 명령: 먼저 `python scripts/kb.py doctor --redetect`로 한 번 더. 그래도 같으면 `config.local.yaml`의 그 프로필 `detected.reasoning_toggle` 블록을 **손으로 고친다**.
  - `method`(`param`/`system_prefix`/`none`)와 켜기·끄기 값(`on_value`/`off_value`, 사전도 가능)을 쓴다.
  - 자동 탐지가 시험하지 않는 `max_reasoning_tokens`·`reasoning_budget_tokens`·`medium_effort` 방식도 이렇게 넣는다.
  - 구조는 `docs/LIVE.md`의 실제 기록(`method: param`, `param_name`, `on_value`, `off_value`)을 따른다.
  - 저장 뒤 `python scripts/kb.py doctor`로 다시 확인한다.

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

### `inbox/` 이미지가 `raw/assets/`로 옮겨지지 않거나 사이드카가 `<이름.확장자>.md`로 생긴다

- 원인: 정상 동작이다.
  - `daily/`·`inbox/`·`wiki/`의 문서가 그 이미지를 링크하고 있으면 링크가 깨지지 않도록 옮기지 않는다.
  - `inbox/<이름>.md`가 사용자 노트이거나 다른 확장자 이미지의 사이드카면 `inbox/<이름.확장자>.md`에 만든다.
- 명령: 그대로 둔다. 이미지를 `raw/assets/`로 옮기고 싶으면 먼저 문서 안의 링크를 고친다.

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
- 원인: Obsidian이 Bases를 지원하지 않는 오래된 버전이거나 코어 플러그인이 꺼져 있다. 정렬(`sort`) 문법은 앱 버전에 따라 다를 수 있다.
- 명령: Obsidian을 업데이트하고 설정 → 코어 플러그인에서 Bases를 켠다.
  - 파일이 없으면(볼트를 따로 둔 경우 등) 저장소의 `jira/_views/일감.base`와 `wiki/엔티티.base`를 볼트의 같은 경로로 복사한다. 코드는 `.base`를 만들지 않는다.
  - 파일은 있는데 표가 비면 `python scripts/kb.py sync`·`python scripts/kb.py link`를 실행한다.
  - 점검 목록은 `obsidian/setup.md`. Bases 없이도 `jira/_views/*.md` 표로 같은 정보를 볼 수 있다.

## 스케줄

### 작업이 돌지 않는다

- 명령: `schtasks /Query /TN work-kb-sync /V /FO LIST`로 마지막 실행 결과를 보고 `.state/logs/`의 로그를 확인한다. 대화형 토큰(로그온 상태)이어야 keyring을 읽는다. 절차는 `docs/SCHEDULING.md`.
