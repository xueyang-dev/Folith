"""Dialogs shared by the preparation and workspace outline surfaces."""
from PySide6.QtWidgets import QInputDialog, QMessageBox

from folith import outline_corrections
from . import i18n


def edit(parent, job_id, extracted, outline, operation, node_id):
    node = next((n for n in outline.get("nodes") or [] if n.get("id") == node_id), {})
    if operation != "restore" and not node:
        return False
    try:
        if operation == "restore":
            if QMessageBox.question(parent, i18n.tr('恢复原始结构'),
                    i18n.tr('清除这份文档的所有结构修正？')) != QMessageBox.StandardButton.Yes:
                return False
            outline_corrections.clear(job_id)
        elif operation == "delete":
            outline_corrections.save(job_id, extracted, node_id, delete=True)
        elif operation == "rename":
            value, ok = QInputDialog.getText(parent, i18n.tr('重命名'),
                i18n.tr('标题'), text=str(node.get("title") or ""))
            if not ok:
                return False
            outline_corrections.save(job_id, extracted, node_id, title=value)
        elif operation == "level":
            value, ok = QInputDialog.getInt(parent, i18n.tr('修改层级'),
                i18n.tr('层级'), int(node.get("level") or 1), 1, 20)
            if not ok:
                return False
            outline_corrections.save(job_id, extracted, node_id, level=value)
        elif operation == "boundary":
            count = len(extracted.get("paragraphs") or [])
            if not count:
                return False
            value, ok = QInputDialog.getInt(parent, i18n.tr('移动起始段落'),
                i18n.tr('起始段落编号（从 1 开始）'),
                int(node.get("para_start") or 0) + 1, 1, count)
            if not ok:
                return False
            outline_corrections.save(job_id, extracted, node_id, para_start=value - 1)
        else:
            return False
        return True
    except (OSError, ValueError) as exc:
        QMessageBox.warning(parent, i18n.tr('无法修正结构'), str(exc))
        return False
