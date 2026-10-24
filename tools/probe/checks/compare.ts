import { mkdirSync, writeFileSync } from "node:fs";
import { MIND_TOOLS } from "../../../src/agent/mindTools";
import { SERVER, functionCallOutput, inputAudioCommit, responseCreate, textItem } from "../../../src/realtime/events";
import { buildSessionConfig } from "../../../src/realtime/sessionConfig";
import { msOf, streamAudio, synthesize, writeWav } from "../audio";
import type { CheckContext } from "../harness";
import type { ProbeSession } from "../session";
import { MIND_PROMPT, SCENARIOS } from "./mind";

/**
 * Q: What does higgs-realtime-1.5-preview buy us over higgs-realtime?
 *    The same work on each model, TRIALS times (COMPARE_TRIALS, default 3),
 *    each trial in a fresh session:
 *      voice  spoken questions -> first-audio latency, correctness, a WAV each
 *      mind   the interrupt / wait / speak scenarios -> right tool, latency
 *      search the server-side web_search tool -> accepted? used? answered?
 *    Writes a side-by-side table to captures/compare-<time>.md and the
 *    replies to captures/compare-<time>/<model>-<question>.wav.
 */
const MODELS = process.env.COMPARE_MODELS?.split(",").map((m) => m.trim()) ?? ["higgs-realtime", "higgs-realtime-1.5-preview"];
const TRIALS = Number(process.env.COMPARE_TRIALS ?? 3);
/** Sessions open at once. 1.5 failed its first ~10 s of trials at 3 (twice, same window); this tells load from warm-up. */
const PARALLEL = Number(process.env.COMPARE_PARALLEL ?? 3);

const QUESTIONS = [
  { name: "fact", text: "What is the capital of Australia? Answer in one sentence.", ok: /canberra/i },
  {
    name: "math",
    text: "My meeting starts at 3 pm and runs 90 minutes, then I have a 20 minute call right after. What time am I free?",
    ok: /4[:.]?\s?50|ten to five|four[- ]fifty/i,
  },
  { name: "spanish", text: "¿Cuál es la capital de España? Responde en una frase.", ok: /madrid/i, alsoOk: /\b(la|es|de)\b/i },
  { name: "open", text: "Give me two quick tips for staying focused when working from home.", ok: /./ },
];

interface VoiceResult {
  model: string;
  q: string;
  firstAudioMs: number | null;
  audioMs: number;
  transcript: string;
  correct: boolean;
  failed: boolean;
}
interface MindResult {
  model: string;
  sc: string;
  decisionMs: number | null;
  correct: boolean;
  failed: boolean;
  filler: boolean;
  tool: string;
}

export async function compareCheck(ctx: CheckContext): Promise<void> {
  const dir = `captures/compare-${ctx.stamp}`;
  mkdirSync(dir, { recursive: true });
  ctx.info(`${TRIALS} trial(s) per model; models: ${MODELS.join(" vs ")}`);

  // Voice the inputs once, so both models hear identical audio.
  const questionAudio = await Promise.all(QUESTIONS.map((q) => synthesize(ctx.apiKey, q.text)));
  const scenarioAudio = await Promise.all(SCENARIOS.map((s) => synthesize(ctx.apiKey, s.line)));

  const voice: VoiceResult[] = [];
  const mind: MindResult[] = [];
  const search: Record<string, string> = {};
  const saved = new Set<string>();

  for (const model of MODELS) {
    const short = model === "higgs-realtime" ? "v1" : "v1.5";
    const jobs: (() => Promise<void>)[] = [];
    for (let t = 0; t < TRIALS; t++) {
      QUESTIONS.forEach((q, i) =>
        jobs.push(async () => {
          const r = await voiceTrial(ctx, model, `${short}:${q.name}:${t}`, questionAudio[i]);
          const correct = q.ok.test(r.transcript) && (!q.alsoOk || q.alsoOk.test(r.transcript));
          voice.push({ model, q: q.name, ...r, correct });
          // The first reply that has audio, so a failed first trial still leaves something to hear.
          const wav = `${dir}/${short}-${q.name}.wav`;
          if (r.audio.length && !saved.has(wav)) {
            saved.add(wav);
            writeWav(wav, r.audio);
          }
        }),
      );
      SCENARIOS.forEach((sc, i) =>
        jobs.push(async () => {
          const r = await mindTrial(ctx, model, `${short}:mind-${sc.name}:${t}`, scenarioAudio[i]);
          mind.push({ model, sc: sc.name, ...r, correct: r.tool === sc.expect });
        }),
      );
    }
    jobs.push(async () => {
      search[model] = await searchTrial(ctx, model, `${short}:search`);
    });
    await pool(jobs, PARALLEL);
  }

  const report = renderReport(voice, mind, search);
  writeFileSync(`captures/compare-${ctx.stamp}.md`, report);
  console.log("\n" + report);
  ctx.info(`report: captures/compare-${ctx.stamp}.md; audio: ${dir}/`);
}

