"""Read/write projects YAML with atomic save and in-memory reload."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from openchat.config import ProjectConfig, parse_project_email


def resolve_projects_config_path() -> Path:
    """Match load_settings() path resolution."""
    projects_env = (os.getenv("PROJECTS_CONFIG") or "").strip()
    rooms_env = (os.getenv("ROOMS_CONFIG") or "").strip()
    if projects_env:
        return Path(projects_env)
    if rooms_env:
        return Path(rooms_env)
    projects_path = Path("config/projects.yaml")
    if projects_path.is_file():
        return projects_path
    return Path("config/rooms.yaml")


def atomic_write_yaml(path: Path, data: dict[str, Any]) -> None:
    """Write YAML via temp file in the same directory, then replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(
        data,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        tmp_path.replace(path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _item_to_project(item: dict[str, Any]) -> ProjectConfig:
    titles = item.get("titles")
    legacy_title = item.get("title")
    if titles is None:
        titles = [legacy_title] if legacy_title else []
    norm_titles = [str(t).strip() for t in titles if str(t).strip()]
    email_sender, email_receivers = parse_project_email(item)
    return ProjectConfig(
        id=str(item["id"]).strip(),
        title=norm_titles[0] if norm_titles else "",
        titles=norm_titles,
        label=str(item.get("label") or (norm_titles[0] if norm_titles else "")),
        enabled=bool(item.get("enabled", True)),
        update_notes_url=str(item.get("update_notes_url") or "").strip(),
        email_sender=email_sender,
        email_receivers=email_receivers,
    )


def _project_to_item(project: ProjectConfig) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": project.id,
        "label": project.label,
        "enabled": project.enabled,
        "titles": list(project.titles),
    }
    url = (project.update_notes_url or "").strip()
    if url:
        item["update_notes_url"] = url
    if project.email_sender or project.email_receivers:
        item["email"] = {
            "sender": project.email_sender,
            "receivers": list(project.email_receivers),
        }
    return item


@dataclass
class ProjectsDocument:
    """Full on-disk YAML document (projects + global exclude rules)."""

    config_path: Path
    projects: list[ProjectConfig] = field(default_factory=list)
    exclude_nicks: list[str] = field(default_factory=list)
    exclude_body_patterns: list[str] = field(default_factory=list)

    def to_raw(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "projects": [_project_to_item(p) for p in self.projects],
        }
        if self.exclude_nicks:
            payload["exclude_nicks"] = list(self.exclude_nicks)
        if self.exclude_body_patterns:
            payload["exclude_body_patterns"] = list(self.exclude_body_patterns)
        return payload


def load_projects_document(path: Path | None = None) -> ProjectsDocument:
    config_path = path or resolve_projects_config_path()
    if not config_path.is_file():
        raise FileNotFoundError(f"Projects config not found: {config_path}")

    with config_path.open(encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}

    items = raw.get("projects") or raw.get("rooms") or []
    projects: list[ProjectConfig] = []
    for item in items:
        if not item.get("id"):
            continue
        try:
            projects.append(_item_to_project(item))
        except ValueError:
            continue

    return ProjectsDocument(
        config_path=config_path,
        projects=projects,
        exclude_nicks=[str(x) for x in raw.get("exclude_nicks") or []],
        exclude_body_patterns=[str(x) for x in raw.get("exclude_body_patterns") or []],
    )


class ProjectsStore:
    """In-memory project registry backed by YAML; call reload() to refresh."""

    def __init__(self, config_path: Path | None = None) -> None:
        self._config_path = config_path
        self._document = load_projects_document(config_path)

    @property
    def config_path(self) -> Path:
        return self._document.config_path

    def reload(self) -> ProjectsDocument:
        self._document = load_projects_document(self._config_path)
        return self._document

    def list_projects(self, *, enabled_only: bool = False) -> list[ProjectConfig]:
        items = self._document.projects
        if enabled_only:
            return [p for p in items if p.enabled]
        return list(items)

    def get_project(self, project_id: str) -> ProjectConfig | None:
        for project in self._document.projects:
            if project.id == project_id:
                return project
        return None

    def create_project(
        self,
        *,
        project_id: str,
        label: str,
        titles: list[str],
        enabled: bool = True,
        update_notes_url: str = "",
        email_sender: str = "",
        email_receivers: list[str] | None = None,
    ) -> ProjectConfig:
        project_id = project_id.strip()
        if not project_id:
            raise ValueError("project id is required")
        if self.get_project(project_id):
            raise ValueError(f"project already exists: {project_id}")
        project = ProjectConfig(
            id=project_id,
            titles=[t.strip() for t in titles if t.strip()],
            label=label.strip() or (titles[0] if titles else project_id),
            enabled=enabled,
            update_notes_url=update_notes_url.strip(),
            email_sender=email_sender.strip(),
            email_receivers=list(email_receivers or []),
        )
        self._document.projects.append(project)
        self._persist()
        return project

    def update_project(
        self,
        project_id: str,
        *,
        label: str | None = None,
        titles: list[str] | None = None,
        enabled: bool | None = None,
        update_notes_url: str | None = None,
        email_sender: str | None = None,
        email_receivers: list[str] | None = None,
    ) -> ProjectConfig:
        project = self.get_project(project_id)
        if project is None:
            raise KeyError(f"project not found: {project_id}")

        if label is not None:
            project.label = label.strip() or project.label
        if titles is not None:
            norm = [t.strip() for t in titles if t.strip()]
            if not norm:
                raise ValueError("at least one title is required")
            project.titles = norm
            project.title = norm[0]
        if enabled is not None:
            project.enabled = enabled
        if update_notes_url is not None:
            project.update_notes_url = update_notes_url.strip()
        if email_sender is not None:
            project.email_sender = email_sender.strip()
        if email_receivers is not None:
            project.email_receivers = [r.strip() for r in email_receivers if r.strip()]

        self._persist()
        return project

    def delete_project(self, project_id: str) -> None:
        before = len(self._document.projects)
        self._document.projects = [p for p in self._document.projects if p.id != project_id]
        if len(self._document.projects) == before:
            raise KeyError(f"project not found: {project_id}")
        self._persist()

    def _persist(self) -> None:
        atomic_write_yaml(self._document.config_path, self._document.to_raw())

    def project_to_dict(self, project: ProjectConfig) -> dict[str, Any]:
        return {
            "id": project.id,
            "label": project.label,
            "enabled": project.enabled,
            "titles": list(project.titles),
            "title": project.title,
            "update_notes_url": project.update_notes_url,
            "email_sender": project.email_sender,
            "email_receivers": list(project.email_receivers),
        }
