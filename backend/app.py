from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import logging
import os
import random
import sqlite3
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps
from pydantic import BaseModel, Field

from .db import (
    IS_VERCEL,
    MEDIA_DIR,
    PUBLIC_DIR,
    connect,
    init_db,
    now_iso,
    serialize_task,
    using_postgres,
)
from .recommender import INTENTS, recommend

STATIC_DIR = PUBLIC_DIR / "static"
MAX_PHOTO_BYTES = 3 * 1024 * 1024
BLOB_TOKEN = os.getenv("BLOB_READ_WRITE_TOKEN", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_BASE_URL = (
    os.getenv("OPENAI_BASE_URL", "https://api.siliconflow.cn/v1").strip().rstrip("/")
    or "https://api.siliconflow.cn/v1"
)
OPENAI_MODEL = (
    os.getenv("OPENAI_MODEL", "deepseek-ai/DeepSeek-V4-Flash").strip()
    or "deepseek-ai/DeepSeek-V4-Flash"
)
APP_PASSWORD = os.getenv("APP_PASSWORD", "").strip()
SESSION_SECRET = os.getenv("SESSION_SECRET", "").strip() or APP_PASSWORD or "yibai-local-dev"
SESSION_COOKIE = "yibai_session"
SESSION_MAX_AGE = 30 * 24 * 60 * 60
AUTH_REQUIRED = bool(APP_PASSWORD) or IS_VERCEL
PHOTO_COLORS = ["#D8A47F", "#B8C6B0", "#A9C4D4", "#D6A5A0", "#C7A5A0", "#A8ADA4"]
PUBLIC_API_PATHS = {"/api/health", "/api/session", "/api/login", "/api/logout"}
STARTUP_ERROR: Optional[str] = None

logger = logging.getLogger("yibai")

if not IS_VERCEL:
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="一百个今天 MVP")
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
if MEDIA_DIR.exists():
    app.mount("/media", StaticFiles(directory=str(MEDIA_DIR)), name="media")


def _session_token() -> str:
    stamp = str(int(time.time()))
    signature = hmac.new(
        SESSION_SECRET.encode("utf-8"), stamp.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{stamp}.{signature}"


def _session_valid(token: Optional[str]) -> bool:
    if not token or "." not in token:
        return False
    stamp, signature = token.split(".", 1)
    if not stamp.isdigit():
        return False
    if int(time.time()) - int(stamp) > SESSION_MAX_AGE:
        return False
    expected = hmac.new(
        SESSION_SECRET.encode("utf-8"), stamp.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


@app.middleware("http")
async def protect_private_api(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and path not in PUBLIC_API_PATHS:
        if AUTH_REQUIRED and not _session_valid(request.cookies.get(SESSION_COOKIE)):
            return JSONResponse(
                {"detail": "需要先输入访问密码"}, status_code=401
            )
    return await call_next(request)


class LoginRequest(BaseModel):
    password: str = Field(default="", max_length=200)


class ProfileUpdate(BaseModel):
    name: str = Field(default="你", max_length=24)
    target_count: int = Field(default=100, ge=1, le=1000)
    duration_days: int = Field(default=365, ge=0, le=36500)


class SelectRequest(BaseModel):
    task_id: int
    version: str = "standard"
    intent: str = ""
    reason: str = ""


class CompleteRequest(BaseModel):
    user_task_id: int
    note: str = Field(default="", max_length=1200)
    photo_data: Optional[str] = None
    mood_after: Optional[int] = Field(default=None, ge=1, le=5)
    color: str = Field(default="#D8A47F", max_length=16)
    repeat_wish: bool = False
    is_private: bool = True
    energy: int = Field(default=3, ge=1, le=5)


class CheckinRequest(BaseModel):
    intent: str
    energy: int = Field(default=3, ge=1, le=5)


class CustomTaskRequest(BaseModel):
    title: str = Field(min_length=1, max_length=60)
    summary: str = Field(default="", max_length=160)
    category: str = Field(default="自定义", max_length=20)
    minutes_standard: int = Field(default=15, ge=1, le=600)


class AIInspirationRequest(BaseModel):
    intent: str = Field(default="", max_length=24)
    energy: int = Field(default=3, ge=1, le=5)
    minutes: int = Field(default=10, ge=1, le=180)
    count: int = Field(default=3, ge=1, le=5)


AI_CATEGORIES = ["安静", "身体", "创造", "观察", "城市", "连接", "照顾", "勇气"]
AI_TASK_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "category": {"type": "string", "enum": AI_CATEGORIES},
        "minutes_min": {"type": "integer"},
        "minutes_standard": {"type": "integer"},
        "minutes_adventure": {"type": "integer"},
        "min_action": {"type": "string"},
        "standard_action": {"type": "string"},
        "adventure_action": {"type": "string"},
        "prompt": {"type": "string"},
        "color": {"type": "string"},
        "social": {"type": "string", "enum": ["solo", "any", "together"]},
        "place": {"type": "string", "enum": ["anywhere", "home", "outside"]},
        "cost": {"type": "integer", "enum": [0, 1]},
        "difficulty": {"type": "integer", "enum": [1, 2, 3]},
    },
    "required": [
        "title",
        "summary",
        "category",
        "minutes_min",
        "minutes_standard",
        "minutes_adventure",
        "min_action",
        "standard_action",
        "adventure_action",
        "prompt",
        "color",
        "social",
        "place",
        "cost",
        "difficulty",
    ],
    "additionalProperties": False,
}
AI_TASK_BATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "tasks": {
            "type": "array",
            "minItems": 1,
            "maxItems": 5,
            "items": AI_TASK_SCHEMA,
        }
    },
    "required": ["tasks"],
    "additionalProperties": False,
}


