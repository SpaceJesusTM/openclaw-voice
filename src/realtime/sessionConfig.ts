import { AUDIO_RATE, MODEL, type ClientEvent } from "./events";

export interface FunctionTool {
  type: "function";
  name: string;
  description: string;
  parameters: Record<string, unknown>;
}

export interface SessionOptions {
  instructions: string;
  /** Defaults to MODEL. */
  model?: string;
  /** "audio" for an agent's voice, "text" for its private mind. */
  modality: "audio" | "text";
  voice?: string;
  /** null = manual turns (commit + response.create); we drive every turn ourselves. */
  turnDetection?: null | { type: "server_vad" | "semantic_vad"; [k: string]: unknown };
  /** Transcribe input audio so we can read what was heard. Off by default server-side. */
  transcribe?: boolean;
  tools?: FunctionTool[];
  toolChoice?: string;
}

/**
 * The first session.update. Acked by session.created, which carries the
 * resolved config; always read it rather than assuming defaults.
 *
 * Keep `instructions` short: long rule lists made the model stop calling tools.
 * Tool-specific guidance belongs in the tool's description.
 */
export function buildSessionConfig(opts: SessionOptions): ClientEvent {
  const format = { type: "audio/pcm", rate: AUDIO_RATE };
  return {
    type: "session.update",
    session: {
      type: "realtime",
      model: opts.model ?? MODEL,
      instructions: opts.instructions,
      output_modalities: [opts.modality],
      audio: {
        input: {
          format,
          turn_detection: opts.turnDetection ?? null,
          ...(opts.transcribe ? { transcription: { model: "higgs-stt-3.1", language: "en" } } : {}),
        },
        output: { format, voice: opts.voice ?? "default" },
      },
      tools: opts.tools ?? [],
      tool_choice: opts.toolChoice ?? "auto",
    },
  };
}
