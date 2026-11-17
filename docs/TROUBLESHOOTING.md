# 문제 해결 (TROUBLESHOOTING)

형식: **증상 → 원인 → 대처**. `kb.py doctor`의 `✗ 이름: 문제 → 해결 방법` 줄 이름으로 찾는다.
명령은 저장소 루트에서 `.venv\Scripts\python.exe scripts\kb.py …`로 실행한다. 아래에서는 `kb.py`로 줄인다.
여기 없는 종료 코드는 `docs/README.md`의 종료 코드 표를 본다.

## 연결·비밀값

### keyring 오류 (`✗ 비밀값`, `NoKeyringError`)

- 증상: PAT를 저장·읽지 못한다. 스케줄로 돌릴 때만 실패하기도 한다.
- 원인: Windows 자격 증명 저장소를 못 쓰는 세션이다(서비스 계정, `/NP`·SYSTEM으로 등록한 스케줄).
- 대처: `kb.py doctor`의 `- keyring` 줄이 Windows 백엔드인지 본다. `kb.py init --section jira`로 다시 저장한다. 스케줄은 대화형(InteractiveToken)으로 다시 등록한다(`docs/SCHEDULING.md`).

### Jira PAT 만료·거부 (`✗ Jira`, 종료 2)

- 증상: Jira 명령이 모두 종료 2로 끝난다.
- 원인: PAT 만료·폐기·오타.
- 대처: 새 PAT를 만들고 `kb.py init --section jira` → `kb.py doctor`. 대기 중인 쓰기는 `kb.py act flush`로 나간다.

### Jira 403 (`권한이 없습니다 (403, …)`)

- 증상: `sync`가 종료 2, `act` 쓰기가 `failed`. 또는 `sync --full` 뒤 기존 일감에 `in_scope: false`가 붙는다.
- 원인: 그 프로젝트·일감 권한이 없거나, Jira가 웹 CAPTCHA를 요구한다.
- 대처: `kb.py doctor`에서 Jira가 ✓인데 403이면 브라우저로 한 번 로그인해 CAPTCHA를 풀고 권한을 확인한다. 일감 하나는 `kb.py sync --key KEY`.

### SSL 인증서 오류 (`SSLError`, `CERTIFICATE_VERIFY_FAILED`, 종료 3)

- 증상: Jira·Confluence·LLM 호출이 인증서 오류로 끝난다.
- 원인: Python(`requests`)이 사내 인증서 체인을 모른다.
- 대처: `config.local.yaml`의 해당 구역(jira, confluence, LLM 프로필)에 `ca_bundle:`로 사내 루트 CA(PEM) 경로를 적는다(`init`을 다시 해도 남는다). 프록시는 `trust_env`나 `proxies`. 검증 끄기는 권하지 않는다.

### 쓰기 응답이 3xx (`failed`, "https 주소로 고치세요")

- 증상: 쓰기가 `failed`, 오류에 Location이 보인다.
- 원인: base_url이 `http://`라 리다이렉트되는데, 쓰기는 리다이렉트를 따르지 않는다.
- 대처: `kb.py init --section jira`로 `https://…`로 고치고 `kb.py act flush --retry-failed`.

### 회사에서 LLM 주소가 공개 호스트 (`✗ 공개 호스트 차단`, 종료 5)

