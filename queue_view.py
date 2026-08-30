import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from status_events import (
    PENDING_STATUSES,
    STATUS_DISMISSED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_UPLOADED,
    STATUS_UPLOADING,
    StatusEvent,
)
import theme

MAX_ITEMS = 500
FILTER_ALL = "All"
FILTER_PENDING = "Pending"
FILTER_UPLOADED = "Uploaded"
FILTER_FAILED = "Failed"
FILTERS = (FILTER_ALL, FILTER_PENDING, FILTER_UPLOADED, FILTER_FAILED)

BADGE_LABELS = {
    STATUS_PENDING: "PENDING",
    STATUS_UPLOADING: "UPLOADING",
    STATUS_UPLOADED: "UPLOADED",
    STATUS_FAILED: "FAILED",
}


@dataclass
class QueueItem:
    key: str
    file_path: str
    filename: str
    monitor_id: str
    status: str
    collection: str
    reason: Optional[str] = None
    timestamp: float = 0.0


@dataclass
class QueueCounts:
    pending: int = 0
    uploaded: int = 0
    failed: int = 0
    total: int = 0


def relative_time(ts):
    delta = max(0, time.time() - ts)
    if delta < 5:
        return "just now"
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta / 60)}m ago"
    return f"{int(delta / 3600)}h ago"


def _item_key(event: StatusEvent):
    return f"{event.monitor_id}::{event.file_path}"


def _badge_colors(status):
    if status == STATUS_UPLOADING:
        return theme.UPLOADING, theme.UPLOADING_BG
    if status == STATUS_UPLOADED:
        return theme.GOOD, theme.GOOD_BG
    if status == STATUS_FAILED:
        return theme.BAD, theme.BAD_BG
    return theme.WARN, theme.WARN_BG


class QueueRow(QWidget):
    def __init__(self, item: QueueItem, parent=None):
        super().__init__(parent)
        self.setObjectName("queueRow")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(8)
        name = QLabel(item.filename)
        name.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        top.addWidget(name, 1)

        badge = QLabel(BADGE_LABELS.get(item.status, item.status.upper()))
        badge.setStyleSheet(theme.chip_style(*_badge_colors(item.status)))
        top.addWidget(badge)

        self.time_label = QLabel(relative_time(item.timestamp))
        self.time_label.setObjectName("dim")
        self.timestamp = item.timestamp
        top.addWidget(self.time_label)
        layout.addLayout(top)

        if item.collection:
            col = QLabel(item.collection)
            col.setObjectName("dim")
            layout.addWidget(col)
        if item.reason and item.status == STATUS_FAILED:
            reason = QLabel(item.reason)
            reason.setWordWrap(True)
            reason.setStyleSheet(f"color: {theme.BAD};")
            layout.addWidget(reason)

    def refresh_time(self):
        self.time_label.setText(relative_time(self.timestamp))


