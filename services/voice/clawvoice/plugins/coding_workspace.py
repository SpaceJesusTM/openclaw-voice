"""Hot-context resolvers and change verifiers for coding-workspace agents.

Connects ClawVoice's generic cache and job-verification framework to a coding
workspace helper (`helpers.project`, e.g. `manage.py --agent astra project`):

- ``projects_active``: active project metadata + real on-disk state + live
  ClawVoice running jobs + live local preview server status. Never equates a
  recent project on disk with an actively running task.
- ``projects_recent``: recently worked-on projects with descriptions, modification
  timestamps, and latest meaningful updates.
- ``project_latest_update``: latest completed coding action, files changed,
  HTML title/heading, validation/build exit status, and live preview status.
- ``verify_coding_change``: independent read-back verification of reported project
  files, title/heading changes, validation check exit codes, and local preview.
"""

from __future__ import annotations

import re
from typing import Any

from clawvoice.agents import ReceiptType, TopicSpec
from clawvoice.context.builtin import bounded
from clawvoice.context.models import Resolved, ResolverContext, ResolverError
from clawvoice.jobs.models import Expectations, VerificationItem
from clawvoice.jobs.verifiers import ReceiptError, VerifyContext

_SAFE_TEXT = re.compile(r"^[^\x00-\x1f\x7f]{1,240}$")
_SAFE_SLUG = re.compile(r"^[A-Za-z0-9_./-]{1,120}$")


def _clean_text(value: Any, limit: int = 160) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _safe_opt(value: Any, field: str, allow_spaces: bool = False) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    pattern = _SAFE_TEXT if allow_spaces else _SAFE_SLUG
    if not pattern.fullmatch(text) or text.startswith("-") or ".." in text:
        raise ReceiptError(f"receipt field {field!r} contains unsafe characters")
    return text


def _running_jobs(ctx: ResolverContext) -> list[dict[str, Any]]:
    jobs = ctx.jobs() if ctx.jobs else []
    return [
        {
            "job_id": j.get("job_id"),
            "title": _clean_text(j.get("title"), 120),
            "status": j.get("status"),
            "age": j.get("age", "just now"),
        }
        for j in jobs
        if isinstance(j, dict) and j.get("status") in ("accepted", "queued", "running", "verifying")
    ]


# ------------------------------------------------------------------- Resolvers
async def projects_active(ctx: ResolverContext, spec: TopicSpec) -> Resolved:
    helper = str(spec.args.get("helper", "project"))
    result = await ctx.helpers.run_json(helper, ["status", "--json"])
    data = result.data
    if not isinstance(data, dict):
        raise ResolverError("The coding workspace helper returned an invalid status payload")

    active = data.get("active_project")
    preview = data.get("preview") if isinstance(data.get("preview"), dict) else {"running": False}
    running = _running_jobs(ctx)
    has_running_job = len(running) > 0

    if not isinstance(active, dict):
        if has_running_job:
            job_titles = "; ".join(f"{j['title']} ({j['status']})" for j in running)
            summary = f"Active coding job in progress: {job_titles}. No completed project directory is registered on disk yet."
            work_state = "active_job_running"
        else:
            summary = "No active coding project yet in the workspace, and no coding tasks are currently running."
            work_state = "idle"
        return Resolved(
            value=bounded(
                {
                    "active_project": None,
                    "work_state": work_state,
                    "active_job_running": has_running_job,
                    "running_jobs": running,
                    "dev_server_running": bool(preview.get("running")),
                    "preview_url": preview.get("url") if preview.get("running") else None,
                }
            ),
            summary=summary,
            complete=bool(data.get("complete", True)),
            source="astra-project status --json + ClawVoice job store",
            coverage=(
                "active project metadata, on-disk file count/title/heading, currently running "
                "ClawVoice coding jobs, and live local preview server status on port 18950 "
                "(does not include full file contents)"
            ),
        )

    name = _clean_text(active.get("name") or active.get("slug"), 80)
    slug = _clean_text(active.get("slug"), 60)
    path = _clean_text(active.get("path") or f"projects/{slug}", 80)
    desc = _clean_text(active.get("description"), 140)
    last_activity = _clean_text(active.get("last_activity_at"), 40)
    html_title = _clean_text(active.get("html_title"), 80)
    html_heading = _clean_text(active.get("html_heading"), 80)
    proj_preview = active.get("preview") if isinstance(active.get("preview"), dict) else {}
    preview_running = bool(proj_preview.get("running"))
    preview_url = proj_preview.get("url") if preview_running else None
    validation = active.get("validation") if isinstance(active.get("validation"), dict) else {}
    val_status = validation.get("status", "not_run")

    parts = [f"Current workspace project: {name} ({path})."]
    if desc and desc.lower() != name.lower():
        parts.append(f"Description: {desc}.")
    if html_title:
        title_bit = f"On-disk HTML title: '{html_title}'"
        if html_heading:
            title_bit += f", heading: '{html_heading}'"
        parts.append(title_bit + ".")

    if has_running_job:
        job_titles = "; ".join(f"{j['title']} ({j['status']})" for j in running)
        parts.append(f"Currently running coding task: {job_titles}.")
        work_state = "active_job_running"
        current_work = f"Running task: {job_titles}"
    else:
        parts.append("Task status: idle (no coding job is actively running right now).")
        work_state = "idle"
        current_work = "Idle (no background coding job running)"

    if val_status == "passed":
        parts.append("Latest validation check passed.")
    elif val_status == "failed":
        parts.append("Latest validation check failed.")

    if preview_running and preview_url:
        parts.append("Local preview server is live on localhost port 18950.")
    else:
        parts.append("Local preview server is not currently running.")

    value = {
        "name": name,
        "slug": slug,
        "description": desc,
        "path": path,
        "project_status": active.get("status", "ready"),
        "work_state": work_state,
        "active_job_running": has_running_job,
        "current_work": current_work,
        "running_jobs": running,
        "last_activity_at": last_activity,
        "file_count": active.get("file_count", 0),
        "files": (active.get("files") or [])[:15],
        "html_title": html_title,
        "html_heading": html_heading,
        "validation_status": val_status,
        "dev_server_running": preview_running,
        "preview_url": preview_url,
    }
    return Resolved(
        value=bounded(value),
        summary=" ".join(parts),
        complete=bool(data.get("complete", True)),
        source="astra-project status --json + ClawVoice job store",
        coverage=(
            "active project metadata, on-disk file list/title/heading, currently running "
            "ClawVoice coding jobs, and live local preview server status on port 18950 "
            "(does not include full source code)"
        ),
    )


