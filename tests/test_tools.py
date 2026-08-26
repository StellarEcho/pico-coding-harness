from pathlib import Path

from pico.tool_context import ToolContext
from pico.tools import (
    build_tool_registry,
    tool_delegate,
    tool_read_file,
    tool_update_plan,
    validate_tool,
)


def test_tool_context_supports_file_tools_without_full_pico(tmp_path):
    (tmp_path / "sample.txt").write_text("alpha\n", encoding="utf-8")
    context = ToolContext(
        root=tmp_path,
        path_resolver=lambda raw_path: (tmp_path / raw_path).resolve(),
        shell_env_provider=lambda: {"PWD": str(tmp_path)},
        depth=0,
        max_depth=1,
        spawn_delegate=lambda args: "unused",
    )

    result = tool_read_file(context, {"path": "sample.txt", "start": 1, "end": 1})

    assert "# sample.txt" in result
    assert "alpha" in result


def test_delegate_uses_context_spawn_without_runtime_import(tmp_path):
    calls = []
    context = ToolContext(
        root=tmp_path,
        path_resolver=lambda raw_path: Path(tmp_path / raw_path),
        shell_env_provider=lambda: {"PWD": str(tmp_path)},
        depth=0,
        max_depth=1,
        spawn_delegate=lambda args: calls.append(args) or "delegate_result:\nDone",
    )

    result = tool_delegate(context, {"task": "inspect README.md", "max_steps": 2})

    assert result == "delegate_result:\nDone"
    assert calls == [{"task": "inspect README.md", "max_steps": 2}]


def test_build_tool_registry_binds_runners_to_tool_context(tmp_path):
    context = ToolContext(
        root=tmp_path,
        path_resolver=lambda raw_path: Path(tmp_path / raw_path),
        shell_env_provider=lambda: {"PWD": str(tmp_path)},
        depth=1,
        max_depth=1,
        spawn_delegate=lambda args: "unused",
    )

    tools = build_tool_registry(context)

    assert "read_file" in tools
    assert "delegate" not in tools


def test_build_tool_registry_includes_update_plan(tmp_path):
    context = ToolContext(
        root=tmp_path,
        path_resolver=lambda raw_path: Path(tmp_path / raw_path),
        shell_env_provider=lambda: {"PWD": str(tmp_path)},
        depth=0,
        max_depth=1,
        spawn_delegate=lambda args: "unused",
    )

    tools = build_tool_registry(context)

    assert "update_plan" in tools
    assert tools["update_plan"]["risky"] is False


def test_validate_update_plan_accepts_init_and_update(tmp_path):
    context = ToolContext(
        root=tmp_path,
        path_resolver=lambda raw_path: Path(tmp_path / raw_path),
        shell_env_provider=lambda: {"PWD": str(tmp_path)},
        depth=0,
        max_depth=1,
        spawn_delegate=lambda args: "unused",
    )

    validate_tool(
        context,
        "update_plan",
        {"action": "init", "title": "Fix tests", "plan": "1. A\n2. B"},
    )
    validate_tool(context, "update_plan", {"action": "update", "step_id": 1, "status": "done"})


def test_validate_update_plan_rejects_invalid_actions(tmp_path):
    context = ToolContext(
        root=tmp_path,
        path_resolver=lambda raw_path: Path(tmp_path / raw_path),
        shell_env_provider=lambda: {"PWD": str(tmp_path)},
        depth=0,
        max_depth=1,
        spawn_delegate=lambda args: "unused",
    )

    cases = [
        {"action": "delete"},
        {"action": "init", "title": "", "plan": "1. A"},
        {"action": "init", "title": "T", "plan": ""},
        {"action": "update", "step_id": 0, "status": "done"},
        {"action": "update", "step_id": 1, "status": ""},
    ]
    for args in cases:
        try:
            validate_tool(context, "update_plan", args)
        except ValueError:
            continue
        raise AssertionError(f"validate_tool accepted invalid update_plan args: {args}")


def test_tool_update_plan_calls_context_callback_and_returns_rendered_plan(tmp_path):
    calls = []

    def plan_update(args):
        calls.append(args)
        return "Plan: T\n- [ ] 1. A"

    context = ToolContext(
        root=tmp_path,
        path_resolver=lambda raw_path: Path(tmp_path / raw_path),
        shell_env_provider=lambda: {"PWD": str(tmp_path)},
        depth=0,
        max_depth=1,
        spawn_delegate=lambda args: "unused",
        plan_update=plan_update,
    )

    result = tool_update_plan(context, {"action": "init", "title": "T", "plan": "1. A"})

    assert result == "Plan: T\n- [ ] 1. A"
    assert calls == [{"action": "init", "title": "T", "plan": "1. A"}]


def test_tool_update_plan_raises_without_runtime_callback(tmp_path):
    context = ToolContext(
        root=tmp_path,
        path_resolver=lambda raw_path: Path(tmp_path / raw_path),
        shell_env_provider=lambda: {"PWD": str(tmp_path)},
        depth=0,
        max_depth=1,
        spawn_delegate=lambda args: "unused",
    )

    try:
        tool_update_plan(context, {"action": "init", "title": "T", "plan": "1. A"})
    except ValueError as exc:
        assert "plan feature is disabled" in str(exc)
    else:
        raise AssertionError("tool_update_plan should fail without a runtime callback")
