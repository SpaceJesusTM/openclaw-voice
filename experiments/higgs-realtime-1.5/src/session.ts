import { object, resolvedModel, toolOutput, userText, type WireEvent } from './protocol';
import { dispatchTool, parseArguments, type ToolHandler, type ToolResult } from './tools';

export interface ToolTrace {
  call_id: string; name: string; arguments: unknown; result?: ToolResult;
  status: 'running' | 'completed' | 'error' | 'duplicate' | 'server'; ms?: number;
}
interface Hooks {
  send: (event: WireEvent) => void;
  event?: (event: WireEvent) => void;
  ready?: (model: string, session: unknown) => void;
  error: (message: string) => void;
  tool?: (trace: ToolTrace) => void;
  idle?: () => void;
}
type Batch = { promises: Promise<WireEvent>[]; flushed: boolean };

/** Transport-independent session + exactly-once local function execution. */
export class Session {
  ready = false;
  private disposed = false;
  private handled = new Set<string>();
  private batches = new Map<string, Batch>();
  private metadata = new Map<string, Record<string, unknown>>();
  private active = new Set<string>();
  private currentResponse = '';
  private awaitingResponse = false;
  private userSpeaking = false;
  private pendingContinuation = false;
  private pendingBatches = 0;

  constructor(private hooks: Hooks, private handler: ToolHandler = dispatchTool) {}

  get busy(): boolean {
    return this.active.size > 0 || this.awaitingResponse || this.pendingBatches > 0 || this.pendingContinuation || this.userSpeaking;
  }

  dispose(): void { this.disposed = true; this.ready = false; }

  sendText(text: string, id?: string): void {
    if (!this.ready || this.disposed) throw new Error('Wait for a verified session acknowledgement');
    if (this.busy) throw new Error('Wait for the current turn, or speak to interrupt');
    this.hooks.send(userText(text, id));
    this.continueResponse();
  }

  receive(event: WireEvent): void {
    if (this.disposed) return;
    try {
      if (event.type === 'session.created' || event.type === 'session.updated') {
        const model = resolvedModel(event);
        this.ready = true;
        this.hooks.ready?.(model, event.session);
      }
      if (event.type === 'error') throw new Error(`Boson API error: ${JSON.stringify(event.error ?? event)}`);
      if (event.type === 'response.created') {
        this.currentResponse = String(object(event.response).id ?? event.response_id ?? 'current');
        this.active.add(this.currentResponse);
        this.awaitingResponse = false;
        // A server-VAD turn incorporates results already delivered before it began.
        this.pendingContinuation = false;
      }
      if (event.type === 'input_audio_buffer.speech_started') this.userSpeaking = true;
      if (event.type === 'input_audio_buffer.speech_stopped') this.userSpeaking = false;
      if (event.type === 'response.output_item.added') {
        const item = object(event.item);
        if (typeof item.call_id === 'string') this.metadata.set(item.call_id, item);
      }
      if (event.type === 'response.function_call_arguments.done') {
        this.startCall(event, String(event.response_id ?? this.currentResponse));
      }
      if (event.type === 'response.done') {
        const response = object(event.response);
        const id = String(response.id ?? event.response_id ?? this.currentResponse);
        if (Array.isArray(response.output)) {
          for (const item of response.output) {
            if (object(item).type === 'function_call') this.startCall(object(item), id);
          }
        }
        this.active.delete(id);
        this.awaitingResponse = false;
        if (response.status === 'failed') throw new Error(`Response failed: ${JSON.stringify(response.status_details ?? response)}`);
        const batch = this.batches.get(id);
        if (batch && !batch.flushed) {
          batch.flushed = true;
          this.pendingBatches++;
          void this.flush(batch);
        } else this.maybeContinue();
      }
      this.hooks.event?.(event);
      this.notifyIdle();
    } catch (error) {
      this.ready = false;
      this.hooks.error(error instanceof Error ? error.message : String(error));
    }
  }

  private startCall(raw: Record<string, unknown>, responseId: string): void {
    const callId = typeof raw.call_id === 'string' ? raw.call_id : '';
    const call = { ...this.metadata.get(callId), ...raw };
    const name = typeof call.name === 'string' ? call.name : '';
    if (!callId || !name) throw new Error('Malformed function call: missing call_id or name');
    if (call.executor === 'server' || name === 'web_search') {
      this.hooks.tool?.({ call_id: callId, name, arguments: call.arguments, status: 'server' });
      return;
    }
    if (this.handled.has(callId)) {
      this.hooks.tool?.({ call_id: callId, name, arguments: call.arguments, status: 'duplicate' });
      return;
    }
    this.handled.add(callId); // Mark BEFORE awaiting; concurrent duplicates cannot reenter.
    let batch = this.batches.get(responseId);
    if (!batch) { batch = { promises: [], flushed: false }; this.batches.set(responseId, batch); }
    const started = performance.now();
    this.hooks.tool?.({ call_id: callId, name, arguments: call.arguments, status: 'running' });
    batch.promises.push((async () => {
      let result: ToolResult;
      let args: unknown = call.arguments;
      try {
        args = parseArguments(call.arguments);
        result = await this.handler(name, args);
      } catch (error) {
        result = { summary: 'Local function failed.', error: error instanceof Error ? error.message : String(error) };
      }
      this.hooks.tool?.({ call_id: callId, name, arguments: args, result,
        status: result.error ? 'error' : 'completed', ms: Math.round(performance.now() - started) });
      return toolOutput(callId, result);
    })());
  }

  private async flush(batch: Batch): Promise<void> {
    try {
      const outputs = await Promise.all(batch.promises);
      if (this.disposed) return; // Handler completes; never inject stale results into a new socket.
      for (const output of outputs) this.hooks.send(output);
      this.pendingContinuation = true;
      this.maybeContinue();
    } catch (error) {
      this.hooks.error(`Tool delivery failed: ${String(error)}`);
    } finally { this.pendingBatches--; this.notifyIdle(); }
  }

  private maybeContinue(): void {
    if (this.pendingContinuation && !this.userSpeaking && !this.active.size && !this.awaitingResponse) {
      this.pendingContinuation = false;
      this.continueResponse();
    }
  }
  private continueResponse(): void {
    this.awaitingResponse = true;
    this.hooks.send({ type: 'response.create' });
  }
  private notifyIdle(): void { if (!this.busy && this.ready && !this.disposed) this.hooks.idle?.(); }
}
