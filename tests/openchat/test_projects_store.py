from __future__ import annotations

from pathlib import Path

import yaml

from openchat.projects_store import ProjectsStore, atomic_write_yaml, load_projects_document


def _write_projects(path: Path, projects: list[dict], **extra) -> None:
    payload = {"projects": projects, **extra}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")


def test_atomic_write_yaml(tmp_path: Path):
    target = tmp_path / "nested" / "projects.yaml"
    atomic_write_yaml(target, {"projects": [{"id": "a", "titles": ["t"]}]})
    assert target.is_file()
    loaded = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert loaded["projects"][0]["id"] == "a"


def test_projects_store_crud(tmp_path: Path, monkeypatch):
    cfg = tmp_path / "projects.yaml"
    _write_projects(
        cfg,
        [{"id": "p1", "label": "P1", "enabled": True, "titles": ["Room A"]}],
        exclude_nicks=["bot"],
    )
    monkeypatch.setenv("PROJECTS_CONFIG", str(cfg))

    store = ProjectsStore()
    assert len(store.list_projects()) == 1

    store.create_project(
        project_id="p2",
        label="P2",
        titles=["Room B", "Room C"],
        update_notes_url="https://example.com",
        report_send_time="09:00",
    )
    assert len(store.list_projects()) == 2
    p2 = store.get_project("p2")
    assert p2 is not None
    assert p2.titles == ["Room B", "Room C"]
    assert p2.report_send_time == "09:00"

    store.update_project("p2", label="P2-updated", enabled=False)
    p2 = store.get_project("p2")
    assert p2 is not None
    assert p2.label == "P2-updated"
    assert p2.enabled is False

    store.delete_project("p1")
    assert store.get_project("p1") is None

    reloaded = load_projects_document(cfg)
    assert len(reloaded.projects) == 1
    assert reloaded.exclude_nicks == ["bot"]
    on_disk = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert "projects" in on_disk
    assert on_disk["projects"][0]["titles"] == ["Room B", "Room C"]
    assert on_disk["projects"][0]["report_send_time"] == "09:00"


def test_projects_store_reload(tmp_path: Path, monkeypatch):
    cfg = tmp_path / "projects.yaml"
    _write_projects(cfg, [{"id": "a", "label": "A", "titles": ["t"]}])
    monkeypatch.setenv("PROJECTS_CONFIG", str(cfg))

    store = ProjectsStore()
    assert len(store.list_projects()) == 1

    _write_projects(
        cfg,
        [
            {"id": "a", "label": "A", "titles": ["t"]},
            {"id": "b", "label": "B", "titles": ["t2"]},
        ],
    )
    store.reload()
    assert len(store.list_projects()) == 2
