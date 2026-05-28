from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from db.web_jobs import ProjectLastRuns
from openchat.config import AppSettings, ProjectConfig
from openchat.report_scheduler import ReportScheduler, compute_next_send_at
from openchat.webapp import _build_next_runs_map

TZ = ZoneInfo("Asia/Seoul")


def test_analyze_due_defaults_to_daily_midnight(monkeypatch):
    scheduler = ReportScheduler()
    settings = AppSettings(tz="Asia/Seoul")
    project = ProjectConfig(id="p1", titles=["Room A"])
    now = datetime(2026, 5, 28, 7, 0, tzinfo=TZ)

    monkeypatch.setattr(
        scheduler,
        "_last_analyze_finished_at",
        lambda _settings, _project_id: datetime(2026, 5, 27, 16, 0, tzinfo=TZ),
    )

    assert scheduler._analyze_due(settings, project, now=now) is True


def test_analyze_due_skips_after_today_midnight_run(monkeypatch):
    scheduler = ReportScheduler()
    settings = AppSettings(tz="Asia/Seoul")
    project = ProjectConfig(id="p1", titles=["Room A"])
    now = datetime(2026, 5, 28, 7, 0, tzinfo=TZ)

    monkeypatch.setattr(
        scheduler,
        "_last_analyze_finished_at",
        lambda _settings, _project_id: datetime(2026, 5, 28, 0, 5, tzinfo=TZ),
    )

    assert scheduler._analyze_due(settings, project, now=now) is False


def test_report_default_next_send_is_monday_0800():
    project = ProjectConfig(id="p1", titles=["Room A"])
    now = datetime(2026, 5, 28, 7, 0, tzinfo=TZ)

    next_at = compute_next_send_at(project, now=now, tz_name="Asia/Seoul")

    assert next_at == datetime(2026, 6, 1, 8, 0, tzinfo=TZ)


def test_next_analyze_display_defaults_to_due_today(monkeypatch):
    settings = AppSettings(tz="Asia/Seoul")
    project = ProjectConfig(id="p1", titles=["Room A"])
    last_runs = {
        "p1": ProjectLastRuns(
            project_id="p1",
            last_collect_at=None,
            last_analyze_at="2026-05-27T16:00:00+09:00",
            last_report_at=None,
        )
    }

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 5, 28, 7, 0, tzinfo=tz or TZ)

    monkeypatch.setattr("openchat.webapp.datetime", FixedDateTime)

    next_runs = _build_next_runs_map([project], last_runs, settings)

    assert next_runs["p1"].next_analyze_at == "2026-05-28T00:00:00+09:00"
