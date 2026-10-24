export const PREVIEW_MODEL = 'higgs-realtime-1.5-preview';
export const AUDIO_RATE = 24_000;
export type WireEvent = { type: string; [key: string]: unknown };
export type ObjectValue = Record<string, unknown>;

export function object(value: unknown): ObjectValue {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as ObjectValue : {};
}

export function parseEvent(raw: string): WireEvent {
  const value = object(JSON.parse(raw));
  if (typeof value.type !== 'string' || !value.type) throw new Error('Event has no type');
  return value as WireEvent;
}

export function requirePreview(model = PREVIEW_MODEL): string {
  if (model !== PREVIEW_MODEL) {
    throw new Error(`Required model is ${PREVIEW_MODEL}; rejected configured model: ${model || '(empty)'}. No fallback.`);
  }
  return model;
}

export function websocketUrl(model: string): string {
  return `wss://api.boson.ai/v1/realtime?model=${encodeURIComponent(requirePreview(model))}`;
}

export function resolvedModel(event: WireEvent): string {
  const model = object(event.session).model;
  if (model !== PREVIEW_MODEL) {
    throw new Error(`Server acknowledged model ${typeof model === 'string' ? model : '(missing)'}; required ${PREVIEW_MODEL}. Connection stopped; no fallback.`);
  }
  return model;
}

export function userText(text: string, id?: string): WireEvent {
  return { type: 'conversation.item.create', item: {
    ...(id ? { id } : {}), type: 'message', role: 'user', content: [{ type: 'input_text', text }],
  } };
}

export function toolOutput(callId: string, result: unknown): WireEvent {
  return { type: 'conversation.item.create', item: {
    type: 'function_call_output', call_id: callId, output: JSON.stringify(result),
  } };
}

export function truncate(itemId: string, milliseconds: number, contentIndex = 0): WireEvent {
  return { type: 'conversation.item.truncate', item_id: itemId,
    content_index: contentIndex, audio_end_ms: Math.max(0, Math.floor(milliseconds)) };
}

export function closeMessage(code: number, reason: string): string {
  const detail: Record<number, string> = {
    1000: 'Session closed', 1006: 'Network or WebSocket handshake failed',
    1013: 'Server at concurrency limit', 3000: 'Authentication failed or client secret expired',
    4429: 'Billing entitlement refused',
  };
  return `${detail[code] ?? 'Socket closed'} (${code})${reason ? `: ${reason}` : ''}`;
}

/** Log metadata instead of raw microphone/speaker recordings or credentials. */
export function debugEvent(event: WireEvent): WireEvent {
  if (event.type === 'response.output_audio.delta') {
    return { ...event, delta: `[${String(event.delta ?? '').length} base64 audio characters]` };
  }
  if (event.type === 'input_audio_buffer.append') {
    return { type: event.type, audio: `[${String(event.audio ?? '').length} base64 audio characters]` };
  }
  if (event.type === 'session.created' || event.type === 'session.updated') {
    const { client_secret: _secret, ...session } = object(event.session);
    return { ...event, session };
  }
  return event;
}
