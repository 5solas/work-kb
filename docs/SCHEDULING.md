# 스케줄 (Windows 작업 스케줄러)

work-kb는 작업을 **직접 등록하지 않는다.** `kb.py schedule`(또는 `kb.py init` 마지막 단계)이 작업 스케줄러 XML과
`schtasks` 명령을 만들어 출력하고, 등록은 사용자가 그 명령을 직접 실행해서 한다(D58).

## 작업 표

| 작업 이름 | 실행 | 일정 | 회사(company) |
|---|---|---|---|
| `work-kb-sync` | `kb.py sync` | 15분마다 (매일 00:00부터 하루 내내 반복) | 켜짐 |
| `work-kb-prepare` | `kb.py today` | 매일 08:30 | 켜짐 |
| `work-kb-link` | `kb.py link` | 매일 12:30, 18:30 | 켜짐 |
| `work-kb-ingest` | `kb.py ingest` | 매일 12:31, 18:31 | **꺼진 상태로 만듦** |
| `work-kb-compile` | `kb.py compile` | 매일 12:32, 18:32 | **꺼진 상태로 만듦** |
| `work-kb-lint` | `kb.py lint` | 금요일 17:00 | 켜짐 |

- 이름은 폴더 없는 평면 이름이다(`work-kb\sync` 같은 폴더는 일반 권한으로 만들 수 있는지 확인하지 못했다).
- link → ingest → compile을 1분씩 띄워 실패가 따로 보이게 했다. 앞 작업이 아직 돌면 뒤 작업은 볼트 잠금을
  최대 60초 기다리고, 그래도 잠겨 있으면 종료 6으로 끝난다(다음 회차에 다시 한다).
