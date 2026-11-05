# 포장: wheels 묶음과 이식 복사본

회사 PC에서 `pip`가 인터넷에 닿지 않을 때를 위한 오프라인 설치 묶음(`wheels/`)과,
회사로 가져갈 복사본을 만드는 도구를 설명한다. 이식 절차 전체는 `docs/DEPLOY.md`를 따른다.

## 1. `kb.py wheels` — 오프라인 설치 묶음 (집에서)

```
.venv\Scripts\python.exe scripts\kb.py wheels                    # 기본: 3.13·3.14, wheels\
.venv\Scripts\python.exe scripts\kb.py wheels --targets 3.14 --dir D:\임시\wheels
.venv\Scripts\python.exe scripts\kb.py wheels --dry-run           # 실행할 pip 명령만 보기
```

| 옵션 | 뜻 |
|---|---|
| `--targets 3.13,3.14` | 대상 Python 버전(3.13 이상). 기본 `3.13,3.14` |
| `--dir wheels` | 받을 폴더. 상대 경로는 저장소 폴더 기준 |
| `--no-sdist` | 3.15용 PyYAML 원본을 받지 않는다 |
| `--dry-run` | 명령만 출력, 아무것도 받지 않는다 |

대상마다 지금 venv의 pip로 다음을 실행한다(네트워크 필요, `KB_AGENT=1`이면 거부).

```
python -m pip download --only-binary=:all: --platform win_amd64 --implementation cp ^
    --python-version 3.14 --abi cp314 -r requirements.txt -d wheels
python -m pip download --no-binary pyyaml --no-deps pyyaml==<같은 판> -d wheels\sdist
```

결과:

```
wheels\
├── *.whl                 cp313·cp314 wheel (순수 Python wheel은 함께 씀)
├── sdist\pyyaml-X.tar.gz Python 3.15용 PyYAML 원본
└── MANIFEST.txt          대상별 wheel 목록, sha256·크기
```

`MANIFEST.txt`의 `[cp313]`·`[cp314]` 구역에 `requirements.txt`의 다섯 패키지
(requests, pyyaml, markdown, keyring, pillow)가 모두 있어야 한다. 빠지면 `누락:` 줄이 생기고 종료 코드 1이다.
`wheels/`는 `.gitignore`에 있어 커밋되지 않지만, 이식 복사본에는 들어간다(§3).

## 2. 회사에서 설치

지원 인터프리터는 Python 3.13·3.14다.

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
REM 인터넷이 안 되면:
.venv\Scripts\python.exe -m pip install --no-index --find-links wheels -r requirements.txt
```

### Python 3.15

PyPI에 PyYAML의 cp315 wheel이 없다. 3.15 venv에서는 `kb.py`의 사전 점검이 설치 안내와 함께 종료 4로 멈춘다.
회사에는 3.14(또는 3.13)를 쓰는 것이 기본이다.

집에서 해 본 결과(2026-11-05, PyYAML 6.0.3, Python 3.15.2):

- `python -m pip wheel wheels\sdist\pyyaml-6.0.3.tar.gz --no-deps -w <폴더>`가 **성공**해
  `pyyaml-6.0.3-cp315-cp315-win_amd64.whl`이 나왔다. 단, C 컴파일러가 없어 libyaml C 확장 없이
  **순수 Python**으로 빌드되었다(느리지만 동작은 같다). 빌드에는 빌드 도구를 내려받을 네트워크가 필요하다.
- pillow와 charset_normalizer는 PyPI에 cp315 wheel이 있다.
- `kb.py wheels --targets 3.15`는 PyYAML 때문에 실패한다. 3.15 묶음이 꼭 필요하면 위 빌드 결과를
  `wheels\`에 넣은 다음 나머지는 `pip download ... --python-version 3.15 --abi cp315`로 따로 받아야 한다.

## 3. `scripts/deploy_check.py` — 이식 복사본 만들기 (집에서)

```
.venv\Scripts\python.exe scripts\deploy_check.py "D:\옮길 곳\work-kb"
```

대상 폴더는 없거나 비어 있어야 하고 저장소 밖이어야 한다(아니면 종료 4). 표준 라이브러리만 쓴다.

복사에서 빼는 것(지시서 §9.1 + D60):

| 범위 | 제외 |
|---|---|
| 어디서든 | `.git`, `.venv*`, `__pycache__`, `.pytest_cache`, `.obsidian`, 임시·백업 파일(`.*.tmp`, `*~`, `#*#`, `.#*`) |
| 최상위 | `config.local.yaml`, `.env`, `.state/`, `_trash/`, `examples/`, `tests/` |
| 볼트 내용 | `daily/`·`inbox/`·`raw/` 안의 파일 전부, `jira/*.md`, `jira/_views/*.md`, `jira/outbox.jsonl`, `jira/.cache.sqlite*`, `wiki/` 아래 `*.md` |

`.gitkeep`, Bases 파일(`jira/_views/일감.base`, `wiki/엔티티.base`), `wheels/`는 복사한다.
`tests/`가 빠지므로 복사본에서 `kb.py demo`는 종료 4다(집 전용).

복사가 끝나면 파일 수·크기(최상위 폴더별)를 보여 주고, 제외 대상이 하나도 없는지와 필수 파일
(`scripts/kb.py`, `config.yaml`, `requirements.txt`, `AGENTS.md`, `install.ps1` 등)이 있는지를
별도 목록으로 다시 확인한다. 문제가 있으면 `✗` 줄과 함께 종료 1이다.

## 4. 테스트

```
.venv\Scripts\python.exe -m pytest tests\test_packaging.py              # 네트워크 없음
.venv\Scripts\python.exe -m pytest tests\test_packaging.py -m packaging # 실제 내려받기 (집에서만)
```

`packaging` 표시 테스트는 cp314 묶음을 임시 폴더에 받아, 한글·공백 경로의 새 3.14 venv에
`--no-index`로 설치하고 `kb.py --help`(사전 점검)를 통과하는지 본다. 설치 단계는 외부 접속을 막은 채로 돈다.
새 venv는 집의 빌드 도구(uv)로 만든다. 회사 PC에는 uv가 필요 없다.
Python 3.15가 있으면 PyYAML cp315 빌드도 시도해 결과를 출력한다(실패해도 테스트는 통과).
