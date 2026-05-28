"""Per-project report email scheduler for web service mode."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from analyzer.bucketizer import parse_period_spec
from db.connection import init_db
from db.web_jobs import has_report_email_sent_since
from openchat.config import AppSettings, ProjectConfig, load_settings
from openchat.job_service import (
    submit_analyze,
    submit_collect,
    submit_report_and_email,
)

logger = logging.getLogger("openchat.report_scheduler")


@dataclass(frozen=True)
class ProjectScheduleView:
    project_id: str
    report_send_time: str
    next_send_at: str | None


def compute_next_send_at(
    project: ProjectConfig,
    *,
    now: datetime,
    tz_name: str,
) -> datetime | None:
    tz = ZoneInfo(tz_name)
    local_now = now.astimezone(tz)
    if project.report_send_time:
        hh, mm = [int(x) for x in project.report_send_time.split(":", 1)]
        today_target = local_now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if local_now < today_target:
            return today_target
        return today_target + timedelta(days=1)

    # Default when unset: every Monday at 08:00.
    hh, mm = 8, 0
    days_ahead = (0 - local_now.weekday()) % 7
    target = (local_now + timedelta(days=days_ahead)).replace(
        hour=hh, minute=mm, second=0, microsecond=0
    )
    if days_ahead == 0 and local_now >= target:
        target = target + timedelta(days=7)
    return target


def project_schedule_view(
    project: ProjectConfig,
    *,
    now: datetime,
    tz_name: str,
) -> ProjectScheduleView:
    next_at = compute_next_send_at(project, now=now, tz_name=tz_name)
    return ProjectScheduleView(
        project_id=project.id,
        report_send_time=project.report_send_time or "월요일 08:00 (기본)",
        next_send_at=next_at.isoformat(timespec="seconds") if next_at else None,
    )


class ReportScheduler:
    def __init__(self, *, tick_seconds: int = 30) -> None:
        self._tick_seconds = max(10, int(tick_seconds))
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="openchat-report-scheduler",
            daemon=True,
        )
        self._thread.start()
        logger.info("Report scheduler started")
        # self._emit("scheduler started", tick_seconds=self._tick_seconds)

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        logger.info("Report scheduler stopped")
        # self._emit("scheduler stopped")

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                logger.exception("Scheduler tick failed")
            self._stop.wait(self._tick_seconds)

    def _tick(self) -> None:
        settings = load_settings()
        tz = ZoneInfo(settings.tz)
        now = datetime.now(tz)
        # self._emit("tick", now=now.isoformat(timespec="seconds"), projects=len(settings.rooms))
        for project in settings.rooms:
            if not project.enabled:
                continue

            if self._collect_due(settings, project.id, now=now):
                try:
                    submit_collect(project.id)
                except ValueError:
                    pass
                except Exception:
                    logger.exception("Failed to submit collect for %s", project.id)

            if self._analyze_due(settings, project, now=now):
                try:
                    submit_analyze(project.id)
                except ValueError:
                    pass
                except Exception:
                    logger.exception("Failed to submit analyze for %s", project.id)

            if project.report_send_time:
                hh, mm = [int(x) for x in project.report_send_time.split(":", 1)]
                due_at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            else:
                if now.weekday() != 0:
                    continue
                due_at = now.replace(hour=8, minute=0, second=0, microsecond=0)
            if now < due_at:
                continue
            if self._already_sent_since(settings, project.id, due_at):
                continue
            try:
                submit_report_and_email(project.id)
                logger.info(
                    "Scheduled report+email submitted: project=%s due_at=%s",
                    project.id,
                    due_at.isoformat(timespec="seconds"),
                )
            except Exception:
                logger.exception("Failed to submit scheduled report for %s", project.id)
                # self._emit("submit failed", project=project.id)

    def _collect_due(self, settings: AppSettings, project_id: str, *, now: datetime) -> bool:
        last = self._last_collect_finished_at(settings, project_id)
        if last is None:
            return True
        interval = timedelta(minutes=max(1, int(settings.collect_interval_minutes)))
        return now >= (last + interval)

    def _analyze_due(self, settings: AppSettings, project: ProjectConfig, *, now: datetime) -> bool:
        last = self._last_analyze_finished_at(settings, project.id)
        analyze_time = project.analyze_send_time or "00:00"
        hh, mm = [int(x) for x in analyze_time.split(":", 1)]
        due_at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if now < due_at:
            return False
        if last is None:
            return True
        return last < due_at

    def _analyzer_period_delta(self, raw: str) -> timedelta:
        spec = parse_period_spec(raw)
        if spec.unit == "h":
            return timedelta(hours=spec.size)
        if spec.unit == "d":
            return timedelta(days=spec.size)
        if spec.unit == "w":
            return timedelta(weeks=spec.size)
        return timedelta(days=1)

    def _last_collect_finished_at(self, settings: AppSettings, project_id: str) -> datetime | None:
        conn = init_db(settings.database_path)
        try:
            row = conn.execute(
                """
                SELECT MAX(finished_at) AS ts
                FROM background_jobs
                WHERE project_id = ?
                  AND kind = 'collect'
                  AND status = 'succeeded'
                """,
                (project_id,),
            ).fetchone()
            raw = row["ts"] if row else None
            if not raw:
                return None
            return datetime.fromisoformat(str(raw)).astimezone(ZoneInfo(settings.tz))
        finally:
            conn.close()

    def _last_analyze_finished_at(self, settings: AppSettings, project_id: str) -> datetime | None:
        conn = init_db(settings.database_path)
        try:
            row = conn.execute(
                """
                SELECT MAX(finished_at) AS ts
                FROM background_jobs
                WHERE project_id = ?
                  AND kind IN ('analyze', 'collect_analyze_report_email')
                  AND status = 'succeeded'
                """,
                (project_id,),
            ).fetchone()
            raw = row["ts"] if row else None
            if not raw:
                return None
            return datetime.fromisoformat(str(raw)).astimezone(ZoneInfo(settings.tz))
        finally:
            conn.close()

    def _emit(self, event: str, **fields: object) -> None:
        # stamp = datetime.now().isoformat(timespec="seconds")
        # suffix = " ".join(f"{k}={v}" for k, v in fields.items())
        # print(
        #     f"[openchat.scheduler] {stamp} {event}" + (f" {suffix}" if suffix else ""),
        #     flush=True,
        # )
        pass

    def _already_sent_since(
        self,
        settings: AppSettings,
        project_id: str,
        due_at: datetime,
    ) -> bool:
        conn = init_db(settings.database_path)
        try:
            return has_report_email_sent_since(
                conn,
                project_id=project_id,
                since_at=due_at.isoformat(timespec="seconds"),
            )
        finally:
            conn.close()
