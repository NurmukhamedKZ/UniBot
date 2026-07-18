import asyncio
import html as html_module
import logging
from datetime import datetime, timezone
from langchain.messages import HumanMessage, SystemMessage
from playwright.async_api import async_playwright, Page
from langchain_openai import ChatOpenAI
from typing import Literal, Callable, Awaitable
from dotenv import load_dotenv
import os
from pydantic import BaseModel

import app.db.queries as db
from app.utils.log_utils import log_duration

logger = logging.getLogger(__name__)


class UnixSiteUnavailableError(Exception):
    """Raised when the UniX site appears to be down (video duration stuck at 00:00)."""


_UNIX_API_BASE = "https://uni-x.almv.kz/api"
_FAST_WATCH_MAGIC_LESSON_ID = 15379

# Base64-encoded SVG of the grey circle icon shown on unwatched lessons on UniX.
_GREY_LESSON_SVG_B64 = "PHN2ZyB3aWR0aD0iMTYiIGhlaWdodD0iMTciIHZpZXdCb3g9IjAgMCAxNiAxNyIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHBhdGggZD0iTTE2IDguNUMxNiAxMi45MTgzIDEyLjQxODMgMTYuNSA4IDE2LjVDMy41ODE3MiAxNi41IDAgMTIuOTE4MyAwIDguNUMwIDQuMDgxNzIgMy41ODE3MiAwLjUgOCAwLjVDMTIuNDE4MyAwLjUgMTYgNC4wODE3MiAxNiA4LjVaTTEuOTkyIDguNUMxLjk5MiAxMS44MTgxIDQuNjgxODcgMTQuNTA4IDggMTQuNTA4QzExLjMxODEgMTQuNTA4IDE0LjAwOCAxMS44MTgxIDE0LjAwOCA4LjVDMTQuMDA4IDUuMTgxODcgMTEuMzE4MSAyLjQ5MiA4IDIuNDkyQzQuNjgxODcgMi40OTIgMS45OTIgNS4xODE4NyAxLjk5MiA4LjVaIiBmaWxsPSIjQUZCN0NBIiBmaWxsLW9wYWNpdHk9IjAuNSIvPgo8L3N2Zz4K"


def extract_lesson_ids_from_hrefs(hrefs: list[str | None]) -> list[str]:
    """Extract deduplicated numeric lesson IDs from /platform/lessons/<id> hrefs."""
    lesson_ids: list[str] = []
    for href in hrefs:
        if href and "/lessons/" in href:
            lid = href.rstrip("/").split("/")[-1]
            if lid.isdigit():
                lesson_ids.append(lid)
    return list(dict.fromkeys(lesson_ids))


class TestAnswer(BaseModel):
    answer_index: Literal[0,1,2,3]


