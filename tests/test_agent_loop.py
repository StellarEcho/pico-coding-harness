import json

import pytest

from pico import FakeModelClient, Pico, SessionStore, WorkspaceContext
from pico.agent_loop import AgentLoop
from pico.task_state import TaskState


def build_agent(tmp_path, outputs, **kwargs):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    workspace = WorkspaceContext.build(tmp_path)
    store = SessionStore(tmp_path / ".pico" / "sessions")
    return Pico(
        model_client=FakeModelClient(outputs),
        workspace=workspace,
        session_store=store,
        approval_policy="auto",
        **kwargs,
    )


def test_agent_loop_runs_same_control_flow_as_pico_ask(tmp_path):
    (tmp_path / "hello.txt").write_text("alpha\n", encoding="utf-8")
    agent = build_agent(
        tmp_path,
        [
            '<tool>{"name":"read_file","args":{"path":"hello.txt","start":1,"end":1}}</tool>',
            "<final>Done.</final>",
        ],
    )

    answer = AgentLoop(agent).run("Inspect hello.txt")

    assert answer == "Done."
    assert agent.current_task_state.status == "completed"
    assert agent.run_store.report_path(agent.current_task_state.run_id).exists()


def test_pico_ask_delegates_to_agent_loop(tmp_path):
    agent = build_agent(tmp_path, ["<final>Facade works.</final>"])

    assert agent.ask("Use facade") == "Facade works."


def test_agent_loop_reserves_a_final_answer_after_tool_budget_is_exhausted(tmp_path):
    (tmp_path / "facts.txt").write_text("one\ntwo\nthree\nfour\nfive\nsix\n", encoding="utf-8")
    tool_outputs = [
        f'<tool>{{"name":"read_file","args":{{"path":"facts.txt","start":{line},"end":{line}}}}}</tool>'
        for line in range(1, 7)
    ]
    agent = build_agent(
        tmp_path,
        [*tool_outputs, "<final>All six facts were inspected.</final>"],
        max_steps=6,
    )

    answer = agent.ask("Inspect all facts and summarize them")

    assert answer == "All six facts were inspected."
    assert agent.current_task_state.status == "completed"
    assert agent.current_task_state.tool_steps == 6
    assert agent.current_task_state.attempts == 7
    trace_path = agent.run_store.trace_path(agent.current_task_state)
    trace_events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert any(
        event["event"] == "model_requested" and event.get("purpose") == "finalization"
        for event in trace_events
    )


def test_agent_loop_persists_model_failure_before_reraising(tmp_path):
    class FailingModelClient:
        supports_prompt_cache = False
        last_completion_metadata = {
            "stop_reason": "max_tokens",
            "content_block_types": ["thinking"],
        }

        def complete(self, *args, **kwargs):
            raise RuntimeError(
                "Anthropic-compatible response ended before a text block "
                "(stop_reason=max_tokens, content_types=thinking)"
            )

    agent = build_agent(tmp_path, [])
    agent.model_client = FailingModelClient()

    with pytest.raises(RuntimeError, match="ended before a text block"):
        agent.ask("Inspect the tests")

    state = agent.current_task_state
    assert state.status == "failed"
    assert state.stop_reason == "model_error"
    assert state.attempts == 1
    assert agent.run_store.task_state_path(state).exists()
    assert agent.run_store.report_path(state).exists()
    report = agent.run_store.load_report(state.run_id)
    assert report["stop_reason"] == "model_error"
    assert report["prompt_metadata"]["stop_reason"] == "max_tokens"


