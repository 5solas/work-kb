"""config.parse_env_overrides가 값을 yaml.safe_load로 읽어 `[TEAM] ` 같은 문자열 값을 목록으로 바꾼다.
기본값이 "[KB] "인 confluence.title_prefix를 환경변수로 바꾸면 제목 앞이 "['TEAM']"이 된다(confluence_push는 str(prefix))."""
import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[2] / 'scripts'))
from lib import config

out = config.parse_env_overrides({"KB_CONFLUENCE_TITLE_PREFIX": "[TEAM] "})
value = out["confluence"]["title_prefix"]
print("parsed:", repr(value), "-> title would start with", repr(str(value)))
assert isinstance(value, str) and value == "[TEAM] ", "env override of a string setting turned into a list / lost trailing space"
