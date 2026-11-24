"""act create·promote의 항목(--field)·결과(--json), promote --expect-line, act discard."""
from __future__ import annotations

import hashlib
import json

import pytest

import jira_act
import jira_meta
from lib import core, outbox
from test_jira_act import DAY, LINE_REVIEW, env, make_env, start_jira, writes  # noqa: F401 (픽스처)

LINE = "- 11:40 개인: PR 리뷰 요청 옴"
SPEC = f"daily/{DAY}.md:{LINE_REVIEW}"


def act(capsys, vault, *args):
    try:
        code, err = jira_act.main([*args, "--root", str(vault)]), ""
    except core.KbError as exc:
        code, err = exc.exit_code, str(exc)
    out, stderr = capsys.readouterr()
    return code, out, err + stderr


def result(out):
    return json.loads(out.strip().splitlines()[-1])


@pytest.fixture
def meta(env, capsys):  # noqa: F811
    jira_meta.main(["create", "KB", "--json", "--root", str(env.vault)])
    capsys.readouterr()
    return env


def create(*extra):
    return ("create", "KB", "--summary", "x", "--type", "Task", "--yes", *extra)


def test_fields_are_converted(meta, capsys):
    code, _, err = act(
        capsys, meta.vault,
        *create(
            "--field", 'priority="2"', "--field", 'assignee="kimcs"', "--field", 'components=["301","302"]',
            "--field", 'labels=["a","b"]', "--field", 'duedate="2026-11-30"', "--field", 'customfield_10001="KB-12"',
            "--field", 'customfield_10003="1.5"', "--field", 'customfield_10004="501"',
            "--field", 'customfield_10006="2026-11-30T09:00"', "--field", 'customfield_10007=["kimcs"]',
            "--field", 'customfield_10008="여러\\n줄"', "--field", 'customfield_10009="a=b"',
        ),
    )  # fmt: skip
    assert code == 0, err
    fields = writes(meta.jira)[0]["json"]["fields"]
    base = {"project": {"key": "KB"}, "summary": "x", "issuetype": {"name": "Task"}}
    assert fields == base | {
        "priority": {"id": "2"}, "assignee": {"name": "kimcs"}, "components": [{"id": "301"}, {"id": "302"}],
        "labels": ["a", "b"], "duedate": "2026-11-30", "customfield_10001": "KB-12", "customfield_10003": 1.5,
        "customfield_10004": {"id": "501"}, "customfield_10006": "2026-11-30T09:00:00.000+0900",
        "customfield_10007": [{"name": "kimcs"}], "customfield_10008": "여러\n줄", "customfield_10009": "a=b",
    }  # fmt: skip
    assert outbox.records(meta.vault)[0]["payload"]["fields"]["priority"] == {"id": "2"}


@pytest.mark.parametrize(
    "field",
    ['priority="99"', 'components="301"', 'components=["999"]', 'labels=["a b"]', 'duedate="내일"', 'customfield_10003="일"',
     'customfield_10002="1"', 'nope="1"', 'summary="y"', 'project="OPS"', 'description="y"', "priority", "priority=깨짐",
     'assignee=""', 'customfield_10001="에픽"', 'priority={"id":"2"}', 'customfield_10003="nan"', 'duedate="2026-13-01"'],
)  # fmt: skip
def test_bad_fields_are_refused_before_outbox(meta, capsys, field):
    code, out, err = act(capsys, meta.vault, *create("--field", field))
    assert code == 4, (out, err)
    assert writes(meta.jira) == [] and outbox.records(meta.vault) == []


def test_field_without_cache_or_for_other_type_is_refused(env, capsys):  # noqa: F811
    code, _, err = act(capsys, env.vault, *create("--field", 'priority="2"'))
    assert code == 4 and "meta create KB" in err


def test_secret_in_fields_is_refused(meta, capsys):
    code, _, err = act(capsys, meta.vault, *create("--field", 'customfield_10009="act-test-token-1234"', "--json"))
    assert code == 4 and writes(meta.jira) == [] and "act-test-token" not in err


def test_json_done(env, capsys):  # noqa: F811
    code, out, _ = act(capsys, env.vault, *create("--json"))
    data = result(out)
    assert code == 0 and data["state"] == "done" and data["key"] == "KB-16" and data["record"]


