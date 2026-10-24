import type { FunctionTool } from "../realtime/sessionConfig";

/**
 * Tools for an agent with no private mind, whose voice decides alone. At each
 * pause it is asked for a turn and either speaks or calls stay_silent. Guidance
 * lives in the descriptions: long session prompts made Higgs stop calling tools.
 */
export const STAY_SILENT: FunctionTool = {
  type: "function",
  name: "stay_silent",
  description:
    "Say nothing this time. Use it when you aren't addressed and have nothing new and useful for your goal. Never speak just to agree, thank, or repeat yourself.",
  parameters: { type: "object", properties: {} },
};

export const START_WORK: FunctionTool = {
  type: "function",
  name: "start_work",
  description:
    "Leave the conversation and go do your job. Use it once the conversation has given you what you need, or when it's going in circles: say a short goodbye first, then call this.",
  parameters: {
    type: "object",
    properties: { task: { type: "string", description: "The work you are leaving to do, in one sentence." } },
    required: ["task"],
  },
};

export const VOICE_ONLY_TOOLS: FunctionTool[] = [STAY_SILENT, START_WORK];

/** The task from a start_work call's arguments; never throws. */
export function taskOf(args: string): string {
  try {
    return String(JSON.parse(args || "{}").task ?? "").trim() || "my part of the work";
  } catch {
    return "my part of the work";
  }
}
