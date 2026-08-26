import json

from pico import FakeModelClient, Pico, SessionStore, WorkspaceContext
from pico.features.skills import Skill, SkillRegistry, validate_skill


def _write_skill(root, name, payload):
    skills_dir = root / ".pico" / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    path = skills_dir / f"{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _valid_payload(**overrides):
    payload = {
        "skill_id": "pytest-runner",
        "version": "1.0.0",
        "description": "Run and interpret pytest failures.",
        "prompt_fragment": "Prefer running focused tests before broad suites.",
        "tools": ["run_shell", "read_file"],
        "memory_hooks": ["after_tool"],
        "enabled": True,
    }
    payload.update(overrides)
    return payload


def test_validate_skill_accepts_valid_manifest():
    skill = validate_skill(_valid_payload(), source="test")

    assert isinstance(skill, Skill)
    assert skill.skill_id == "pytest-runner"
    assert skill.version == "1.0.0"
    assert skill.tools == ("run_shell", "read_file")
    assert skill.memory_hooks == ("after_tool",)
    assert skill.enabled is True
    assert skill.source == "test"


def test_validate_skill_rejects_missing_fields_and_bad_id():
    cases = [
        _valid_payload(skill_id=""),
        _valid_payload(version=""),
        _valid_payload(description=""),
        _valid_payload(skill_id="Bad ID"),
    ]
    for payload in cases:
        try:
            validate_skill(payload, source="case")
        except ValueError:
            continue
        raise AssertionError(f"validate_skill accepted invalid payload: {payload}")


def test_validate_skill_rejects_unknown_tools_and_oversized_fragments():
    try:
        validate_skill(_valid_payload(tools=["run_shell", "delete_everything"]))
    except ValueError as exc:
        assert "unknown names" in str(exc)
    else:
        raise AssertionError("unknown skill tool should be rejected")

    try:
        validate_skill(_valid_payload(prompt_fragment="x" * 2001))
    except ValueError as exc:
        assert "prompt_fragment exceeds" in str(exc)
    else:
        raise AssertionError("oversized prompt fragment should be rejected")


def test_validate_skill_rejects_secret_shaped_fragment():
    try:
        validate_skill(_valid_payload(prompt_fragment="api key is sk-live-123456789"))
    except ValueError as exc:
        assert "secret-shaped" in str(exc)
    else:
        raise AssertionError("secret-shaped fragment should be rejected")


def test_skill_registry_loads_workspace_skills_and_collects_errors(tmp_path):
    _write_skill(tmp_path, "good", _valid_payload())
    _write_skill(tmp_path, "bad", _valid_payload(skill_id="bad skill", tools=["rm_workspace"]))

    registry = SkillRegistry.load(workspace_root=tmp_path)

    assert [skill.skill_id for skill in registry.all()] == ["pytest-runner"]
    assert len(registry.load_errors) == 1
    assert "invalid skill manifest" in registry.load_errors[0]


def test_skill_registry_filters_by_enabled_and_exposes_capabilities(tmp_path):
    _write_skill(
        tmp_path,
        "a",
        _valid_payload(skill_id="skill-a", prompt_fragment="fragment-a", tools=["read_file"], memory_hooks=["after_tool"]),
    )
    _write_skill(
        tmp_path,
        "b",
        _valid_payload(
            skill_id="skill-b",
            prompt_fragment="fragment-b",
            tools=["run_shell"],
            memory_hooks=["plan_updated"],
            enabled=False,
        ),
    )
    registry = SkillRegistry.load(workspace_root=tmp_path)

    assert [skill.skill_id for skill in registry.enabled()] == ["skill-a"]
    assert registry.prompt_fragments() == ["fragment-a"]
    assert registry.tool_names() == ["read_file"]
    assert registry.memory_hooks() == ["after_tool"]

    assert registry.set_enabled("skill-b", True) is True
    assert registry.set_enabled("missing", True) is False
    assert [skill.skill_id for skill in registry.enabled()] == ["skill-a", "skill-b"]
    assert registry.tool_names() == ["read_file", "run_shell"]


def test_skill_registry_render_and_summary(tmp_path):
    _write_skill(tmp_path, "good", _valid_payload())

    registry = SkillRegistry.load(workspace_root=tmp_path)
    text = registry.render()
    summary = registry.summary()

    assert "Skills: 1 loaded, 1 enabled" in text
    assert "pytest-runner v1.0.0 [enabled]" in text
    assert "tools: run_shell, read_file" in text
    assert summary == {
        "loaded": 1,
        "enabled": 1,
        "ids": ["pytest-runner"],
        "enabled_ids": ["pytest-runner"],
        "tool_names": ["read_file", "run_shell"],
        "load_errors": [],
    }


def test_pico_loads_workspace_skills_and_reports_them(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    _write_skill(tmp_path, "good", _valid_payload())
    workspace = WorkspaceContext.build(tmp_path)
    store = SessionStore(tmp_path / ".pico" / "sessions")
    agent = Pico(
        model_client=FakeModelClient(["<final>Done.</final>"]),
        workspace=workspace,
        session_store=store,
        approval_policy="auto",
    )

    assert agent.skills.get("pytest-runner") is not None
    assert "pytest-runner v1.0.0 [enabled]" in agent.skills_summary()

    assert agent.ask("Inspect") == "Done."
    report = agent.run_store.load_report(agent.current_task_state.run_id)
    assert report["skills"]["loaded"] == 1
    assert report["skills"]["ids"] == ["pytest-runner"]


def test_pico_merges_skill_tools_into_allowed_tools(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    _write_skill(tmp_path, "shell-user", _valid_payload(skill_id="shell-user", tools=["run_shell"]))
    workspace = WorkspaceContext.build(tmp_path)
    store = SessionStore(tmp_path / ".pico" / "sessions")
    agent = Pico(
        model_client=FakeModelClient(["<final>Done.</final>"]),
        workspace=workspace,
        session_store=store,
        approval_policy="auto",
        allowed_tools=["read_file"],
    )

    prompt = agent.prompt("Run a command")

    assert "- read_file(" in prompt
    assert "- run_shell(" in prompt
    result = agent.run_tool("run_shell", {"command": "echo hi", "timeout": 20})
    assert "exit_code: 0" in result

    assert agent.ask("Finish") == "Done."
    report = agent.run_store.load_report(agent.current_task_state.run_id)
    assert report["skills"]["tool_names"] == ["run_shell"]
