import { SERVER, inputAudioClear, inputAudioCommit, responseCreate, textItem } from "../../../src/realtime/events";
import { buildSessionConfig } from "../../../src/realtime/sessionConfig";
import { CHUNK_SAMPLES, msOf, streamAudio, synthesize } from "../audio";
import type { CheckContext } from "../harness";

/**
 * Q: With turn_detection null, does append + commit create a user item the
 *    model answers, in several sessions at once?
 * Also: does a per-response `instructions` override take effect, and is a
 *    commit rejected while that session is generating (voice_output_task_ongoing)?
 */
/**
 * Variants, run in parallel (so this is also the "several sessions at once"
 * test). Each steers the reply a different way:
 *   none           plain response.create
 *   override       response.create { instructions } — probed 2026-10-23:
 *                  applied, but the model then ignores the conversation
 *                  ("hello, how can I help?"), paced or not
 *   user-item      a user message "[note to self] ..." before response.create —
 *                  probed 2026-10-23: works (steered AND in context)
 *   session-prompt session.update appending the steer to the session prompt —
 *                  probed 2026-10-23: works, and leaves nothing in history
 * (A system-role message is not an option: the server rejects it with
 * "Invalid role: system". An earlier variant here looked "ignored" because
 * of that rejection.)
 */
type Steer = "none" | "override" | "user-item" | "session-prompt";
const VARIANTS: { name: string; paced: boolean; steer: Steer }[] = [
  { name: "dump", paced: false, steer: "none" },
  { name: "paced+override", paced: true, steer: "override" },
  { name: "paced+user-item", paced: true, steer: "user-item" },
  { name: "paced+session-prompt", paced: true, steer: "session-prompt" },
];

const BASE_PROMPT = "You are a concise voice assistant. Reply in one short sentence.";
const STEER = "Begin your reply with the exact word 'Banana', then answer in one short sentence.";

export async function commitCheck(ctx: CheckContext): Promise<void> {
  const question = await synthesize(ctx.apiKey, "What is the capital of France? Answer in one short sentence.");
  ctx.info(`TTS utterance is ${msOf(question.length)} ms`);

  const config = buildSessionConfig({
    instructions: BASE_PROMPT,
    modality: "audio",
    transcribe: true,
  });
  const sessions = await Promise.all(VARIANTS.map((v) => ctx.open(`commit:${v.name}`, config)));
  ctx.info(`resolved turn_detection: ${JSON.stringify(sessions[0].resolved?.audio?.input?.turn_detection)}`);

  const results = await Promise.all(
    sessions.map(async (s, i) => {
      const v = VARIANTS[i];
      const start = s.mark();
      await streamAudio(s, question, v.paced);
      s.send(inputAudioCommit());
      const ack = await s.waitFor(
        (e) => e.type === SERVER.audioCommitted || e.type === SERVER.error,
        10_000,
        "committed",
        start,
      );
      if (ack.evt.type === SERVER.error) return { s, v, error: JSON.stringify(ack.evt.error) };

      const before = s.mark();
      if (v.steer === "user-item") s.send(textItem(`[note to self] ${STEER}`));
      if (v.steer === "session-prompt") {
        const at = s.mark();
        s.send({ type: "session.update", session: { type: "realtime", instructions: `${BASE_PROMPT} ${STEER}` } });
        await s.waitFor((e) => e.type === SERVER.sessionUpdated || e.type === SERVER.error, 8_000, "session.updated", at);
      }
      s.send(v.steer === "override" ? responseCreate({ instructions: STEER }) : responseCreate());
      const resp = await s.collectResponse(before);
      const heard = await s
        .waitType(SERVER.transcriptionCompleted, 15_000, start)
        .then((t) => String(t.evt.transcript ?? ""))
        .catch(() => null);
      return { s, v, resp, heard };
    }),
  );

  for (const r of results) {
    if ("error" in r) {
      ctx.fail(`${r.s.name}: commit rejected: ${r.error}`);
      continue;
    }
    const { resp, heard, v } = r;
    ctx.info(`${r.s.name}: heard ${JSON.stringify(heard)}; replied ${JSON.stringify(resp!.transcript)} (${msOf(resp!.audio.length)} ms audio)`);
    ctx.expect(resp!.audio.length > 0 && /paris/i.test(resp!.transcript), `${r.s.name}: answered the committed audio (mentions Paris)`);
    if (v.steer !== "none") ctx.expect(/^\W*banana/i.test(resp!.transcript), `${r.s.name}: steering applied (starts with Banana)`);
  }

  // Commit while generating: should be rejected with voice_output_task_ongoing.
  const s = sessions[0];
  const before = s.mark();
  s.send(textItem("Count slowly from one to fifteen."));
  s.send(responseCreate());
  await s.waitType(SERVER.outputAudioDelta, 20_000, before);
  const probeAt = s.mark();
  await streamAudio(s, question.subarray(0, CHUNK_SAMPLES * 5), false);
  s.send(inputAudioCommit());
  // Audio arrives ~6x real time, so generation may already be over; say so
  // rather than misreport an accepted commit.
  const stillGenerating = s.since(before, SERVER.responseDone).length === 0;
  const busy = await s
    .waitFor((e) => e.type === SERVER.audioCommitted || e.type === SERVER.error, 5_000, "commit-while-busy", probeAt)
    .catch(() => null);
  const when = stillGenerating ? "during generation" : "after generation had already finished (inconclusive)";
  if (!busy) ctx.info(`commit ${when}: no ack and no error within 5 s`);
  else if (busy.evt.type === SERVER.error) ctx.info(`commit ${when} rejected: ${JSON.stringify(busy.evt.error)}`);
  else ctx.info(`commit ${when} was accepted${stillGenerating ? " (docs say it should be rejected)" : ""}`);
  s.send(inputAudioClear());
  await s.waitType(SERVER.responseDone, 30_000, before).catch(() => undefined);
}