def test_agent_loop_creates_plan_via_update_plan_and_uses_it_in_prompts_and_checkpoints(tmp_path):
    agent = build_agent(
        tmp_path,
        [
            '<tool>{"name":"update_plan","args":{"action":"init","title":"Fix failing tests","plan":"1. Reproduce the failure\\n2. Locate the faulty code\\n3. Patch the implementation\\n4. Run pytest"}}</tool>',
            '<tool>{"name":"update_plan","args":{"action":"update","step_id":1,"status":"done","note":"fails on parser"}}</tool>',
            '<tool>{"name":"read_file","args":{"path":"README.md","start":1,"end":1}}</tool>',
            "<final>Done.</final>",
        ],
    )

    answer = agent.ask("Fix the failing tests")

    assert answer == "Done."
    assert agent.current_task_state.status == "completed"
    assert agent.plan.state["title"] == "Fix failing tests"
    assert agent.plan.state["steps"][0]["status"] == "done"
    assert agent.plan.state["steps"][0]["note"] == "fails on parser"
    assert agent.plan.state["steps"][1]["status"] == "pending"

    prompts = agent.model_client.prompts
    assert "- [ ] 1. Reproduce the failure" in prompts[1]
    assert "- [x] 1. Reproduce the failure -- fails on parser" in prompts[2]

    checkpoints = agent.checkpoint_state()["items"]
    assert any(
        item["current_goal"] == "Fix failing tests" and item["next_step"] == "Step 2: Locate the faulty code"
        for item in checkpoints.values()
    )

    trace_path = agent.run_store.trace_path(agent.current_task_state)
    trace_events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert any(event["event"] == "plan_reset" for event in trace_events)
    assert not any(event["event"] == "plan_auto_initialized" for event in trace_events)

    report = agent.run_store.load_report(agent.current_task_state.run_id)
    assert report["plan"]["step_count"] == 4
    assert report["plan"]["status_counts"]["done"] == 1
    assert report["plan"]["next_pending"] == {"id": 2, "text": "Locate the faulty code"}


def test_agent_loop_auto_initializes_placeholder_plan_when_model_never_plans(tmp_path):
    agent = build_agent(
        tmp_path,
        [
            '<tool>{"name":"list_files","args":{"path":"."}}</tool>',
            '<tool>{"name":"search","args":{"pattern":"demo","path":"."}}</tool>',
            '<tool>{"name":"read_file","args":{"path":"README.md","start":1,"end":1}}</tool>',
            "<final>Done.</final>",
        ],
    )

    answer = agent.ask("Inspect the repository and report")

    assert answer == "Done."
    assert agent.plan.state["title"] == "Inspect the repository and report"
    assert [step["text"] for step in agent.plan.state["steps"]] == [
        "Understand the request",
        "Investigate the workspace",
        "Implement the change",
        "Verify with tests",
    ]

    prompts = agent.model_client.prompts
    assert "Plan:\n- none" in prompts[0]
    assert "Plan: Inspect the repository and report" in prompts[3]
    assert "- [ ] 1. Understand the request" in prompts[3]

    trace_path = agent.run_store.trace_path(agent.current_task_state)
    trace_events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert any(event["event"] == "plan_auto_initialized" for event in trace_events)

    report = agent.run_store.load_report(agent.current_task_state.run_id)
    assert report["plan"]["step_count"] == 4
    assert report["plan"]["status_counts"] == {"pending": 4}


def test_plan_auto_init_threshold_is_configurable(tmp_path):
    (tmp_path / "sample.txt").write_text("alpha\n", encoding="utf-8")
    agent = build_agent(
        tmp_path,
        [
            '<tool>{"name":"list_files","args":{"path":"."}}</tool>',
            '<tool>{"name":"search","args":{"pattern":"demo","path":"."}}</tool>',
            '<tool>{"name":"read_file","args":{"path":"README.md","start":1,"end":1}}</tool>',
            '<tool>{"name":"read_file","args":{"path":"sample.txt","start":1,"end":1}}</tool>',
            "<final>Done.</final>",
        ],
        plan_auto_init_after=5,
    )

    assert agent.ask("Inspect the repo") == "Done."

    assert agent.plan.state["steps"] == []
    trace_path = agent.run_store.trace_path(agent.current_task_state)
    trace_events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert not any(event["event"] == "plan_auto_initialized" for event in trace_events)


def test_plan_survives_resume_and_resets_on_new_request(tmp_path):
    agent = build_agent(tmp_path, ["<final>Done.</final>"])
    agent.plan.reset(request="Fix tests")
    agent.plan.apply({"action": "init", "title": "Fix tests", "plan": "1. Reproduce\n2. Patch"})
    agent.session["plan"] = agent.plan.to_dict()
    agent.session_path = agent.session_store.save(agent.session)

    resumed = Pico.from_session(
        model_client=FakeModelClient(["<final>Done.</final>"]),
        workspace=agent.workspace,
        session_store=agent.session_store,
        session_id=agent.session["id"],
    )

    assert resumed.ensure_plan_for_request("Fix tests") is False
    assert [step["text"] for step in resumed.plan.state["steps"]] == ["Reproduce", "Patch"]

    assert resumed.ensure_plan_for_request("Fix the docs instead") is True
    assert resumed.plan.state["request"] == "Fix the docs instead"
    assert resumed.plan.state["steps"] == []