- 증상: `doctor` ✗, `compile`·`ingest`가 종료 5로 거부된다.
- 원인: LLM 주소가 `public_hosts_blocklist`에 걸리거나, 공개 IP이거나, 공백·`\`·`@`·ASCII 아닌 문자가 있다(사설·루프백 IP는 허용).
- 대처: 진행하지 않는다. `kb.py init --section llm`으로 사내 주소로 고친다.

## Python

### `py -3`이 동작하지 않는다 / Python을 찾지 못한다

- 증상: `py -3` 오류, Store 별칭(`WindowsApps\python.exe`)이 열림, Neovim이 `Python을 찾지 못했습니다`.
- 원인: `py` 런처가 없거나 Store 별칭이 PATH를 가리고, `.venv`도 없다.
- 대처: 저장소 폴더에서 `install.ps1 -Only venv`로 `.venv`를 만든다. 다른 Python을 쓰려면 nvim-config `opts`에 `python = { "C:/…/python.exe" }`.

### Python 3.14가 아니다 / 패키지 없음 (종료 4)

- 증상: `"오류: Python 3.14가 필요합니다"`, `"오류: 패키지가 없습니다"`(종료 4), `doctor`의 `✗ Python`, `install.ps1`의 `.venv is Python …, not 3.14`.
- 원인: 지원 버전은 3.14 하나다. 3.15는 PyYAML wheel이 없어 오프라인 설치가 실패한다.
- 대처: `py -3.14 -m venv .venv` 후 `pip install -r requirements.txt`, 또는 `install.ps1 -Only venv -Yes`(옛 `.venv`는 이름을 바꿔 둔다).

### venv에 pip가 없다 (`pip가 없습니다`)

- 증상: `kb.py wheels`가 `"… pip가 없습니다. … -m ensurepip 후 다시 실행하세요"`(종료 4), `install.ps1`이 `pip install failed`.
- 원인: venv를 pip 없이 만들었다(`--without-pip`, `uv venv`에 `--seed` 빠뜨림).
- 대처: `.venv\Scripts\python.exe -m ensurepip --upgrade`. 개발 venv는 `uv venv --seed --python 3.14 .venv`로 만든다.

## 한글·인코딩

### 글자가 깨진다 (`ì•ˆë…•`, `UnicodeEncodeError`, `cp949`)

- 증상: 터미널 출력이 깨지거나 `UnicodeDecodeError`.
- 원인: 콘솔·편집기가 cp949로 읽고 쓰는데, 저장소 파일은 모두 UTF-8이다.
- 대처: `setx PYTHONUTF8 1` 후 새 터미널, 콘솔은 `chcp 65001`. 메모장 대신 Neovim·Obsidian으로 편집한다.

### 한글 경로에서 외부 도구가 실패

- 증상: 한글·공백 경로에서 일부 외부 도구가 실패한다.
- 원인: 그 도구가 인수를 ANSI로 받는다(`kb.py`와 Neovim 호출은 안전하다).
- 대처: 그 도구만 ASCII 경로로 우회한다.

## 클립보드·PowerShell

### 클립보드 캡처(`C`)가 안 된다

- 증상: "클립보드를 열 수 없음"류 메시지, 스크립트 실행 차단, 또는 "이미지 없음".
- 원인: GPO가 PowerShell 실행을 막았거나, 데스크톱 세션이 아니거나, 클립보드에 이미지가 없다(탐색기의 파일 복사는 이미지가 아니다).
- 대처: **`F`(파일 선택)를 쓴다.** 또는 스크린샷을 `inbox/`에 넣고 `kb.py ingest`. 정책은 `kb.py doctor`의 PowerShell 줄로 확인한다.

## Neovim

### `<leader>k` 키나 `:WorkKb`가 없다

- 증상: Space k c에 반응이 없고 `:WorkKb`가 "Not an editor command".
- 원인: 데몬 환경에 `WORK_KB_ROOT`가 없어 nvim-config가 work-kb를 불러오지 않았다.
- 대처: 새 터미널에서 `echo $env:WORK_KB_ROOT`. 비었으면 `install.ps1 -Only env`. 그다음 `nvd --restart`(먼저 `:wa`).

### `WORK_KB_ROOT`가 다른 클론을 가리킨다

- 증상: `install.ps1`이 `WORK_KB_ROOT is …; rerun with -Yes`, `uninstall.ps1`이 `"WORK_KB_ROOT는 다른 클론(…)이 설정한 값이라 그대로 둡니다"`.
- 원인: 값을 정한 클론만 그 값을 바꾸거나 되돌린다(매니페스트는 클론끼리 공유).
- 대처: 바꾸려면 `install.ps1 -Only env -Yes` 후 새 터미널에서 `nvd --restart`. 되돌리려면 값을 정한 클론에서 `uninstall.ps1`.

### 명령이 "시간 초과"로 끝난다

- 증상: `"work-kb: … 시간 초과. 다시 실행하지 않았습니다. *work-kb* 버퍼를 보세요"`, 버퍼에 `"90초 안에 답이 없어 서버를 멈췄습니다"`.
- 원인: 상주 서버 요청이 90초를 넘었다(보통 잠금 대기). 쓰기 명령(`today`·`link`)은 두 번 쓰지 않도록 다시 돌리지 않는다.
- 대처: 잠시 뒤 같은 키를 다시 누른다. 계속되면 "잠금 중 (종료 6)".

### `nvd`가 "daemon is not responding"

- 증상: `nvd`가 응답하지 않는다.
- 원인: 데몬이 질문(스왑 파일 등)에서 입력을 기다린다.
- 대처: `nvd`로 창을 열어 답한다. 안 되면 `nvd --stop --force`(저장 안 한 수정은 사라짐) 후 `nvd`.

전체 상태는 `:checkhealth work-kb`로 본다(Neovim 0.12 이상). 상주 서버 "놀고 있음"은 정상이다.

## 잠금·캐시

### 잠금 중 (종료 6)

- 증상: `link`·`sync`·`act`가 종료 6.
- 원인: 다른 명령(스케줄, Neovim)이 실행 중이거나 Obsidian·백신·동기화 프로그램이 파일을 잡고 있다.
- 대처: 수십 초 뒤 다시 한다. 계속되면 `kb.py status`와 작업 관리자의 `python.exe`·`pythonw.exe`를 본다.

### 파일 쓰기 실패 (`파일을 바꾸지 못했습니다`, `읽은 뒤 바뀌었습니다`)

- 증상: 캡처가 `"파일을 바꾸지 못했습니다"`·`"파일이 읽은 뒤 바뀌었습니다. 다시 시도하세요"`, `kb.py`가 `"다른 프로그램이 파일을 잡고 있어 쓰지 못했습니다"`(종료 6)·`"읽은 뒤에 파일이 바뀌어 쓰지 않았습니다"`(종료 1).
- 원인: 다른 프로그램이 파일을 계속 잡고 있거나, 읽은 뒤 누가 고쳐서 덮지 않았다.
- 대처: 잡고 있는 프로그램·열린 편집기를 확인하고 다시 한다.
- `kb.py today`는 이전 daily나 `.state/today.json`이 잡혀 있으면 그 파일만 건너뛰고 `건너뜀: …` 한 줄을 낸다(종료 0). 다시 실행하면 `[>]` 표시와 기록 저장을 마친다. 그사이 일감 제목·상태가 바뀌었으면 `## 오늘`에 옛 줄이 남을 수 있으니 손으로 지운다.

