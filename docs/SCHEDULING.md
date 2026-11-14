# 스케줄 (Windows 작업 스케줄러)

work-kb는 작업을 **직접 등록하지 않는다.** `kb.py schedule`(또는 `kb.py init` 마지막 단계)이 XML과 `schtasks` 명령을
출력하고, 사용자가 그 명령을 실행해 등록한다(D58).

## 작업 표

| 작업 이름 | 실행 | 일정 | 회사(company) |
|---|---|---|---|
| `work-kb-sync` | `kb.py sync` | 15분마다 (매일 00:00부터 하루 내내 반복) | 켜짐 |
| `work-kb-prepare` | `kb.py today` | 매일 08:30 | 켜짐 |
| `work-kb-link` | `kb.py link` | 매일 12:30, 18:30 | 켜짐 |
| `work-kb-ingest` | `kb.py ingest` | 매일 12:31, 18:31 | **꺼진 상태로 만듦** |
| `work-kb-compile` | `kb.py compile` | 매일 12:32, 18:32 | **꺼진 상태로 만듦** |
| `work-kb-lint` | `kb.py lint` | 금요일 17:00 | 켜짐 |

- link → ingest → compile은 1분 간격이다. 앞 작업이 볼트 잠금을 쥐고 있으면 60초까지 기다린 뒤 다음 회차로 미룬다.
- ingest·compile은 LLM이 꺼져 있으면 건너뛴다. 주제 제안(T4)은 7일에 한 번 돈다.
- lint는 LLM을 부르지 않는다. 모순 검사(T5)까지 하려면 작업 인수에 `--llm`을 더한다.
- 회사에서 ingest·compile을 꺼 두는 것은 첫 2주는 캡처·link·sync만 쓰기 때문이다(D59, DEPLOY 10단계).

## 각 작업이 실행하는 것

```
프로그램:   "<저장소>\.venv\Scripts\pythonw.exe"
인수:       -X utf8 scripts\kb.py <명령>
시작 위치:  <저장소>
```

- `pythonw.exe`라 창이 뜨지 않는다. `-X utf8`이라 `PYTHONUTF8`이 필요 없다.
- 볼트가 저장소 폴더가 아니면 인수 끝에 `--root "<볼트>"`가 붙는다(예약 작업은 `KB_ROOT`를 못 본다).
- 한글·공백 경로도 된다.

XML 공통 설정(D58):

| 항목 | 값 |
|---|---|
| LogonType | `InteractiveToken` (비밀번호를 저장하지 않는다) |
| RunLevel | `LeastPrivilege` (관리자 권한 불필요) |
| StartWhenAvailable | `true` (놓친 회차는 PC가 켜진 뒤 실행) |
| MultipleInstancesPolicy | `IgnoreNew` (앞 회차가 돌면 건너뜀) |
| 배터리 | 배터리에서도 시작·계속 |
| 실행 시간 제한 | sync·prepare·link 30분, lint 1시간, ingest·compile 2시간 |

### 자격 증명 저장소(keyring)와 InteractiveToken

PAT와 LLM 키는 Windows 자격 증명 관리자(keyring, 서비스 `work-kb`)에 있다. 이 저장소는 로그온한 그 사용자 세션에서만 열린다.

- 그래서 작업은 사용자가 로그온해 있을 때만 돈다. 로그오프 중 놓친 회차는 로그온 뒤 한 번 돈다.
- `schtasks /Create`에 `/NP`, `/RU SYSTEM`, `/RU`·`/RP`를 쓰지 않는다. 자격 증명 저장소를 못 연다.

## 등록하기

1. XML과 명령을 만든다(등록은 하지 않는다).

   ```powershell
   .\.venv\Scripts\python.exe scripts\kb.py schedule
   ```

   | 옵션 | 뜻 |
   |---|---|
   | (없음) | `.state\schedule\<작업 이름>.xml`에 UTF-16(BOM)으로 쓴다 |
   | `--xml-dir <폴더>` | 다른 폴더에 쓴다 |
   | `--print` | 파일 없이 XML과 명령만 보여 준다(`--xml-dir`과 함께 못 씀) |
   | `--company` | 설정과 상관없이 회사용으로 만든다. 없으면 `config.local.yaml`의 `environment`(없으면 company) |

   `kb.py init`의 [5] 스케줄 단계도 같은 일을 한다. `KB_AGENT=1`이면 거부된다.

2. 출력된 `schtasks /Create` 줄 여섯 개를 그대로 실행한다. 관리자 권한은 필요 없다.

   ```powershell
   schtasks /Create /TN work-kb-sync /XML "D:\work\work-kb\.state\schedule\work-kb-sync.xml" /F
   ```

   경로에 `$`나 백틱(`` ` ``)이 있으면 그 줄은 cmd에서 실행하거나 작은따옴표로 감싼다.

3. 저장소를 옮기거나 `.venv`를 다시 만들면 1~2를 다시 한다(XML에 절대 경로가 있다).

## 확인하기

```powershell
schtasks /Query /TN work-kb-sync /V /FO LIST
schtasks /Run /TN work-kb-sync    # 지금 한 번 실행
```

| 항목 | 기대값 |
|---|---|
| `Logon Mode` | `Interactive only` |
| `Run As User` | 자기 계정 |
| `Scheduled Task State` | `Enabled` (회사의 ingest·compile은 `Disabled`) |
| `Last Result` | 종료 코드. 0~7은 `docs/README.md`의 종료 코드 표, 267009는 실행 중, 267011은 아직 실행 안 됨 |

## 로그

오류는 볼트의 `.state\logs\kb.log`에 남는다. 콘솔이 없을 때의 사전 점검 오류와 traceback도 여기 들어간다.
부족하면 같은 명령을 콘솔에서 실행한다(저장소 밖 볼트면 `--root "<볼트>"` 추가).

```powershell
cd <저장소>
.\.venv\Scripts\python.exe -X utf8 scripts\kb.py sync
```

작업 스케줄러 자체의 기록은 `taskschd.msc` → 작업 → 기록 탭에 있다.

## 끄기·켜기

```powershell
schtasks /Change /TN work-kb-sync /DISABLE
schtasks /Change /TN work-kb-sync /ENABLE
```

회사에서 꺼 둔 ingest·compile은 LLM을 설정하고 `kb.py doctor`에 ✗가 없으며 첫 2주가 지난 뒤 켠다.

```powershell
schtasks /Change /TN work-kb-ingest /ENABLE
schtasks /Change /TN work-kb-compile /ENABLE
```

## 지우기

```powershell
foreach ($n in 'sync','prepare','link','ingest','compile','lint') { schtasks /Delete /TN "work-kb-$n" /F }
```

`.state\schedule\*.xml`은 지워도 된다.

## 확인하지 못한 것

- 실제 등록은 하지 않았다. XML은 작업 스케줄러 COM으로 스키마 검사만 했다(`tests/test_schedule.py`).
- `Principal`에 `UserId`가 없다. `Run As User`가 다른 계정이면 작업을 지우고 수동 실행한다.
- 회사 그룹 정책이 작업 등록이나 `pythonw.exe`를 막으면 수동 실행한다.
