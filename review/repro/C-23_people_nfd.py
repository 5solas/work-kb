"""notes.find_people가 NFD(조합형 자모) 한글 이름을 못 찾거나 잘라 낸다. _PERSON_CHARS에 U+1100–U+11FF가 없고 NFC 정규화를 하지 않는다."""
import sys
import unicodedata
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[2] / 'scripts'))
from lib import notes

nfc = "- 10:00 @김철수 와 회의"
nfd = unicodedata.normalize("NFD", nfc)
a = notes.find_people(nfc)
b = notes.find_people(nfd)
print("NFC:", a, " NFD:", b)
assert [unicodedata.normalize("NFC", x) for x in b] == a, "NFD Korean @name not detected"
