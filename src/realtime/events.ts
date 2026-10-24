// The Higgs Realtime wire protocol: event names, client event builders, and
// close codes. Ported from boson-ai/Higgs-Realtime-Tutorial (realtime-1.5-preview).
//
// Envelopes are typed loosely on purpose: the server emits more event types
// than we act on and may add more. Log everything, act on what we know.

export interface ServerEvent {
  type: string;
  event_id?: string;
  [key: string]: unknown;
}

export interface ClientEvent {
  type: string;
  [key: string]: unknown;
}

// Higgs Realtime 1.5. It was rejected ("not available on this endpoint") until
// about 00:20 UTC on 2026-10-24, then accepted on every URL form; the docs
// still list only "higgs-realtime". "higgs-realtime-1.5" is not a valid name.
// Override with BOSON_REALTIME_MODEL; `npm run probe -- connect` checks names.
export const MODEL =
  (typeof process !== "undefined" && process.env.BOSON_REALTIME_MODEL?.trim()) || "higgs-realtime-1.5-preview";
export const AUDIO_RATE = 24000;

export const SERVER = {
  sessionCreated: "session.created",
  sessionUpdated: "session.updated",

  speechStarted: "input_audio_buffer.speech_started",
  speechStopped: "input_audio_buffer.speech_stopped",
  audioCommitted: "input_audio_buffer.committed",
  transcriptionCompleted: "conversation.item.input_audio_transcription.completed",
  transcriptionFailed: "conversation.item.input_audio_transcription.failed",

  itemAdded: "conversation.item.added",
  itemTruncated: "conversation.item.truncated",

  responseCreated: "response.created",
  outputItemAdded: "response.output_item.added",
  responseDone: "response.done",

  outputAudioDelta: "response.output_audio.delta",
  outputAudioDone: "response.output_audio.done",
  audioTranscriptDelta: "response.output_audio_transcript.delta",
  /** Boson extension: a transcript fragment annotated with its audio duration. */
  audioTranscriptLength: "response.output_audio_transcript.length",
  audioTranscriptDone: "response.output_audio_transcript.done",

  /** Emitted instead of the audio events when output_modalities is ["text"]. */
  outputTextDelta: "response.output_text.delta",
  outputTextDone: "response.output_text.done",

  functionCallArgsDone: "response.function_call_arguments.done",

  error: "error",
  idleTimeout: "session.idle_timeout",
  maxDuration: "session.max_duration_reached",
} as const;

// --- Close codes -------------------------------------------------------------

export interface CloseInfo {
  code: number;
  reason: string;
  message: string;
  retryable: boolean;
}

export function describeClose(code: number, reason: string): CloseInfo {
  switch (code) {
    case 1000:
      // Includes idle timeout and max duration, which are preceded by
      // session.idle_timeout / session.max_duration_reached.
      return {
        code,
        reason,
        message: reason ? `Session closed normally: ${reason}` : "Session closed normally.",
        retryable: true,
      };
    case 1013:
      return { code, reason, message: "Server at max concurrency (1013). Retry with backoff.", retryable: true };
    case 3000:
      return { code, reason, message: "Authentication failed (3000): bad or expired key.", retryable: false };
    case 4429:
      return {
        code,
        reason,
        message: "Billing entitlement refused (4429). The preceding `error` event has the detail.",
        retryable: false,
      };
    default:
      return {
        code,
        reason,
        message: `Socket closed (${code})${reason ? `: ${reason}` : ""}.`,
        // 1006: no close frame at all, usually a network drop.
        retryable: code === 1006,
      };
  }
}

// --- Client event builders ---------------------------------------------------

/**
 * A text message added to the conversation. Does not start a response.
 * Only "user" works here: role "system" is rejected ("Invalid role: system").
 */
export function textItem(text: string, role: "user" = "user"): ClientEvent {
  return {
    type: "conversation.item.create",
    item: { type: "message", role, content: [{ type: "input_text", text }] },
  };
}

export interface ResponseOverrides {
  /** System prompt for this response only. */
  instructions?: string;
  max_output_tokens?: number;
  temperature?: number;
  metadata?: Record<string, string>;
}

/** Start a response. Forgetting this after a text item is a silent no-op. */
export function responseCreate(overrides?: ResponseOverrides): ClientEvent {
  return overrides ? { type: "response.create", response: overrides } : { type: "response.create" };
}

/** Stop the response in progress. It ends with response.done, status "cancelled" (probed 2026-10-24). */
export function responseCancel(): ClientEvent {
  return { type: "response.cancel" };
}

/** One chunk of PCM16 24 kHz audio, base64. ~100 ms per chunk. */
export function inputAudioAppend(base64: string): ClientEvent {
  return { type: "input_audio_buffer.append", audio: base64 };
}

/**
 * Turn the buffered audio into a user item. Only valid with turn_detection
 * null; does not start a response. Rejected (voice_output_task_ongoing) while
 * this session is generating.
 */
export function inputAudioCommit(): ClientEvent {
  return { type: "input_audio_buffer.commit" };
}

export function inputAudioClear(): ClientEvent {
  return { type: "input_audio_buffer.clear" };
}

/** `output` must be a string: JSON.stringify structured results. */
export function functionCallOutput(callId: string, output: string): ClientEvent {
  return {
    type: "conversation.item.create",
    item: { type: "function_call_output", call_id: callId, output },
  };
}

/** Forget everything in an assistant item past the first `audioEndMs` actually played. */
export function truncateItem(itemId: string, audioEndMs: number): ClientEvent {
  return {
    type: "conversation.item.truncate",
    item_id: itemId,
    content_index: 0,
    audio_end_ms: Math.round(audioEndMs),
  };
}
