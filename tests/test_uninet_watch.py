"""Uninet.watch_week: тики в реальном времени, пока сервер не засчитает видео (без сети и без ожидания)."""

from __future__ import annotations

import pytest

from packages.core import uninet

pytestmark = pytest.mark.asyncio


class FakeServer(uninet.Uninet):
    """Засчитывает delta не больше прошедшего времени, как uninet; completed — по requiredSec."""

    def __init__(self, clock):
        super().__init__("s@kbtu.kz", "pw")
        self.clock = clock
        self.videos = {
            1: {"id": 1, "durationSec": 100, "requiredSec": 90, "watchProgressSec": 0, "cumulativeWatchSec": 0},
            2: {"id": 2, "durationSec": 50, "requiredSec": 45, "watchProgressSec": 0, "cumulativeWatchSec": 50},
        }
        self.last_beat = None
        self.beats = []

    async def week(self, week_id):
        videos = [{**v, "completed": v["cumulativeWatchSec"] >= v["requiredSec"]} for v in self.videos.values()]
        return {"week": {"id": week_id}, "content": [{"videos": videos}]}

    async def progress(self, video_id, position_sec, delta_sec):
        now = self.clock.t
        credit = delta_sec if self.last_beat is None else min(delta_sec, now - self.last_beat)
        self.last_beat = now
        self.videos[video_id]["cumulativeWatchSec"] += credit
        self.beats.append((video_id, position_sec, delta_sec))


class Clock:
    t = 1000.0


async def test_watch_week_until_completed(monkeypatch):
    clock = Clock()

    async def sleep(sec):
        clock.t += sec

    monkeypatch.setattr(uninet.asyncio, "sleep", sleep)
    monkeypatch.setattr(uninet.time, "monotonic", lambda: clock.t)
    s = FakeServer(clock)

    await s.watch_week(7)

    assert s.videos[1]["cumulativeWatchSec"] >= 90
    assert all(vid == 1 for vid, _, _ in s.beats)  # уже засчитанное видео 2 не трогаем
    assert s.beats[0] == (1, 0, 0)  # точка отсчёта
    assert all(d <= uninet.BEAT_SEC for _, _, d in s.beats)
    assert [p for _, p, _ in s.beats] == sorted(p for _, p, _ in s.beats)  # без перемотки


async def test_watch_week_gives_up_if_server_never_completes(monkeypatch):
    clock = Clock()

    async def sleep(sec):
        clock.t += sec

    monkeypatch.setattr(uninet.asyncio, "sleep", sleep)
    monkeypatch.setattr(uninet.time, "monotonic", lambda: clock.t)
    s = FakeServer(clock)

    async def no_credit(video_id, position_sec, delta_sec):
        pass

    monkeypatch.setattr(s, "progress", no_credit)
    with pytest.raises(uninet.UninetUnavailable):
        await s.watch_week(7)