async def projects_recent(ctx: ResolverContext, spec: TopicSpec) -> Resolved:
    helper = str(spec.args.get("helper", "project"))
    limit = max(1, min(10, int(spec.args.get("limit", 5))))
    result = await ctx.helpers.run_json(helper, ["recent", "--limit", str(limit), "--json"])
    data = result.data
    if not isinstance(data, dict) or not isinstance(data.get("projects"), list):
        raise ResolverError("The coding workspace helper returned an invalid recent-projects view")

    projects = data["projects"]
    if not projects:
        summary = "No coding projects have been created in the workspace yet."
    else:
        items_desc = []
        for proj in projects[:limit]:
            if not isinstance(proj, dict):
                continue
            pname = _clean_text(proj.get("name") or proj.get("slug"), 60)
            ppath = _clean_text(proj.get("path"), 60)
            upd = proj.get("latest_update") if isinstance(proj.get("latest_update"), dict) else {}
            upd_summary = _clean_text(upd.get("summary") or proj.get("description"), 100)
            items_desc.append(f"{pname} ({ppath}): {upd_summary}")
        summary = f"Recent projects ({len(projects)}): " + "; ".join(items_desc) + "."

    return Resolved(
        value=bounded(
            {
                "active_project_slug": data.get("active_project_slug"),
                "count": len(projects),
                "projects": projects[:limit],
            }
        ),
        summary=summary,
        complete=bool(data.get("complete", True)),
        source="astra-project recent --json",
        coverage=(
            f"up to {limit} recently modified projects in the workspace with descriptions, "
            "modification timestamps, and latest recorded update summaries"
        ),
    )


async def project_latest_update(ctx: ResolverContext, spec: TopicSpec) -> Resolved:
    helper = str(spec.args.get("helper", "project"))
    result = await ctx.helpers.run_json(helper, ["latest-update", "--json"])
    data = result.data
    if not isinstance(data, dict):
        raise ResolverError("The coding workspace helper returned an invalid latest-update view")

    if not data.get("has_update") or not data.get("project"):
        summary = "No completed coding updates or builds have been recorded in the workspace yet."
        return Resolved(
            value=bounded(data),
            summary=summary,
            complete=bool(data.get("complete", True)),
            source="astra-project latest-update --json",
            coverage="the most recent coding update, changed files, validation result, and live preview status",
        )

    name = _clean_text(data.get("name") or data.get("project"), 80)
    path = _clean_text(data.get("path"), 80)
    upd_summary = _clean_text(data.get("summary"), 160)
    files = data.get("files_changed") if isinstance(data.get("files_changed"), list) else []
    html_title = _clean_text(data.get("html_title"), 80)
    html_heading = _clean_text(data.get("html_heading"), 80)
    validation = data.get("validation") if isinstance(data.get("validation"), dict) else {}
    val_status = validation.get("status", "not_run")
    exit_code = validation.get("exit_code")
    preview = data.get("preview") if isinstance(data.get("preview"), dict) else {}

    sentences = [f"Latest update on {name} ({path}): {upd_summary}."]
    if files:
        sentences.append(f"Files changed: {', '.join(str(f) for f in files[:6])}.")
    if html_title:
        t_part = f"Current HTML title is '{html_title}'"
        if html_heading:
            t_part += f" and heading is '{html_heading}'"
        sentences.append(t_part + ".")
    if val_status == "passed":
        sentences.append(f"Validation/build passed (exit code {exit_code if exit_code is not None else 0}).")
    elif val_status == "failed":
        sentences.append(f"Validation/build failed (exit code {exit_code}).")
    else:
        sentences.append("No build or validation command has been recorded for this update.")
    if preview.get("running"):
        sentences.append("Local preview is live on localhost port 18950.")

    return Resolved(
        value=bounded(data),
        summary=" ".join(sentences),
        complete=bool(data.get("complete", True)),
        source="astra-project latest-update --json",
        coverage=(
            "the latest completed coding action on the active project, files changed, "
            "on-disk HTML title/heading, recorded validation/build exit status, and live preview status"
        ),
    )


