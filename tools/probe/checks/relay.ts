import { SERVER, inputAudioCommit, responseCreate, textItem } from "../../../src/realtime/events";
import { buildSessionConfig } from "../../../src/realtime/sessionConfig";
import { CHUNK_SAMPLES, appendChunk, msOf, sleep, writeWav } from "../audio";
import type { CheckContext } from "../harness";
import { concat, decodeB64Pcm } from "../session";

/**
 * Q: Can one agent hear another's REAL audio? Alice speaks; her output audio is
 *    forwarded into Bob's input buffer at playback pace (what a listener would
 *    hear), then committed, and Bob replies.
 */
export async function relayCheck(ctx: CheckContext): Promise<void> {
  const alice = await ctx.open(
    "alice",
    buildSessionConfig({
      instructions: "You are Alice, in a live voice meeting with Bob. Speak in one or two short sentences.",
      modality: "audio",
    }),
  );
  const bob = await ctx.open(
    "bob",
    buildSessionConfig({
      instructions: "You are Bob, in a live voice meeting with Alice. Reply to what you just heard in one short sentence.",
      modality: "audio",
      transcribe: true,
    }),
  );

  // Forward Alice's audio to Bob in real time: every 100 ms, pick up whatever
  // new audio deltas she has produced and send Bob the next 100 ms of it.
  // Her audio arrives ~6x faster than real time, so this is a playback queue.
  const from = alice.mark();
  let cursor = from;
  let pending: Int16Array = new Int16Array(0);
  let forwarded = 0;
  let aliceDone = false;
  const forwarder = (async () => {
    for (;;) {
      const fresh = alice.events
        .slice(cursor)
        .filter((t) => t.evt.type === SERVER.outputAudioDelta)
        .map((t) => decodeB64Pcm(String(t.evt.delta)));
      cursor = alice.events.length;
      if (fresh.length) pending = concat([pending, ...fresh]);
      if (pending.length >= CHUNK_SAMPLES || (aliceDone && pending.length > 0)) {
        const chunk = pending.subarray(0, Math.min(CHUNK_SAMPLES, pending.length));
        appendChunk(bob, chunk);
        forwarded += chunk.length;
        pending = pending.subarray(chunk.length);
      } else if (aliceDone) {
        return;
      }
      await sleep(100);
    }
  })();

  alice.send(textItem("Tell Bob that the design review moved to Thursday at 3 pm, and ask whether that works for him."));
  alice.send(responseCreate());
  const said = await alice.collectResponse(from);
  aliceDone = true;
  await forwarder;

  writeWav(ctx.capturePath("-alice.wav"), said.audio);
  ctx.info(`Alice said ${JSON.stringify(said.transcript)} (${msOf(said.audio.length)} ms audio, forwarded ${msOf(forwarded)} ms to Bob)`);

  const commitFrom = bob.mark();
  bob.send(inputAudioCommit());
  const ack = await bob.waitFor((e) => e.type === SERVER.audioCommitted || e.type === SERVER.error, 10_000, "committed", commitFrom);
  if (ack.evt.type === SERVER.error) {
    ctx.fail(`Bob's commit rejected: ${JSON.stringify(ack.evt.error)}`);
    return;
  }
  const before = bob.mark();
  bob.send(responseCreate());
  const reply = await bob.collectResponse(before);
  const heard = await bob
    .waitType(SERVER.transcriptionCompleted, 15_000, commitFrom)
    .then((t) => String(t.evt.transcript ?? ""))
    .catch(() => null);

  ctx.info(`Bob's transcription of Alice's audio: ${JSON.stringify(heard)}`);
  ctx.info(`Bob replied ${JSON.stringify(reply.transcript)}; first audio ${reply.firstAudioAt ? Math.round(reply.firstAudioAt - reply.createdAt) : "—"} ms after response.create`);
  ctx.expect(heard !== null && /thursday/i.test(heard), "Bob's session transcribed Alice's real audio (mentions Thursday)");
  ctx.expect(reply.audio.length > 0 && reply.transcript.length > 0, "Bob produced a spoken reply to Alice's audio");
}