class QueueView(QWidget):
    counts_changed = Signal(object)

    def __init__(self, parent=None, on_counts_changed: Optional[Callable] = None):
        super().__init__(parent)
        self.on_counts_changed = on_counts_changed
        self._items: Dict[str, QueueItem] = {}
        self._order: List[str] = []
        self._monitors: Dict[str, dict] = {}
        self._filter = FILTER_ALL
        self._rows: List[QueueRow] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 8)
        layout.setSpacing(10)

        title = QLabel("Upload queue")
        title.setObjectName("queueTitle")
        title.setWordWrap(True)
        layout.addWidget(title)

        filters = QGridLayout()
        filters.setContentsMargins(0, 0, 0, 0)
        filters.setHorizontalSpacing(6)
        filters.setVerticalSpacing(6)
        self._filter_group = QButtonGroup(self)
        self._filter_group.setExclusive(True)
        for index, name in enumerate(FILTERS):
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setProperty("chip", True)
            btn.setChecked(name == FILTER_ALL)
            btn.setMinimumHeight(32)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda checked=False, value=name: self._on_filter(value))
            self._filter_group.addButton(btn)
            filters.addWidget(btn, index // 2, index % 2)
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        layout.addLayout(filters)

        self.summary = QLabel("No uploads this session")
        self.summary.setObjectName("dim")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self._scroll, 1)
        self._rebuild_rows()

        self._time_timer = QTimer(self)
        self._time_timer.timeout.connect(self._tick_times)
        self._time_timer.start(5000)

    def register_monitor(self, monitor_id, event_name, collection_display):
        self._monitors[monitor_id] = {
            "event_name": event_name,
            "collection": collection_display,
        }

    def apply_events(self, events: List[StatusEvent]):
        if not events:
            return
        for event in events:
            self._apply_one(event)
        self._trim()
        self._rebuild_rows()
        self._emit_counts()

    def counts(self) -> QueueCounts:
        counts = QueueCounts()
        for item in self._items.values():
            counts.total += 1
            if item.status in PENDING_STATUSES:
                counts.pending += 1
            elif item.status == STATUS_UPLOADED:
                counts.uploaded += 1
            elif item.status == STATUS_FAILED:
                counts.failed += 1
        return counts

    def _collection_for(self, monitor_id):
        meta = self._monitors.get(monitor_id) or {}
        return meta.get("collection") or ""

    def _apply_one(self, event: StatusEvent):
        key = _item_key(event)
        if event.status == STATUS_DISMISSED:
            self._remove(key)
            return

        collection = self._collection_for(event.monitor_id)
        existing = self._items.get(key)
        if existing is None:
            self._items[key] = QueueItem(
                key=key,
                file_path=event.file_path,
                filename=event.filename,
                monitor_id=event.monitor_id,
                status=event.status,
                collection=collection,
                reason=event.reason,
                timestamp=event.timestamp,
            )
            self._order.append(key)
        else:
            existing.status = event.status
            existing.reason = event.reason
            existing.timestamp = event.timestamp
            if collection:
                existing.collection = collection

    def _remove(self, key):
        if key in self._items:
            del self._items[key]
        if key in self._order:
            self._order.remove(key)

    def _trim(self):
        while len(self._items) > MAX_ITEMS:
            drop_key = None
            for key in self._order:
                if self._items[key].status == STATUS_UPLOADED:
                    drop_key = key
                    break
            if drop_key is None:
                for key in self._order:
                    if self._items[key].status == STATUS_FAILED:
                        drop_key = key
                        break
            if drop_key is None:
                break
            self._remove(drop_key)

    def _on_filter(self, value):
        self._filter = value
        self._rebuild_rows()

    def _filtered_keys(self):
        keys = []
        for key in self._order:
            item = self._items[key]
            if self._filter == FILTER_ALL:
                keys.append(key)
            elif self._filter == FILTER_PENDING and item.status in PENDING_STATUSES:
                keys.append(key)
            elif self._filter == FILTER_UPLOADED and item.status == STATUS_UPLOADED:
                keys.append(key)
            elif self._filter == FILTER_FAILED and item.status == STATUS_FAILED:
                keys.append(key)
        return keys

    def _rebuild_rows(self):
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(4, 0, 4, 8)
        layout.setSpacing(6)
        self._rows = []

        keys = self._filtered_keys()
        if not keys:
            empty_text = (
                "No uploads this session"
                if not self._items
                else f"No {self._filter.lower()} items"
            )
            empty = QLabel(empty_text)
            empty.setObjectName("dim")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setWordWrap(True)
            layout.addWidget(empty)
        else:
            for key in reversed(keys):
                row = QueueRow(self._items[key])
                self._rows.append(row)
                layout.addWidget(row)
        layout.addStretch()

        old = self._scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        self._scroll.setWidget(host)

    def _tick_times(self):
        for row in self._rows:
            row.refresh_time()

    def _emit_counts(self):
        counts = self.counts()
        if counts.total == 0:
            self.summary.setText("No uploads this session")
        else:
            self.summary.setText(
                f"{counts.pending} pending  ·  {counts.uploaded} uploaded  ·  {counts.failed} failed"
            )
        self.counts_changed.emit(counts)
        if self.on_counts_changed:
            self.on_counts_changed(counts)
