from __future__ import annotations

from pathlib import Path

import yaml

from openchat.projects_store import ProjectsStore


def test_projects_store_email_roundtrip(tmp_path: Path):
    cfg = tmp_path / "projects.yaml"
    cfg.write_text("projects: []\n", encoding="utf-8")
    store = ProjectsStore(cfg)
    store.create_project(
        project_id="p1",
        label="P1",
        titles=["방"],
        email_sender="a@test.com",
        email_receivers=["b@test.com", "c@test.com"],
    )
    raw = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert raw["projects"][0]["email"]["sender"] == "a@test.com"
    assert len(raw["projects"][0]["email"]["receivers"]) == 2

    store.reload()
    p = store.get_project("p1")
    assert p is not None
    assert p.email_sender == "a@test.com"
    assert p.email_receivers == ["b@test.com", "c@test.com"]
