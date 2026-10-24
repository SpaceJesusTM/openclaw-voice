import { SERVER, responseCreate, textItem } from "../../../src/realtime/events";
import { buildSessionConfig } from "../../../src/realtime/sessionConfig";
import type { CheckContext } from "../harness";
import type { ProbeSession } from "../session";

/**
 * Q: Can one session switch output_modalities between audio and text with
 *    session.update? If so, a single session could think silently and then
 *    speak, instead of two sessions per agent.
 */
export async function modalityCheck(ctx: CheckContext): Promise<void> {
  const s = await ctx.open("modality", buildSessionConfig({ instructions: "You are a concise assistant.", modality: "audio" }));

  const turn = async (label: string, prompt: string) => {
    const before = s.mark();
    s.send(textItem(prompt));
    s.send(responseCreate());
    const r = await s.collectResponse(before);
    const kind = r.audio.length > 0 ? "audio" : r.text.length > 0 ? "text" : "nothing";
    ctx.info(`${label}: got ${kind} — ${JSON.stringify(r.transcript || r.text)}`);
    return kind;
  };

  ctx.expect((await turn("start (audio)", "Say hello in three words.")) === "audio", "audio session replies with audio");
  if (await switchTo(ctx, s, "text")) {
    ctx.expect((await turn("after switch to text", "Say goodbye in three words.")) === "text", "after session.update to text, replies are text");
    if (await switchTo(ctx, s, "audio")) {
      ctx.expect((await turn("after switch back to audio", "Say thanks in three words.")) === "audio", "switching back to audio works");
    }
  }
}

async function switchTo(ctx: CheckContext, s: ProbeSession, modality: "audio" | "text"): Promise<boolean> {
  const before = s.mark();
  s.send({ type: "session.update", session: { type: "realtime", output_modalities: [modality] } });
  const ack = await s
    .waitFor((e) => e.type === SERVER.sessionUpdated || e.type === SERVER.error, 8_000, "session.updated", before)
    .catch(() => null);
  if (!ack) {
    ctx.fail(`switch to ${modality}: no session.updated or error within 8 s`);
    return false;
  }
  if (ack.evt.type === SERVER.error) {
    ctx.fail(`switch to ${modality} rejected: ${JSON.stringify(ack.evt.error)}`);
    return false;
  }
  const resolved = (ack.evt.session as Record<string, unknown> | undefined)?.output_modalities;
  ctx.info(`switch to ${modality}: session.updated reports output_modalities ${JSON.stringify(resolved)}`);
  return true;
}
