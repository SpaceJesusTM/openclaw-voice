// Voice stage: the orb and its radial waveform.
// Reads the state app.js already writes on #orb (state-idle/listening/speaking/
// offline, plus working, waiting and thinking) and draws the live spectrum of
// whoever is talking: your microphone in ink, the agent's voice in clay.

(function (root) {
  const BARS = 64;
  const SPECTRUM = 0.45; // lowest share of the FFT bins; speech lives there

  // Per-bar target heights (0..1) for one frame. Pure, so it can be tested.
  function targets({ state, bins, time, reduced }) {
    const out = new Array(BARS).fill(0);
    if ((state === "speaking" || state === "listening") && bins && bins.length) {
      const usable = Math.max(1, Math.floor(bins.length * SPECTRUM));
      for (let i = 0; i < BARS; i++) {
        // Mirror the spectrum so the ring is symmetric left/right.
        const half = i < BARS / 2 ? i : BARS - 1 - i;
        const bin = Math.min(usable - 1, Math.floor((half / (BARS / 2)) * usable));
        out[i] = Math.min(1, (bins[bin] / 255) * 1.25);
      }
    } else if (state === "thinking" && !reduced) {
      for (let i = 0; i < BARS; i++) out[i] = 0.08 + 0.1 * (0.5 + 0.5 * Math.sin(i * 0.4 - time * 3));
    } else {
      out.fill(state === "offline" ? 0.02 : 0.05);
    }
    return out;
  }

  function stateOf(orb) {
    const has = (c) => orb.classList.contains(c);
    const base = ["speaking", "listening", "offline"].find((s) => has(`state-${s}`)) || "idle";
    return {
      state: base === "idle" && has("thinking") ? "thinking" : base,
      working: has("working"),
      waiting: has("waiting"),
    };
  }

  function mount(orb) {
    const canvas = orb.querySelector("canvas");
    const g = canvas.getContext("2d");
    const levels = new Array(BARS).fill(0);
    const motion = root.matchMedia ? root.matchMedia("(prefers-reduced-motion: reduce)") : { matches: false };
    let analysers = { local: null, remote: null };
    let bins = null;
    let colors = null;
    let colorsAt = 0;
    let size = 0;
    let frame = 0;

    function readColors(now) {
      if (colors && now - colorsAt < 1000) return colors;
      const css = getComputedStyle(orb);
      const read = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
      colors = {
        ink: read("--ink", "#1f1e1d"),
        clay: read("--clay", "#d97757"),
        clayWash: read("--clay-wash", "rgba(217,119,87,.14)"),
        surface: read("--surface", "#ffffff"),
        line: read("--line-strong", "rgba(0,0,0,.14)"),
      };
      colorsAt = now;
      return colors;
    }

    function fit() {
      const dpr = root.devicePixelRatio || 1;
      const css = canvas.clientWidth;
      if (!css) return false;
      const px = Math.round(css * dpr);
      if (px !== size) {
        size = px;
        canvas.width = canvas.height = px;
      }
      return true;
    }

    function draw(now) {
      frame = root.requestAnimationFrame(draw);
      if (document.hidden || !fit()) return;
      const { state, working, waiting } = stateOf(orb);
      const reduced = motion.matches;
      const c = readColors(now);
      const analyser = state === "speaking" ? analysers.remote : state === "listening" ? analysers.local : null;
      if (analyser) {
        if (!bins || bins.length !== analyser.frequencyBinCount) bins = new Uint8Array(analyser.frequencyBinCount);
        analyser.getByteFrequencyData(bins);
      }
      const t = now / 1000;
      const goal = targets({ state, bins: analyser ? bins : null, time: t, reduced });
      for (let i = 0; i < BARS; i++) levels[i] += (goal[i] - levels[i]) * (goal[i] > levels[i] ? 0.45 : 0.18);

      const s = size;
      const cx = s / 2;
      const unit = s / 260; // drawing is laid out on a 260-unit square
      const breathe = state === "idle" && !reduced ? Math.sin(t * 1.3) * 2 : 0;
      const R = (78 + breathe) * unit;
      g.clearRect(0, 0, s, s);

      g.beginPath();
      g.arc(cx, cx, R, 0, Math.PI * 2);
      g.fillStyle = state === "speaking" ? c.clayWash : c.surface;
      g.fill();
      g.lineWidth = 1.5 * unit;
      g.strokeStyle = state === "speaking" ? c.clay : c.line;
      g.stroke();

      const bar = state === "speaking" ? c.clay : state === "listening" ? c.ink : c.line;
      g.lineCap = "round";
      g.lineWidth = 3.4 * unit;
      g.strokeStyle = bar;
      for (let i = 0; i < BARS; i++) {
        const a = (i / BARS) * Math.PI * 2 - Math.PI / 2;
        const r0 = R + 10 * unit;
        const r1 = r0 + (3 + levels[i] * 34) * unit;
        g.beginPath();
        g.moveTo(cx + Math.cos(a) * r0, cx + Math.sin(a) * r0);
        g.lineTo(cx + Math.cos(a) * r1, cx + Math.sin(a) * r1);
        g.stroke();
      }

      if (working) {
        const start = reduced ? -Math.PI / 2 : t * 1.6;
        g.beginPath();
        g.arc(cx, cx, R - 9 * unit, start, start + 1.2);
        g.strokeStyle = c.clay;
        g.lineWidth = 2.5 * unit;
        g.stroke();
      }
      if (waiting) {
        g.beginPath();
        g.arc(cx + R * 0.72, cx - R * 0.72, 6 * unit, 0, Math.PI * 2);
        g.fillStyle = c.clay;
        g.fill();
      }
    }

    frame = root.requestAnimationFrame(draw);
    return {
      setAnalysers(next) { analysers = { ...analysers, ...next }; },
      stop() { root.cancelAnimationFrame(frame); },
    };
  }

  const api = { mount, targets, stateOf, BARS };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ClawStage = api;
})(typeof window !== "undefined" ? window : globalThis);
