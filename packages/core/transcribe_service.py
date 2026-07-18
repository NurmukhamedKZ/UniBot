"""Transcription service for UniX lecture videos using OpenAI GPT-4o Transcribe"""
import asyncio
import logging
import os
from io import BytesIO

import requests
from dotenv import load_dotenv
from openai import OpenAI

from playwright.async_api import async_playwright

import app.db.queries as db

load_dotenv()

logger = logging.getLogger(__name__)

LOGIN_URL = "https://uni-x.almv.kz/platform/login"


class TranscribeService:
    def __init__(self, pool) -> None:
        self.email = os.getenv("UNIX_EMAIL", "")
        self.password = os.getenv("UNIX_PASSWORD", "")
        self.pool = pool
        self._openai = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    async def get_or_create_transcription(self, lesson_id: str) -> str:
        """Return cached transcription or generate a new one."""
        if self.pool:
            row = await db.get_unix_video(self.pool, lesson_id)
            if row and row["video_transcribe"]:
                logger.info("lesson_id=%s | Returning cached transcription", lesson_id)
                return row["video_transcribe"]

        video_url = await self._get_video_url(lesson_id)
        logger.info("lesson_id=%s | Downloading video from %s", lesson_id, video_url)

        audio_data = await asyncio.to_thread(self._download_video, video_url)
        logger.info("lesson_id=%s | Transcribing with GPT-4o mini Transcribe", lesson_id)

        text = await asyncio.to_thread(self._transcribe, audio_data)
        logger.info("lesson_id=%s | Transcription complete (%d chars)", lesson_id, len(text))

        if self.pool:
            await db.save_transcription(self.pool, lesson_id, text)

        return text

    async def _get_video_url(self, lesson_id: str) -> str:
        """Log into UniX, navigate to lesson page, and extract the 360p video source URL."""
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            await page.goto(LOGIN_URL)
            await page.locator("input[type='email']").fill(self.email)
            await page.locator("input[type='password']").fill(self.password)
            await page.locator("button[type='submit']").click()
            await asyncio.sleep(1)

            lesson_url = f"https://uni-x.almv.kz/platform/lessons/{lesson_id}"
            await page.goto(lesson_url)

            video_url = await page.locator("source[size='360']").get_attribute("src")

            await browser.close()
            return video_url

    def _download_video(self, video_url: str) -> BytesIO:
        """Download video bytes (runs in a thread)."""
        resp = requests.get(video_url, timeout=300)
        resp.raise_for_status()
        buf = BytesIO(resp.content)
        buf.name = "lecture.mp4"
        return buf

    def _transcribe(self, audio_data: BytesIO) -> str:
        """Call OpenAI GPT-4o Transcribe (runs in a thread)."""
        result = self._openai.audio.transcriptions.create(
            model="gpt-4o-mini-transcribe",
            file=audio_data
        )
        return result.text
