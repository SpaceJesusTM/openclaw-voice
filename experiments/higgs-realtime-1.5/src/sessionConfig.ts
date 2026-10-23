import { AUDIO_RATE, requirePreview, type WireEvent } from './protocol';
import { plannerDefinition } from './tools';

export interface SessionOptions { model: string; voice: string; webSearch: boolean }

export function sessionConfig(options: SessionOptions): WireEvent {
  return { type: 'session.update', session: {
    model: requirePreview(options.model),
    instructions: 'You are a concise voice assistant testing a demo planner. Use get_demo_planner for planner questions and answer from its result. Speak one or two sentences without markdown.' +
      (options.webSearch ? ' Search the web for current or uncertain public facts.' : ''),
    output_modalities: ['audio'],
    audio: {
      input: { format: { type: 'audio/pcm', rate: AUDIO_RATE },
        turn_detection: { type: 'server_vad' },
        transcription: { model: 'higgs-stt-3.1', language: 'en' } },
      output: { format: { type: 'audio/pcm', rate: AUDIO_RATE }, voice: options.voice },
    },
    tools: [plannerDefinition, ...(options.webSearch ? [{ type: 'web_search' }] : [])],
    tool_choice: 'auto',
  } };
}
