import {
  SERVER,
  functionCallOutput,
  inputAudioCommit,
  responseCreate,
} from "../../../src/realtime/events";
import { MIND_TOOLS } from "../../../src/agent/mindTools";
import { buildSessionConfig } from "../../../src/realtime/sessionConfig";
import { streamAudio, synthesize } from "../audio";
import type { CheckContext } from "../harness";

/**
 * Q: Can a text-mode session act as an agent's private "mind"? It hears real
 *    audio, thinks one sentence, and signals its decision with a tool call.
 *    How fast is the decision, and does it pick the right tool?
 *
 * Probed 2026-10-23: right tool 3/3 in 330–470 ms, but with no text before
 * the call — the model goes straight to the tool. So the thought is now a
 * required FIRST argument of every tool: arguments stream as they are
 * generated (function_call_arguments.delta), so the thought still streams,
 * and it cannot be skipped. The tools are the agents' own (src/agent/mindTools.ts).
 */

export const MIND_PROMPT =
  "You are the private inner voice of Alice, the team's scheduler, in a live voice meeting with Bob and a human. " +
  "You know the design review is on Thursday at 3 pm. You never speak aloud. " +
  "After each thing you hear, reply only with exactly one tool call, no text.";

export const SCENARIOS = [
  { name: "wrong-fact", expect: "interrupt", line: "Hi all, Bob here. Quick reminder that the design review is on Friday at 10 am." },
  { name: "chit-chat", expect: "wait", line: "By the way, I tried that new coffee place downstairs, it was pretty good." },
  { name: "addressed", expect: "speak", line: "Alice, can you remind me when the design review is?" },
];

export async function mindCheck(ctx: CheckContext): Promise<void> {
  const config = buildSessionConfig({
    instructions: MIND_PROMPT,
    modality: "text",
    transcribe: true,
    tools: MIND_TOOLS,
  });
  ctx.info(`tool_choice "auto"; scenarios run in parallel, one fresh session each`);

  const results = await Promise.all(
    SCENARIOS.map(async (sc) => {
      const [audio, s] = await Promise.all([synthesize(ctx.apiKey, sc.line), ctx.open(`mind-${sc.name}`, config)]);
      await streamAudio(s, audio, false);
      const start = s.mark();
      s.send(inputAudioCommit());
      const ack = await s.waitFor((e) => e.type === SERVER.audioCommitted || e.type === SERVER.error, 10_000, "committed", start);
      if (ack.evt.type === SERVER.error) return { sc, error: JSON.stringify(ack.evt.error) };
      const before = s.mark();
      s.send(responseCreate());
      const resp = await s.collectResponse(before);
      // Close the tool loop properly, but do not ask for another response.
      for (const c of resp.calls) s.send(functionCallOutput(c.callId, JSON.stringify({ ok: true })));
      return {
        sc,
        resp,
        audioOut: s.since(before, SERVER.outputAudioDelta).length,
        firstArgsAt: s.since(before, "response.function_call_arguments.delta")[0]?.at ?? null,
      };
    }),
  );

  for (const r of results) {
    if ("error" in r) {
      ctx.fail(`${r.sc.name}: commit rejected in text mode: ${r.error}`);
      continue;
    }
    const { resp, sc } = r;
    const call = resp.calls[0];
    const callMs = call ? Math.round(call.at - resp.createdAt) : null;
    let thoughtText = "";
    try {
      thoughtText = String(JSON.parse(call?.args ?? "{}").thought ?? "");
    } catch {
      /* malformed args: reported below as no thought */
    }
    const ms = (at: number | null) => (at === null ? "—" : String(Math.round(at - resp.createdAt)));
    ctx.info(
      `${sc.name}: ${call ? `${call.name}(${call.args})` : "no tool"}${resp.text ? `; text ${JSON.stringify(resp.text)}` : ""}; ` +
        `first args delta ${ms(r.firstArgsAt)} ms, call done ${callMs ?? "—"} ms after response.create`,
    );
    ctx.expect(r.audioOut === 0, `${sc.name}: text-mode session produced no audio`);
    ctx.expect(resp.calls.length > 0, `${sc.name}: called a tool in text mode`);
    ctx.expect(thoughtText.length > 0, `${sc.name}: wrote a thought (${JSON.stringify(thoughtText)})`);
    ctx.expect(call?.name === sc.expect, `${sc.name}: chose ${sc.expect} (got ${call?.name ?? "nothing"})`);
  }
}
