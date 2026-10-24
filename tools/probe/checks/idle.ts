import { SERVER, inputAudioCommit, textItem } from "../../../src/realtime/events";
import { buildSessionConfig } from "../../../src/realtime/sessionConfig";
import { now } from "../session";
import { sleep, streamAudio, synthesize } from "../audio";
import type { CheckContext } from "../harness";

/**
 * Q: The idle timeout is "5 minutes without user speech". Which inputs reset
 *    it for a manual-turn session? Three sessions, ~7 minutes:
 *      control  — nothing after setup (should time out at ~5 min)
 *      audio    — 3 s of committed speech audio every 60 s (another agent talking)
 *      text     — a text message every 60 s
 *    No responses are requested, so this costs only input.
 */
export async function idleCheck(ctx: CheckContext): Promise<void> {
  const RUN_MS = 7 * 60_000;
  const config = buildSessionConfig({ instructions: "You are concise.", modality: "audio" });
  const speech = await synthesize(ctx.apiKey, "Bob here, just checking in with a quick update on the schedule.");
  const [control, audio, text] = await Promise.all([ctx.open("control", config), ctx.open("audio", config), ctx.open("text", config)]);
  const startedAt = now();
  ctx.info(`running for up to ${RUN_MS / 60_000} minutes`);

  const feeders = [
    (async () => {
      while (now() - startedAt < RUN_MS && !audio.closed) {
        await streamAudio(audio, speech, true);
        audio.send(inputAudioCommit());
        await sleep(60_000);
      }
    })(),
    (async () => {
      while (now() - startedAt < RUN_MS && !text.closed) {
        text.send(textItem("[Bob]: just checking in with a quick update on the schedule."));
        await sleep(60_000);
      }
    })(),
  ];

  await Promise.race([
    Promise.all([control, audio, text].map((s) => s.whenClosed())),
    sleep(RUN_MS),
  ]);
  void feeders;

  for (const s of [control, audio, text]) {
    const idle = s.events.find((t) => t.evt.type === SERVER.idleTimeout);
    if (idle) ctx.info(`${s.name}: session.idle_timeout after ${((idle.at - startedAt) / 60_000).toFixed(2)} min`);
    else ctx.info(`${s.name}: no idle timeout within ${RUN_MS / 60_000} min${s.closed ? ` (closed: ${s.closed.message})` : ""}`);
  }
  const timedOut = (name: string) => [control, audio, text].find((s) => s.name === name)!.events.some((t) => t.evt.type === SERVER.idleTimeout);
  ctx.expect(timedOut("control"), "control session idled out (validates the test)");
  ctx.expect(!timedOut("audio"), "committed agent audio keeps a manual-turn session alive");
  ctx.info(`text-only messages ${timedOut("text") ? "do NOT" : "DO"} keep a session alive`);
}
