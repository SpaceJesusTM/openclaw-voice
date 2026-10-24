/**
 * Peer conversation demo. Agents with a voice and a private mind share one
 * room with a scripted human. The bus only carries audio; every agent decides
 * for itself when to speak, cut in, or yield.
 *
 *   npm run demo                       Alice + Bob
 *   npm run demo -- --agents 3         add Carol
 *   npm run demo -- --max-seconds 120 --max-agent-turns 4
 *   npm run demo -- --mind-model higgs-realtime     minds on Higgs 1, voices on .env's model
 *   npm run demo -- --no-mind                       voice-only agents
 * Ends with tokens and cost per agent (voice vs mind).
 *
 * Writes captures/demo-<time>.wav (what the room heard), .md (transcript with
 * every agent's private thoughts) and .log.
 */
// First import, so .env is loaded before any module reads process.env.
import "dotenv/config";
import { appendFileSync, mkdirSync, writeFileSync } from "node:fs";
import { Agent } from "../src/agent/Agent";
import { AgentLoop, DEFAULT_LOOP_TIMING } from "../src/agent/AgentLoop";
import { msOf, writeWav } from "../src/audio/pcm";
import { costOf, sumUsage, type Usage } from "../src/realtime/pricing";
import { loadApiKey, synthesize } from "../src/audio/tts";
import { AudioBus } from "../src/room/AudioBus";
import { Journal } from "../src/room/Journal";
import { ScriptedHuman } from "../src/room/ScriptedHuman";
import { AGENTS, HUMAN_LINES, HUMAN_VOICE } from "./scenario";

const HUMAN = "the human";

function arg(name: string, fallback: number): number {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 ? Number(process.argv[i + 1]) : fallback;
}

function strArg(name: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 ? process.argv[i + 1] : undefined;
}

async function main() {
  const n = arg("agents", 2);
  const maxSeconds = arg("max-seconds", 180);
  const maxAgentTurns = arg("max-agent-turns", 6);
  const apiKey = loadApiKey();

  mkdirSync("captures", { recursive: true });
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const base = `captures/demo-${stamp}`;
  const bus = new AudioBus();
  const t0 = performance.now();
  const clock = () => (bus.elapsedMs() ? bus.elapsedMs() : performance.now() - t0);
  const log = (line: string) => {
    const s = clock() / 1000;
    const out = `[${String(Math.floor(s / 60)).padStart(2, "0")}:${(s % 60).toFixed(1).padStart(4, "0")}] ${line}`;
    console.log(out);
    appendFileSync(`${base}.log`, out + "\n");
  };
  const journal = new Journal(clock, log);

  // --mind-model higgs-realtime: run minds on Higgs 1 (the voices stay on BOSON_REALTIME_MODEL).
  // --no-mind: voice-only agents (each voice decides by speaking or staying silent).
  const mindModel = strArg("mind-model");
  const noMind = process.argv.includes("--no-mind");
  const configs = AGENTS.slice(0, n).map((c) => ({ ...c, ...(mindModel ? { mindModel } : {}), ...(noMind ? { mind: false } : {}) }));
  log(`voicing ${HUMAN_LINES.length} human lines`);
  const lines = await Promise.all(HUMAN_LINES.map(async (text) => ({ text, samples: await synthesize(apiKey, text, HUMAN_VOICE) })));

  // One agent at a time: the server's voice validation is rate limited.
  const agents: Agent[] = [];
  for (const c of configs) {
    log(`starting ${c.name} (${c.voice})`);
    agents.push(await Agent.create(c, { others: configs.filter((o) => o !== c).map((o) => o.name) }, apiKey, (s, m) => log(`   {${s}} ${m}`)));
  }

  for (const agent of agents) {
    bus.join(
      new AgentLoop(agent, bus, {
        ...DEFAULT_LOOP_TIMING,
        prepareAtMs: arg("prepare-at", DEFAULT_LOOP_TIMING.prepareAtMs),
        maxAgentTurns,
        humanName: HUMAN,
        journal,
      }),
    );
  }
  const human = new ScriptedHuman(HUMAN, bus, lines, { replyAfterMs: 2500, giveUpAfterMs: 8000 }, journal);
  bus.join(human);

  log(`room open: ${agents.map((a) => a.name).join(", ")} and ${HUMAN}`);
  bus.start();
  human.start();
  try {
    const limit = new Promise<"limit">((r) => setTimeout(() => r("limit"), maxSeconds * 1000));
    if ((await Promise.race([human.done, limit])) === "limit") log(`-- time limit (${maxSeconds} s) reached`);
  } finally {
    await bus.stop();
    const mix = bus.mixdown();
    writeWav(`${base}.wav`, mix);
    writeFileSync(`${base}.md`, journal.markdown("Peer conversation"));
    log(`wrote ${base}.wav (${(msOf(mix.length) / 1000).toFixed(1)} s), ${base}.md, ${base}.log`);
    logCost(agents, msOf(mix.length) / 60_000, log);
    await Promise.all(agents.map((a) => a.close()));
  }
}

/** Tokens and cost per agent, split into speech (voice) and thoughts (mind). */
function logCost(agents: Agent[], minutes: number, log: (line: string) => void) {
  const fmt = (u: Usage) =>
    `${u.responses} responses, ${u.inputTokens} in (${u.cachedTokens} cached), ${u.outputTokens} out = $${costOf(u).toFixed(4)}`;
  const voices: Usage[] = [];
  const minds: Usage[] = [];
  for (const a of agents) {
    const { voice, mind } = a.usage;
    voices.push(voice);
    minds.push(mind);
    log(`cost ${a.name} voice: ${fmt(voice)}${a.hasMind ? ` | mind: ${fmt(mind)}` : ""}`);
  }
  const v = sumUsage(...voices);
  const m = sumUsage(...minds);
  const total = costOf(v) + costOf(m);
  log(`cost total: voices $${costOf(v).toFixed(4)} + minds $${costOf(m).toFixed(4)} = $${total.toFixed(4)} over ${minutes.toFixed(1)} min ≈ $${(total / Math.max(minutes, 0.01)).toFixed(4)}/min`);
}

main().catch((e) => {
  console.error(e instanceof Error ? e.message : e);
  process.exit(1);
});