@app.on_event("startup")
def startup() -> None:
    global STARTUP_ERROR
    STARTUP_ERROR = None
    try:
        init_db()
    except Exception as exc:
        STARTUP_ERROR = _friendly_database_error(exc)
        logger.exception("数据库初始化失败")


def _friendly_database_error(exc: Exception) -> str:
    message = str(exc)
    if "DATABASE_URL" in message:
        return "线上数据库尚未配置：请在 Vercel 连接 Postgres，并确认 DATABASE_URL 已加入 Production 环境。"
    if "psycopg" in message:
        return "线上数据库驱动加载失败，请检查 Vercel 构建日志和 requirements.txt。"
    if "timeout" in message.lower() or "timed out" in message.lower():
        return "数据库连接超时，请检查 Postgres 是否暂停或连接串是否正确。"
    return "数据库暂时连接不上，请检查 DATABASE_URL 和 Postgres 运行状态。"


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("请求处理失败：%s", request.url.path)
    if STARTUP_ERROR:
        return JSONResponse(status_code=503, content={"detail": STARTUP_ERROR})
    return JSONResponse(
        status_code=500,
        content={"detail": "服务暂时不可用，请稍后再试。"},
    )


@app.get("/")
def index() -> FileResponse:
    return FileResponse(PUBLIC_DIR / "index.html")


@app.get("/favicon.ico")
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/robots.txt")
def robots() -> FileResponse:
    return FileResponse(PUBLIC_DIR / "robots.txt", media_type="text/plain")


@app.get("/api/session")
def session(request: Request) -> dict:
    return {
        "auth_required": AUTH_REQUIRED,
        "authenticated": (not AUTH_REQUIRED)
        or _session_valid(request.cookies.get(SESSION_COOKIE)),
        "configured": bool(APP_PASSWORD) if AUTH_REQUIRED else True,
    }


@app.post("/api/login")
def login(payload: LoginRequest, response: Response) -> dict:
    if not APP_PASSWORD:
        if IS_VERCEL:
            raise HTTPException(
                status_code=503,
                detail="访问密码尚未配置：请在 Vercel 环境变量中设置 APP_PASSWORD。",
            )
        return {"ok": True}
    if not hmac.compare_digest(payload.password, APP_PASSWORD):
        raise HTTPException(status_code=401, detail="密码不正确，再试一次吧。")
    response.set_cookie(
        SESSION_COOKIE,
        _session_token(),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=IS_VERCEL,
        samesite="lax",
        path="/",
    )
    return {"ok": True}


