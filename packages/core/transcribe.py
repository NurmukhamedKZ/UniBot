"""Транскрибация лекции uninet: аудио из Kinescope HLS (ffmpeg) → OpenAI."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from openai import AsyncOpenAI

from packages.core.uninet import kinescope_audio_url
from packages.shared.config import OPENAI_API_KEY

MODEL = "gpt-4o-mini-transcribe"


async def transcribe(kid: str) -> str:
    audio_url = await kinescope_audio_url(kid)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "audio.mp3"
        # Моно 16 kHz 32 kbps: ~14 МБ/час. ponytail: лимит OpenAI 25 МБ ≈ 100 мин лекции,
        # для более длинных видео — резать на куски через `-f segment`.
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-loglevel", "error", "-y",
            "-headers", "Referer: https://kinescope.io/\r\n",
            "-i", audio_url, "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k", str(out),
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {err.decode()[-500:]}")
        with out.open("rb") as f:
            result = await AsyncOpenAI(api_key=OPENAI_API_KEY).audio.transcriptions.create(model=MODEL, file=f)
    return result.text
