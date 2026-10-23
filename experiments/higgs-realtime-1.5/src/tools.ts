import planner from '../fixtures/planner.json';
import { object } from './protocol';

export const plannerDefinition = {
  type: 'function', name: 'get_demo_planner',
  description: 'Look up the demo planner. Always call this when asked what is on the planner today. Do not invent entries. Include the first entry title and verification code from the result in your answer.',
  parameters: { type: 'object', properties: { day: { type: 'string', enum: ['today'] } },
    required: ['day'], additionalProperties: false },
};

export interface ToolResult { summary: string; detail?: unknown; error?: string }
export type ToolHandler = (name: string, args: unknown) => Promise<ToolResult>;

/** Replace this handler with an adapter later; the wire loop need not change. */
export const dispatchTool: ToolHandler = async (name, args) => {
  if (name !== 'get_demo_planner') return { summary: 'Unknown local function.', error: 'unknown_tool' };
  const parsed = object(args);
  if (parsed.day !== 'today' || Object.keys(parsed).length !== 1) {
    return { summary: 'Planner expects exactly {"day":"today"}.', error: 'invalid_arguments' };
  }
  return {
    summary: `${planner.entries.length} demo entries today: ${planner.entries.map(e => `${e.time} ${e.title} in ${e.location}`).join('; ')}. Verification code ${planner.verification_code}.`,
    detail: structuredClone(planner),
  };
};

export function parseArguments(raw: unknown): unknown {
  if (typeof raw !== 'string') throw new Error('Function arguments must be a JSON string');
  const parsed: unknown = JSON.parse(raw);
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('Function arguments must decode to an object');
  }
  return parsed;
}
