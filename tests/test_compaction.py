from pico.compaction import (
    MAX_SUMMARY_LINES,
    CompactionPolicy,
    RunningSummaryBuilder,
    TokenEstimator,
    default_compaction_state,
    normalize_compaction_state,
)


def _item(role, name="", args=None, content=""):
    if role == "tool":
        return {"role": "tool", "name": name, "args": args or {}, "content": content}
    return {"role": role, "content": content}


def _history(count=10):
    items = [_item("user", content="Inspect the repo")]
    for index in range(count - 1):
        items.append(_item("tool", name="list_files", args={"path": "."}, content=f"result-{index}"))
    return items


def test_token_estimator_counts_ascii_and_cjk():
    estimator = TokenEstimator()

    assert estimator.estimate("a" * 400) == 100
    assert estimator.estimate("中" * 10) == 10
    assert estimator.estimate(("a" * 400) + ("中" * 10)) == 110
    assert estimator.estimate("") == 1


def test_token_estimator_calibration_is_bounded():
    estimator = TokenEstimator()

    assert estimator.calibrate(400, 100) is True
    assert estimator.estimate("a" * 400) == 100

    # 实际 8 字符/token：缩放被钳制在 2.0
    estimator.calibrate(400, 50)
    assert estimator.estimate("a" * 400) == 200

    # 实际 1 字符/token：观察值被钳制到 2.0，缩放 0.5
    estimator.calibrate(400, 400)
    assert estimator.estimate("a" * 400) == 50

    assert estimator.calibrate(0, 0) is False
    assert estimator.calibrate("bad", None) is False


def test_default_compaction_state_is_shaped():
    assert default_compaction_state() == {
        "summary_entries": [],
        "summarized_through": 0,
        "stats": {"triggers": {}, "entries_folded": 0, "last_trigger": ""},
        "updated_at": "",
    }


def test_normalize_compaction_state_accepts_legacy_state():
    assert normalize_compaction_state(None) == default_compaction_state()
    assert normalize_compaction_state({}) == default_compaction_state()

    normalized = normalize_compaction_state(
        {
            "summary_entries": [" line one ", "", "x" * 500],
            "summarized_through": -3,
            "stats": {"triggers": {"manual": "2"}, "entries_folded": -1, "last_trigger": "manual"},
            "updated_at": "2026-08-26T00:00:00+00:00",
        }
    )

    assert normalized["summary_entries"][0] == "line one"
    assert normalized["summary_entries"][-1].endswith("...")
    assert normalized["summarized_through"] == 0
    assert normalized["stats"]["triggers"] == {"manual": 2}
    assert normalized["stats"]["entries_folded"] == 0


def test_running_summary_builder_extracts_compact_lines():
    history = [
        _item("user", content="Fix the failing tests"),
        _item("tool", name="run_shell", args={"command": "pytest -q"}, content="exit_code: 1\nstdout:\nFAIL test_one\nstderr:\n(empty)"),
        _item("tool", name="read_file", args={"path": "src/parser.py"}, content="# src/parser.py\nline"),
        _item("tool", name="update_plan", args={"action": "init", "title": "Fix tests"}, content="Plan: Fix tests"),
        _item("tool", name="delegate", args={"task": "inspect parser"}, content="delegate_result:\nok"),
        _item("assistant", content="Found the root cause"),
    ]

    entries = RunningSummaryBuilder.extend([], history, 0, 3)
    assert entries[0] == "user: Fix the failing tests"
    assert entries[1].startswith("run_shell pytest -q exit=1 FAIL test_one")
    assert entries[2] == "read_file src/parser.py"

    full = RunningSummaryBuilder.extend([], history, 0, len(history))
    assert full == ["update_plan init Fix tests", "delegate inspect parser", "assistant: Found the root cause"]


def test_running_summary_builder_caps_lines_and_appends_incrementally():
    history = _history(count=10)

    entries = RunningSummaryBuilder.extend([], history, 0, 8)
    assert len(entries) == MAX_SUMMARY_LINES

    extended = RunningSummaryBuilder.extend(entries, history, 8, 9)
    assert extended == entries[-2:] + ['list_files {"path": "."}']


def test_compaction_policy_evaluate_triggers():
    policy = CompactionPolicy(threshold=0.8, step_interval=8, recent_window=6)
    metrics = {"prompt_chars": 10000, "prompt_budget_chars": 12000}

    assert policy.evaluate(metrics, tool_steps=0, manual=True) == "manual"
    assert policy.evaluate(metrics, tool_steps=0, history_len=10) == "budget_pressure"
    assert policy.evaluate(metrics, tool_steps=0, history_len=4) is None
    assert policy.evaluate({}, tool_steps=8) == "step_interval"
    assert policy.evaluate({}, tool_steps=7) is None
    assert policy.evaluate(metrics, tool_steps=16, history_len=10) == "budget_pressure"


def test_compaction_policy_compact_folds_older_entries_and_updates_state():
    policy = CompactionPolicy(recent_window=3)
    history = _history(count=10)

    state, decision = policy.compact(history, default_compaction_state(), "manual")

    assert decision.trigger == "manual"
    assert decision.strategies == ["running_summary"]
    assert decision.entries_folded == 7
    assert decision.total_summary_lines == MAX_SUMMARY_LINES
    assert state["summarized_through"] == 7
    assert state["stats"]["triggers"] == {"manual": 1}
    assert state["stats"]["entries_folded"] == 7
    assert state["stats"]["last_trigger"] == "manual"
    assert len(history) == 10

    # 再次压缩且没有新事件：不重复折叠
    state2, decision2 = policy.compact(history, state, "step_interval")
    assert decision2.entries_folded == 0
    assert state2["stats"]["triggers"] == {"manual": 1, "step_interval": 1}
    assert state2["stats"]["entries_folded"] == 7

    # 新事件到达后，窗口后移一位，只增量补算新越界的那条
    history.append(_item("tool", name="search", args={"pattern": "beta"}, content="match"))
    state3, decision3 = policy.compact(history, state2, "step_interval")
    assert decision3.entries_folded == 1
    assert state3["summarized_through"] == 8
    assert state3["summary_entries"][-1] == 'list_files {"path": "."}'


def test_compaction_decision_estimates_token_savings():
    policy = CompactionPolicy(recent_window=3)
    history = _history(count=10)

    _, decision = policy.compact(history, default_compaction_state(), "budget_pressure")

    assert decision.estimated_before_tokens > decision.estimated_after_tokens
    assert decision.history_raw_before_chars > decision.history_raw_after_chars
    assert decision.to_dict()["trigger"] == "budget_pressure"