@app.post("/api/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@app.get("/api/health")
def health(response: Response) -> dict:
    result = {
        "ok": True,
        "storage": "postgres" if using_postgres() else ("unconfigured" if IS_VERCEL else "sqlite"),
        "blob": bool(BLOB_TOKEN),
        "ai": bool(OPENAI_API_KEY),
        "auth_required": AUTH_REQUIRED,
        "auth_configured": bool(APP_PASSWORD) if AUTH_REQUIRED else True,
    }
    if STARTUP_ERROR:
        response.status_code = 503
        return {**result, "ok": False, "error": STARTUP_ERROR}
    conn = None
    try:
        conn = connect()
        count = conn.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"]
        return {**result, "tasks": count}
    except Exception as exc:
        logger.exception("健康检查失败")
        response.status_code = 503
        return {**result, "ok": False, "error": _friendly_database_error(exc)}
    finally:
        if conn is not None:
            conn.close()


def _profile(conn: Any) -> dict:
    row = conn.execute("SELECT * FROM profile WHERE id = 1").fetchone()
    return {
        "name": row["name"],
        "target_count": row["target_count"],
        "duration_days": row["duration_days"],
        "created_at": row["created_at"],
    }


def _active(conn: Any) -> Optional[dict]:
    row = conn.execute(
        """
        SELECT ut.id AS user_task_id, ut.version, ut.intent, ut.reason, ut.selected_at, t.*
        FROM user_tasks ut
        JOIN tasks t ON t.id = ut.task_id
        WHERE ut.status = 'active'
        ORDER BY ut.id DESC LIMIT 1
        """
    ).fetchone()
    if not row:
        return None
    task = serialize_task(row)
    return {
        "user_task_id": row["user_task_id"],
        "version": row["version"],
        "intent": row["intent"],
        "reason": row["reason"],
        "selected_at": row["selected_at"],
        "task": task,
    }


def _photo_url(photo_path: Optional[str]) -> Optional[str]:
    if not photo_path:
        return None
    if photo_path.startswith(("http://", "https://")):
        return photo_path
    return f"/media/{photo_path}"


def _memory_from_row(row: sqlite3.Row) -> dict:
    task = serialize_task(row)
    return {
        "entry_id": row["entry_id"],
        "user_task_id": row["user_task_id"],
        "task": task,
        "note": row["note"] or "",
        "photo_url": _photo_url(row["photo_path"]),
        "mood_after": row["mood_after"],
        "color": row["entry_color"] or task["color"],
        "repeat_wish": bool(row["repeat_wish"]),
        "is_private": bool(row["is_private"]),
        "completed_at": row["completed_at"],
        "created_at": row["entry_created_at"],
    }


def _memories(conn: Any, limit: Optional[int] = None) -> list[dict]:
    sql = """
        SELECT ut.id AS user_task_id, ut.completed_at, t.*,
               e.id AS entry_id, e.note, e.photo_path, e.mood_after,
               e.color AS entry_color, e.repeat_wish, e.is_private,
               e.created_at AS entry_created_at
        FROM user_tasks ut
        JOIN tasks t ON t.id = ut.task_id
        JOIN entries e ON e.user_task_id = ut.id
        WHERE ut.status = 'completed'
        ORDER BY ut.completed_at DESC
    """
    params = []
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [_memory_from_row(row) for row in rows]


def _stats(conn: Any) -> dict:
    profile = _profile(conn)
    completed = conn.execute(
        "SELECT COUNT(*) AS n FROM user_tasks WHERE status = 'completed'"
    ).fetchone()["n"]
    categories = conn.execute(
        """
        SELECT t.category, COUNT(*) AS n
        FROM user_tasks ut JOIN tasks t ON t.id = ut.task_id
        WHERE ut.status = 'completed'
        GROUP BY t.category ORDER BY n DESC
        """
    ).fetchall()
    return {
        "completed": completed,
        "target": profile["target_count"],
        "remaining": max(profile["target_count"] - completed, 0),
        "categories": [{"name": row["category"], "count": row["n"]} for row in categories],
    }


def _last_intent(conn: Any) -> str:
    row = conn.execute(
        """
        SELECT intent FROM user_tasks
        WHERE status = 'completed' AND intent IS NOT NULL AND intent != ''
        ORDER BY completed_at DESC LIMIT 1
        """
    ).fetchone()
    return row["intent"] if row else ""


@app.get("/api/bootstrap")
def bootstrap() -> dict:
    conn = connect()
    try:
        active = _active(conn)
        stats = _stats(conn)
        recent = _memories(conn, limit=4)
        intent = active["intent"] if active else _last_intent(conn)
        exclude = {active["task"]["id"]} if active else set()
        suggestions = recommend(conn, intent=intent, energy=3, limit=3, exclude_ids=exclude)
        return {
            "profile": _profile(conn),
            "active": active,
            "stats": stats,
            "memories": recent,
            "intents": INTENTS,
            "suggestions": suggestions,
        }
    finally:
        conn.close()


@app.get("/api/tasks")
def tasks(
    intent: str = Query(default=""),
    energy: int = Query(default=3, ge=1, le=5),
    limit: int = Query(default=8, ge=1, le=24),
    refresh: int = Query(default=0),
    exclude: str = Query(default=""),
) -> dict:
    conn = connect()
    try:
        seed = int(datetime.now().timestamp() * 1000) if refresh else None
        exclude_ids = {
            int(part.strip())
            for part in exclude.split(",")
            if part.strip().isdigit()
        }
        items = recommend(
            conn,
            intent=intent,
            energy=energy,
            limit=limit,
            seed=seed,
            exclude_ids=exclude_ids,
        )
        return {"tasks": items, "intent": intent, "energy": energy}
    finally:
        conn.close()


@app.get("/api/tasks/{task_id}")
def task_detail(task_id: int) -> dict:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="没有找到这件事")
        return serialize_task(row)
    finally:
        conn.close()