class UniXWatcher:
    def __init__(
        self,
        email: str,
        password: str,
        pool,
        telegram_id: int,
        log_callback: Callable[[str], Awaitable[None]] | None = None,
        edit_callback: Callable[[str], Awaitable[None]] | None = None,
        reset_video_callback: Callable[[], Awaitable[None]] | None = None,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        self.email = email
        self.password = password
        self.pool = pool
        self.telegram_id = telegram_id
        self.log_callback = log_callback
        self.edit_callback = edit_callback
        self.reset_video_callback = reset_video_callback
        self.stop_event = stop_event
        self.current_lesson_id: str = ""
        self.lesson_start_time: float = 0.0
        self._video_watch_token: str | None = None
        self._llm = ChatOpenAI(
            model="gpt-5.4-mini",
            api_key=os.getenv("OPENAI_API_KEY"),
            reasoning={"effort": "high"}
        ).with_structured_output(TestAnswer)

    async def _log(self, msg: str) -> None:
        logger.info("telegram_id=%s | %s", self.telegram_id, msg)
        if self.log_callback:
            await self.log_callback(msg)

    async def _edit_video(self, msg: str) -> None:
        if self.edit_callback:
            await self.edit_callback(msg)

    async def _reset_video_msg(self) -> None:
        if self.reset_video_callback:
            await self.reset_video_callback()

    def _format_qa_card(
        self,
        q_number: int,
        total: int,
        question: str,
        options: list[str],
        selected_index: int,
    ) -> str:
        """Build a formatted HTML Q&A card string to send as a Telegram message."""
        letter = chr(ord("A") + selected_index)
        lines = ["────────────────", f"📝 <b>Q{q_number} / {total}</b>  {html_module.escape(question)}"]
        for i, opt in enumerate(options):
            prefix = "☑" if i == selected_index else "○"
            lines.append(f"  {prefix} {chr(ord('A') + i)}  {html_module.escape(opt)}")
        return "\n".join(lines)

    def _elapsed(self) -> str:
        """Return elapsed time since lesson start as M:SS or H:MM:SS string."""
        secs = int(asyncio.get_running_loop().time() - self.lesson_start_time)
        h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
        if h:
            return f"{h}:{m:02}:{s:02}"
        return f"{m}:{s:02}"

    @staticmethod
    def _lesson_url(lesson_id: str) -> str:
        return f"https://uni-x.almv.kz/platform/lessons/{lesson_id}"

    async def _call_unix_api(
        self,
        page: Page,
        path: str,
        method: str = "GET",
        body: dict | None = None,
    ) -> dict:
        return await page.evaluate(
            """async ({ url, method, body }) => {
                try {
                    const rawAuth = localStorage.getItem("user-store") ?? "{}";
                    const authToken = JSON.parse(rawAuth)?.token;
                    if (!authToken) {
                        return { ok: false, status: 0, error: "missing-auth-token", data: null };
                    }

                    const headers = {
                        authorization: `Bearer ${authToken}`,
                    };
                    if (body !== null) {
                        headers["content-type"] = "application/json";
                    }

                    const response = await fetch(url, {
                        method,
                        headers,
                        body: body === null ? undefined : JSON.stringify(body),
                    });

                    const text = await response.text();
                    let data = null;
                    try {
                        data = text ? JSON.parse(text) : null;
                    } catch {
                        data = text;
                    }

                    return {
                        ok: response.ok,
                        status: response.status,
                        error: null,
                        data,
                    };
                } catch (error) {
                    return {
                        ok: false,
                        status: 0,
                        error: String(error),
                        data: null,
                    };
                }
            }""",
            {
                "url": f"{_UNIX_API_BASE}{path}",
                "method": method,
                "body": body,
            },
        )

    @staticmethod
    def _extract_video_duration(lesson_data: dict) -> int:
        for key in ("videoDurationEn", "videoDurationKz", "videoDurationRu"):
            value = lesson_data.get(key)
            if isinstance(value, (int, float)) and value > 0:
                return int(value)
        return 100

    @log_duration
    async def _fetch_lesson_api_data(self, page: Page, lesson_id: str) -> dict | None:
        result = await self._call_unix_api(page, f"/lessons/{lesson_id}")
        if result.get("ok") and isinstance(result.get("data"), dict):
            return result["data"]

        logger.warning(
            "telegram_id=%s | Failed to fetch lesson data for %s: status=%s error=%s",
            self.telegram_id,
            lesson_id,
            result.get("status"),
            result.get("error"),
        )
        return None

    @log_duration
    async def _generate_fast_watch_token(self, page: Page) -> str | None:
        if self._video_watch_token:
            return self._video_watch_token

        client_timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        base_body = {
            "lessonId": _FAST_WATCH_MAGIC_LESSON_ID,
            "currentSpeed": 1,
            "clientTimestamp": client_timestamp,
            "lang": "EN",
        }

        start_result = await self._call_unix_api(
            page,
            "/validates/watched",
            method="POST",
            body={**base_body, "event": "video-start"},
        )
        start_data = start_result.get("data") if isinstance(start_result.get("data"), dict) else {}
        start_token = start_data.get("token")
        if not start_result.get("ok") or not isinstance(start_token, str) or not start_token:
            logger.warning(
                "telegram_id=%s | Fast-watch token start failed: status=%s error=%s",
                self.telegram_id,
                start_result.get("status"),
                start_result.get("error"),
            )
            return None

        end_result = await self._call_unix_api(
            page,
            "/validates/watched",
            method="POST",
            body={**base_body, "event": "video-end", "token": start_token},
        )
        end_data = end_result.get("data") if isinstance(end_result.get("data"), dict) else {}
        end_token = end_data.get("token")
        if not end_result.get("ok") or not isinstance(end_token, str) or not end_token:
            logger.warning(
                "telegram_id=%s | Fast-watch token end failed: status=%s error=%s",
                self.telegram_id,
                end_result.get("status"),
                end_result.get("error"),
            )
            return None

        self._video_watch_token = end_token
        return self._video_watch_token

    @log_duration
    async def _mark_lesson_watched_fast(self, page: Page, lesson_id: str, duration: int) -> bool:
        token = await self._generate_fast_watch_token(page)
        if not token:
            return False

        payload = {
            "token": token,
            "videoDuration": max(1, int(duration)),
            "videoWatched": max(1, int(duration)),
        }
        result = await self._call_unix_api(
            page,
            f"/lessons/{lesson_id}/watched",
            method="POST",
            body=payload,
        )
        if result.get("ok"):
            return True

        if result.get("status") in (400, 401):
            self._video_watch_token = None
            token = await self._generate_fast_watch_token(page)
            if not token:
                return False

            retry_result = await self._call_unix_api(
                page,
                f"/lessons/{lesson_id}/watched",
                method="POST",
                body={**payload, "token": token},
            )
            if retry_result.get("ok"):
                return True

            logger.warning(
                "telegram_id=%s | Fast-watch retry failed for lesson %s: status=%s error=%s",
                self.telegram_id,
                lesson_id,
                retry_result.get("status"),
                retry_result.get("error"),
            )
            return False

        logger.warning(
            "telegram_id=%s | Fast-watch request failed for lesson %s: status=%s error=%s",
            self.telegram_id,
            lesson_id,
            result.get("status"),
            result.get("error"),
        )
        return False

    async def _open_test_entry(self, page: Page) -> None:
        go_to_test_button = page.locator("button.bg-\\[\\#FFDD33\\]", has_text="Go to test")
        if await go_to_test_button.is_visible():
            await go_to_test_button.click()
            await page.wait_for_timeout(2000)
            return

        test_tab = page.locator("a").filter(has_text="Test task").first
        if await test_tab.is_visible():
            await test_tab.click()
            await page.wait_for_timeout(5000)

    @log_duration
    async def _ensure_lesson_watched_fast(self, page: Page, lesson_id: str) -> bool:
        lesson_data = await self._fetch_lesson_api_data(page, lesson_id)
        if not lesson_data:
            return False

        if lesson_data.get("isWatched") is True:
            logger.info("telegram_id=%s | Lesson %s already marked watched", self.telegram_id, lesson_id)
            await page.goto(self._lesson_url(lesson_id))
            await page.wait_for_timeout(3000)
            return True

        await self._edit_video(f"📹 Fast-watching lesson #{lesson_id}...")
        if not await self._mark_lesson_watched_fast(
            page,
            lesson_id,
            self._extract_video_duration(lesson_data),
        ):
            return False

        logger.info("telegram_id=%s | Lesson %s marked watched via fast-watch", self.telegram_id, lesson_id)
        await page.goto(self._lesson_url(lesson_id))
        await page.wait_for_timeout(3000)
        return True

    @log_duration
    async def _login(self, page: Page, email: str, password: str, max_retries: int = 3) -> bool:
        """Log into UniX platform with retry logic. Returns True if successful."""
        LOGIN_URL = "https://uni-x.almv.kz/platform/login"

        if not email:
            email = self.email

        if not password:
            password = self.password

        for _ in range(max_retries):
            try:
                await page.goto(LOGIN_URL, timeout=60000)

                if LOGIN_URL not in page.url:
                    return True

                await page.locator("input[type='email']").click()
                await page.locator("input[type='email']").fill(email)

                await page.locator("input[type='password']").click()
                await page.locator("input[type='password']").fill(password)

                await page.locator("button[type='submit']").click()

                await asyncio.sleep(1)

                if LOGIN_URL not in page.url:
                    return True
            except Exception:
                pass

        return False

    @log_duration
    async def _wait_for_video_completion(self, page, check_interval=10):
        completed_overlay = page.locator("div.video-overlay").filter(
            has_text="You have completed watching the video lesson"
        )

        while True:
            if self.stop_event and self.stop_event.is_set():
                try:
                    await page.locator("button[data-plyr='play']").click()
                except Exception:
                    pass
                return

            if await completed_overlay.is_visible():
                return

            current_time = await page.locator(".plyr__time--current").inner_text()
            duration = await page.locator(".plyr__time--duration").inner_text()
            await self._edit_video(
                f"📹 Watching lesson #{self.current_lesson_id}...\n⏱ {current_time} / {duration}"
            )

            if current_time == duration and duration != "00:00":
                break

            if self.stop_event:
                try:
                    await asyncio.wait_for(self.stop_event.wait(), timeout=check_interval)
                except asyncio.TimeoutError:
                    pass
            else:
                await asyncio.sleep(check_interval)

    async def _extract_question_and_answers(self, page):
        question = await page.locator('p.select-none span.text-unix-text-black, p.select-none span.dark\\:text-\\[\\#AFB7CA\\]').last.inner_text()
        answer_elements = await page.locator('.flex.flex-col.mt-5 > .flex.flex-row p.ml-4').all()
        answers = [await el.inner_text() for el in answer_elements]
        return {"question": question, "answers": answers}

    @log_duration
    async def _invoke_llm(self, question: str, answers: list[str], unix_video_id: str) -> Literal[0,1,2,3]:
        system_prompt = SystemMessage("You have to answer the question based on the provided question and answer options. Only respond with the index of the correct answer option (0-based).")
        user_prompt = HumanMessage(f"Question: {question}\nAnswer options:\n" + "\n".join([f"{i}: {a}" for i, a in enumerate(answers)]))

        output = await self._llm.ainvoke([system_prompt, user_prompt])

        question_id = await db.add_question_answer(
            pool=self.pool,
            unix_video_id=unix_video_id,
            question=question,
            answer1=answers[0],
            answer2=answers[1],
            answer3=answers[2],
            answer4=answers[3],
            llm_answer=str(answers[output.answer_index]),
        )

        return output.answer_index, question_id

    async def _click_answer(self, page: Page, answer_index: Literal[0,1,2,3]):
        """Click an answer option and then click Next. answer_index is 0-based."""
        answer_rows = await page.locator('.flex.flex-col.mt-5 > .flex.flex-row').all()

        if not answer_rows:
            return

        if answer_index >= len(answer_rows):
            answer_index = 0

        await answer_rows[answer_index].click()

        await page.wait_for_timeout(1000)

        next_button = page.locator('button:has-text("Next")').first
        if await next_button.is_visible():
            await next_button.click()

    async def _get_test_result(self, page: Page) -> str | None:
        """Wait for the result page and return a formatted score string, or None."""
        try:
            await page.wait_for_timeout(5000)
            await page.wait_for_selector("text=Test completed!", timeout=10000)
            await page.wait_for_timeout(5000)
            score_para = page.locator("p.mt-5.font-bold")
            spans = await score_para.locator("span").all()
            score = (await spans[0].inner_text()).strip()
            total = (await spans[1].inner_text()).strip()
            return f"{score} {total}"
        except Exception as e:
            return f"Error: {e}"

    @log_duration
    async def _do_test(self, page: Page, unix_video_id: str) -> list[str] | str:
        """Run the 5-question test, send a Q&A card after each answered question."""
        question_ids: list[str] = []
        total = 5

        error_count = 0
        while error_count <= 3:
            try:
                active_bubble = page.locator("div.relative.cursor-pointer.bg-\\[\\#0068FF\\]")

                text = await active_bubble.inner_text()
                current_question = int(text.strip())
                logger.debug("Q%d active", current_question)
                if current_question == 1:
                    question_ids = []

                test_data = await self._extract_question_and_answers(page)
                question = test_data["question"]
                answers = test_data["answers"]

                if len(answers) < 4:
                    logger.warning("Q%d: only %d answers found, skipping", current_question, len(answers))
                    await page.wait_for_timeout(3000)
                    error_count += 1
                    continue

                answer_index, question_id = await self._invoke_llm(
                    question, answers, unix_video_id=unix_video_id
                )
                question_ids.append(question_id)
                answer_index = max(0, min(answer_index, len(answers) - 1))
                await page.wait_for_timeout(3000)

                await self._click_answer(page, answer_index)

                card = self._format_qa_card(current_question, total, question, answers, answer_index)
                await self._log(card)
                if current_question == 5:
                    break
                await page.wait_for_timeout(3000)
            except Exception as e:
                return f"Error: {e}"
        return question_ids

    async def _start_and_watch_video(self, page: Page) -> None:
        # await page.wait_for_timeout(500)
        # await page.locator("button[data-plyr='settings']").first.click(timeout=5000)
        # await page.wait_for_timeout(500)
        # await page.locator("button[data-plyr='settings']").filter(has_text="Quality").click()
        # await page.wait_for_timeout(500)
        # await page.locator("button[value='360']").click()
        await page.wait_for_timeout(500)
        await page.locator("button.plyr__control--overlaid[aria-label='Play']").click()
        await self._wait_for_video_completion(page, check_interval=10)

    @log_duration
    async def watch_unix(self, page: Page, lesson_id: str) -> str | None:
        self.current_lesson_id = lesson_id
        self.lesson_start_time = asyncio.get_running_loop().time()
        await self._reset_video_msg()
        await page.goto(self._lesson_url(lesson_id))
        await page.wait_for_timeout(3000)

        is_not_available = await page.locator("text=Для доступа к следующему уроку").is_visible()

        if is_not_available:
            self.current_lesson_id = page.url.split("/")[-1]
            await self._log(f"😮 Lesson {lesson_id} is not available\n✅ Redirected to the lesson: {self.current_lesson_id}")
            await page.locator('img[src^="data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMjQiIGhlaWdodD0iMjQi"]').nth(2).click()
            lesson_id = self.current_lesson_id

        await db.ensure_unix_video_exists(self.pool, lesson_id)

        # Case 1: Test mid-session
        if await page.locator("text=Time for the test").first.is_visible():
            question_ids = await self._do_test(page, unix_video_id=lesson_id)
            if isinstance(question_ids, list):
                await db.add_unix_video(self.pool, lesson_id, question_ids)
                return await self._get_test_result(page)
            else:
                return question_ids

        # Case 2: No test tab → video-only lesson
        if not await page.locator("a").filter(has_text="Test task").first.is_visible():
            if not await self._ensure_lesson_watched_fast(page, lesson_id):
                logger.info(
                    "telegram_id=%s | Fast-watch unavailable for lesson %s, falling back to video playback",
                    self.telegram_id,
                    lesson_id,
                )
                await self._start_and_watch_video(page)
            return "video doesn't have test"

        await page.locator("a").filter(has_text="Test task").first.click()
        await page.wait_for_timeout(5000)

        # Case 3: Test already completed
        if await page.locator("text=Test completed!").is_visible():
            return await self._get_test_result(page)

        # Case 4: Video already watched
        if await page.locator("button").filter(has_text="Start the test").is_visible():
            await page.locator("button").filter(has_text="Start the test").click()
            question_ids = await self._do_test(page, unix_video_id=lesson_id)
            if isinstance(question_ids, list):
                await db.add_unix_video(self.pool, lesson_id, question_ids)
                return await self._get_test_result(page)
            else:
                return question_ids

        # Case 5: Watch video, then take test
        if await self._ensure_lesson_watched_fast(page, lesson_id):
            await self._open_test_entry(page)
        else:
            logger.info(
                "telegram_id=%s | Fast-watch unavailable for lesson %s, falling back to video playback",
                self.telegram_id,
                lesson_id,
            )
            await self._start_and_watch_video(page)

            if self.stop_event and self.stop_event.is_set():
                return None
            await page.locator("button.bg-\\[\\#FFDD33\\]", has_text="Go to test").click()

        await page.locator("button").filter(has_text="Start the test").click()
        question_ids = await self._do_test(page, unix_video_id=lesson_id)
        if isinstance(question_ids, list):
            await db.add_unix_video(self.pool, lesson_id, question_ids)
            return await self._get_test_result(page)
        else:
            return question_ids

    @log_duration
    async def watch_unix_videos(self, lesson_ids: list) -> list:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-dev-shm-usage",
                    "--disable-extensions",
                    "--disable-gpu",
                    "--no-sandbox",
                    "--disable-background-networking",
                    "--disable-default-apps",
                    "--mute-audio",
                    "--js-flags=--max-old-space-size=256",
                ])
            try:
                context = await browser.new_context(
                    ignore_https_errors=True,
                    viewport={"width": 1280, "height": 720}
                )

                page = await context.new_page()
                if not await self._login(page, self.email, self.password):
                    await self._log("❌ Login failed. Check your credentials with /setcredentials.")
                    if self.pool:
                        for lid in lesson_ids:
                            await db.mark_task_failed(self.pool, self.telegram_id, lid)
                    return []

                lesson_count = 0
                error_count = 0

                while lesson_count < len(lesson_ids) and error_count < 3:
                    lesson_id = lesson_ids[lesson_count]

                    if self.pool:
                        await db.mark_task_in_progress(self.pool, self.telegram_id, lesson_id)

                    if self.stop_event and self.stop_event.is_set():
                        await self._log("🛑 Stop requested, halting lesson queue.")
                        break

                    if self.pool:
                        can_watch, remaining = await db.can_watch_video(self.pool, self.telegram_id)
                        if not can_watch:
                            await self._log(
                                "⚠️ Лимит видео исчерпан!\n"
                                "Используй /upgrade, чтобы перейти на другой план.\n"
                                "Или пригласи друга /referral что бы получить +3 видео"
                            )
                            break

                    try:
                        result: str = await self.watch_unix(page, lesson_id)
                        elapsed = self._elapsed()

                        if result is None:
                            break

                        if "Error" in result:
                            await self._log(
                                f"⚠️ <b>Lesson #{self.current_lesson_id} — Error occured</b>\n"
                                f"Result: {html_module.escape(str(result))}\n\n"
                            )
                            error_count += 1
                            if self.pool:
                                await db.mark_task_failed(self.pool, self.telegram_id, self.current_lesson_id)
                            continue

                        elif result == "video doesn't have test":
                            await self._log(f"✅ <b>Lesson #{self.current_lesson_id} done</b> — no test\n⏱ {elapsed} total")
                            await db.add_unix_video(self.pool, self.current_lesson_id, [])
                            await db.add_user_video(self.pool, self.telegram_id, self.current_lesson_id)
                            await db.increment_watched_videos(self.pool, self.telegram_id)
                            if self.pool:
                                await db.decrement_video(self.pool, self.telegram_id)
                            if self.pool:
                                await db.mark_task_completed(self.pool, self.telegram_id, self.current_lesson_id)
                            lesson_count += 1
                            error_count = 0
                            continue

                        else:
                            try:
                                score_int = int(result.split()[0])
                            except (ValueError, IndexError):
                                score_int = -1

                            if score_int >= 3.5:
                                await self._log(f"✅ <b>Lesson #{self.current_lesson_id} done — {html_module.escape(str(result))}</b>\n⏱ {elapsed} total")
                                await db.add_user_video(self.pool, self.telegram_id, self.current_lesson_id)
                                await db.increment_watched_videos(self.pool, self.telegram_id)
                                if self.pool:
                                    await db.decrement_video(self.pool, self.telegram_id)
                                if self.pool:
                                    await db.mark_task_completed(self.pool, self.telegram_id, self.current_lesson_id)
                                lesson_count += 1
                                error_count = 0

                            elif 0 <= score_int < 3.5:
                                await self._log(
                                    f"⚠️ <b>Lesson #{self.current_lesson_id} — AI failed</b>\n"
                                    f"Score: {html_module.escape(str(result))}  (below passing)\n\n"
                                    "Stopped. Send lesson IDs again to retry."
                                )
                                if self.pool:
                                    await db.mark_task_failed(self.pool, self.telegram_id, self.current_lesson_id)
                                break

                            else:
                                await self._log(f"⚠️ <b>Lesson #{self.current_lesson_id}</b> — unexpected result: {html_module.escape(str(result))}, skipping")
                                error_count += 1
                                if self.pool:
                                    await db.mark_task_failed(self.pool, self.telegram_id, self.current_lesson_id)

                    except UnixSiteUnavailableError:
                        await self._log(
                            "⚠️ Сайт UniX сейчас не работает.\n"
                            "Попробуй снова позже."
                        )
                        if self.pool:
                            await db.mark_task_failed(self.pool, self.telegram_id, self.current_lesson_id)
                        break

                    except Exception as e:
                        await self._log(f"❌ Error on lesson #{self.current_lesson_id}: {html_module.escape(str(e))}")
                        error_count += 1
                        if self.pool:
                            await db.mark_task_failed(self.pool, self.telegram_id, self.current_lesson_id)
            finally:
                await context.close()
                await browser.close()
                logger.info("telegram_id=%s | Browser closed", self.telegram_id)

    def _extract_lesson_ids_from_hrefs(self, hrefs: list[str | None]) -> list[str]:
        """Extract deduplicated numeric lesson IDs from /platform/lessons/<id> hrefs."""
        lesson_ids: list[str] = []
        for href in hrefs:
            if href and "/lessons/" in href:
                lid = href.rstrip("/").split("/")[-1]
                if lid.isdigit():
                    lesson_ids.append(lid)
        return list(dict.fromkeys(lesson_ids))


    @log_duration
    async def scrape_course(self, course_id: str) -> list[str]:
        """Return deduplicated unwatched lesson IDs for a course. Empty list on login failure."""
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-dev-shm-usage",
                    "--disable-extensions",
                    "--disable-gpu",
                    "--no-sandbox",
                    "--disable-background-networking",
                    "--disable-default-apps",
                    "--mute-audio",
                    "--js-flags=--max-old-space-size=256",
                ],
            )
            context = None
            try:
                context = await browser.new_context(
                    ignore_https_errors=True,
                    viewport={"width": 1280, "height": 720},
                )
                page = await context.new_page()

                if not await self._login(page, self.email, self.password):
                    return []

                await page.goto(f"https://uni-x.almv.kz/platform/courses/{course_id}")
                await page.wait_for_timeout(5000)

                sections = await page.locator(
                    "div.flex.flex-row.items-start.cursor-pointer"
                ).all()
                for section in sections:
                    try:
                        await section.click(timeout=5000)
                    except Exception:
                        pass

                links = await page.evaluate(f"""() => {{
                    const container = document.querySelector('div.overflow-y-auto div.mt-5');
                    if (!container) return [];
                    return Array.from(container.querySelectorAll('a'))
                        .filter(a => {{
                            const img = a.querySelector('img');
                            return img && img.src && img.src.includes('{_GREY_LESSON_SVG_B64}');
                        }})
                        .map(a => a.getAttribute('href'));
                }}""")

                return self._extract_lesson_ids_from_hrefs(links)


            finally:
                if context:
                    await context.close()
                await browser.close()


if __name__ == "__main__":
    load_dotenv()
    unix_watcher = UniXWatcher(os.getenv("UNIX_EMAIL", ""), os.getenv("UNIX_PASSWORD", ""), pool=None, telegram_id=0)
    asyncio.run(unix_watcher.watch_unix_videos(["9882"]))