- `kb.py compile`은 LLM이 꺼져 있으면 조용히 건너뛴다. 주제 제안(T4)은 마지막 실행 뒤 7일이 지났을 때만 돈다.
- `kb.py lint`는 LLM을 부르지 않는다. 모순 후보 검사(T5)까지 하려면 그 작업의 인수에 `--llm`을 직접 더한다.
- 회사에서 ingest·compile을 꺼 두는 이유(D59): 사내 LLM 연결과 탐지를 doctor로 확인하고, 첫 2주는 캡처·link·sync만
  쓰기로 했기 때문이다(DEPLOY 10단계). 켜는 방법은 아래 [끄기·켜기](#끄기켜기).

## 각 작업이 실행하는 것

모든 작업은 같은 꼴이다.

```
프로그램:   "<저장소>\.venv\Scripts\pythonw.exe"
인수:       -X utf8 scripts\kb.py <명령>
시작 위치:  <저장소>
```

- `pythonw.exe`라 콘솔 창이 뜨지 않는다.
- **PYTHONUTF8:** `-X utf8`을 인수로 넘기므로 예약 작업에는 `PYTHONUTF8` 환경변수가 필요 없다.
- 볼트 루트가 저장소 폴더가 아니면(`kb.py schedule --root <볼트>` 또는 `KB_ROOT`로 만든 경우) 인수 끝에
  `--root "<볼트>"`가 붙는다. 예약 작업은 `KB_ROOT`를 보지 못하기 때문이다.
- 한글·공백이 든 경로도 된다. XML은 UTF-16이고 경로는 따옴표로 감싼다.

XML 공통 설정(D58):

| 항목 | 값 | 이유 |
|---|---|---|
| LogonType | `InteractiveToken` | 로그온한 사용자의 권한으로 실행. 비밀번호를 저장하지 않는다 |
| RunLevel | `LeastPrivilege` | 관리자 권한 필요 없음 |
| StartWhenAvailable | `true` | PC가 꺼져 있어 놓친 회차는 켜진 뒤 실행 |
| MultipleInstancesPolicy | `IgnoreNew` | 앞 회차가 아직 돌면 새 회차는 건너뜀 |
| 배터리 | 배터리에서도 시작·계속 | 노트북에서도 동기화 |
| 실행 시간 제한 | sync·prepare·link 30분, lint 1시간, ingest·compile 2시간 | 멈춘 작업이 다음 회차를 막지 않게 |

### 자격 증명 저장소(keyring)와 InteractiveToken

PAT와 LLM 키는 Windows 자격 증명 관리자(keyring, 서비스 `work-kb`)에 있다. 이 저장소는 **로그온한 그 사용자**의
세션에서만 열린다. 그래서:

- 작업은 `InteractiveToken`으로 만든다. 사용자가 로그온해 있을 때만 돌고, 그 사용자의 자격 증명 저장소를 쓴다.
- `schtasks /Create`에 **`/NP`를 쓰지 않는다**(비밀번호 없이 S4U로 돌면 자격 증명 저장소를 못 연다).
  `/RU SYSTEM`도 쓰지 않는다. `/RU`·`/RP`로 비밀번호를 저장하지도 않는다.
- 로그오프한 동안에는 돌지 않는다. 다시 로그온하면 `StartWhenAvailable`로 놓친 회차를 한 번 실행한다.

## 등록하기

1. XML과 명령 만들기 (아무것도 등록하지 않는다):

   ```powershell
   .\.venv\Scripts\python.exe scripts\kb.py schedule
   ```

   - XML은 `.state\schedule\<작업 이름>.xml`에 UTF-16(BOM)으로 쓴다. `schtasks /XML`이 기대하는 인코딩이다.
   - `--xml-dir <폴더>`: 다른 폴더에 쓴다.
   - `--print`: 파일을 쓰지 않고 XML 내용과 명령만 보여 준다.
   - `--company`: 설정과 상관없이 회사용(ingest·compile 꺼짐)으로 만든다. 없으면 `config.local.yaml`의
     `environment`를 따른다(없거나 잘못되면 company).
   - `kb.py init`을 처음부터 돌리면 마지막 [5] 스케줄 단계에서 같은 것을 묻는다(기본값 y). 여기서도 명령을
     출력만 하고 실행하지 않는다.
   - `KB_AGENT=1` 세션에서는 거부된다(종료 7).

2. 출력된 `schtasks /Create` 줄을 PowerShell(또는 cmd)에 그대로 붙여 넣어 실행한다. 관리자 권한은 필요 없다.

   ```powershell
   schtasks /Create /TN work-kb-sync /XML "D:\work\work-kb\.state\schedule\work-kb-sync.xml" /F
   schtasks /Create /TN work-kb-prepare /XML "D:\work\work-kb\.state\schedule\work-kb-prepare.xml" /F
   schtasks /Create /TN work-kb-link /XML "D:\work\work-kb\.state\schedule\work-kb-link.xml" /F
   schtasks /Create /TN work-kb-ingest /XML "D:\work\work-kb\.state\schedule\work-kb-ingest.xml" /F
   schtasks /Create /TN work-kb-compile /XML "D:\work\work-kb\.state\schedule\work-kb-compile.xml" /F
   schtasks /Create /TN work-kb-lint /XML "D:\work\work-kb\.state\schedule\work-kb-lint.xml" /F
   ```

   경로는 예시다. 실제 경로는 `kb.py schedule` 출력을 쓴다. `/F`는 같은 이름의 작업이 있으면 덮어쓴다
   (다시 만들 때 쓰면 된다).

   `/XML` 경로는 위처럼 큰따옴표로 감싼 채 붙여 넣는다(한글·공백 경로). PowerShell의 큰따옴표 안에서는 `$`와
   백틱(`` ` ``)이 변수·이스케이프로 해석되므로, 경로에 이 문자가 있으면 그 줄은 cmd에서 실행하거나 작은따옴표(`'…'`)로
   바꿔 감싼다.

3. 저장소를 다른 폴더로 옮겼거나 `.venv`를 다시 만들었으면 1~2를 다시 한다(XML에 절대 경로가 들어 있다).

## 확인하기

```powershell
schtasks /Query /TN work-kb-sync /V /FO LIST
```

볼 곳:

- `Logon Mode`: `Interactive only` (InteractiveToken)
- `Run As User`: 자기 계정
- `Task To Run`: `"…\.venv\Scripts\pythonw.exe" -X utf8 scripts\kb.py sync`
- `Start In`: 저장소 폴더
- `Scheduled Task State`: `Enabled` (회사에서 ingest·compile은 `Disabled`)
- `Last Result`: 마지막 실행의 종료 코드

지금 한 번 돌려 보기:

```powershell
schtasks /Run /TN work-kb-sync
```

몇 초 뒤 다시 `/Query`로 `Last Result`를 본다.

| Last Result | 뜻 |
|---|---|
| 0 | 성공 |
| 1 | 일반 실패 (볼트의 `.state/logs/kb.log` 확인, 필요하면 콘솔에서 같은 명령을 직접 실행) |
| 2 | 인증 실패 (PAT·키) → `kb.py doctor` |
| 3 | 네트워크 실패 (다음 회차에 다시 시도) |
| 4 | 설정 오류 → `kb.py doctor` |
| 5 | 회사 환경에서 공개 호스트 거부 |
| 6 | 잠금(다른 작업이 실행 중) 또는 파일 사용 중 → 다음 회차에 다시 |
| 7 | 정책 거부 (`KB_AGENT=1` 등) |
| 267009 (0x41301) | 지금 실행 중 |
| 267011 (0x41303) | 아직 한 번도 실행되지 않음 |

종료 코드 0~7은 `scripts/lib/core.py`의 오류 계층과 같다.

## 로그

- 각 스크립트가 볼트의 `.state/logs/kb.log`(`<볼트>\.state\logs\kb.log`)에 남긴다. 오류도 여기에 기록된다
  (1MB씩 3개까지 돌려 씀, 비밀값은 가림). `Last Result`가 0이 아니면 먼저 이 로그의 마지막 줄들을 본다.
- `pythonw.exe`에는 콘솔이 없어 화면 출력은 창에 뜨지 않는다. 로그로 부족하면 같은 명령을 콘솔에서 직접 실행해 본다:

  ```powershell
  cd <저장소>
  .\.venv\Scripts\python.exe -X utf8 scripts\kb.py sync
  ```

- 작업 스케줄러 자체의 기록은 `taskschd.msc` → 작업 스케줄러 라이브러리 → 작업 → 기록 탭(기록이 꺼져 있으면
  오른쪽 '모든 작업 기록 사용').

## 끄기·켜기

```powershell
schtasks /Change /TN work-kb-sync /DISABLE
schtasks /Change /TN work-kb-sync /ENABLE
```

회사에서 꺼진 상태로 만든 ingest·compile은, LLM을 설정하고 `kb.py doctor`에 ✗가 없으며 첫 2주가 지난 뒤 켠다:

```powershell
schtasks /Change /TN work-kb-ingest /ENABLE
schtasks /Change /TN work-kb-compile /ENABLE
```

## 지우기

```powershell
schtasks /Delete /TN work-kb-sync /F
schtasks /Delete /TN work-kb-prepare /F
schtasks /Delete /TN work-kb-link /F
schtasks /Delete /TN work-kb-ingest /F
schtasks /Delete /TN work-kb-compile /F
schtasks /Delete /TN work-kb-lint /F
```

`.state\schedule\*.xml`은 지워도 된다(등록된 작업은 XML 파일과 상관없이 남는다).

## 확인하지 못한 것

- 이 빌드에서는 작업을 실제로 등록하지 않았다. XML은 작업 스케줄러 COM의 메모리 안 정의(`NewTask` + `XmlText`,
  등록 없음)로 스키마 검사만 통과했다(`tests/test_schedule.py`).
- `Principal`에 `UserId`를 넣지 않았다. `schtasks /Create /XML`을 실행한 사용자로 등록되는 것이 정상이며,
  `/Query`의 `Run As User`로 확인한다. 다른 계정이 보이면 그 작업을 `/Delete`로 지우고 수동 실행으로 쓴다.
- 회사 PC의 그룹 정책이 사용자 작업 등록이나 `pythonw.exe` 실행을 막을 수 있다. 그때는 수동 실행
  (`kb.py sync` 등)으로 쓴다.
