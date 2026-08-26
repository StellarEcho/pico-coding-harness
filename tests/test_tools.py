from pathlib import Path

from pico.tool_context import ToolContext
from pico.tools import (
    build_tool_registry,
    tool_delegate,
    tool_memory_read,
    tool_memory_update,
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


def test_build_tool_registry_includes_memory_tools(tmp_path):
    context = ToolContext(
        root=tmp_path,
        path_resolver=lambda raw_path: Path(tmp_path / raw_path),
        shell_env_provider=lambda: {"PWD": str(tmp_path)},
        depth=0,
        max_depth=1,
        spawn_delegate=lambda args: "unused",
    )

    tools = build_tool_registry(context)

    assert "memory_read" in tools
    assert "memory_update" in tools
    assert tools["memory_read"]["risky"] is False
    assert tools["memory_update"]["risky"] is False


def test_validate_memory_tools_accepts_and_rejects(tmp_path):
    context = ToolContext(
        root=tmp_path,
        path_resolver=lambda raw_path: Path(tmp_path / raw_path),
        shell_env_provider=lambda: {"PWD": str(tmp_path)},
        depth=0,
        max_depth=1,
        spawn_delegate=lambda args: "unused",
    )

    validate_tool(context, "memory_read", {"query": "deploy conventions", "limit": 3})
    validate_tool(
        context,
        "memory_update",
        {"action": "add", "topic": "project-conventions", "note": "Run focused tests first."},
    )
    validate_tool(
        context,
        "memory_update",
        {"action": "delete", "topic": "project-conventions", "note": "Run focused tests first."},
    )

    cases = [
        {"query": "", "limit": 3},
        {"query": "x", "limit": 0},
        {"query": "x", "limit": 11},
        {"action": "drop", "topic": "project-conventions", "note": "x"},
        {"action": "add", "topic": "unknown-topic", "note": "x"},
        {"action": "add", "topic": "project-conventions", "note": ""},
    ]
    for args in cases:
        try:
            validate_tool(context, "memory_read" if "query" in args else "memory_update", args)
        except ValueError:
            continue
        raise AssertionError(f"validate_tool accepted invalid memory tool args: {args}")


def test_tool_memory_read_and_update_call_context_callbacks(tmp_path):
    calls = []

    def memory_query(args):
        calls.append(("query", args))
        return "Relevant memory:\n- note"

    def memory_update(args):
        calls.append(("update", args))
        return "added durable note to project-conventions"

    context = ToolContext(
        root=tmp_path,
        path_resolver=lambda raw_path: Path(tmp_path / raw_path),
        shell_env_provider=lambda: {"PWD": str(tmp_path)},
        depth=0,
        max_depth=1,
        spawn_delegate=lambda args: "unused",
        memory_query=memory_query,
        memory_update=memory_update,
    )

    assert tool_memory_read(context, {"query": "conventions", "limit": 2}) == "Relevant memory:\n- note"
    assert tool_memory_update(context, {"action": "add", "topic": "project-conventions", "note": "x"}) == "added durable note to project-conventions"
    assert calls == [
        ("query", {"query": "conventions", "limit": 2}),
        ("update", {"action": "add", "topic": "project-conventions", "note": "x"}),
    ]


def test_tool_memory_read_raises_without_runtime_callback(tmp_path):
    context = ToolContext(
        root=tmp_path,
        path_resolver=lambda raw_path: Path(tmp_path / raw_path),
        shell_env_provider=lambda: {"PWD": str(tmp_path)},
        depth=0,
        max_depth=1,
        spawn_delegate=lambda args: "unused",
    )

    try:
        tool_memory_read(context, {"query": "x"})
    except ValueError as exc:
        assert "memory feature is disabled" in str(exc)
    else:
        raise AssertionError("tool_memory_read should fail without a runtime callback")
