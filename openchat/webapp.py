"""FastAPI web UI for project CRUD (internal network, no auth)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from db.connection import init_db
from db.web_jobs import get_report_run, list_jobs_for_project, list_report_runs
from openchat.config import DataScopeConfig, load_settings
from openchat.job_service import (
    fetch_job,
    job_to_dict,
    submit_analyze,
    submit_collect,
    submit_report,
    submit_report_and_email,
    submit_report_email,
)
from openchat.projects_store import ProjectsStore
from openchat.ui_settings_store import UiSettings, UiSettingsStore
from stats.project_stats import ProjectStats, query_project_stats


class ProjectBody(BaseModel):
    id: str | None = None
    label: str = ""
    titles: list[str] = Field(default_factory=list)
    enabled: bool = True
    update_notes_url: str = ""
    email_sender: str = ""
    email_receivers: list[str] = Field(default_factory=list)


class ReportEmailBody(BaseModel):
    run_id: int | None = None


class OperationalSettingsBody(BaseModel):
    collect_interval_minutes: int = Field(ge=1)
    analyzer_period: str
    reporter_window: str


class DataScopeBody(BaseModel):
    mode: str = "last_days"
    last_days: int = Field(ge=1)
    time_field: str = "message_at"
    tz: str | None = None


TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

_store: ProjectsStore | None = None
_ui_store: UiSettingsStore | None = None


def get_store() -> ProjectsStore:
    global _store
    if _store is None:
        _store = ProjectsStore()
    return _store


def get_ui_store() -> UiSettingsStore:
    global _ui_store
    if _ui_store is None:
        app_settings = load_settings()
        _ui_store = UiSettingsStore(
            app_settings.ui_settings_config,
            default_tz=app_settings.tz,
        )
    return _ui_store


def _ui_settings_to_api(ui: UiSettings) -> dict[str, Any]:
    return ui.to_dict()


def _scope_to_api(scope: DataScopeConfig) -> dict[str, Any]:
    return {
        "mode": scope.mode,
        "last_days": scope.last_days,
        "time_field": scope.time_field,
        "tz": scope.tz,
    }


def _require_project(project_id: str):
    store = get_store()
    project = store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project


def _project_detail_context(project_id: str) -> dict[str, Any]:
    project = _require_project(project_id)
    settings = load_settings()
    conn = init_db(settings.database_path)
    try:
        jobs = list_jobs_for_project(conn, project_id, limit=8)
        reports = list_report_runs(conn, project_id=project_id, limit=8)
    finally:
        conn.close()
    store = get_store()
    return {
        "project": project,
        "config_path": store.config_path,
        "jobs": jobs,
        "reports": reports,
        "flash": None,
        "flash_error": None,
    }


def _load_project_stats(project_id: str) -> ProjectStats:
    project = _require_project(project_id)
    settings = load_settings()
    conn = init_db(settings.database_path)
    try:
        return query_project_stats(
            conn,
            settings,
            project_id,
            project_label=project.label,
        )
    finally:
        conn.close()


def _resolve_report_path(run_id: int) -> Path:
    settings = load_settings()
    conn = init_db(settings.database_path)
    try:
        run = get_report_run(conn, run_id)
    finally:
        conn.close()
    if run is None:
        raise HTTPException(status_code=404, detail="report not found")
    path = Path(run.output_path).resolve()
    base = settings.output_dir.resolve()
    try:
        path.relative_to(base)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="invalid report path") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="report file missing")
    return path


def create_app() -> FastAPI:
    app = FastAPI(title="OpenChat", docs_url="/api/docs", redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request) -> HTMLResponse:
        store = get_store()
        ui_store = get_ui_store()
        projects = store.list_projects()
        saved = request.query_params.get("saved")
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "projects": projects,
                "config_path": store.config_path,
                "ui_settings": ui_store.settings,
                "ui_config_path": ui_store.config_path,
                "project_count": len(projects),
                "enabled_count": sum(1 for p in projects if p.enabled),
                "settings_saved": saved == "1",
            },
        )

    @app.get("/projects", response_class=HTMLResponse)
    async def projects_list(request: Request) -> HTMLResponse:
        store = get_store()
        return templates.TemplateResponse(
            request,
            "projects_list.html",
            {
                "projects": store.list_projects(),
                "config_path": store.config_path,
            },
        )

    @app.get("/projects/new", response_class=HTMLResponse)
    async def project_new_form(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "project_form.html",
            {"project": None, "error": None},
        )

    @app.post("/projects/new", response_model=None)
    async def project_new_submit(
        request: Request,
        project_id: Annotated[str, Form()],
        label: Annotated[str, Form()] = "",
        titles: Annotated[str, Form()] = "",
        enabled: Annotated[str | None, Form()] = None,
        update_notes_url: Annotated[str, Form()] = "",
        email_sender: Annotated[str, Form()] = "",
        email_receivers: Annotated[str, Form()] = "",
    ):
        store = get_store()
        title_list = _parse_titles(titles)
        try:
            store.create_project(
                project_id=project_id,
                label=label,
                titles=title_list,
                enabled=enabled == "on",
                update_notes_url=update_notes_url,
                email_sender=email_sender,
                email_receivers=_parse_receivers(email_receivers),
            )
        except (ValueError, KeyError) as exc:
            return templates.TemplateResponse(
                request,
                "project_form.html",
                {
                    "project": None,
                    "error": str(exc),
                    "form": {
                        "id": project_id,
                        "label": label,
                        "titles": titles,
                        "enabled": enabled == "on",
                        "update_notes_url": update_notes_url,
                    },
                },
                status_code=400,
            )
        return RedirectResponse(url=f"/projects/{project_id.strip()}", status_code=303)

    @app.get("/projects/{project_id}", response_class=HTMLResponse)
    async def project_detail(request: Request, project_id: str) -> HTMLResponse:
        ctx = _project_detail_context(project_id)
        if request.query_params.get("collected") == "1":
            ctx["flash"] = "수집이 완료되었습니다."
        return templates.TemplateResponse(request, "project_detail.html", ctx)

    @app.post("/projects/{project_id}/collect")
    async def project_collect(request: Request, project_id: str):
        _require_project(project_id)
        try:
            submit_collect(project_id)
        except Exception as exc:
            ctx = _project_detail_context(project_id)
            ctx["flash_error"] = f"수집 실패: {exc}"
            return templates.TemplateResponse(
                request,
                "project_detail.html",
                ctx,
                status_code=500,
            )
        return RedirectResponse(
            url=f"/projects/{project_id}?collected=1",
            status_code=303,
        )

    @app.post("/projects/{project_id}/analyze")
    async def project_analyze(project_id: str) -> RedirectResponse:
        _require_project(project_id)
        submitted = submit_analyze(project_id)
        return RedirectResponse(url=f"/jobs/{submitted.job_id}", status_code=303)

    @app.post("/projects/{project_id}/report")
    async def project_report(project_id: str) -> RedirectResponse:
        _require_project(project_id)
        submitted = submit_report(project_id)
        return RedirectResponse(url=f"/jobs/{submitted.job_id}", status_code=303)

    @app.post("/projects/{project_id}/report-email")
    async def project_report_email(
        project_id: str,
        run_id: Annotated[int | None, Form()] = None,
    ) -> RedirectResponse:
        _require_project(project_id)
        submitted = submit_report_email(project_id, run_id=run_id)
        return RedirectResponse(url=f"/jobs/{submitted.job_id}", status_code=303)

    @app.post("/projects/{project_id}/report-and-email")
    async def project_report_and_email(project_id: str) -> RedirectResponse:
        _require_project(project_id)
        submitted = submit_report_and_email(project_id)
        return RedirectResponse(url=f"/jobs/{submitted.job_id}", status_code=303)

    @app.post("/reports/{run_id}/email")
    async def report_send_email(run_id: int) -> RedirectResponse:
        settings = load_settings()
        conn = init_db(settings.database_path)
        try:
            run = get_report_run(conn, run_id)
        finally:
            conn.close()
        if run is None:
            raise HTTPException(status_code=404, detail="report not found")
        submitted = submit_report_email(run.project_id, run_id=run_id)
        return RedirectResponse(url=f"/jobs/{submitted.job_id}", status_code=303)

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    async def job_detail_page(request: Request, job_id: str) -> HTMLResponse:
        job = fetch_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return templates.TemplateResponse(
            request,
            "job_detail.html",
            {"job": job_to_dict(job)},
        )

    @app.get("/reports", response_class=HTMLResponse)
    async def reports_list_page(
        request: Request,
        project_id: str | None = Query(default=None),
    ) -> HTMLResponse:
        settings = load_settings()
        conn = init_db(settings.database_path)
        try:
            reports = list_report_runs(conn, project_id=project_id, limit=100)
        finally:
            conn.close()
        return templates.TemplateResponse(
            request,
            "reports_list.html",
            {"reports": reports, "filter_project_id": project_id},
        )

    @app.get("/reports/{run_id}", response_class=HTMLResponse)
    async def report_detail_page(request: Request, run_id: int) -> HTMLResponse:
        settings = load_settings()
        conn = init_db(settings.database_path)
        try:
            run = get_report_run(conn, run_id)
        finally:
            conn.close()
        if run is None:
            raise HTTPException(status_code=404, detail="report not found")
        return templates.TemplateResponse(
            request,
            "report_detail.html",
            {"run": run},
        )

    @app.get("/reports/{run_id}/file")
    async def report_file(run_id: int) -> FileResponse:
        path = _resolve_report_path(run_id)
        return FileResponse(path, media_type="text/html; charset=utf-8")

    @app.get("/stats/projects/{project_id}", response_class=HTMLResponse)
    async def project_stats_page(request: Request, project_id: str) -> HTMLResponse:
        stats = _load_project_stats(project_id)
        return templates.TemplateResponse(
            request,
            "project_stats.html",
            {"stats": stats},
        )

    @app.get("/api/projects/{project_id}/stats")
    async def api_project_stats(project_id: str) -> dict[str, Any]:
        return _load_project_stats(project_id).to_dict()

    @app.get("/projects/{project_id}/edit", response_class=HTMLResponse)
    async def project_edit_form(request: Request, project_id: str) -> HTMLResponse:
        store = get_store()
        project = store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        return templates.TemplateResponse(
            request,
            "project_form.html",
            {"project": project, "error": None},
        )

    @app.post("/projects/{project_id}/edit", response_model=None)
    async def project_edit_submit(
        request: Request,
        project_id: str,
        label: Annotated[str, Form()] = "",
        titles: Annotated[str, Form()] = "",
        enabled: Annotated[str | None, Form()] = None,
        update_notes_url: Annotated[str, Form()] = "",
        email_sender: Annotated[str, Form()] = "",
        email_receivers: Annotated[str, Form()] = "",
    ):
        store = get_store()
        project = store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        title_list = _parse_titles(titles)
        try:
            store.update_project(
                project_id,
                label=label,
                titles=title_list,
                enabled=enabled == "on",
                update_notes_url=update_notes_url,
                email_sender=email_sender,
                email_receivers=_parse_receivers(email_receivers),
            )
        except (ValueError, KeyError) as exc:
            return templates.TemplateResponse(
                request,
                "project_form.html",
                {
                    "project": project,
                    "error": str(exc),
                    "form": {
                        "label": label,
                        "titles": titles,
                        "enabled": enabled == "on",
                        "update_notes_url": update_notes_url,
                    },
                },
                status_code=400,
            )
        return RedirectResponse(url=f"/projects/{project_id}", status_code=303)

    @app.post("/projects/{project_id}/delete")
    async def project_delete(project_id: str) -> RedirectResponse:
        store = get_store()
        try:
            store.delete_project(project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return RedirectResponse(url="/projects", status_code=303)

    @app.post("/settings/reload")
    async def settings_reload() -> RedirectResponse:
        get_store().reload()
        get_ui_store().reload(default_tz=load_settings().tz)
        return RedirectResponse(url="/", status_code=303)

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request) -> HTMLResponse:
        ui_store = get_ui_store()
        return templates.TemplateResponse(
            request,
            "settings.html",
            {
                "settings": ui_store.settings,
                "config_path": ui_store.config_path,
                "message": request.query_params.get("saved"),
                "error": None,
            },
        )

    @app.post("/settings", response_model=None)
    async def settings_submit(
        request: Request,
        collect_interval_minutes: Annotated[str, Form()],
        analyzer_period: Annotated[str, Form()],
        reporter_window: Annotated[str, Form()],
        last_days: Annotated[str, Form()],
        scope_tz: Annotated[str, Form()] = "",
        time_field: Annotated[str, Form()] = "message_at",
        scope_mode: Annotated[str, Form()] = "last_days",
    ):
        ui_store = get_ui_store()
        try:
            interval = max(1, int(collect_interval_minutes.strip()))
            days = max(1, int(last_days.strip()))
        except ValueError:
            return templates.TemplateResponse(
                request,
                "settings.html",
                {
                    "settings": ui_store.settings,
                    "config_path": ui_store.config_path,
                    "message": None,
                    "error": "수집 주기와 최근 N일은 정수여야 합니다.",
                },
                status_code=400,
            )
        try:
            ui_store.update(
                collect_interval_minutes=interval,
                analyzer_period=analyzer_period,
                reporter_window=reporter_window,
            )
            ui_store.update_scope(
                last_days=days,
                mode=scope_mode,
                time_field=time_field,
                tz=scope_tz or load_settings().tz,
                sync_reporter_window=True,
            )
        except ValueError as exc:
            return templates.TemplateResponse(
                request,
                "settings.html",
                {
                    "settings": ui_store.settings,
                    "config_path": ui_store.config_path,
                    "message": None,
                    "error": str(exc),
                },
                status_code=400,
            )
        return RedirectResponse(url="/settings?saved=1", status_code=303)

    # --- JSON API ---

    @app.get("/api/projects")
    async def api_list_projects() -> list[dict[str, Any]]:
        store = get_store()
        return [store.project_to_dict(p) for p in store.list_projects()]

    @app.post("/api/projects", status_code=201)
    async def api_create_project(payload: ProjectBody) -> dict[str, Any]:
        if not payload.id:
            raise HTTPException(status_code=400, detail="id is required")
        store = get_store()
        try:
            project = store.create_project(
                project_id=payload.id,
                label=payload.label,
                titles=payload.titles,
                enabled=payload.enabled,
                update_notes_url=payload.update_notes_url,
                email_sender=payload.email_sender,
                email_receivers=payload.email_receivers,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return store.project_to_dict(project)

    @app.get("/api/projects/{project_id}")
    async def api_get_project(project_id: str) -> dict[str, Any]:
        store = get_store()
        project = store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        return store.project_to_dict(project)

    @app.put("/api/projects/{project_id}")
    async def api_update_project(
        project_id: str, payload: ProjectBody
    ) -> dict[str, Any]:
        store = get_store()
        try:
            project = store.update_project(
                project_id,
                label=payload.label,
                titles=payload.titles if payload.titles else None,
                enabled=payload.enabled,
                update_notes_url=payload.update_notes_url,
                email_sender=payload.email_sender or None,
                email_receivers=payload.email_receivers or None,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return store.project_to_dict(project)

    @app.delete("/api/projects/{project_id}", status_code=204)
    async def api_delete_project(project_id: str) -> None:
        store = get_store()
        try:
            store.delete_project(project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/settings/reload")
    async def api_reload() -> dict[str, str]:
        doc = get_store().reload()
        ui_store = get_ui_store()
        ui_store.reload(default_tz=load_settings().tz)
        return {
            "status": "ok",
            "config_path": str(doc.config_path),
            "project_count": str(len(doc.projects)),
            "ui_settings_path": str(ui_store.config_path),
        }

    @app.get("/api/settings")
    async def api_get_settings() -> dict[str, Any]:
        ui_store = get_ui_store()
        return {
            "config_path": str(ui_store.config_path),
            **_ui_settings_to_api(ui_store.settings),
        }

    @app.put("/api/settings")
    async def api_put_settings(payload: OperationalSettingsBody) -> dict[str, Any]:
        ui_store = get_ui_store()
        try:
            ui_store.update(
                collect_interval_minutes=payload.collect_interval_minutes,
                analyzer_period=payload.analyzer_period,
                reporter_window=payload.reporter_window,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "config_path": str(ui_store.config_path),
            **_ui_settings_to_api(ui_store.settings),
        }

    @app.get("/api/settings/scope")
    async def api_get_scope() -> dict[str, Any]:
        scope = get_ui_store().settings.data_scope
        return {
            "config_path": str(get_ui_store().config_path),
            **_scope_to_api(scope),
        }

    @app.post("/api/projects/{project_id}/collect")
    async def api_project_collect(project_id: str) -> dict[str, Any]:
        _require_project(project_id)
        try:
            submitted = submit_collect(project_id)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        job = fetch_job(submitted.job_id)
        return job_to_dict(job) if job else {"job_id": submitted.job_id, "status": "succeeded"}

    @app.post("/api/projects/{project_id}/analyze", status_code=202)
    async def api_project_analyze(project_id: str) -> dict[str, Any]:
        _require_project(project_id)
        submitted = submit_analyze(project_id)
        return {"job_id": submitted.job_id, "status": "pending", "kind": "analyze"}

    @app.post("/api/projects/{project_id}/report", status_code=202)
    async def api_project_report(project_id: str) -> dict[str, Any]:
        _require_project(project_id)
        submitted = submit_report(project_id)
        return {"job_id": submitted.job_id, "status": "pending", "kind": "report"}

    @app.post("/api/projects/{project_id}/report-email", status_code=202)
    async def api_project_report_email(
        project_id: str, payload: ReportEmailBody | None = None
    ) -> dict[str, Any]:
        _require_project(project_id)
        run_id = payload.run_id if payload else None
        submitted = submit_report_email(project_id, run_id=run_id)
        return {
            "job_id": submitted.job_id,
            "status": "pending",
            "kind": "report_email",
            "run_id": run_id,
        }

    @app.post("/api/projects/{project_id}/report-and-email", status_code=202)
    async def api_project_report_and_email(project_id: str) -> dict[str, Any]:
        _require_project(project_id)
        submitted = submit_report_and_email(project_id)
        return {
            "job_id": submitted.job_id,
            "status": "pending",
            "kind": "report_and_email",
        }

    @app.get("/api/jobs/{job_id}")
    async def api_get_job(job_id: str) -> dict[str, Any]:
        job = fetch_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return job_to_dict(job)

    @app.get("/api/reports")
    async def api_list_reports(
        project_id: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> list[dict[str, Any]]:
        settings = load_settings()
        conn = init_db(settings.database_path)
        try:
            rows = list_report_runs(conn, project_id=project_id, limit=limit)
        finally:
            conn.close()
        return [
            {
                "run_id": r.run_id,
                "project_id": r.project_id,
                "created_at": r.created_at,
                "output_path": r.output_path,
                "window_label": r.window_label,
                "bucket_count": r.bucket_count,
                "reporter_backend": r.reporter_backend,
                "scope_mode": r.scope_mode,
            }
            for r in rows
        ]

    @app.get("/api/reports/{run_id}")
    async def api_get_report(run_id: int) -> dict[str, Any]:
        settings = load_settings()
        conn = init_db(settings.database_path)
        try:
            run = get_report_run(conn, run_id)
        finally:
            conn.close()
        if run is None:
            raise HTTPException(status_code=404, detail="report not found")
        return {
            "run_id": run.run_id,
            "project_id": run.project_id,
            "job_id": run.job_id,
            "created_at": run.created_at,
            "output_path": run.output_path,
            "window_label": run.window_label,
            "scope_json": run.scope_json,
            "period_keys_json": run.period_keys_json,
            "bucket_count": run.bucket_count,
            "reporter_backend": run.reporter_backend,
            "scope_mode": run.scope_mode,
            "view_url": f"/reports/{run.run_id}/file",
        }

    @app.put("/api/settings/scope")
    async def api_put_scope(payload: DataScopeBody) -> dict[str, Any]:
        ui_store = get_ui_store()
        tz = (payload.tz or load_settings().tz).strip()
        try:
            ui_store.update_scope(
                last_days=payload.last_days,
                mode=payload.mode,
                time_field=payload.time_field,
                tz=tz,
                sync_reporter_window=True,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "config_path": str(ui_store.config_path),
            **_scope_to_api(ui_store.settings.data_scope),
        }

    return app


def _parse_titles(raw: str) -> list[str]:
    lines = [line.strip() for line in raw.replace("\r", "").split("\n")]
    return [line for line in lines if line]


def _parse_receivers(raw: str) -> list[str]:
    lines = [line.strip() for line in raw.replace("\r", "").split("\n")]
    out: list[str] = []
    for line in lines:
        if not line:
            continue
        for part in line.replace(",", " ").split():
            p = part.strip()
            if p:
                out.append(p)
    return out


app = create_app()


def run_server(*, host: str | None = None, port: int | None = None) -> None:
    import uvicorn

    bind_host = host or os.getenv("SERVE_HOST", "0.0.0.0")
    bind_port = port or int(os.getenv("SERVE_PORT", "8000"))
    uvicorn.run(
        "openchat.webapp:app",
        host=bind_host,
        port=bind_port,
        reload=os.getenv("SERVE_RELOAD", "").lower() in ("1", "true", "yes"),
    )
