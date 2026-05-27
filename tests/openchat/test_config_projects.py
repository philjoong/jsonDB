from __future__ import annotations

from pathlib import Path

import yaml

from openchat.config import load_settings


def _write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")


def test_load_settings_projects_yaml(monkeypatch, tmp_path: Path):
    cfg = tmp_path / "projects.yaml"
    _write_yaml(
        cfg,
        {
            "projects": [
                {
                    "id": "p1",
                    "label": "P1",
                    "enabled": True,
                    "titles": ["방A", "방B"],
                    "update_notes_url": "https://example.com/patch",
                }
            ]
        },
    )
    monkeypatch.setenv("PROJECTS_CONFIG", str(cfg))
    monkeypatch.delenv("ROOMS_CONFIG", raising=False)

    settings = load_settings(env_path=None)
    assert len(settings.rooms) == 1
    p = settings.rooms[0]
    assert p.id == "p1"
    assert p.titles == ["방A", "방B"]
    assert p.title == "방A"


def test_load_settings_legacy_rooms_yaml(monkeypatch, tmp_path: Path):
    cfg = tmp_path / "rooms.yaml"
    _write_yaml(
        cfg,
        {
            "rooms": [
                {
                    "id": "r1",
                    "title": "단일방",
                    "label": "R1",
                    "enabled": True,
                }
            ]
        },
    )
    monkeypatch.setenv("ROOMS_CONFIG", str(cfg))
    monkeypatch.delenv("PROJECTS_CONFIG", raising=False)

    settings = load_settings(env_path=None)
    assert len(settings.rooms) == 1
    r = settings.rooms[0]
    assert r.id == "r1"
    assert r.titles == ["단일방"]
    assert r.title == "단일방"