### 캐시 잠김 (`캐시를 다른 프로그램이 쓰고 있습니다`, 종료 6)

- 증상: `sync`·`search`가 위 메시지로 끝난다.
- 원인: 다른 `sync`, DB 도구, 동기화 프로그램이 `jira/.cache.sqlite`를 잡고 있다.
- 대처: 끝난 뒤 `kb.py sync`. 안 되면 `jira/.cache.sqlite*`를 `_trash/`로 옮기고(삭제 말고) `kb.py sync --full`.

## outbox

### `pending`이 쌓인다

- 증상: `kb.py act list`에 `pending`이 남는다.
- 원인: 네트워크·5xx·429. 5번 실패하기 전까지 `pending`이다.
- 대처: `kb.py act flush`. 종료 2면 PAT부터 갱신한다.

### `uncertain` (결과를 모름)

- 증상: `kb.py status`에 `불확실 N개`, `act list`에 `uncertain`. 일반 flush는 건너뛴다.
- 원인: 요청이 닿았을 수 있는데 응답을 못 받았거나 이상했다. 이미 반영됐을 수 있다.
- 대처: **먼저 Jira에서 실제로 있는지 본다.** 없으면 `kb.py act flush --retry-uncertain <id>`, 있으면 `kb.py sync --key KEY`.

### `failed`

- 증상: `act list`에 `failed`.
- 원인: 403, 4xx, 전환 500, 또는 5번 실패. 이유는 `kb.py act list --json`의 `last_error`.
- 대처: 원인을 고친 뒤 `kb.py act flush --retry-failed <id>`.

### 전환이 0개·여러 개 (종료 4) / 오프라인 (종료 3)

