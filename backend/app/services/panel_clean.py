"""组件清洗业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "panel_clean"
REQUIRED_FIELDS = ["清洗编号", "清洗区域", "组件数量"]

# 正向环节：待安排 → 清洗中 → 待验收 → 已完成；已取消是旁路状态
STATUS_ORDER = ["待安排", "清洗中", "待验收", "已完成", "已取消"]
TERMINAL_STATUSES = ["已完成", "已取消"]

ACTION_RULES = {
    "安排清洗": "清洗中",
    "提交验收": "待验收",
    "验收通过": "已完成",
    "取消任务": "已取消",
    "恢复清洗": "清洗中",
}

# 每个状态确认后才出现下一步动作；取消任务只允许从待安排、清洗中进入
STATUS_ACTIONS = {
    "待安排": ["安排清洗", "取消任务"],
    "清洗中": ["提交验收", "取消任务"],
    "待验收": ["验收通过"],
    "已完成": [],
    "已取消": ["恢复清洗"],
}

NEGATIVE_ACTIONS = ["取消任务"]

# 恢复清洗时必须原样保留的字段
RESTORE_KEEP_FIELDS = ["清洗区域", "清洗日期"]
SNAPSHOT_KEY = "_cancel_snapshot"


class PanelCleanService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("清洗编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [self._with_actions(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return self._with_actions(entry) if entry is not None else None

    def _with_actions(self, entry: dict[str, Any]) -> dict[str, Any]:
        """附上当前环节允许执行的动作，页面只渲染这些按钮；内部快照字段不下发。"""
        public = {key: value for key, value in entry.items() if not str(key).startswith("_")}
        public["available_actions"] = list(STATUS_ACTIONS.get(str(entry.get("status") or ""), []))
        return public

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["清洗状态"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"清洗任务 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于组件清洗可执行范围"
        current = str(entry.get("status") or STATUS_ORDER[0])
        if action not in STATUS_ACTIONS.get(current, []):
            return None, f"清洗任务正处于「{current}」，不能执行「{action}」"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        if action == "取消任务":
            # 取消时留一份快照，恢复清洗时把原清洗区域和清洗日期原样带回
            entry[SNAPSHOT_KEY] = {field: entry.get(field) for field in RESTORE_KEEP_FIELDS}
        if action == "恢复清洗":
            snapshot = entry.pop(SNAPSHOT_KEY, None) or {}
            for field in RESTORE_KEEP_FIELDS:
                if field in snapshot:
                    entry[field] = snapshot[field]
        entry["status"] = target
        entry["清洗状态"] = target
        entry["pending"] = target not in TERMINAL_STATUSES
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return self._with_actions(entry), f"清洗任务已{action}"
