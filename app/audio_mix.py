"""20ms stereo 48kHz signed-16-bit PCM mix utilities (no dependencies)."""

from array import array

FRAME_BYTES = 3840  # 48000 Hz * .02 seconds * 2 channels * 2 bytes
SILENCE = bytes(FRAME_BYTES)


def normalize(frame: bytes) -> bytes:
    if len(frame) >= FRAME_BYTES:
        return frame[:FRAME_BYTES]
    return frame + bytes(FRAME_BYTES - len(frame))


def mix_pcm(music: bytes, speech: bytes, music_gain: float, speech_gain: float = 1.0) -> bytes:
    """Mix samples, duck the music, and clip safely into int16 range."""
    music_samples = array("h")
    speech_samples = array("h")
    music_samples.frombytes(normalize(music))
    speech_samples.frombytes(normalize(speech))
    mixed = array("h")
    for a, b in zip(music_samples, speech_samples):
        sample = round(a * music_gain + b * speech_gain)
        mixed.append(max(-32768, min(32767, sample)))
    return mixed.tobytes()

