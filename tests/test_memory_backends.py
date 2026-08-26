from pico import FakeModelClient, Pico, SessionStore, WorkspaceContext
from pico.features.memory import LayeredMemory
from pico.features.memory_backends import (
    MemoryBackend,
    available_memory_backends,
    create_memory_backend,
)


def test_layered_memory_conforms_to_memory_backend_protocol():
    memory = LayeredMemory()

    assert isinstance(memory, MemoryBackend)
    assert memory.backend_id == "keyword"


def test_memory_backend_store_and_retrieve_round_trip():
    memory = LayeredMemory()

    stored = memory.store({"text": "deploy uses gitee mirror", "tags": ["deploy"], "source": "test"})
    candidates = memory.retrieve("gitee mirror", limit=3)

    assert stored["text"] == "deploy uses gitee mirror"
    assert stored["kind"] == "episodic"
    assert [note["text"] for note in candidates] == ["deploy uses gitee mirror"]


def test_memory_backend_store_accepts_plain_string():
    memory = LayeredMemory()

    stored = memory.store("remember the api prefix")

    assert stored["text"] == "remember the api prefix"
    assert memory.retrieve("api prefix")[0]["text"] == "remember the api prefix"


def test_memory_backend_delete_removes_exact_note():
    memory = LayeredMemory()
    memory.store("stale note")
    memory.store("keep note")

    assert memory.delete("stale note") == 1
    assert memory.delete("stale note") == 0
    assert [note["text"] for note in memory.retrieve("note")] == ["keep note"]


def test_memory_backend_snapshot_and_durable_flags():
    memory = LayeredMemory()
    memory.store("snapshot me")

    snapshot = memory.snapshot()

    assert snapshot["notes"] == ["snapshot me"]
    assert snapshot["working"]["task_summary"] == ""
    assert memory.durable is False

    rooted = LayeredMemory(workspace_root="/tmp")
    assert rooted.durable is True


def test_memory_backend_render_text_matches_existing_view():
    memory = LayeredMemory()
    memory.store("rendered note")

    assert memory.render_text() == memory.render_memory_text()
    assert "rendered note" not in memory.render_text()


def test_memory_backend_registry_creates_keyword_and_rejects_unknown():
    assert available_memory_backends() == ["keyword"]

    backend = create_memory_backend("KEYWORD", workspace_root=None, state=None)
    assert isinstance(backend, LayeredMemory)

    try:
        create_memory_backend("vector")
    except ValueError as exc:
        assert "unknown memory backend" in str(exc)
    else:
        raise AssertionError("unknown memory backend should raise")


def test_pico_accepts_memory_backend_parameter(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    workspace = WorkspaceContext.build(tmp_path)
    store = SessionStore(tmp_path / ".pico" / "sessions")

    agent = Pico(
        model_client=FakeModelClient(["<final>Done.</final>"]),
        workspace=workspace,
        session_store=store,
        approval_policy="auto",
        memory_backend="keyword",
    )

    assert agent.memory_backend == "keyword"
    assert isinstance(agent.memory, LayeredMemory)
