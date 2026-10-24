// "Now": a short, honest list of what the agent is doing in the current turn.
// Built only from events the page already receives: voice-tool calls
// (clawvoice.tool started/result), job events, and each job's tools_seen.
// It shows steps that happened, never predicted ones, and clears per request
// in single-agent mode while preserving concurrent agent activity in Team mode.

(function (root) {
  const MAX = 7;
  const ACTIVE = new Set(["accepted", "queued", "running", "verifying"]);
  const TOPICS = {
    "projects.active": "the active project",
    "projects.recent": "recent projects",
    "project.latest_update": "the latest update",
    "jobs.recent": "recent tasks",
    "planner.today": "today's planner",
    "planner.tomorrow": "tomorrow's planner",
    "calendar.today": "today's calendar",
    "calendar.next": "your next event",
    "checklist.focus": "your checklist",
  };
  const topic = (key) => TOPICS[key] || key || "context";
  const esc = (text) =>
    String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const prefix = (who, text) => (who ? `${who}: ${text}` : text);

  // Starting text for a voice-tool call.
  function describeCall(name, args = {}) {
    if (name === "get_context") return { icon: "lookup", text: `Looking up ${topic(args.topic)}` };
    if (name === "delegate_task") return { icon: "handoff", text: `Handing off: ${args.title || args.request || "a task"}` };
    if (name === "check_tasks") return { icon: "lookup", text: "Checking your tasks" };
    if (name === "cancel_task") return { icon: "cancel", text: "Cancelling a task" };
    return { icon: "lookup", text: `Using ${name}` };
  }

  // Finished text for a voice-tool call, from the result metadata.
  function describeResult(name, data, args = {}) {
    const failed = data.status === "error";
    if (name === "get_context") {
      const bits = [data.cache, data.read_ms != null ? `${Math.round(data.read_ms)} ms` : null].filter(Boolean);
      return { icon: failed ? "error" : "lookup", text: `${failed ? "Couldn't check" : "Checked"} ${topic(data.topic || args.topic)}${bits.length ? ` · ${bits.join(" · ")}` : ""}` };
    }
    if (name === "delegate_task") {
      return { icon: failed ? "error" : "handoff", text: failed ? "Couldn't hand off the task" : `Handed off to the agent${data.job_id ? ` · ${data.job_id}` : ""}` };
    }
    if (name === "check_tasks") return { icon: "lookup", text: "Checked your tasks" };
    if (name === "cancel_task") return { icon: "cancel", text: failed ? "Couldn't cancel the task" : "Asked to cancel the task" };
    return { icon: failed ? "error" : "lookup", text: `${failed ? "Failed" : "Used"} ${name}` };
  }

  const ICONS = {
    heard: '<path d="M8 13a4 4 0 1 1 8 0c0 3-3 3.5-3 6a2 2 0 0 1-4 0M12 9a2 2 0 0 0-2 2"/>',
    think: '<path d="M12 3v3M12 18v3M3 12h3M18 12h3M6 6l2 2M16 16l2 2M6 18l2-2M16 8l2-2"/>',
    speak: '<path d="M4 10v4M8 7v10M12 4v16M16 8v8M20 11v2"/>',
    lookup: '<ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v6c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6"/>',
    handoff: '<path d="M4 12h14M13 6l6 6-6 6"/>',
    tool: '<path d="M4 6l5 5-5 5M12 18h8"/>',
    done: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    cancel: '<path d="M6 6l12 12M18 6L6 18"/>',
    error: '<path d="M12 8v5M12 16.5v.5M10.3 3.9L2.6 17.2A2 2 0 0 0 4.3 20h15.4a2 2 0 0 0 1.7-2.8L13.7 3.9a2 2 0 0 0-3.4 0z"/>',
  };

  function mount(list) {
    let items = [];
    const callArgs = new Map();
    const toolsSeen = new Map(); // job key -> Set of labels already shown

    function render() {
      if (!items.length) {
        list.innerHTML = '<li class="activity-empty">Quiet for now.</li>';
        return;
      }
      list.innerHTML = items
        .map((item) => `<li class="activity-item${item.live ? " live" : ""}${item.mono ? " mono" : ""}">
          <svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[item.icon] || ICONS.lookup}</svg>
          <span>${esc(item.text)}</span></li>`)
        .join("");
    }

    function add(icon, text, { live = false, mono = false, key = null } = {}) {
      for (const item of items) item.live = false;
      const last = items[items.length - 1];
      if (last && last.text === text && !key) { last.live = live; render(); return; }
      items.push({ icon, text, live, mono, key });
      if (items.length > MAX) items = items.slice(-MAX);
      render();
    }

    function update(key, icon, text) {
      const item = items.find((i) => i.key === key);
      if (!item) return add(icon, text);
      Object.assign(item, { icon, text, live: false });
      render();
    }

    return {
      // A new request from the user starts a fresh list in single-agent mode,
      // or appends in Team mode so another agent's concurrent work stays visible.
      newTurn(text, { append = false } = {}) {
        if (!append) items = [];
        else items = items.filter((i) => i.icon !== "think" && i.icon !== "speak");
        add("heard", text);
      },
      thinking(who = "") { add("think", prefix(who, "Thinking"), { live: true }); },
      speaking(who = "") { add("speak", prefix(who, "Speaking"), { live: true }); },
      settle() { for (const item of items) item.live = false; render(); },
      tool(data, who = "") {
        const key = `call:${data.agent || ""}:${data.call_id || data.name}`;
        if (data.phase === "started") {
          callArgs.set(key, data.arguments || {});
          const { icon, text } = describeCall(data.name, data.arguments);
          add(icon, prefix(who, text), { live: true, key });
        } else {
          const { icon, text } = describeResult(data.name, data, callArgs.get(key));
          callArgs.delete(key);
          update(key, icon, prefix(who, text));
        }
      },
      job(job, event, who = "") {
        if (!job) return;
        if (event === "job.accepted") add("handoff", prefix(who, `Started: ${job.title}`), { live: true });
        else if (event === "job.finished") {
          const ok = job.status === "completed";
          add(
            ok ? "done" : job.status === "cancelled" ? "cancel" : "error",
            prefix(who, `${ok ? "Finished" : job.status === "cancelled" ? "Cancelled" : "Didn't finish"}: ${job.title}`)
          );
        }
      },
      // New coding-agent tool labels on running jobs (e.g. "exec: ./bin/astra-project check …").
      jobTools(jobs, nameFor = null) {
        for (const job of jobs || []) {
          const jobKey = job.agent_id ? `${job.agent_id}:${job.job_id}` : job.job_id;
          if (!toolsSeen.has(jobKey)) toolsSeen.set(jobKey, new Set());
          const seen = toolsSeen.get(jobKey);
          // A job that already ended before we saw it adds nothing: those steps are history.
          const active = ACTIVE.has(job.status);
          const who = typeof nameFor === "function" ? nameFor(job) : "";
          for (const label of job.tools_seen || []) {
            if (seen.has(label)) continue;
            seen.add(label);
            if (active) add("tool", prefix(who, label), { live: true, mono: true });
          }
        }
      },
      reset() { items = []; callArgs.clear(); toolsSeen.clear(); render(); },
      items: () => items.map(({ icon, text, live, mono }) => ({ icon, text, live, mono })),
    };
  }

  const api = { mount, describeCall, describeResult, topic };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ClawActivity = api;
})(typeof window !== "undefined" ? window : globalThis);
