from pico import FakeModelClient, Pico, SessionStore, WorkspaceContext
from pico.checkpoint import (
    CHECKPOINT_FULL_VALID_STATUS,
    CHECKPOINT_NONE_STATUS,
    CHECKPOINT_SCHEMA_MISMATCH_STATUS,
    CHECKPOINT_SCHEMA_VERSION,
    current_runtime_identity,
    evaluate_resume_state,
    infer_next_step,
    render_checkpoint_text,
)
from pico.planner import PlanManager
from pico.task_state import TaskState


def build_agent(tmp_path, outputs=None, **kwargs):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    workspace = WorkspaceContext.build(tmp_path)
    store = SessionStore(tmp_path / ".pico" / "sessions")
    return Pico(
        model_client=FakeModelClient(outputs or []),
        workspace=workspace,
        session_store=store,
        approval_policy=kwargs.pop("approval_policy", "auto"),
        **kwargs,
    )


def test_current_runtime_identity_captures_execution_contract(tmp_path):
    agent = build_agent(tmp_path, max_steps=9, max_new_tokens=1024, read_only=True)

    identity = current_runtime_identity(agent)

    assert identity["session_id"] == agent.session["id"]
    assert identity["cwd"] == str(tmp_path)
    assert identity["read_only"] is True
    assert identity["max_steps"] == 9
    assert identity["max_new_tokens"] == 1024
    assert identity["workspace_fingerprint"] == agent.workspace.fingerprint()
    assert identity["tool_signature"] == agent.tool_signature()


def test_evaluate_resume_state_distinguishes_no_checkpoint_full_valid_and_schema_mismatch(tmp_path):
    agent = build_agent(tmp_path)

    assert evaluate_resume_state(agent)["status"] == CHECKPOINT_NONE_STATUS

    identity = current_runtime_identity(agent)
    agent.session["checkpoints"] = {
        "current_id": "ckpt_valid",
        "items": {
            "ckpt_valid": {
                "checkpoint_id": "ckpt_valid",
                "schema_version": CHECKPOINT_SCHEMA_VERSION,
                "key_files": [],
                "runtime_identity": identity,
            }
        },
    }
    assert evaluate_resume_state(agent)["status"] == CHECKPOINT_FULL_VALID_STATUS

    agent.session["checkpoints"]["items"]["ckpt_valid"]["schema_version"] = "old"
    assert evaluate_resume_state(agent)["status"] == CHECKPOINT_SCHEMA_MISMATCH_STATUS


def test_infer_next_step_uses_plan_next_pending_when_available(tmp_path):
    plan = PlanManager(
        {
            "request": "Fix tests",
            "title": "Fix tests",
            "steps": [
                {"id": 1, "text": "Reproduce", "status": "done", "note": ""},
                {"id": 2, "text": "Patch", "status": "pending", "note": ""},
            ],
            "updated_at": "",
        }
    )
    task_state = TaskState.create(task_id="task_1", user_request="Fix tests")

    assert infer_next_step(task_state, plan=plan) == "Step 2: Patch"

    task_state.finish_success("done")
    assert infer_next_step(task_state, plan=plan) == "No next step recorded."


def test_infer_next_step_without_plan_keeps_existing_behavior(tmp_path):
    task_state = TaskState.create(task_id="task_1", user_request="Inspect")
    task_state.last_tool = "read_file"

    assert infer_next_step(task_state) == "Decide the next action after read_file."


def test_render_checkpoint_text_reports_plan_progress(tmp_path):
    agent = build_agent(tmp_path)
    agent.plan.reset(request="Fix tests")
    agent.plan.apply({"action": "init", "title": "Fix tests", "plan": "1. Reproduce\n2. Patch"})
    agent.plan.apply({"action": "update", "step_id": 1, "status": "done"})
    task_state = TaskState.create(task_id="task_1", user_request="Fix tests")
    agent.run_store.start_run(task_state)
    agent.create_checkpoint(task_state, "Fix tests", trigger="test")

    text = render_checkpoint_text(agent)

    assert "Plan progress: 1/2 steps done" in text
