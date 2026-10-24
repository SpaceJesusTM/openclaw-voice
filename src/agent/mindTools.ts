import type { FunctionTool } from "../realtime/sessionConfig";

/**
 * The tools an agent's mind decides with. Every tool takes a required `thought`
 * FIRST: left to itself the model calls the tool without thinking aloud
 * (probed 2026-10-23), and the arguments are where the thought can live.
 */
const thought = { type: "string", description: "Your private thought, one short sentence, written first." };

export const MIND_TOOLS: FunctionTool[] = [
  {
    type: "function",
    name: "speak",
    description:
      "Speak next: now if nobody is talking, otherwise as soon as the current speaker finishes. Use when you are addressed, or have something new your role should add. Not to repeat yourself, thank, or just agree.",
    parameters: {
      type: "object",
      properties: { thought, intent: { type: "string", description: "What you will say, in a few words." } },
      required: ["thought", "intent"],
    },
  },
  {
    type: "function",
    name: "interrupt",
    description:
      "Cut in on the current speaker immediately, or keep talking if they are talking over you. Only when they just said something wrong that matters to your role. Never cut off the human.",
    parameters: {
      type: "object",
      properties: { thought, reason: { type: "string", description: "What was wrong, in a few words." } },
      required: ["thought", "reason"],
    },
  },
  {
    type: "function",
    name: "wait",
    description: "Stay silent for now.",
    parameters: { type: "object", properties: { thought }, required: ["thought"] },
  },
  {
    type: "function",
    name: "start_work",
    description:
      "Leave the conversation to go do your job: once it has given you what you need, or it's going in circles. Your voice says a short goodbye first.",
    parameters: {
      type: "object",
      properties: { thought, task: { type: "string", description: "The work you are leaving to do, in one sentence." } },
      required: ["thought", "task"],
    },
  },
];

export type Decision =
  | { kind: "speak"; thought: string; intent: string }
  | { kind: "interrupt"; thought: string; reason: string }
  | { kind: "wait"; thought: string }
  | { kind: "start_work"; thought: string; task: string };

/** Never throws: a malformed or missing call is a "wait". */
export function parseDecision(name: string, args: string): Decision {
  let a: Record<string, unknown> = {};
  try {
    a = JSON.parse(args || "{}");
  } catch {
    /* fall through with empty args */
  }
  const thought = String(a.thought ?? "");
  if (name === "speak") return { kind: "speak", thought, intent: String(a.intent ?? "") };
  if (name === "interrupt") return { kind: "interrupt", thought, reason: String(a.reason ?? "") };
  if (name === "start_work") return { kind: "start_work", thought, task: String(a.task ?? "").trim() || "my part of the work" };
  return { kind: "wait", thought };
}
