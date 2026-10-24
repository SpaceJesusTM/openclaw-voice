/**
 * Higgs Realtime token rates, from docs.boson.ai/pricing (read 2026-10-24),
 * in USD per 1M tokens. Billing is by tokens; Boson's per-minute audio figures
 * ($0.0023/min in, $0.014/min out) are labelled estimates. The billing
 * dashboard (boson.ai/workspace/billing/usage) is the source of truth.
 */
export const RATES = { input: 0.75, cachedInput: 0.25, output: 4.5 } as const;

/** Usage as reported on response.done, summed. */
export interface Usage {
  responses: number;
  inputTokens: number;
  cachedTokens: number;
  outputTokens: number;
}

export const emptyUsage = (): Usage => ({ responses: 0, inputTokens: 0, cachedTokens: 0, outputTokens: 0 });

export function addUsage(into: Usage, reported: unknown): void {
  const u = reported as { input_tokens?: number; output_tokens?: number; input_token_details?: { cached_tokens?: number } } | null;
  if (!u) return;
  into.responses++;
  into.inputTokens += u.input_tokens ?? 0;
  into.cachedTokens += u.input_token_details?.cached_tokens ?? 0;
  into.outputTokens += u.output_tokens ?? 0;
}

/** USD at RATES. Cached tokens are part of input_tokens, billed at the cached rate. */
export function costOf(u: Usage): number {
  const uncached = Math.max(0, u.inputTokens - u.cachedTokens);
  return (uncached * RATES.input + u.cachedTokens * RATES.cachedInput + u.outputTokens * RATES.output) / 1_000_000;
}

export function sumUsage(...us: Usage[]): Usage {
  const out = emptyUsage();
  for (const u of us) {
    out.responses += u.responses;
    out.inputTokens += u.inputTokens;
    out.cachedTokens += u.cachedTokens;
    out.outputTokens += u.outputTokens;
  }
  return out;
}
