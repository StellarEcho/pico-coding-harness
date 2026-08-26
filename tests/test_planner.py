import pytest

from pico.planner import (
    PLAN_STEP_LIMIT,
    PLAN_TEXT_LIMIT,
    PlanManager,
    default_plan_state,
    normalize_plan_state,
)


def test_default_plan_state_is_empty_but_shaped():
    state = default_plan_state()

    assert state == {"request": "", "title": "", "steps": [], "updated_at": ""}


def test_normalize_plan_state_accepts_missing_or_legacy_states():
    assert normalize_plan_state(None) == default_plan_state()
    assert normalize_plan_state({}) == default_plan_state()

    legacy = {
        "request": " Fix tests ",
        "title": "Long title " + "x" * 300,
        "steps": [
            {"id": 99, "text": "  First step  ", "status": "almost-done"},
            "Second step",
            {"id": 7, "text": "", "status": "done"},
            {"id": 3, "text": "Third step", "status": "blocked", "note": "waiting"},
        ],
    }
    normalized = normalize_plan_state(legacy)

    assert normalized["request"] == "Fix tests"
    assert normalized["title"].endswith("...")
    assert [step["text"] for step in normalized["steps"]] == ["First step", "Second step", "Third step"]
    assert [step["id"] for step in normalized["steps"]] == [1, 2, 3]
    assert normalized["steps"][0]["status"] == "pending"
    assert normalized["steps"][2]["note"] == "waiting"


def test_init_parses_numbered_bullet_and_dashed_lines():
    plan = PlanManager()

    rendered = plan.apply(
        {
            "action": "init",
            "title": "Fix failing tests",
            "plan": "1. Reproduce the failure\n2. Locate the faulty code\n- Patch the implementation\n* Run pytest\n• Verify",
        }
    )

    assert plan.state["title"] == "Fix failing tests"
    assert [step["text"] for step in plan.state["steps"]] == [
        "Reproduce the failure",
        "Locate the faulty code",
        "Patch the implementation",
        "Run pytest",
        "Verify",
    ]
    assert all(step["status"] == "pending" for step in plan.state["steps"])
    assert rendered == plan.render()


def test_init_keeps_words_starting_with_digits():
    plan = PlanManager()

    plan.apply({"action": "init", "title": "T", "plan": "1. Fix 3-D rendering\n2. Add 2FA\n"})

    assert [step["text"] for step in plan.state["steps"]] == ["Fix 3-D rendering", "Add 2FA"]


def test_init_ignores_blank_lines_and_caps_step_count():
    plan = PlanManager()

    plan.apply(
        {
            "action": "init",
            "title": "T",
            "plan": "\n\n" + "\n".join(f"{i}. step {i}" for i in range(1, 20)) + "\n",
        }
    )

    assert len(plan.state["steps"]) == PLAN_STEP_LIMIT
    assert plan.state["steps"][0]["text"] == "step 1"
    assert plan.state["steps"][-1]["text"] == f"step {PLAN_STEP_LIMIT}"


def test_init_clips_long_step_text():
    plan = PlanManager()
    long_text = "word " * 100

    plan.apply({"action": "init", "title": "T", "plan": f"1. {long_text}"})

    assert len(plan.state["steps"][0]["text"]) <= PLAN_TEXT_LIMIT


def test_init_requires_title_and_plan():
    plan = PlanManager()

    with pytest.raises(ValueError, match="non-empty title"):
        plan.apply({"action": "init", "title": "", "plan": "1. A"})
    with pytest.raises(ValueError, match="non-empty plan"):
        plan.apply({"action": "init", "title": "T", "plan": "  "})


def test_update_transitions_status_and_note():
    plan = PlanManager()
    plan.apply({"action": "init", "title": "T", "plan": "1. A\n2. B"})

    plan.apply({"action": "update", "step_id": 1, "status": "done", "note": "root cause in parser.py"})
    plan.apply({"action": "update", "step_id": 2, "status": "in_progress"})

    assert plan.state["steps"][0]["status"] == "done"
    assert plan.state["steps"][0]["note"] == "root cause in parser.py"
    assert plan.state["steps"][1]["status"] == "in_progress"