async function voiceTrial(ctx: CheckContext, model: string, name: string, audio: Int16Array) {
  let s: ProbeSession | null = null;
  try {
    s = await ctx.open(name, buildSessionConfig({ instructions: "You are a helpful, concise voice assistant.", modality: "audio", model }), model);
    await streamAudio(s, audio, false);
    s.send(inputAudioCommit());
    await s.waitFor((e) => e.type === SERVER.audioCommitted, 10_000, "committed");
    const before = s.mark();
    s.send(responseCreate());
    const r = await s.collectResponse(before);
    const failed = r.audio.length === 0 || s.since(before, SERVER.responseDone).some((t) => (t.evt.response as any)?.status === "failed");
    return {
      firstAudioMs: r.firstAudioAt === null ? null : Math.round(r.firstAudioAt - r.createdAt),
      audioMs: msOf(r.audio.length),
      transcript: r.transcript.trim(),
      audio: r.audio,
      failed,
    };
  } catch (e) {
    return { firstAudioMs: null, audioMs: 0, transcript: `(error: ${e instanceof Error ? e.message : e})`, audio: new Int16Array(0), failed: true };
  } finally {
    await s?.close();
  }
}

async function mindTrial(ctx: CheckContext, model: string, name: string, audio: Int16Array) {
  let s: ProbeSession | null = null;
  try {
    s = await ctx.open(name, buildSessionConfig({ instructions: MIND_PROMPT, modality: "text", tools: MIND_TOOLS, model }), model);
    await streamAudio(s, audio, false);
    s.send(inputAudioCommit());
    await s.waitFor((e) => e.type === SERVER.audioCommitted, 10_000, "committed");
    const before = s.mark();
    s.send(responseCreate());
    const r = await s.collectResponse(before);
    for (const c of r.calls) s.send(functionCallOutput(c.callId, JSON.stringify({ ok: true })));
    const call = r.calls[0];
    const failed = s.since(before, SERVER.responseDone).some((t) => (t.evt.response as any)?.status === "failed");
    return {
      decisionMs: call ? Math.round(call.at - r.createdAt) : null,
      failed,
      filler: r.text.trim().length > 0,
      tool: call?.name ?? (failed ? "(failed)" : "(none)"),
    };
  } catch (e) {
    return { decisionMs: null, failed: true, filler: false, tool: "(error)" };
  } finally {
    await s?.close();
  }
}

/** Server-side web_search: is the tool accepted, does the model use it, does an answer follow? */
async function searchTrial(ctx: CheckContext, model: string, name: string): Promise<string> {
  let s: ProbeSession | null = null;
  try {
    s = await ctx.open(
      name,
      buildSessionConfig({ instructions: "You are a helpful voice assistant.", modality: "text", model, tools: [{ type: "web_search" } as never] }),
      model,
    );
    const before = s.mark();
    s.send(textItem("Search the web: what is the weather in Tokyo right now? One sentence."));
    s.send(responseCreate());
    // A server search ends the first response after a preamble, then the server starts a second one itself.
    const deadline = Date.now() + 40_000;
    while (Date.now() < deadline && s.since(before, SERVER.responseDone).length < 2) await new Promise((r) => setTimeout(r, 200));
    const items = s.since(before, SERVER.outputItemAdded).map((t) => t.evt.item as any);
    const searched = items.some((i) => i?.type === "function_call" && i?.name === "web_search");
    const text = s
      .since(before, SERVER.outputTextDone)
      .map((t) => String(t.evt.text ?? ""))
      .join(" | ");
    const errors = s.since(before, SERVER.error).map((t) => JSON.stringify((t.evt.error as any)?.message ?? t.evt.error));
    return `${searched ? "searched" : "did NOT search"}; ${s.since(before, SERVER.responseDone).length} responses; text: ${JSON.stringify(text.slice(0, 220))}${errors.length ? `; errors: ${errors.join(", ")}` : ""}`;
  } catch (e) {
    return `rejected: ${e instanceof Error ? e.message.slice(0, 200) : e}`;
  } finally {
    await s?.close();
  }
}

