"""Conservative fallback candidates; context-aware extraction belongs to Codex."""

from __future__ import annotations

import re


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



def visible_rule_item(source: str, status: str, title: str) -> bool:
    """Hide unreviewed fallback fragments without changing saved review decisions."""
    return source != "rule" or status != "awaiting_review" or rule_task_title(title) is not None
