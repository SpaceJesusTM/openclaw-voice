"""Test-only WAV microphone, using aiortc MediaPlayer for decoding and pacing.

Keeps one continuous 48 kHz RTP clock when switching between silence and a file.
The bot itself uses only Pipecat's native capture/playback and Boson service.
"""

import asyncio
import time
from fractions import Fraction

from aiortc import AudioStreamTrack
from aiortc.contrib.media import MediaPlayer
from aiortc.mediastreams import MediaStreamError
from av import AudioFrame


class WavMicrophone(AudioStreamTrack):
    def __init__(self):
        super().__init__()
        self.player = None
        self.samples = 0
        self.started = None

    def play(self, path):
        if self.player:
            self.player.audio.stop()
        self.player = MediaPlayer(str(path))
        if self.player.audio is None:
            raise ValueError("WAV input has no audio track")

    async def recv(self):
        if self.started is None:
            self.started = time.monotonic()
        await asyncio.sleep(max(0, self.started + self.samples / 48000 - time.monotonic()))
        frame = None
        if self.player:
            try:
                frame = await self.player.audio.recv()
            except MediaStreamError:
                self.player.audio.stop()
                self.player = None
        if frame is None:
            frame = AudioFrame(format="s16", layout="stereo", samples=960)
            for plane in frame.planes:
                plane.update(bytes(plane.buffer_size))
        frame.pts, frame.time_base, frame.sample_rate = self.samples, Fraction(1, 48000), 48000
        self.samples += frame.samples
        return frame

    def stop(self):
        if self.player:
            self.player.audio.stop()
            self.player = None
        super().stop()