async function pool(jobs: (() => Promise<void>)[], n: number): Promise<void> {
  let next = 0;
  await Promise.all(
    Array.from({ length: n }, async () => {
      while (next < jobs.length) await jobs[next++]();
    }),
  );
}

// --- report -------------------------------------------------------------------

const median = (xs: number[]) => {
  const s = [...xs].sort((a, b) => a - b);
  return s.length ? s[Math.floor(s.length / 2)] : NaN;
};
const fmtMs = (xs: (number | null)[]) => {
  const v = xs.filter((x): x is number => x !== null);
  return v.length ? `${median(v)} ms (${Math.min(...v)}–${Math.max(...v)})` : "—";
};
const rate = (n: number, d: number) => `${n}/${d}`;

function renderReport(voice: VoiceResult[], mind: MindResult[], search: Record<string, string>): string {
  const col = (m: string) => (m === "higgs-realtime" ? "higgs-realtime" : "1.5-preview");
  const rows: string[][] = [];
  const by = <T extends { model: string }>(xs: T[], m: string) => xs.filter((x) => x.model === m);

  rows.push(["**Voice:** first audio after response.create (median, range)", ...MODELS.map((m) => fmtMs(by(voice, m).map((v) => v.firstAudioMs)))]);
  rows.push(["**Voice:** correct answers", ...MODELS.map((m) => rate(by(voice, m).filter((v) => v.correct).length, by(voice, m).length))]);
  for (const q of QUESTIONS) {
    rows.push([`  · ${q.name}`, ...MODELS.map((m) => rate(by(voice, m).filter((v) => v.q === q.name && v.correct).length, by(voice, m).filter((v) => v.q === q.name).length))]);
  }
  rows.push(["**Voice:** failed responses", ...MODELS.map((m) => rate(by(voice, m).filter((v) => v.failed).length, by(voice, m).length))]);
  rows.push(["**Voice:** reply length (median audio)", ...MODELS.map((m) => `${median(by(voice, m).map((v) => v.audioMs))} ms`)]);
  rows.push(["**Mind:** decision time (median, range)", ...MODELS.map((m) => fmtMs(by(mind, m).map((x) => x.decisionMs)))]);
  rows.push(["**Mind:** right tool", ...MODELS.map((m) => rate(by(mind, m).filter((x) => x.correct).length, by(mind, m).length))]);
  rows.push(["**Mind:** failed responses", ...MODELS.map((m) => rate(by(mind, m).filter((x) => x.failed).length, by(mind, m).length))]);
  rows.push(["**Mind:** wrote filler text before the tool", ...MODELS.map((m) => rate(by(mind, m).filter((x) => x.filler).length, by(mind, m).length))]);
  rows.push(["**Voice:** correct, of responses that did not fail", ...MODELS.map((m) => rate(by(voice, m).filter((v) => v.correct).length, by(voice, m).filter((v) => !v.failed).length))]);
  rows.push(["**Mind:** right tool, of responses that did not fail", ...MODELS.map((m) => rate(by(mind, m).filter((x) => x.correct).length, by(mind, m).filter((x) => !x.failed).length))]);
  rows.push(["**Server web_search**", ...MODELS.map((m) => search[m] ?? "—")]);

  const out = [
    `# higgs-realtime vs higgs-realtime-1.5-preview`,
    "",
    `${TRIALS} trial(s) per model, each in a fresh session, identical TTS input audio.`,
    "",
    `| | ${MODELS.map(col).join(" | ")} |`,
    `|---|${MODELS.map(() => "---").join("|")}|`,
    ...rows.map((r) => `| ${r.join(" | ")} |`),
    "",
    "## What each model said (voice)",
  ];
  for (const q of QUESTIONS) {
    out.push("", `**${q.name}**: "${q.text}"`);
    for (const m of MODELS) for (const v of by(voice, m).filter((v) => v.q === q.name)) out.push(`- ${col(m)}: ${v.correct ? "✓" : "✗"} ${JSON.stringify(v.transcript)}`);
  }
  out.push("", "## Mind decisions");
  for (const sc of SCENARIOS) {
    out.push("", `**${sc.name}** (expect ${sc.expect})`);
    for (const m of MODELS) out.push(`- ${col(m)}: ${by(mind, m).filter((x) => x.sc === sc.name).map((x) => `${x.tool}${x.decisionMs !== null ? ` ${x.decisionMs} ms` : ""}`).join(", ")}`);
  }
  return out.join("\n") + "\n";
}