def test_json_failed_then_discard(env, capsys):  # noqa: F811
    code, out, _ = act(capsys, env.vault, "create", "KB", "--summary", "x", "--type", "없는유형", "--yes", "--json")
    data = result(out)
    assert code == 1 and data["state"] == "failed" and "issue type" in data["message"]
    code, _, _ = act(capsys, env.vault, "discard", data["record"])
    assert code == 0 and outbox.records(env.vault)[0]["status"] == "discarded"
    code, out, _ = act(capsys, env.vault, "flush", "--retry-failed")
    assert code == 0 and len(writes(env.jira)) == 1
    _, out, _ = act(capsys, env.vault, "list")
    assert data["record"] not in out
    _, out, _ = act(capsys, env.vault, "list", "--all")
    assert data["record"] in out
    code, _, err = act(capsys, env.vault, "discard", data["record"])
    assert code == 4


def test_discard_refuses_other_states(env, capsys):  # noqa: F811
    _, out, _ = act(capsys, env.vault, *create("--json"))
    code, _, _ = act(capsys, env.vault, "discard", result(out)["record"])
    assert code == 4 and outbox.records(env.vault)[0]["status"] == "done"
    code, _, _ = act(capsys, env.vault, "discard", "없음")
    assert code == 4


def test_json_pending_when_down(env, capsys):  # noqa: F811
    env.jira.go_down()
    code, out, _ = act(capsys, env.vault, *create("--json"))
    assert code == 3 and result(out)["state"] == "pending"


def test_json_uncertain_on_odd_2xx(make_env, capsys):  # noqa: F811
    e = make_env(html_200=True)
    code, out, _ = act(capsys, e.vault, *create("--json"))
    assert code == 3 and result(out)["state"] == "uncertain"


def test_json_uncertain_when_result_cannot_be_recorded(env, capsys, monkeypatch):  # noqa: F811
    def busy(*_a, **_k):
        raise core.LockBusy("잠김")

    monkeypatch.setattr(jira_act, "_finish", busy)
    code, out, _ = act(capsys, env.vault, *create("--json"))
    data = result(out)
    assert code == 6 and data["state"] == "uncertain" and data["record"]


def test_json_none_without_record(env, capsys, monkeypatch):  # noqa: F811
    code, out, _ = act(capsys, env.vault, "create", "ZZ", "--summary", "x", "--type", "Task", "--yes", "--json")
    data = result(out)
    assert code == 4 and data["state"] == "none" and data["record"] is None and "project_keys" in data["message"]


def test_json_message_hides_secret(env, capsys):  # noqa: F811
    _, out, err = act(capsys, env.vault, "create", "KB", "--summary", "act-test-token-1234", "--type", "Task", "--yes", "--json")
    assert result(out)["state"] == "none" and "act-test-token" not in out + err


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_promote_with_fields_and_expected_line(meta, capsys):
    code, out, err = act(
        capsys, meta.vault, "promote", "--from", SPEC, "KB", "--yes", "--json", "--summary", "고친 제목", "--description", "설명",
        "--field", 'priority="2"', "--expect-line", _hash(LINE),
    )  # fmt: skip
    assert code == 0, err
    fields = writes(meta.jira)[0]["json"]["fields"]
    assert fields["summary"] == "고친 제목" and fields["description"] == "설명" and fields["priority"] == {"id": "2"}
    assert f"{LINE} [[KB-16]]" in meta.daily.read_text(encoding="utf-8")
    data = result(out)
    assert data["state"] == "done" and data["linked"] is True


def test_promote_refuses_changed_line(env, capsys):  # noqa: F811
    before = env.daily.read_bytes()
    code, out, _ = act(capsys, env.vault, "promote", "--from", SPEC, "KB", "--yes", "--json", "--expect-line", _hash(LINE + " 바뀜"))
    assert code == 4 and result(out)["state"] == "none"
    assert writes(env.jira) == [] and env.daily.read_bytes() == before


def test_old_records_without_fields_still_flush(env, capsys):  # noqa: F811
    rec = outbox.make_record(action="create", key="KB", payload={"summary": "옛 기록", "issuetype": "Task"}, ts="2026-11-05T10:00:00+09:00")
    outbox.append(env.vault, rec)
    code, _, _ = act(capsys, env.vault, "flush")
    assert code == 0 and writes(env.jira)[0]["json"]["fields"]["summary"] == "옛 기록"


def test_discard_refused_under_agent(env, run_kb):  # noqa: F811
    assert run_kb("act", "discard", "x", env={"KB_AGENT": "1"}).returncode == 7