def test_plan_feature_disabled_hides_update_plan_tool(tmp_path):
    agent = build_agent(tmp_path, [], feature_flags={"plan": False})

    prompt = agent.prompt("Do something")

    assert "- update_plan(" not in prompt
    result = agent.run_tool("update_plan", {"action": "init", "title": "T", "plan": "1. A"})
    assert result == "error: unknown tool 'update_plan'"


def test_agent_loop_compacts_history_on_step_interval(tmp_path):
    (tmp_path / "sample.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    agent = build_agent(
        tmp_path,
        [
            '<tool>{"name":"list_files","args":{"path":"."}}</tool>',
            '<tool>{"name":"search","args":{"pattern":"demo","path":"."}}</tool>',
            '<tool>{"name":"read_file","args":{"path":"README.md","start":1,"end":1}}</tool>',
            '<tool>{"name":"search","args":{"pattern":"alpha","path":"."}}</tool>',
            '<tool>{"name":"read_file","args":{"path":"sample.txt","start":1,"end":2}}</tool>',
            '<tool>{"name":"list_files","args":{"path":"."}}</tool>',
            '<tool>{"name":"search","args":{"pattern":"beta","path":"."}}</tool>',
            "<final>Done.</final>",
        ],
        max_steps=8,
    )
    agent.compaction_policy.step_interval = 2
    agent.compaction_policy.recent_window = 6

    answer = agent.ask("Inspect the repo")

    assert answer == "Done."
    prompts = agent.model_client.prompts
    assert "- summary: user: Inspect the repo" in prompts[6]
    assert "[tool:list_files]" in prompts[6]

    trace_path = agent.run_store.trace_path(agent.current_task_state)
    trace_events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    compact_events = [event for event in trace_events if event["event"] == "context_compacted"]
    assert any(event.get("trigger") == "step_interval" for event in compact_events)
    assert any(event.get("entries_folded") == 1 for event in compact_events)

    # 非破坏性：session history 仍然完整（user + 7 条工具 + 最终 assistant 回答）
    assert len(agent.session["history"]) == 9
    report = agent.run_store.load_report(agent.current_task_state.run_id)
    assert report["compaction"]["triggers"]["step_interval"] >= 1
    assert report["compaction"]["summary_lines"] >= 1


def test_agent_loop_compacts_history_on_budget_pressure(tmp_path):
    agent = build_agent(tmp_path, ["<final>Done.</final>"])
    agent.context_manager.total_budget = 600
    agent.compaction_policy.threshold = 0.8
    for index in range(8):
        agent.record(
            {
                "role": "user" if index % 2 == 0 else "assistant",
                "content": f"history-{index}-" + ("A" * 260),
                "created_at": f"2026-04-07T10:{index:02d}:00+00:00",
            }
        )

    assert agent.ask("Continue the long task") == "Done."

    prompts = agent.model_client.prompts
    assert "- summary: user: history-0-" in prompts[0]
    assert "- summary: assistant: history-1-" in prompts[0]
    trace_path = agent.run_store.trace_path(agent.current_task_state)
    trace_events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert any(event["event"] == "context_compacted" and event.get("trigger") == "budget_pressure" for event in trace_events)


def test_runtime_manual_compact_writes_session_and_trace(tmp_path):
    agent = build_agent(tmp_path, [])
    for index in range(8):
        agent.record(
            {
                "role": "user" if index % 2 == 0 else "assistant",
                "content": f"entry-{index}-" + ("B" * 120),
                "created_at": f"2026-04-07T10:{index:02d}:00+00:00",
            }
        )
    state = TaskState.create(task_id="task_compact", user_request="Compact")
    agent.current_task_state = state
    agent.current_run_dir = agent.run_store.start_run(state)

    decision = agent.compact_history(trigger="manual")

    assert decision is not None
    assert decision.trigger == "manual"
    assert decision.entries_folded == 2
    assert agent.session["compaction"]["summarized_through"] == 2
    trace_path = agent.run_store.trace_path(state)
    trace_events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert any(event["event"] == "context_compacted" and event.get("trigger") == "manual" for event in trace_events)


