import { SERVER, responseCreate, textItem } from "../../../src/realtime/events";
import { buildSessionConfig } from "../../../src/realtime/sessionConfig";
import { msOf } from "../audio";
import type { CheckContext } from "../harness";
import { b64Samples, stripBig } from "../session";

/**
 * Q: Does a reply's text arrive ahead of the point where its audio would be
 *    HEARD? (Needed to judge a sentence before the listener hears it, and to
 *    time a cut-off.) Also: what does the `.length` extension event carry?
 */
export async function timingCheck(ctx: CheckContext): Promise<void> {
  const s = await ctx.open("timing", buildSessionConfig({ instructions: "You are a helpful voice assistant.", modality: "audio" }));
  const before = s.mark();
  s.send(textItem("In four or five sentences, explain how a ring buffer works."));
  s.send(responseCreate());
  const resp = await s.collectResponse(before);
  const evs = s.since(before);

  const audioMs = msOf(resp.audio.length);
  const firstAudio = resp.firstAudioAt!;
  const lastAudio = evs.filter((t) => t.evt.type === SERVER.outputAudioDelta).at(-1)!.at;
  ctx.info(`${audioMs} ms of audio delivered in ${Math.round(lastAudio - firstAudio)} ms (${(audioMs / Math.max(1, lastAudio - firstAudio)).toFixed(1)}x real time)`);

  // Playback starts at the first audio chunk and runs at real time. A text
  // fragment is "early" if it arrives before the audio it describes would be
  // heard. Without per-fragment durations, the best proxy is: how much audio
  // had been delivered (and so could be queued) when each fragment arrived,
  // versus how much would have been played by then.
  let delivered = 0;
  let chars = 0;
  console.log("\n    t(ms)  text chars  audio delivered  audio played  fragment");
  for (const { at, evt } of evs) {
    if (evt.type === SERVER.outputAudioDelta) delivered += b64Samples(String(evt.delta));
    if (evt.type === SERVER.audioTranscriptDelta) {
      const d = String(evt.delta);
      chars += d.length;
      const played = Math.max(0, at - firstAudio);
      console.log(
        `  ${Math.round(at - resp.createdAt).toString().padStart(6)}  ${chars.toString().padStart(10)}  ${(msOf(delivered) + "ms").padStart(15)}  ${(Math.round(played) + "ms").padStart(12)}  ${JSON.stringify(d.slice(0, 50))}`,
      );
    }
  }
  console.log();

  const transcriptDone = evs.find((t) => t.evt.type === SERVER.audioTranscriptDone)?.at;
  if (transcriptDone !== undefined) {
    const lead = firstAudio + audioMs - transcriptDone;
    ctx.info(`full transcript arrived ${Math.round(transcriptDone - resp.createdAt)} ms after response.create; it would finish PLAYING at ${Math.round(firstAudio + audioMs - resp.createdAt)} ms, so the text is ${Math.round(lead)} ms ahead of the end of playback`);
    ctx.expect(lead > 0, "complete reply text is available before the listener finishes hearing it");
  }

  const lengths = evs.filter((t) => t.evt.type === SERVER.audioTranscriptLength);
  ctx.info(`${lengths.length} response.output_audio_transcript.length events`);
  for (const t of lengths.slice(0, 4)) ctx.info(`  .length @${Math.round(t.at - resp.createdAt)}ms: ${JSON.stringify(stripBig(t.evt))}`);
}