@app.post("/api/select")
def select_task(payload: SelectRequest) -> dict:
    if payload.version not in {"min", "standard", "adventure"}:
        raise HTTPException(status_code=400, detail="版本不正确")
    conn = connect()
    try:
        task = conn.execute("SELECT * FROM tasks WHERE id = ?", (payload.task_id,)).fetchone()
        if not task:
            raise HTTPException(status_code=404, detail="没有找到这件事")
        conn.execute(
            "UPDATE user_tasks SET status = 'released' WHERE status = 'active'"
        )
        conn.execute(
            """
            INSERT INTO user_tasks (task_id, version, intent, reason, status, selected_at)
            VALUES (?, ?, ?, ?, 'active', ?)
            """,
            (payload.task_id, payload.version, payload.intent, payload.reason or "", now_iso()),
        )
        conn.commit()
        active = _active(conn)
        return {"active": active}
    finally:
        conn.close()


def _safe_recommend(
    conn: Any, intent: str, energy: int, exclude_ids: set[int]
) -> list[dict]:
    try:
        return recommend(conn, intent=intent, energy=energy, limit=3, exclude_ids=exclude_ids)
    except Exception:
        return []


@app.post("/api/complete")
def complete_task(payload: CompleteRequest) -> dict:
    conn = connect()
    try:
        row = conn.execute(
            """
            SELECT ut.*, t.title FROM user_tasks ut
            JOIN tasks t ON t.id = ut.task_id
            WHERE ut.id = ?
            """,
            (payload.user_task_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="没有找到这次记录")
        if row["status"] == "completed":
            existing = conn.execute(
                """
                SELECT ut.id AS user_task_id, ut.completed_at, t.*,
                       e.id AS entry_id, e.note, e.photo_path, e.mood_after,
                       e.color AS entry_color, e.repeat_wish, e.is_private,
                       e.created_at AS entry_created_at
                FROM user_tasks ut
                JOIN tasks t ON t.id = ut.task_id
                JOIN entries e ON e.user_task_id = ut.id
                WHERE ut.id = ?
                """,
                (payload.user_task_id,),
            ).fetchone()
            if existing:
                return {
                    "memory": _memory_from_row(existing),
                    "recommendations": _safe_recommend(
                        conn, row["intent"] or "", payload.energy, {row["task_id"]}
                    ),
                    "stats": _stats(conn),
                }
            raise HTTPException(status_code=400, detail="这件事已经记录过了")
        if row["status"] != "active":
            raise HTTPException(status_code=400, detail="这件事已经记录过了")

        photo_path = _save_photo(payload.photo_data) if payload.photo_data else None
        completed_at = now_iso()
        conn.execute(
            """
            UPDATE user_tasks SET status = 'completed', completed_at = ? WHERE id = ?
            """,
            (completed_at, payload.user_task_id),
        )
        conn.execute(
            """
            INSERT INTO entries (
                user_task_id, note, photo_path, mood_after, color,
                repeat_wish, is_private, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_task_id) DO UPDATE SET
                note = excluded.note,
                photo_path = COALESCE(excluded.photo_path, entries.photo_path),
                mood_after = excluded.mood_after,
                color = excluded.color,
                repeat_wish = excluded.repeat_wish,
                is_private = excluded.is_private,
                created_at = excluded.created_at
            """,
            (
                payload.user_task_id,
                payload.note.strip(),
                photo_path,
                payload.mood_after,
                payload.color or "#D8A47F",
                1 if payload.repeat_wish else 0,
                1 if payload.is_private else 0,
                completed_at,
            ),
        )
        conn.commit()

        memory_row = conn.execute(
            """
            SELECT ut.id AS user_task_id, ut.completed_at, t.*,
                   e.id AS entry_id, e.note, e.photo_path, e.mood_after,
                   e.color AS entry_color, e.repeat_wish, e.is_private,
                   e.created_at AS entry_created_at
            FROM user_tasks ut
            JOIN tasks t ON t.id = ut.task_id
            JOIN entries e ON e.user_task_id = ut.id
            WHERE ut.id = ?
            """,
            (payload.user_task_id,),
        ).fetchone()
        memory = _memory_from_row(memory_row)
        recommendations = _safe_recommend(
            conn, row["intent"] or "", payload.energy, {row["task_id"]}
        )
        return {
            "memory": memory,
            "recommendations": recommendations,
            "stats": _stats(conn),
        }
    finally:
        conn.close()


@app.post("/api/skip/{user_task_id}")
def skip_task(user_task_id: int) -> dict:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM user_tasks WHERE id = ? AND status = 'active'",
            (user_task_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="没有找到正在进行的这件事")
        conn.execute("UPDATE user_tasks SET status = 'skipped' WHERE id = ?", (user_task_id,))
        conn.commit()
        items = recommend(conn, intent=row["intent"] or "", energy=3, limit=3)
        return {"recommendations": items}
    finally:
        conn.close()


@app.post("/api/checkin")
def checkin(payload: CheckinRequest) -> dict:
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO checkins (intent, energy, created_at) VALUES (?, ?, ?)",
            (payload.intent, payload.energy, now_iso()),
        )
        conn.commit()
        return {
            "ok": True,
            "suggestions": recommend(
                conn, intent=payload.intent, energy=payload.energy, limit=3
            ),
        }
    finally:
        conn.close()


