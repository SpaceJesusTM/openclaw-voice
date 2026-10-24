import { SERVER, responseCreate, textItem } from "../../../src/realtime/events";
import { buildSessionConfig } from "../../../src/realtime/sessionConfig";
import { bosonGet, sleep } from "../audio";
import type { CheckContext } from "../harness";

/** The TTS preset voices. The Realtime docs only promise "default". */
const PRESETS = ["chloe", "eleanor", "jake", "marcus", "nora", "oliver"];

/**
 * Q: Which voices can a Realtime session use? Each agent needs a distinct one.
 *    Tries each TTS preset at session start, ONE AT A TIME: the server
 *    validates non-default voices against the voices API, and opening six at
 *    once got every one rejected with "voices API returned HTTP 429"
 *    (2026-10-23). That matters for spawning agents too.
 */
export async function voicesCheck(ctx: CheckContext): Promise<void> {
  const listed = await bosonGet(ctx.apiKey, "/v1/audio/voices");
  ctx.info(`GET /v1/audio/voices -> ${listed.status} ${JSON.stringify(listed.body).slice(0, 300)}`);

  for (const voice of PRESETS) {
    const r = await tryVoice(ctx, voice);
    ctx.expect(r.ok && r.resolvedVoice === voice, `voice "${voice}": resolved as ${JSON.stringify(r.resolvedVoice)}${r.errors.length ? `, errors ${JSON.stringify(r.errors)}` : ""}`);
  }

  // Mid-session voice change on a default session.
  const s = await ctx.open("v-switch", buildSessionConfig({ instructions: "You are concise.", modality: "audio" }));
  const before = s.mark();
  s.send({ type: "session.update", session: { type: "realtime", audio: { output: { voice: "oliver" } } } });
  const ack = await s
    .waitFor((e) => e.type === SERVER.sessionUpdated || e.type === SERVER.error, 8_000, "session.updated", before)
    .catch(() => null);
  if (!ack) ctx.info("mid-session voice change: no ack within 8 s");
  else if (ack.evt.type === SERVER.error) ctx.info(`mid-session voice change rejected: ${JSON.stringify(ack.evt.error)}`);
  else ctx.info(`mid-session voice change accepted; voice now ${JSON.stringify((ack.evt.session as any)?.audio?.output?.voice)}`);
}

/** Open a session with `voice` and get a short reply; retry on the voices API's 429. */
async function tryVoice(ctx: CheckContext, voice: string) {
  let lastError = "";
  for (let attempt = 0; attempt < 3; attempt++) {
    if (attempt) await sleep(2000 * attempt);
    try {
      const s = await ctx.open(`v-${voice}`, buildSessionConfig({ instructions: "You are concise.", modality: "audio", voice }));
      const resolvedVoice = s.resolved?.audio?.output?.voice;
      const before = s.mark();
      s.send(textItem("Say 'hello there' and nothing else."));
      s.send(responseCreate());
      const r = await s.collectResponse(before, 20_000);
      await s.close();
      return { ok: r.audio.length > 0, resolvedVoice, errors: r.errors };
    } catch (e) {
      lastError = String(e);
      if (!lastError.includes("429")) break;
      ctx.info(`voice "${voice}": 429 from voice validation, retrying`);
    }
  }
  return { ok: false, resolvedVoice: undefined, errors: [lastError] };
}
