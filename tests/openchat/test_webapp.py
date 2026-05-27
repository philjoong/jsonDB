from __future__ import annotations

from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from openchat import webapp
from openchat.job_service import JobSubmitResult
from openchat.projects_store import ProjectsStore


def _write_projects(path: Path, projects: list[dict]) -> None:
    path.write_text(
        yaml.safe_dump({"projects": projects}, allow_unicode=True),
        encoding="utf-8",
    )


def test_webapp_project_api(tmp_path: Path, monkeypatch):
    cfg = tmp_path / "projects.yaml"
    ui_cfg = tmp_path / "ui_settings.yaml"
    _write_projects(
        cfg,
        [{"id": "p1", "label": "P1", "enabled": True, "titles": ["방1"]}],
    )
    monkeypatch.setenv("PROJECTS_CONFIG", str(cfg))
    monkeypatch.setenv("UI_SETTINGS_CONFIG", str(ui_cfg))
    webapp._store = ProjectsStore(cfg)
    webapp._ui_store = None

    client = TestClient(webapp.app)

    listed = client.get("/api/projects")
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    created = client.post(
        "/api/projects",
        json={
            "id": "p2",
            "label": "P2",
            "titles": ["방A", "방B"],
            "enabled": True,
        },
    )
    assert created.status_code == 201
    assert created.json()["id"] == "p2"

    updated = client.put(
        "/api/projects/p2",
        json={
            "label": "P2-new",
            "titles": ["방C"],
            "enabled": False,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["label"] == "P2-new"
    assert updated.json()["enabled"] is False

    reloaded = client.post("/api/settings/reload")
    assert reloaded.status_code == 200
    assert int(reloaded.json()["project_count"]) == 2

    deleted = client.delete("/api/projects/p2")
    assert deleted.status_code == 204
    assert len(client.get("/api/projects").json()) == 1


def test_webapp_settings_scope_api(tmp_path: Path, monkeypatch):
    cfg = tmp_path / "projects.yaml"
    ui_cfg = tmp_path / "ui_settings.yaml"
    _write_projects(
        cfg,
        [{"id": "p1", "label": "P1", "enabled": True, "titles": ["방1"]}],
    )
    monkeypatch.setenv("PROJECTS_CONFIG", str(cfg))
    monkeypatch.setenv("UI_SETTINGS_CONFIG", str(ui_cfg))
    webapp._store = ProjectsStore(cfg)
    webapp._ui_store = None

    client = TestClient(webapp.app)

    scope_get = client.get("/api/settings/scope")
    assert scope_get.status_code == 200
    assert scope_get.json()["last_days"] == 7

    scope_put = client.put(
        "/api/settings/scope",
        json={"mode": "last_days", "last_days": 14, "time_field": "message_at"},
    )
    assert scope_put.status_code == 200
    assert scope_put.json()["last_days"] == 14

    settings = client.get("/api/settings")
    assert settings.status_code == 200
    assert settings.json()["collect_interval_minutes"] == 10
    assert settings.json()["data_scope"]["last_days"] == 14
    assert settings.json()["reporter_window"] == "14d"

    updated = client.put(
        "/api/settings",
        json={
            "collect_interval_minutes": 25,
            "analyzer_period": "6h",
            "reporter_window": "14d",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["collect_interval_minutes"] == 25
    assert updated.json()["analyzer_period"] == "6h"

    page = client.get("/settings")
    assert page.status_code == 200
    assert "최근 N일" in page.text


def test_webapp_project_actions(tmp_path: Path, monkeypatch):
    cfg = tmp_path / "projects.yaml"
    ui_cfg = tmp_path / "ui_settings.yaml"
    db = tmp_path / "t.db"
    _write_projects(
        cfg,
        [{"id": "p1", "label": "P1", "enabled": True, "titles": ["방1"]}],
    )
    monkeypatch.setenv("PROJECTS_CONFIG", str(cfg))
    monkeypatch.setenv("UI_SETTINGS_CONFIG", str(ui_cfg))
    monkeypatch.setenv("DATABASE_PATH", str(db))
    webapp._store = ProjectsStore(cfg)
    webapp._ui_store = None

    def fake_collect(project_id: str) -> JobSubmitResult:
        return JobSubmitResult(
            job_id="c1", kind="collect", project_id=project_id, async_mode=False
        )

    def fake_report(project_id: str) -> JobSubmitResult:
        return JobSubmitResult(
            job_id="r1", kind="report", project_id=project_id, async_mode=True
        )

    monkeypatch.setattr(webapp, "submit_collect", fake_collect)
    monkeypatch.setattr(webapp, "submit_report", fake_report)

    client = TestClient(webapp.app)
    collect = client.post("/api/projects/p1/collect")
    assert collect.status_code == 200

    report = client.post("/api/projects/p1/report", follow_redirects=False)
    assert report.status_code == 202
    assert report.json()["job_id"] == "r1"

    post_report = client.post("/projects/p1/report", follow_redirects=False)
    assert post_report.status_code == 303
    assert post_report.headers["location"] == "/jobs/r1"