@app.post("/api/profile")
def update_profile(payload: ProfileUpdate) -> dict:
    conn = connect()
    try:
        conn.execute(
            "UPDATE profile SET name = ?, target_count = ?, duration_days = ? WHERE id = 1",
            (payload.name.strip() or "你", payload.target_count, payload.duration_days),
        )
        conn.commit()
        return {"profile": _profile(conn), "stats": _stats(conn)}
    finally:
        conn.close()


@app.post("/api/custom-task")
def create_custom_task(payload: CustomTaskRequest) -> dict:
    conn = connect()
    try:
        minutes = payload.minutes_standard
        title = payload.title.strip()
        summary = payload.summary.strip() or "一件由你自己写下的小事。"
        category = payload.category.strip() or "自定义"
        slug = f"custom-{uuid.uuid4().hex[:12]}"
        min_minutes = max(1, min(5, minutes // 3 or 1))
        min_action = f"先花 {min_minutes} 分钟，做一个最小的开始"
        adventure_action = f"给这件事留出 {max(minutes * 2, 30)} 分钟，做得更完整一点"
        color = random.choice(PHOTO_COLORS)
        conn.execute(
            """
            INSERT INTO tasks (
                slug, title, summary, category, tags,
                minutes_min, minutes_standard, minutes_adventure,
                social, place, cost, difficulty, color,
                min_action, standard_action, adventure_action, prompt, is_custom, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'any', 'anywhere', 0, 1, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                slug,
                title,
                summary,
                category,
                json.dumps(["custom", "self"], ensure_ascii=False),
                min_minutes,
                minutes,
                max(minutes * 2, 30),
                color,
                min_action,
                summary,
                adventure_action,
                "做完之后，你想留下什么？",
                now_iso(),
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM tasks WHERE slug = ?", (slug,)).fetchone()
        return {"task": serialize_task(row)}
    finally:
        conn.close()



def _short_text(value: Any, limit: int, fallback: str) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit] or fallback


def _clamp_int(value: Any, low: int, high: int, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = fallback
    return max(low, min(high, number))


def _ai_liked_categories(conn: Any) -> list[str]:
    try:
        rows = conn.execute(
            """
            SELECT t.category, e.mood_after, e.repeat_wish
            FROM user_tasks ut
            JOIN tasks t ON t.id = ut.task_id
            LEFT JOIN entries e ON e.user_task_id = ut.id
            WHERE ut.status = 'completed'
            ORDER BY ut.completed_at DESC
            LIMIT 12
            """
        ).fetchall()
    except Exception:
        return []
    liked = []
    for row in rows:
        if (row["mood_after"] or 0) >= 4 or row["repeat_wish"]:
            category = row["category"]
            if category not in liked:
                liked.append(category)
    return liked[:4]


def _parse_json_text(text: str) -> dict:
    value = (text or "").strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        value = "\n".join(lines).strip()
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        start = value.find("{")
        end = value.rfind("}")
        if start >= 0 and end > start:
            return json.loads(value[start : end + 1])
        raise


def _extract_chat_text(data: dict) -> str:
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("AI 返回内容为空")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        if parts:
            return "\n".join(parts)
    raise RuntimeError("AI 返回内容为空")


def _post_chat_completion(messages: list[dict], with_response_format: bool) -> dict:
    request_body: dict[str, Any] = {
        "model": OPENAI_MODEL,
        "messages": messages,
        "temperature": 0.85,
        "max_tokens": 2200,
        "stream": False,
    }
    if with_response_format:
        request_body["response_format"] = {"type": "json_object"}
    request = urllib.request.Request(
        f"{OPENAI_BASE_URL}/chat/completions",
        data=json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if with_response_format and exc.code in {400, 404, 422}:
            return _post_chat_completion(messages, with_response_format=False)
        body = exc.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(f"AI 接口返回 {exc.code}: {body}") from exc


def _generate_ai_batch(
    intent: str,
    energy: int,
    minutes: int,
    count: int,
    existing_titles: list[str],
    liked_categories: list[str],
) -> list[dict]:
    intent_meta = next((item for item in INTENTS if item["id"] == intent), None)
    intent_label = intent_meta["label"] if intent_meta else "没有特别偏好"
    avoid = "、".join(existing_titles[:90]) or "无"
    liked = "、".join(liked_categories) or "还没有明显偏好"
    instructions = (
        "你是一个温柔、克制的生活灵感助手，为心情低落或孤单的年轻用户设计"
        "可以独自完成的真实小事。不要使用治疗、诊断、疗愈承诺或强迫积极的语言，"
        "不要建议危险、违法、饮酒、用药、陌生人见面或高消费活动。任务必须具体、低门槛、"
        "可以随时停止。只输出一个合法 JSON 对象，不要输出解释、Markdown 或代码块。"
    )
    user_prompt = f"""
为“一百个今天”生成 {count} 件适合现在做的小事。

用户此刻的意图：{intent_label}
今天的电量：{energy}/5（1 很累，5 还不错）
这一次可以留出的时间：约 {minutes} 分钟
用户过去感觉不错的类别：{liked}
已有任务，不要重复或近似改写：{avoid}

每个任务都要有：
- 18 个汉字以内的标题，和一句不超过 45 个汉字的说明
- 最小版、标准版、冒险版三个动作；最小版必须几乎立刻能开始
- 一个记录时可以使用的问题
- 合理的时间、社交方式、地点、花费和难度
- 颜色使用柔和的十六进制色值，例如 #B8C6B0

优先给出安静、身体、观察、照顾、创造类的小事。不要每次都给“喝水、散步、写日记”这种泛泛建议，
要有一点具体场景和画面感，但不要复杂。

必须严格符合下面的 JSON Schema，只返回 JSON：
{json.dumps(AI_TASK_BATCH_SCHEMA, ensure_ascii=False)}
"""
    messages = [
        {"role": "system", "content": instructions},
        {"role": "user", "content": user_prompt},
    ]
    data = _post_chat_completion(messages, with_response_format=True)
    content = _parse_json_text(_extract_chat_text(data))
    tasks = content.get("tasks")
    if not isinstance(tasks, list):
        raise RuntimeError("AI 没有返回任务列表")
    return tasks[:count]

def _clean_ai_task(raw: dict, intent: str, requested_minutes: int) -> dict:
    standard = _clamp_int(
        raw.get("minutes_standard"), 1, 180, max(5, min(requested_minutes, 90))
    )
    minimum = _clamp_int(raw.get("minutes_min"), 1, standard, min(5, standard))
    adventure = _clamp_int(
        raw.get("minutes_adventure"), standard, 240, max(standard + 10, standard * 2)
    )
    if adventure <= standard:
        adventure = min(240, standard + 5)
    category = raw.get("category")
    if category not in AI_CATEGORIES:
        category = "安静"
    social = raw.get("social")
    if social not in {"solo", "any", "together"}:
        social = "solo"
    place = raw.get("place")
    if place not in {"anywhere", "home", "outside"}:
        place = "anywhere"
    color = str(raw.get("color") or "").strip()
    if not (color.startswith("#") and len(color) in {4, 7}):
        color = random.choice(PHOTO_COLORS)
    return {
        "title": _short_text(raw.get("title"), 36, "今天的一件小事"),
        "summary": _short_text(raw.get("summary"), 110, "一件可以让今天松一点的小事。"),
        "category": category,
        "tags": ["ai", intent or "quiet", category],
        "minutes": [minimum, standard, adventure],
        "social": social,
        "place": place,
        "cost": 1 if _clamp_int(raw.get("cost"), 0, 1, 0) else 0,
        "difficulty": _clamp_int(raw.get("difficulty"), 1, 3, 1),
        "color": color,
        "actions": [
            _short_text(raw.get("min_action"), 90, "先做一个最小的开始"),
            _short_text(raw.get("standard_action"), 120, "按自己的节奏完成它"),
            _short_text(raw.get("adventure_action"), 150, "多留一点时间，做得更完整"),
        ],
        "prompt": _short_text(raw.get("prompt"), 80, "做完之后，你想记住什么？"),
    }


def _persist_ai_tasks(conn: Any, raw_tasks: list[dict], intent: str, minutes: int) -> list[dict]:
    existing = {
        _short_text(row["title"], 60, "").lower()
        for row in conn.execute("SELECT title FROM tasks").fetchall()
    }
    saved = []
    for raw in raw_tasks:
        if not isinstance(raw, dict):
            continue
        task = _clean_ai_task(raw, intent, minutes)
        title_key = task["title"].lower()
        if title_key in existing:
            continue
        slug = f"ai-{uuid.uuid4().hex[:12]}"
        conn.execute(
            """
            INSERT INTO tasks (
                slug, title, summary, category, tags,
                minutes_min, minutes_standard, minutes_adventure,
                social, place, cost, difficulty, color,
                min_action, standard_action, adventure_action, prompt, is_custom, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                slug,
                task["title"],
                task["summary"],
                task["category"],
                json.dumps(task["tags"], ensure_ascii=False),
                *task["minutes"],
                task["social"],
                task["place"],
                task["cost"],
                task["difficulty"],
                task["color"],
                *task["actions"],
                task["prompt"],
                now_iso(),
            ),
        )
        existing.add(title_key)
        row = conn.execute("SELECT * FROM tasks WHERE slug = ?", (slug,)).fetchone()
        serialized = serialize_task(row)
        serialized["reason"] = "这是 AI 为此刻想到的一件小事。你可以按自己的方式完成。"
        saved.append(serialized)
    conn.commit()
    return saved


def _fallback_ai_tasks(conn: Any, intent: str, energy: int, count: int) -> list[dict]:
    active = _active(conn)
    exclude_ids = {active["task"]["id"]} if active else set()
    try:
        return recommend(
            conn,
            intent=intent,
            energy=energy,
            limit=count,
            exclude_ids=exclude_ids,
            seed=int(time.time() * 1000),
        )
    except Exception:
        logger.exception("精选小事回退失败")
        return []


@app.post("/api/ai-inspiration")
def ai_inspiration(payload: AIInspirationRequest) -> dict:
    valid_intents = {item["id"] for item in INTENTS}
    intent = payload.intent if payload.intent in valid_intents else ""
    energy = max(1, min(5, payload.energy))
    minutes = max(1, min(180, payload.minutes))
    count = max(1, min(5, payload.count))
    conn = connect()
    try:
        if OPENAI_API_KEY:
            try:
                existing_titles = [
                    _short_text(row["title"], 60, "")
                    for row in conn.execute("SELECT title FROM tasks ORDER BY id").fetchall()
                ]
                generated = _generate_ai_batch(
                    intent=intent,
                    energy=energy,
                    minutes=minutes,
                    count=count,
                    existing_titles=existing_titles,
                    liked_categories=_ai_liked_categories(conn),
                )
                saved = _persist_ai_tasks(conn, generated, intent, minutes)
                if saved:
                    return {"tasks": saved[:count], "mode": "ai"}
            except Exception:
                logger.exception("AI 灵感生成失败")
            tasks = _fallback_ai_tasks(conn, intent, energy, count)
            return {
                "tasks": tasks,
                "mode": "curated",
                "notice": "AI 这次没有接上，先从精选小事里挑给你。",
            }
        tasks = _fallback_ai_tasks(conn, intent, energy, count)
        return {
            "tasks": tasks,
            "mode": "curated",
            "notice": "AI 灵感还没配置 OPENAI_API_KEY，先给你挑了几件精选小事。",
        }
    finally:
        conn.close()

@app.get("/api/memories")
def memories(limit: int = Query(default=200, ge=1, le=500)) -> dict:
    conn = connect()
    try:
        return {"memories": _memories(conn, limit=limit)}
    finally:
        conn.close()


@app.post("/api/demo/seed")
def seed_demo() -> dict:
    conn = connect()
    try:
        existing = conn.execute(
            "SELECT COUNT(*) AS n FROM user_tasks WHERE status = 'completed'"
        ).fetchone()["n"]
        if existing:
            return {"seeded": False, "memories": _memories(conn)}

        demo = [
            ("window-ten", 3, "今天云很厚，楼下有人在收衣服。", 4, "#B8C6B0", 1, "quiet"),
            ("look-at-sky", 5, "天蓝得很安静。", 4, "#A9C4D4", 0, "outside"),
            ("one-song-dark", 7, "听到副歌的时候，突然没那么想解释了。", 5, "#9FA8B8", 1, "quiet"),
            ("tidy-bedside", 9, "只整理了床头，但房间像轻了一点。", 3, "#C8BFAF", 0, "achieve"),
            ("share-a-song", 12, "朋友回了我一个笑脸。", 4, "#C5A4B0", 1, "connect"),
        ]
        for slug, days_ago, note, mood, color, repeat, intent in demo:
            task = conn.execute("SELECT * FROM tasks WHERE slug = ?", (slug,)).fetchone()
            if not task:
                continue
            stamp = datetime.now(timezone.utc) - timedelta(days=days_ago, hours=days_ago)
            insert_sql = """
                INSERT INTO user_tasks (task_id, version, intent, reason, status, selected_at, completed_at)
                VALUES (?, 'standard', ?, '示例记忆', 'completed', ?, ?)
            """
            params = (task["id"], intent, stamp.isoformat(), stamp.isoformat())
            if using_postgres():
                user_task_id = conn.execute(
                    insert_sql + " RETURNING id", params
                ).fetchone()["id"]
            else:
                user_task_id = conn.execute(insert_sql, params).lastrowid
            conn.execute(
                """
                INSERT INTO entries (user_task_id, note, photo_path, mood_after, color, repeat_wish, is_private, created_at)
                VALUES (?, ?, NULL, ?, ?, ?, 1, ?)
                """,
                (user_task_id, note, mood, color, repeat, stamp.isoformat()),
            )
        conn.commit()
        return {"seeded": True, "memories": _memories(conn)}
    finally:
        conn.close()


@app.post("/api/demo/reset")
def reset_demo() -> dict:
    conn = connect()
    try:
        photo_rows = conn.execute(
            "SELECT photo_path FROM entries WHERE photo_path IS NOT NULL"
        ).fetchall()
        remote_urls = [
            row["photo_path"]
            for row in photo_rows
            if str(row["photo_path"]).startswith(("http://", "https://"))
        ]
        conn.execute("DELETE FROM entries")
        conn.execute("DELETE FROM user_tasks")
        conn.execute("DELETE FROM checkins")
        conn.execute("DELETE FROM tasks WHERE is_custom = 1")
        conn.execute(
            "UPDATE profile SET name = '你', target_count = 100, duration_days = 365 WHERE id = 1"
        )
        conn.commit()
        if MEDIA_DIR.exists():
            for path in MEDIA_DIR.glob("*.jpg"):
                try:
                    path.unlink()
                except OSError:
                    pass
        if remote_urls:
            _delete_blobs(remote_urls)
        return {"ok": True}
    finally:
        conn.close()


def _save_photo(data_url: str) -> str:
    if not data_url.startswith("data:image/"):
        raise HTTPException(status_code=400, detail="请上传图片文件")
    try:
        header, encoded = data_url.split(",", 1)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="图片格式不正确") from exc
    if not header:
        raise HTTPException(status_code=400, detail="图片格式不正确")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="图片内容无法读取") from exc
    if len(raw) > MAX_PHOTO_BYTES:
        raise HTTPException(status_code=413, detail="图片超过 3MB，请换一张小一点的")
    try:
        image = Image.open(io.BytesIO(raw))
        image = ImageOps.exif_transpose(image)
        image.thumbnail((1600, 1600))
        image = image.convert("RGB")
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=82, optimize=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="这张图片暂时无法处理，请换一张试试") from exc

    if BLOB_TOKEN:
        return _upload_to_blob(output.getvalue())
    if IS_VERCEL:
        raise HTTPException(
            status_code=500,
            detail="图片存储未配置：请在 Vercel 项目里连接 Blob，并注入 BLOB_READ_WRITE_TOKEN。",
        )
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.jpg"
    (MEDIA_DIR / filename).write_bytes(output.getvalue())
    return filename


def _delete_blobs(urls: list[str]) -> None:
    if not BLOB_TOKEN:
        return
    request = urllib.request.Request(
        "https://blob.vercel-storage.com/delete",
        data=json.dumps({"urls": urls}).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {BLOB_TOKEN}",
            "Content-Type": "application/json",
            "x-api-version": "7",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15):
            pass
    except Exception:
        return


def _upload_to_blob(data: bytes) -> str:
    pathname = f"yibai/{uuid.uuid4().hex}.jpg"
    request = urllib.request.Request(
        f"https://blob.vercel-storage.com/{pathname}",
        data=data,
        method="PUT",
        headers={
            "Authorization": f"Bearer {BLOB_TOKEN}",
            "x-content-type": "image/jpeg",
            "x-add-random-suffix": "1",
            "x-cache-control-max-age": "31536000",
            "x-api-version": "7",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="照片暂时没能上传到 Blob，请稍后再试。",
        ) from exc
    url = payload.get("url")
    if not url:
        raise HTTPException(status_code=502, detail="Blob 没有返回图片地址，请稍后再试。")
    return url