# ------------------------------------------------------------------- Verifiers
async def verify_coding_change(
    vctx: VerifyContext, item: dict[str, Any], rtype: ReceiptType, expect: Expectations
) -> VerificationItem:
    helper = str(rtype.args.get("helper", "project"))
    action = _safe_opt(item.get("action") or "updated", "action") or "updated"
    project = _safe_opt(item.get("project") or "", "project")
    files_raw = item.get("files") or ""
    if isinstance(files_raw, list):
        files_str = ",".join(_safe_opt(f, "files") for f in files_raw if str(f).strip())
    else:
        files_str = ",".join(_safe_opt(f.strip(), "files") for f in str(files_raw).split(",") if f.strip())

    check_id = _safe_opt(item.get("check_id") or item.get("run_id") or "", "check_id")
    expect_title = _safe_opt(
        item.get("title") or item.get("expected_title") or "",
        "title",
        allow_spaces=True,
    )
    expect_heading = _safe_opt(
        item.get("heading") or item.get("expected_heading") or "",
        "heading",
        allow_spaces=True,
    )
    expect_text = _safe_opt(
        item.get("expected_text")
        or (expect.title if action == "updated" else "")
        or "",
        "expected_text",
        allow_spaces=True,
    )
    preview_url = _clean_text(item.get("preview_url") or "", 120)

    argv = ["verify", "--json", "--action", action]
    if project:
        argv += ["--project", project]
    if files_str:
        argv += ["--files", files_str]
    if check_id:
        argv += ["--check-id", check_id]
    if expect_title:
        argv += ["--expect-title", expect_title]
    if expect_heading:
        argv += ["--expect-heading", expect_heading]
    if expect_text:
        argv += ["--expect-text", expect_text]
    if preview_url:
        argv += ["--preview-url", preview_url]

    result = await vctx.helpers.run_json(helper, argv, fresh=True)
    data = result.data
    if not isinstance(data, dict):
        return VerificationItem(
            type=rtype.name,
            action=action,
            status="error",
            message="Workspace verifier returned an unreadable response",
        )

    if not data.get("verified"):
        reason = _clean_text(data.get("reason") or "Workspace verification failed", 200)
        return VerificationItem(
            type=rtype.name,
            action=action,
            status="mismatch",
            facts=bounded(data),
            mismatches=[reason],
            message=reason,
        )

    proj_name = _clean_text(data.get("name") or data.get("project"), 80)
    proj_path = _clean_text(data.get("path"), 80)
    verified_files = data.get("files") if isinstance(data.get("files"), list) else []
    html_title = _clean_text(data.get("html_title"), 80)
    html_heading = _clean_text(data.get("html_heading"), 80)
    preview_verified = bool(data.get("preview_verified"))

    msg_bits = [f"{proj_name} ({proj_path})"]
    if verified_files:
        msg_bits.append(f"files verified: {', '.join(str(f) for f in verified_files[:5])}")
    if html_title:
        msg_bits.append(f"title '{html_title}'")
    if html_heading:
        msg_bits.append(f"heading '{html_heading}'")
    if preview_verified:
        msg_bits.append("local preview verified on localhost port 18950")
    elif data.get("preview_note"):
        msg_bits.append(str(data["preview_note"]))

    facts = {
        "project": data.get("project"),
        "name": proj_name,
        "path": proj_path,
        "files": verified_files,
        "title": html_title or proj_name,
        "heading": html_heading,
        "check_id": data.get("check_id"),
        "preview_verified": preview_verified,
        "preview_url": data.get("preview_url"),
    }
    return VerificationItem(
        type=rtype.name,
        action=action,
        status="verified",
        facts=bounded(facts),
        mismatches=[],
        message="; ".join(msg_bits),
    )


def register(registry) -> None:
    registry.resolvers["projects_active"] = projects_active
    registry.resolvers["projects_recent"] = projects_recent
    registry.resolvers["project_latest_update"] = project_latest_update
    registry.verifiers["coding_change"] = verify_coding_change