- 증상: `act transition`이 종료 4나 3으로 끝난다.
- 원인: 이름에 맞는 전환이 없거나 여럿이다. 또는 오프라인이라 목록을 못 받았다.
- 대처: `kb.py act transitions KEY`로 목록을 보고 다시 지정한다. 오프라인이면 숫자 id(`kb.py act transition KEY <id>`)로 `pending`에 넣을 수 있다.

## LLM 탐지

### `reasoning_toggle`이 `method: none`

- 증상: "추론 켜기·끄기를 구분하지 못함". 작업이 서버 기본 상태로 돈다.
- 원인: 서버에 토글이 없거나 후보 방식이 모두 구분되지 않았다.
- 대처: `kb.py doctor --redetect`. 그래도 같으면 `detected.reasoning_toggle`을 손으로 고친다(`method`, `on_value`/`off_value`; `reasoning_budget_tokens` 등 시험하지 않는 방식도). 구조는 `docs/LIVE.md`.

### `detected: pending`

- 증상: 프로필 탐지가 대기 상태다.
- 원인: 아직 탐지하지 않았다(`--dev`에서 키가 없던 경우 등).
- 대처: `kb.py init --redetect`.

### 이미지가 "미처리"

- 증상: `kb.py status`에 "미처리 이미지 N개".
- 원인: `detected.vision`이 참인 프로필이 없다.
- 대처: `kb.py init --section llm`으로 비전 프로필을 추가하고 `kb.py ingest`.

### 포기한 편찬 단위·이미지 (`gave_up`)

- 증상: `kb.py status`에 "포기한 편찬 단위 N개"·"포기한 이미지 N개", ingest가 포기한 이미지를 건너뛴다.
- 원인: 같은 단위가 3번 연속 `skipped`됐다(스키마 불일치, 긴 입력, 서버 오류).
- 대처: 원인을 고치고 `--dry-run`으로 본 뒤 `kb.py compile --force` 또는 `kb.py ingest --force`.
- 이미지 경고는 그 이미지가 지금도 미처리일 때만 나온다. 이미지를 지우거나 바꾸면 사라진다. `raw/assets` 이미지는 daily에서 임베드를 빼도 사라진다.

### `inbox/` 이미지가 안 옮겨지거나 사이드카가 `<이름.확장자>.md`

- 증상: 이미지가 `raw/assets/`로 가지 않거나 사이드카 이름이 다르다.
- 원인: 정상이다. 문서가 링크한 이미지는 옮기지 않고, `inbox/<이름>.md`가 이미 다른 용도면 `<이름.확장자>.md`를 쓴다.
- 대처: 그대로 둔다. 옮기려면 문서의 링크를 먼저 고친다.

## 캐시 내용

### 일감이 이동(moved)되었다

- 증상: `jira/OLD-1.md`에 `in_scope: false`와 `moved_to: NEW-KEY`가 붙고 `일감.base`에서 사라진다.
- 원인: Jira에서 일감이 다른 프로젝트로 옮겨졌다.
- 대처: `kb.py sync --key NEW-KEY`. 새 프로젝트가 `project_keys`에 없으면 `kb.py init --section jira`로 추가한다. `today --check-done`은 `moved_to`를 따라간다.

### 일감이 사라졌거나 오래되었다

- 대처: `kb.py sync --full`, 하나는 `kb.py sync --key KEY`. 오래된 캐시는 `kb.py lint` 보고(`wiki/_lint.md`)에 나온다.

## Obsidian

### Bases가 표시되지 않는다

- 증상: `.base` 파일이 빈 화면·오류다.
- 원인: Bases를 지원하지 않는 버전이거나 코어 플러그인이 꺼져 있거나 `.base` 파일이 없다.
- 대처: Obsidian을 업데이트하고 Bases를 켠다. 파일이 없으면 저장소의 `jira/_views/일감.base`·`wiki/엔티티.base`를 볼트로 복사한다. 표가 비면 `kb.py sync`·`kb.py link`. 상세는 `obsidian/setup.md`.

## 스케줄

### 작업이 돌지 않는다

- 대처: `schtasks /Query /TN work-kb-sync /V /FO LIST`와 `.state/logs/`를 본다. 대화형 토큰이어야 keyring을 읽는다. 절차는 `docs/SCHEDULING.md`.
