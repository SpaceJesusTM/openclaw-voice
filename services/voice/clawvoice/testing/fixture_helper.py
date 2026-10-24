"""Deterministic fixture helper commands (no Docker, Google or model calls).

Two fixture families, both reading/writing JSON in ``CLAWVOICE_FIXTURE_DIR``
(default ``services/voice/.local/fixtures``):

``project`` (the fixture coding agent, "Astra")::

    python -m clawvoice.testing.fixture_helper project status --json
    python -m clawvoice.testing.fixture_helper project tests --json [--run-id ID]
    python -m clawvoice.testing.fixture_helper project record-test-run ID passed 42 42

``planner`` / ``google`` emulate the *output contracts* of the demo agent's
``day-planner show|checklist --json`` and ``tari-google calendar get`` so the
real day-planner plugin can be tested offline. They are fixtures, never a
replacement for the real helpers at runtime.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DIR = Path(__file__).resolve().parents[2] / ".local" / "fixtures"


def fixture_dir() -> Path:
    path = Path(os.environ.get("CLAWVOICE_FIXTURE_DIR") or DEFAULT_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _load(name: str, default):
    path = fixture_dir() / f"{name}.json"
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _save(name: str, data) -> None:
    path = fixture_dir() / f"{name}.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=1), encoding="utf-8")
    temporary.replace(path)


DEFAULT_PROJECT = {
    "name": "clawvoice-demo",
    "branch": "feat/voice",
    "open_tasks": ["Polish the agent orb", "Write the demo script"],
    "test_runs": [],
}


def project(argv: list[str]) -> int:
    state = _load("project", DEFAULT_PROJECT)
    command = argv[0] if argv else "status"
    if command == "status":
        runs = state.get("test_runs", [])
        last = runs[-1] if runs else None
        summary = (
            f"Project {state['name']} on {state['branch']}: {len(state['open_tasks'])} open tasks. "
            + (f"Last test run: {last['status']} ({last['passed']}/{last['total']})." if last else "No test runs yet.")
        )
        print(json.dumps({**{k: state[k] for k in ("name", "branch", "open_tasks")}, "summary": summary}))
        return 0
    if command == "tests":
        runs = state.get("test_runs", [])
        if "--run-id" in argv:
            run_id = argv[argv.index("--run-id") + 1]
            run = next((r for r in runs if r["run_id"] == run_id), None)
            print(json.dumps({"found": run is not None, **(run or {}),
                              "summary": f"Test run {run['status']}: {run['passed']} of {run['total']} passed." if run else "No such run."}))
            return 0
        last = runs[-1] if runs else None
        print(json.dumps({"found": last is not None, **(last or {}),
                          "summary": f"Latest tests {last['status']}: {last['passed']} of {last['total']} passed." if last else "No test runs yet."}))
        return 0
    if command == "record-test-run":
        run_id, status, passed, total = argv[1], argv[2], int(argv[3]), int(argv[4])
        state.setdefault("test_runs", []).append(
            {"run_id": run_id, "status": status, "passed": passed, "total": total,
             "at": datetime.now(timezone.utc).isoformat()}
        )
        _save("project", state)
        print(json.dumps({"recorded": run_id}))
        return 0
    print(json.dumps({"error": f"unknown project command {command}"}), file=sys.stderr)
    return 2


# ------------------------------------------------------------ planner contract
def _planner_state() -> dict:
    return _load("planner", {"time_zone": "America/Toronto", "days": {}, "checklist": [], "calendar_complete": True})


def planner(argv: list[str]) -> int:
    state = _planner_state()
    if not argv:
        return 2
    if argv[0] == "show" and "--json" in argv:
        day = argv[1]
        items = state["days"].get(day, [])
        calendar_ok = state.get("calendar_complete", True)
        if not calendar_ok:
            items = [item for item in items if item.get("kind") != "calendar"]
        warnings = [] if calendar_ok else ["Calendar could not be read."]
        details = [] if calendar_ok else [{"code": "CALENDAR_UNAVAILABLE", "source": "calendar",
                                           "message": "Calendar could not be read; schedule coverage is incomplete."}]
        status = "complete" if calendar_ok else "incomplete"
        print(json.dumps({
            "date": day, "time_zone": state["time_zone"], "schedule": items, "scope": "calendar-and-local",
            "calendar_complete": calendar_ok, "warnings": warnings, "warning_details": details,
            "data_status": status, "complete": status == "complete", "checklist": state["checklist"],
        }))
        return 0
    if argv[0] == "checklist" and "--json" in argv:
        show_all = "--all" in argv
        print(json.dumps([
            {"section": s["section"], "tasks": [t for t in s["tasks"] if show_all or not t["done"]]}
            for s in state["checklist"]
        ]))
        return 0
    if argv[0] == "fail":
        return 3
    return 2


def google(argv: list[str]) -> int:
    state = _load("calendar", {"events": {}})
    if argv[:2] == ["calendar", "get"]:
        calendar_id, event_id = argv[2], argv[3]
        event = state["events"].get(f"{calendar_id}/{event_id}")
        if event is None:
            if "--allow-absent" in argv:
                print(json.dumps({"event": {"absent": True}, "untrusted_content": True}))
                return 0
            print(json.dumps({"error": "Google request failed", "verified": False}), file=sys.stderr)
            return 1
        print(json.dumps({"event": {**event, "calendar_id": calendar_id}, "untrusted_content": True}))
        return 0
    return 2


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    family = argv.pop(0) if argv else ""
    handlers = {"project": project, "planner": planner, "google": google}
    if family not in handlers:
        print(json.dumps({"error": "unknown fixture family"}), file=sys.stderr)
        return 2
    return handlers[family](argv)


if __name__ == "__main__":
    sys.exit(main())
