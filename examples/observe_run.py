"""Observe pico's agent loop one step at a time (no API key needed).

Runs a real Pico agent with a scripted FakeModelClient, then prints the
run artifacts (trace timeline, task state) that pico wrote to disk.

Usage (from anywhere):
    python examples/observe_run.py

To watch the trace grow live while it runs, open another terminal:
    tail -f <run_dir>/trace.jsonl
"""

import json
import sys
import tempfile
from pathlib import Path

# Make the `pico` package importable without installing it:
# Python adds only the script's directory to sys.path, not the repo root.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pico import FakeModelClient, Pico, SessionStore, WorkspaceContext


def main():
    # 1. Build a throwaway workspace with one small file.
    workspace_dir = Path(tempfile.mkdtemp())
    (workspace_dir / "hello.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    print("workspace:", workspace_dir)

    # 2. Script the model: first it calls read_file, then it answers.
    #    Edit this list to try other behaviors:
    #    - another tool call instead of the final answer -> two tool steps
    #    - "<final></final>" (empty) -> the retry branch
    #    - an invalid tool name -> the error path in run_tool()
    model = FakeModelClient(
        [
            '<tool>{"name":"read_file","args":{"path":"hello.txt","start":1,"end":2}}</tool>',
            "<final>Done.</final>",
        ]
    )

    # 3. Assemble the agent the same way build_agent() does in cli.py.
    agent = Pico(
        model_client=model,
        workspace=WorkspaceContext.build(workspace_dir),
        session_store=SessionStore(workspace_dir / ".pico" / "sessions"),
        approval_policy="auto",
    )

    # 4. Run one request. This is the whole control loop from agent_loop.py.
    final = agent.ask("Inspect hello.txt")
    print("\nFINAL ANSWER =>", final)

    # 5. Inspect the artifacts the loop wrote while running.
    run_dir = agent.run_store.run_dir(agent.current_task_state)
    print("\nrun_dir:", run_dir)
    print("artifacts:", sorted(path.name for path in run_dir.iterdir()))

    print("\ntrace timeline (trace.jsonl):")
    for line in (run_dir / "trace.jsonl").read_text().splitlines():
        event = json.loads(line)
        extra = {key: value for key, value in event.items() if key not in ("event", "created_at")}
        print(f"  {event['event']:22s} {json.dumps(extra, ensure_ascii=False)[:120]}")

    print("\ntask_state.json:")
    print(json.dumps(agent.current_task_state.to_dict(), indent=2, ensure_ascii=False))

    print("\nsession file:", agent.session_path)
    print("prompts sent to model:", len(model.prompts))

    print("\nTo watch the trace grow live, run in another terminal:")
    print(f"  tail -f {run_dir}/trace.jsonl")


if __name__ == "__main__":
    main()
