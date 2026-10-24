import { MODEL } from "../../../src/realtime/events";
import { buildSessionConfig } from "../../../src/realtime/sessionConfig";
import type { CheckContext } from "../harness";

const BASE = "wss://api.boson.ai/v1/realtime";

/**
 * Q: Does the key work, and how do we reach Higgs Realtime 1.5? The docs name
 *    only "higgs-realtime"; the tutorial's realtime-1.5-preview branch uses
 *    "higgs-realtime-1.5-preview" — with a TRAILING-SLASH URL and the model in
 *    session.update only. The no-slash URL with ?model= rejected the preview
 *    as "not available on this endpoint" (2026-10-23). Tries each URL form
 *    with each name and reports what session.created resolves to.
 *    No responses are requested.
 */
export async function connectCheck(ctx: CheckContext): Promise<void> {
  ctx.info(`configured MODEL is ${JSON.stringify(MODEL)}`);
  const models = ["higgs-realtime", "higgs-realtime-1.5-preview", "higgs-realtime-1.5"];
  const urls = (m: string) => [`${BASE}?model=${m}`, `${BASE}/`, `${BASE}/?model=${m}`, BASE];
  for (const model of models) {
    for (const url of urls(model)) {
      const label = `${model} @ ${url.replace(BASE, "…/realtime")}`;
      try {
        const s = await ctx.open(`connect`, buildSessionConfig({ instructions: "You are concise.", modality: "audio", model }), model, url);
        const r = s.resolved ?? {};
        ctx.pass(`${label}: session.created; resolved model ${JSON.stringify(r.model)}`);
        await s.close();
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        ctx.fail(`${label}: ${msg.match(/"message":"([^"]+)"/)?.[1] ?? msg}`);
      }
    }
  }
}
