"""Conservative fallback candidates; context-aware extraction belongs to Codex."""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime


def rule_task_title(text: str) -> str | None:
    """Keep only a complete request with a named object, never a keyword fragment."""
    title = text.strip()
    sentence = title.rstrip("。.!！?？ ")
    if not sentence or len(title) > 200 or any(mark in title for mark in ("「", "」", "『", "』")):
        return None
    if re.search(
        r"必要(?:が|は)?(?:ない|なかった|ありません|なく)|不要|しなくて|しなくても|"
        r"しないで|やらなく|取り消|撤回|中止|完了|済み|終わっ|終わり|"
        r"(?:と言|って言|と聞|と言われ)|(?:なら|たら|の場合)",
        sentence,
    ):
        return None
    if not re.search(
        r"(?:してください|して下さい|しておいて(?:ください)?|お願いします|確認して|"
        r"対応して|やっておく|しなければならない|する必要が(?:ある|あります))$",
        sentence,
    ):
        return None
    # A request like 'あれを確認して' is not useful without its surrounding context.
    if re.search(r"(?:これ|それ|あれ|ここ|そこ|あそこ)(?:を|に|へ|で)", sentence):
        return None
    # Require something the action concerns. Bare 'お願いします'/'対応してください'
    # are left to contextual extraction instead of being presented as explicit tasks.
    if not re.search(r"[^\s。、！？!?]{2,}(?:を|の|へ|に)", sentence):
        return None
    return title


def retire_unreviewed_rule_fragments(connection: sqlite3.Connection) -> int:
    """Retain rejected legacy evidence while removing it from actionable inboxes."""
    rows = connection.execute(
        "SELECT id,title FROM conversation_items WHERE source='rule' AND status='awaiting_review'"
    ).fetchall()
    rejected = [row[0] for row in rows if rule_task_title(row[1]) is None]
    now = datetime.now(UTC).isoformat()
    for item_id in rejected:
        connection.execute(
            "UPDATE conversation_items SET status='superseded',updated_at=? "
            "WHERE id=? AND source='rule' AND status='awaiting_review'",
            (now, item_id),
        )
        connection.execute(
            "UPDATE task_proposals SET status='superseded' WHERE id=? AND status='awaiting_review'",
            (item_id,),
        )
    connection.execute(
        "UPDATE conversation_items SET certainty='ambiguous' "
        "WHERE source='rule' AND status='awaiting_review' AND certainty<>'ambiguous'"
    )
    return len(rejected)
