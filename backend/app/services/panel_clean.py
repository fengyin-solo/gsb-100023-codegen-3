"""组件清洗业务规则：状态流转、字段校验与筛选口径都收在这里。

任务按 待安排 → 清洗中 → 待验收 → 已完成 四段流转，只有前一步完成后
才允许执行下一步动作；任务在待安排、清洗中两个环节可取消，取消后可恢复，
恢复时沿用取消前的环节，并保留原清洗区域与清洗日期。
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.store import store

MODULE = "panel_clean"
REQUIRED_FIELDS = ["清洗编号", "清洗区域", "组件数量"]
OPTIONAL_FIELDS = ["清洗方式", "清洗日期", "清洗班组", "清洗效果"]
ENTRY_FIELDS = REQUIRED_FIELDS + OPTIONAL_FIELDS

# 四段主流程，外加用于取消任务的「已取消」终态。
STATUS_TODO = "待安排"
STATUS_DOING = "清洗中"
STATUS_CHECKING = "待验收"
STATUS_DONE = "已完成"
STATUS_CANCELED = "已取消"
STATUS_ORDER = [STATUS_TODO, STATUS_DOING, STATUS_CHECKING, STATUS_DONE]
STATUSES = STATUS_ORDER + [STATUS_CANCELED]

# 每个环节只暴露下一步动作，前一步未完成时不出现后续动作。
NEXT_ACTIONS = {
    STATUS_TODO: ("安排清洗", STATUS_DOING),
    STATUS_DOING: ("提交验收", STATUS_CHECKING),
    STATUS_CHECKING: ("验收通过", STATUS_DONE),
}
# 只有待安排、清洗中两个环节可以取消任务。
CANCELABLE_STATUSES = {STATUS_TODO, STATUS_DOING}
RESTORE_ACTION = "恢复任务"
CANCEL_ACTION = "取消任务"
# 取消期间必须原样保留的字段。
PRESERVED_FIELDS = ["清洗区域", "清洗日期"]


class PanelCleanService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = [self._present(row) for row in store.rows(MODULE)]
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("清洗编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return self._present(entry) if entry is not None else None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        for field in ENTRY_FIELDS:
            value = values.get(field)
            if value is not None and str(value).strip():
                entry[field] = value
        entry["status"] = STATUS_TODO
        entry["pending"] = True
        entry["abnormal"] = False
        self._sync_fields(entry)
        rows.append(entry)
        return self._present(entry), []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"清洗任务 {entry_id} 不存在或已归档"

        status = str(entry.get("status") or "")

        if action == CANCEL_ACTION:
            return self._cancel(entry_id, entry, status)
        if action == RESTORE_ACTION:
            return self._restore(entry_id, entry, status)

        rule = NEXT_ACTIONS.get(status)
        if rule is None:
            return None, f"当前为{status}环节，没有可继续执行的流转动作"
        expected_action, target = rule
        if action != expected_action:
            return None, f"{status}环节只能执行「{expected_action}」，暂不支持「{action}」"

        entry["status"] = target
        entry["pending"] = target != STATUS_DONE
        entry["abnormal"] = False
        self._sync_fields(entry)
        return self._present(entry), f"清洗任务已{action}"

    def _cancel(
        self, entry_id: int, entry: dict[str, Any], status: str
    ) -> tuple[dict[str, Any] | None, str]:
        if status not in CANCELABLE_STATUSES:
            return None, f"{status}环节的任务不允许取消"
        # 记录取消前的环节与关键信息，恢复时原样带回。
        entry["canceled_from"] = status
        entry["canceled_snapshot"] = {
            field: deepcopy(entry.get(field)) for field in PRESERVED_FIELDS
        }
        entry["status"] = STATUS_CANCELED
        entry["pending"] = False
        entry["abnormal"] = True
        self._sync_fields(entry)
        return self._present(entry), "清洗任务已取消"

    def _restore(
        self, entry_id: int, entry: dict[str, Any], status: str
    ) -> tuple[dict[str, Any] | None, str]:
        if status != STATUS_CANCELED:
            return None, "只有已取消的任务才能恢复"
        target = entry.pop("canceled_from", STATUS_DOING)
        if target not in CANCELABLE_STATUSES:
            target = STATUS_DOING
        snapshot = entry.pop("canceled_snapshot", {})
        # 恢复到清洗中时必须保留原清洗区域和日期。
        for field in PRESERVED_FIELDS:
            if field in snapshot:
                entry[field] = snapshot[field]
        entry["status"] = target
        entry["pending"] = True
        entry["abnormal"] = False
        self._sync_fields(entry)
        return self._present(entry), f"清洗任务已恢复，当前环节为{target}"

    def _sync_fields(self, entry: dict[str, Any]) -> None:
        """列表里的「清洗状态」列与流程状态保持一致。"""
        entry["清洗状态"] = entry["status"]

    def _present(self, entry: dict[str, Any]) -> dict[str, Any]:
        """对外视图不暴露取消快照等内部字段。"""
        view = dict(entry)
        view.pop("canceled_from", None)
        view.pop("canceled_snapshot", None)
        self._sync_fields(view)
        return view