def test_update_rejects_bad_step_status_id_and_missing_plan():
    plan = PlanManager()

    with pytest.raises(ValueError, match="no steps"):
        plan.apply({"action": "update", "step_id": 1, "status": "done"})

    plan.apply({"action": "init", "title": "T", "plan": "1. A\n2. B"})

    with pytest.raises(ValueError, match="step_id must be in"):
        plan.apply({"action": "update", "step_id": 3, "status": "done"})
    with pytest.raises(ValueError, match="step_id must be in"):
        plan.apply({"action": "update", "step_id": 0, "status": "done"})
    with pytest.raises(ValueError, match="status must be one of"):
        plan.apply({"action": "update", "step_id": 1, "status": "finished"})
    with pytest.raises(ValueError, match="integer step_id"):
        plan.apply({"action": "update", "step_id": "abc", "status": "done"})
    with pytest.raises(ValueError, match="action must be one of"):
        plan.apply({"action": "delete"})


def test_next_pending_returns_first_actionable_step():
    plan = PlanManager()
    plan.apply({"action": "init", "title": "T", "plan": "1. A\n2. B\n3. C"})
    plan.apply({"action": "update", "step_id": 1, "status": "done"})
    plan.apply({"action": "update", "step_id": 2, "status": "in_progress"})

    assert plan.next_pending() == {"id": 2, "text": "B"}

    plan.apply({"action": "update", "step_id": 2, "status": "done"})
    plan.apply({"action": "update", "step_id": 3, "status": "skipped"})
    assert plan.next_pending() is None


def test_render_uses_status_markers_and_none_when_empty():
    empty = PlanManager()
    assert empty.render() == "Plan:\n- none"

    plan = PlanManager()
    plan.apply({"action": "init", "title": "Fix failing tests", "plan": "1. A\n2. B\n3. C"})
    plan.apply({"action": "update", "step_id": 1, "status": "done"})
    plan.apply({"action": "update", "step_id": 2, "status": "in_progress", "note": "on parser.py"})
    plan.apply({"action": "update", "step_id": 3, "status": "blocked"})

    text = plan.render()
    assert text.startswith("Plan: Fix failing tests")
    assert "- [x] 1. A" in text
    assert "- [>] 2. B -- on parser.py" in text
    assert "- [!] 3. C" in text


def test_render_clips_to_budget():
    plan = PlanManager()
    plan.apply({"action": "init", "title": "T", "plan": "1. " + "x" * 200})

    rendered = plan.render(budget=80)

    assert len(rendered) <= 80
    assert rendered.endswith("...")


def test_reset_clears_steps_and_sets_request():
    plan = PlanManager()
    plan.reset(request="Fix tests")
    plan.apply({"action": "init", "title": "Fix tests", "plan": "1. A"})

    plan.reset(request="New task")

    assert plan.state["request"] == "New task"
    assert plan.state["steps"] == []
    assert plan.state["title"] == ""


def test_placeholder_generates_generic_steps_and_keeps_request():
    plan = PlanManager()
    plan.reset(request="Fix tests")

    plan.placeholder("Fix tests")

    assert plan.state["request"] == "Fix tests"
    assert plan.state["title"] == "Fix tests"
    assert [step["text"] for step in plan.state["steps"]] == [
        "Understand the request",
        "Investigate the workspace",
        "Implement the change",
        "Verify with tests",
    ]
    assert all(step["status"] == "pending" for step in plan.state["steps"])
    assert plan.next_pending() == {"id": 1, "text": "Understand the request"}


def test_to_dict_round_trip_preserves_plan():
    plan = PlanManager()
    plan.reset(request="Fix tests")
    plan.apply({"action": "init", "title": "Fix tests", "plan": "1. A\n2. B"})
    plan.apply({"action": "update", "step_id": 1, "status": "done"})

    restored = PlanManager(plan.to_dict())

    assert restored.render() == plan.render()
    assert restored.to_dict() == plan.to_dict()


def test_metrics_reports_step_counts_and_next_pending():
    plan = PlanManager()
    plan.reset(request="Fix tests")
    plan.apply({"action": "init", "title": "Fix tests", "plan": "1. A\n2. B\n3. C"})
    plan.apply({"action": "update", "step_id": 1, "status": "done"})

    metrics = plan.metrics()

    assert metrics["title"] == "Fix tests"
    assert metrics["step_count"] == 3
    assert metrics["status_counts"] == {"pending": 2, "done": 1}
    assert metrics["next_pending"] == {"id": 2, "text": "B"}
