from __future__ import annotations

import random
from typing import Any
from collections import Counter

from .db import serialize_task

INTENTS = [
    {
        "id": "quiet",
        "label": "安静陪伴",
        "short": "想慢慢待着",
        "description": "不急着好起来，先和此刻待在一起",
        "emoji": "☁️",
    },
    {
        "id": "distract",
        "label": "换换脑子",
        "short": "想离开脑子一会儿",
        "description": "让感官接管几分钟，不用思考太多",
        "emoji": "🎧",
    },
    {
        "id": "achieve",
        "label": "一点成就",
        "short": "想完成一件小事",
        "description": "给今天一个很小的、可完成的形状",
        "emoji": "🌱",
    },
    {
        "id": "connect",
        "label": "轻轻连接",
        "short": "想和世界有一点联系",
        "description": "不必热闹，只要一个低压力的小小回应",
        "emoji": "🫧",
    },
    {
        "id": "self",
        "label": "和自己说话",
        "short": "想整理一下心里的事",
        "description": "写下来、画下来，或者只是承认它",
        "emoji": "🪞",
    },
    {
        "id": "outside",
        "label": "出去透口气",
        "short": "想换一个空间",
        "description": "让身体先动起来，答案可以晚一点到",
        "emoji": "🚶",
    },
]

INTENT_TAGS = {
    "quiet": {"quiet": 4.0, "slow": 3.0, "rest": 3.5, "observe": 1.5, "care": 1.5, "solo": 1.0},
    "distract": {"play": 4.0, "music": 3.0, "novel": 3.0, "sensory": 2.5, "short": 2.0},
    "achieve": {"make": 3.5, "organize": 4.0, "finish": 4.0, "learn": 2.5, "body": 1.5},
    "connect": {"kind": 4.0, "share": 3.5, "social": 2.0, "together": 4.0, "low": 2.0},
    "self": {"reflect": 4.5, "write": 3.5, "self": 3.0, "memory": 3.0, "quiet": 1.5},
    "outside": {"outside": 4.5, "body": 3.0, "nature": 2.5, "city": 3.0, "observe": 1.5},
}

CATEGORY_REASONS = {
    "安静": "这是一件不会要求你表现得更好的小事。",
    "身体": "让身体先参与进来，比继续在脑子里打转容易一点。",
    "创造": "不需要做得好，只需要留下一个今天独有的痕迹。",
    "观察": "把注意力交给外面的一点点细节，给自己留个空隙。",
    "城市": "不用去很远，熟悉的地方也可以有一次小小的陌生。",
    "连接": "这是一次低压力、可以随时退回自己的连接。",
    "照顾": "照顾好自己不必宏大，一个很小的动作也算数。",
    "勇气": "不是逼自己勇敢，只是把舒适区往外挪一厘米。",
}


def _task_context(conn: Any) -> dict:
    completed = conn.execute(
        """
        SELECT t.*, t.id AS task_id, ut.id AS user_task_id,
               e.mood_after, e.repeat_wish, e.note
        FROM user_tasks ut
        JOIN tasks t ON t.id = ut.task_id
        LEFT JOIN entries e ON e.user_task_id = ut.id
        WHERE ut.status = 'completed'
        ORDER BY ut.completed_at DESC
        """
    ).fetchall()
    skipped = conn.execute(
        "SELECT DISTINCT task_id FROM user_tasks WHERE status = 'skipped'"
    ).fetchall()
    category_counts = Counter(row["category"] for row in completed)
    liked_categories = Counter()
    for row in completed:
        if (row["mood_after"] or 0) >= 4 or row["repeat_wish"]:
            liked_categories[row["category"]] += 1
    return {
        "completed_ids": {row["task_id"] for row in completed},
        "skipped_ids": {row["task_id"] for row in skipped},
        "category_counts": category_counts,
        "liked_categories": liked_categories,
        "completed_count": len(completed),
    }


def _score_task(task: sqlite3.Row, intent: str, energy: int, ctx: dict, rng: random.Random) -> float:
    tags = set(__import__("json").loads(task["tags"] or "[]"))
    score = rng.uniform(-0.45, 0.45)

    for tag, weight in INTENT_TAGS.get(intent, {}).items():
        if tag in tags:
            score += weight

    category = task["category"]
    score += min(ctx["liked_categories"].get(category, 0), 3) * 1.4
    score -= min(ctx["category_counts"].get(category, 0), 5) * 0.55

    if task["id"] in ctx["skipped_ids"]:
        score -= 3.5

    if energy <= 2:
        if task["minutes_standard"] <= 10:
            score += 2.2
        elif task["minutes_standard"] > 30:
            score -= 2.0
        if task["difficulty"] == 1:
            score += 1.0
        elif task["difficulty"] >= 3:
            score -= 2.2
    elif energy >= 4:
        if task["difficulty"] >= 2:
            score += 0.8
        if task["minutes_standard"] >= 20:
            score += 0.35

    if ctx["completed_count"] == 0:
        score += 0.3 if task["difficulty"] == 1 else 0
    return score


def _reason_for(task: sqlite3.Row, intent: str, energy: int, ctx: dict) -> str:
    category = task["category"]
    liked_count = ctx["liked_categories"].get(category, 0)
    base = CATEGORY_REASONS.get(category, "一件很小的事，也可以成为今天的一个坐标。")
    if liked_count >= 2:
        return f"你最近在“{category}”类的小事里感觉不错。{base}"
    if liked_count == 1:
        return f"上次“{category}”类的记录里有一点点好的感觉，今天可以再靠近一点。"
    if ctx["category_counts"].get(category, 0) == 0:
        return f"你还没有试过“{category}”类的小事，给今天留一个新入口。{base}"
    if energy <= 2:
        return f"今天可以从最小版本开始，只要 {task['minutes_min']} 分钟。{base}"
    return base


def recommend(
    conn: Any,
    intent: str = "",
    energy: int = 3,
    limit: int = 3,
    exclude_ids: set[int] | None = None,
    seed: int | None = None,
) -> list[dict]:
    ctx = _task_context(conn)
    exclude_ids = exclude_ids or set()
    excluded = ctx["completed_ids"] | exclude_ids
    rows = conn.execute("SELECT * FROM tasks ORDER BY id").fetchall()
    candidates = [row for row in rows if row["id"] not in excluded]
    if not candidates:
        return []

    rng = random.Random(seed)
    ranked = sorted(
        ((_score_task(row, intent, energy, ctx, rng), row) for row in candidates),
        key=lambda item: item[0],
        reverse=True,
    )

    picked = []
    used_categories = set()
    for _, row in ranked:
        if row["category"] not in used_categories:
            picked.append(row)
            used_categories.add(row["category"])
        if len(picked) >= limit:
            break
    if len(picked) < limit:
        for _, row in ranked:
            if row not in picked:
                picked.append(row)
            if len(picked) >= limit:
                break

    result = []
    for row in picked:
        task = serialize_task(row)
        if energy <= 2:
            suggested_version = "min"
        elif energy >= 4 and task["difficulty"] <= 2:
            suggested_version = "adventure" if task["minutes"]["adventure"] <= 60 else "standard"
        else:
            suggested_version = "standard"
        task["reason"] = _reason_for(row, intent, energy, ctx)
        task["suggested_version"] = suggested_version
        result.append(task)
    return result
