/**
 * Live API probe for the multi-agent design. Each check answers one question
 * the design depends on, prints PASS / FAIL / INFO findings, and writes every
 * event to captures/.
 *
 *   npm run probe -- connect         key + model-name sanity check (cheapest)
 *   npm run probe                    all quick checks (everything except idle)
 *   npm run probe -- commit mind     just those checks
 *   npm run probe -- idle            the ~7 minute idle-timeout check
 *   npm run probe -- compare         higgs-realtime vs 1.5-preview, side by side (COMPARE_TRIALS=3)
 *   npm run probe -- --quiet ...     findings only, no per-event log
 */
// First import, so .env is loaded before any module reads process.env at
// import time (MODEL in events.ts does).
import "dotenv/config";
import { loadApiKey } from "./audio";
import { CheckContext, type Finding } from "./harness";
import { commitCheck } from "./checks/commit";
import { compareCheck } from "./checks/compare";
import { connectCheck } from "./checks/connect";
import { idleCheck } from "./checks/idle";
import { mindCheck } from "./checks/mind";
import { modalityCheck } from "./checks/modality";
import { relayCheck } from "./checks/relay";
import { timingCheck } from "./checks/timing";
import { voicesCheck } from "./checks/voices";

const CHECKS: Record<string, (ctx: CheckContext) => Promise<void>> = {
  connect: connectCheck,
  commit: commitCheck,
  relay: relayCheck,
  mind: mindCheck,
  timing: timingCheck,
  modality: modalityCheck,
  voices: voicesCheck,
  idle: idleCheck,
  compare: compareCheck,
};
const QUICK = ["connect", "commit", "relay", "mind", "timing", "modality", "voices"];

async function main() {
  const args = process.argv.slice(2);
  const verbose = !args.includes("--quiet");
  const names = args.filter((a) => !a.startsWith("--"));
  const selected = names.length ? names : QUICK;
  const unknown = selected.filter((n) => !CHECKS[n]);
  if (unknown.length) throw new Error(`unknown check(s): ${unknown.join(", ")}. Known: ${Object.keys(CHECKS).join(", ")}`);

  const apiKey = loadApiKey();
  const all: { check: string; findings: Finding[]; capture: string }[] = [];

  for (const name of selected) {
    console.log(`\n=== ${name} ===`);
    const ctx = new CheckContext(name, apiKey, verbose);
    try {
      await CHECKS[name](ctx);
    } catch (e) {
      ctx.fail(`check crashed: ${e instanceof Error ? e.message : String(e)}`);
    }
    const capture = await ctx.finish();
    all.push({ check: name, findings: ctx.findings, capture });
  }

  console.log("\n=== summary ===");
  let failed = 0;
  for (const { check, findings, capture } of all) {
    console.log(`\n${check}  (${capture})`);
    for (const f of findings) {
      if (f.verdict === "FAIL") failed++;
      console.log(`  ${f.verdict.padEnd(4)}  ${f.msg}`);
    }
  }
  process.exit(failed ? 1 : 0);
}

main().catch((e) => {
  console.error(e instanceof Error ? e.message : e);
  process.exit(1);
});