def test_compaction_feature_disabled_ignores_policy(tmp_path):
    agent = build_agent(tmp_path, ["<final>Done.</final>"], feature_flags={"compaction": False})
    agent.context_manager.total_budget = 600
    for index in range(8):
        agent.record(
            {
                "role": "user" if index % 2 == 0 else "assistant",
                "content": f"entry-{index}-" + ("B" * 120),
                "created_at": f"2026-04-07T10:{index:02d}:00+00:00",
            }
        )
    agent.session["compaction"]["summary_entries"] = ["user: stale summary"]

    assert agent.maybe_compact({}, 0) is None
    assert agent.compact_history(trigger="manual") is None
    prompt = agent.prompt("Continue")
    assert "- summary:" not in prompt


def test_agent_loop_uses_memory_read_and_memory_update_tools(tmp_path):
    agent = build_agent(
        tmp_path,
        [
            '<tool>{"name":"memory_update","args":{"action":"add","topic":"project-conventions","note":"Run focused tests first."}}</tool>',
            '<tool>{"name":"memory_read","args":{"query":"focused tests","limit":3}}</tool>',
            "<final>Done.</final>",
        ],
    )

    assert agent.ask("Remember the convention") == "Done."

    notes = agent.memory.durable_store.load_topic_notes("project-conventions")
    assert [note["text"] for note in notes] == ["Run focused tests first."]
    tool_events = [item for item in agent.session["history"] if item["role"] == "tool"]
    assert tool_events[0]["name"] == "memory_update"
    assert "Run focused tests first." in tool_events[1]["content"]


def test_memory_feature_disabled_hides_memory_tools(tmp_path):
    agent = build_agent(tmp_path, [], feature_flags={"memory": False})

    prompt = agent.prompt("Inspect memory")

    assert "- memory_read(" not in prompt
    assert "- memory_update(" not in prompt
    assert agent.run_tool("memory_read", {"query": "x"}) == "error: unknown tool 'memory_read'"


def _write_skill(root, payload):
    skills_dir = root / ".pico" / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    (skills_dir / f"{payload['skill_id']}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_skill_after_tool_hook_fires_on_tool_error(tmp_path):
    _write_skill(
        tmp_path,
        {
            "skill_id": "watcher",
            "version": "1.0.0",
            "description": "Watch tool outcomes.",
            "memory_hooks": ["after_tool"],
        },
    )
    agent = build_agent(
        tmp_path,
        [
            '<tool>{"name":"read_file","args":{"path":"missing.txt","start":1,"end":1}}</tool>',
            "<final>Done.</final>",
        ],
    )

    assert agent.ask("Read the missing file") == "Done."

    notes = agent.memory.to_dict()["episodic_notes"]
    assert any("skill after_tool:" in note["text"] for note in notes)
    trace_path = agent.run_store.trace_path(agent.current_task_state)
    trace_events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert any(
        event["event"] == "skill_hook_triggered" and event.get("skill_id") == "watcher" and event.get("hook_event") == "after_tool"
        for event in trace_events
    )


def test_skill_plan_updated_hook_fires(tmp_path):
    _write_skill(
        tmp_path,
        {
            "skill_id": "planner-watcher",
            "version": "1.0.0",
            "description": "Watch plan updates.",
            "memory_hooks": ["plan_updated"],
        },
    )
    agent = build_agent(
        tmp_path,
        [
            '<tool>{"name":"update_plan","args":{"action":"init","title":"Fix tests","plan":"1. Reproduce\\n2. Patch"}}</tool>',
            "<final>Done.</final>",
        ],
    )

    assert agent.ask("Fix the tests") == "Done."

    notes = agent.memory.to_dict()["episodic_notes"]
    assert any("skill plan_updated:" in note["text"] for note in notes)


def test_skill_context_compacted_hook_fires(tmp_path):
    _write_skill(
        tmp_path,
        {
            "skill_id": "compaction-watcher",
            "version": "1.0.0",
            "description": "Watch compactions.",
            "memory_hooks": ["context_compacted"],
        },
    )
    agent = build_agent(tmp_path, [])
    for index in range(8):
        agent.record(
            {
                "role": "user" if index % 2 == 0 else "assistant",
                "content": f"entry-{index}-" + ("B" * 120),
                "created_at": f"2026-04-07T10:{index:02d}:00+00:00",
            }
        )
    state = TaskState.create(task_id="task_hook", user_request="Compact")
    agent.current_task_state = state
    agent.current_run_dir = agent.run_store.start_run(state)

    agent.compact_history(trigger="manual")

    notes = agent.memory.to_dict()["episodic_notes"]
    assert any("skill context_compacted:" in note["text"] for note in notes)
