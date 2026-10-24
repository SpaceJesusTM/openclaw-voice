// Live preview pane for coding agents (Astra).
// Slides in while the agent builds and shows its local preview server
// (127.0.0.1:18950). Frontend only: it reads the job and topic state the page
// already has and probes the preview port directly from the browser.

(function (root) {
  const PREVIEW_ORIGIN = "http://127.0.0.1:18950";
  const ACTIVE = new Set(["accepted", "queued", "running", "verifying"]);
  const PROBE_MS = 2500; // while building or live
  const IDLE_PROBE_MS = 10000; // otherwise; each miss logs a network error in the console
  const MISSES_BEFORE_DOWN = 2;

  const isCodingAgent = (agent) => (agent?.topics || []).some((t) => t.key === "projects.active");

  // What the pane should show for the current inputs.
  function decide({ coding, building, live, dismissed }) {
    if (!coding) return { phase: "hidden", open: false };
    const phase = building ? (live ? "updating" : "building") : live ? "live" : "hidden";
    return { phase, open: phase !== "hidden" && !dismissed };
  }

  // "Latest update on Moonlight Coffee (projects/coffee-shop): ..." -> "Moonlight Coffee"
  function projectName(summary) {
    const match = /^Latest update on (.+?) \(/.exec(String(summary || ""));
    return match ? match[1] : "";
  }

  function mount(doc, { fetchImpl = root.fetch && root.fetch.bind(root), now = () => Date.now(),
    setTimer = setTimeout, clearTimer = clearTimeout, everyMs = setInterval, stopEvery = clearInterval } = {}) {
    const $ = (id) => doc.getElementById(id);
    const s = {
      coding: false, agentName: "Agent", building: false, buildTitle: "", live: false, misses: 0,
      dismissed: false, activeIds: new Set(), latest: undefined, project: "", loaded: false,
      lastProbe: 0, probeTimer: null, reloadTimer: null, toastTimer: null, view: decide({}),
    };

    function sync({ agent, jobs = [], topics = [] }) {
      const coding = isCodingAgent(agent);
      if (coding !== s.coding || (coding && agent.display_name !== s.agentName)) reset(coding);
      s.agentName = agent?.display_name || "Agent";
      if (!coding) return render();

      const active = jobs.filter((j) => ACTIVE.has(j.status));
      const activeIds = new Set(active.map((j) => j.job_id));
      if ([...activeIds].some((id) => !s.activeIds.has(id))) s.dismissed = false; // a new build reopens the pane
      const finished = [...s.activeIds].some((id) => !activeIds.has(id));
      s.activeIds = activeIds;
      s.building = active.length > 0;
      s.buildTitle = active[0]?.title || "";

      const latest = topics.find((t) => t.topic === "project.latest_update")?.summary;
      const changed = latest != null && s.latest !== undefined && latest !== s.latest;
      if (latest != null) {
        s.latest = latest;
        s.project = projectName(latest) || s.project;
      }
      if ((finished || changed) && s.live) reload("Updated");
      render();
    }

    function reset(coding) {
      stopEvery(s.probeTimer);
      Object.assign(s, { coding, building: false, buildTitle: "", live: false, misses: 0, dismissed: false,
        activeIds: new Set(), latest: undefined, project: "", loaded: false, probeTimer: null });
      for (const frame of [...($("preview-stage")?.querySelectorAll(".preview-frame") || [])]) frame.remove();
      if (coding) {
        probe();
        s.probeTimer = everyMs(() => {
          if (s.building || s.live || now() - s.lastProbe >= IDLE_PROBE_MS) probe();
        }, PROBE_MS);
      }
    }

    async function probe() {
      if (!s.coding || doc.hidden || !fetchImpl) return;
      s.lastProbe = now();
      try {
        // Opaque (no-cors) response: we only learn that the server answered.
        await fetchImpl(`${PREVIEW_ORIGIN}/__astra_status__?t=${now()}`, { mode: "no-cors", cache: "no-store" });
        s.misses = 0;
        setLive(true);
      } catch {
        s.misses += 1;
        if (s.misses >= MISSES_BEFORE_DOWN) setLive(false);
      }
    }

    function setLive(live) {
      if (live === s.live) return;
      s.live = live;
      if (live) reload(s.loaded ? "Updated" : null);
      render();
    }

    // Double-buffered reload: the new frame loads behind the visible one and fades in.
    function reload(toast) {
      clearTimer(s.reloadTimer);
      s.reloadTimer = setTimer(() => {
        const stage = $("preview-stage");
        if (!stage || !s.coding) return;
        for (const pending of [...stage.querySelectorAll(".preview-frame.pending")]) pending.remove();
        const frame = doc.createElement("iframe");
        frame.className = "preview-frame pending";
        frame.title = `${s.agentName} live preview`;
        frame.setAttribute("sandbox", "allow-scripts allow-same-origin allow-forms allow-popups");
        frame.addEventListener("load", () => {
          // The old site stays fully visible underneath while the new one fades in on top.
          const old = [...stage.querySelectorAll(".preview-frame.shown")];
          frame.classList.remove("pending");
          setTimer(() => {
            frame.classList.add("shown");
            s.loaded = true;
            render();
            if (toast) showToast(toast);
            setTimer(() => old.forEach((f) => f.remove()), 600);
          }, 80); // let the new page paint before it starts to appear
        }, { once: true });
        frame.src = `${PREVIEW_ORIGIN}/?v=${now()}`;
        stage.append(frame);
      }, 300);
    }

    function showToast(text) {
      const toast = $("preview-toast");
      if (!toast) return;
      toast.textContent = text;
      toast.classList.add("show");
      clearTimer(s.toastTimer);
      s.toastTimer = setTimer(() => toast.classList.remove("show"), 2200);
    }

    function render() {
      s.view = decide(s);
      const { phase, open } = s.view;
      const panel = $("preview");
      if (!panel) return;
      panel.dataset.phase = phase;
      panel.dataset.loaded = String(s.loaded);
      panel.classList.toggle("open", open);
      panel.setAttribute("aria-hidden", String(!open));
      panel.inert = !open;
      $("layout")?.classList.toggle("with-preview", open);
      const reopen = $("preview-reopen");
      if (reopen) reopen.hidden = open || phase === "hidden";

      const label = phase === "building" ? "Building" : phase === "updating" ? "Applying changes" : "Live";
      $("preview-status-text").textContent = label;
      $("preview-title").textContent = s.project || `${s.agentName}'s preview`;
      const caption = $("preview-caption");
      caption.textContent = s.buildTitle ? `${s.agentName} is building · ${s.buildTitle}` : `${s.agentName} is building…`;
    }

    $("preview-close")?.addEventListener("click", () => { s.dismissed = true; render(); });
    $("preview-reopen")?.addEventListener("click", () => { s.dismissed = false; render(); });
    $("preview-reload")?.addEventListener("click", () => { if (s.live) reload("Reloaded"); else probe(); });
    doc.addEventListener?.("visibilitychange", () => { if (!doc.hidden) probe(); });

    return { sync, probe, state: () => ({ ...s.view, live: s.live, loaded: s.loaded, dismissed: s.dismissed }) };
  }

  const api = { mount, decide, projectName, isCodingAgent, PREVIEW_ORIGIN };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ClawPreview = api;
})(typeof window !== "undefined" ? window : globalThis);
