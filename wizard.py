import os
import queue
import threading
from datetime import datetime

from PySide6.QtCore import QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from camera_host import CameraHost, CameraHostError
from camera_protocol import (
    EVENT_CAMERA_FOUND,
    EVENT_CONNECTED,
    EVENT_DISCONNECTED,
    EVENT_ERROR,
    EVENT_RECONNECTING,
    HINT_USB,
)
from event_thumbs import load_event_thumb
from events_model import find_collection
from platform_support import camera_setup_steps, default_parent_path, no_camera_hint
from tether_session import create_tether_folder, load_last_session, suggested_folder_name
import theme

STEPS = (
    ("resume", "Resume"),
    ("event", "Event"),
    ("collection", "Collection"),
    ("folder", "Folder"),
    ("camera", "Camera"),
)

STEP_COPY = {
    "resume": (
        "Continue where you left off?",
        "Reuse last time's event and folder, or start fresh.",
    ),
    "event": (
        "Which gallery are these photos for?",
        "This is the event guests will open. Search or sort, then click a gallery.",
    ),
    "collection": (
        "Which folder inside that gallery?",
        "Photos upload into this collection: ceremony, portraits, and so on.",
    ),
    "folder": (
        "Where should photos land on this computer?",
        "This is a local backup. The app also uploads each photo to the gallery.",
    ),
    "camera": (
        "Plug in the camera so shots appear here",
        "The app finds the Sony camera and connects on its own. Shoot JPEG or RAW+JPEG.",
    ),
}

ID_ROLE = Qt.ItemDataRole.UserRole
SUBTITLE_ROLE = Qt.ItemDataRole.UserRole + 1
DATE_ROLE = Qt.ItemDataRole.UserRole + 2
DATE_KEY_ROLE = Qt.ItemDataRole.UserRole + 3
PIXMAP_ROLE = Qt.ItemDataRole.UserRole + 4
NAME_KEY_ROLE = Qt.ItemDataRole.UserRole + 5
IMAGE_KIND_ROLE = Qt.ItemDataRole.UserRole + 6

EVENT_CARD = QSize(220, 196)
EVENT_THUMB = QSize(200, 118)
FOLDER_CARD = QSize(140, 152)


def clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        child = item.layout()
        if widget is not None:
            widget.deleteLater()
        if child is not None:
            clear_layout(child)


class ChoiceDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        has_sub = bool(index.data(SUBTITLE_ROLE))
        return QSize(0, 64 if has_sub else 50)

    def paint(self, painter, option, index):
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        rect = option.rect.adjusted(2, 2, -2, -2)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        if selected:
            painter.setBrush(QColor(theme.ACCENT))
        elif hovered:
            painter.setBrush(QColor(theme.BG_HOVER))
        else:
            painter.setBrush(QColor(theme.BG_RAISED))
        painter.drawRoundedRect(rect, 8, 8)

        title = index.data(Qt.ItemDataRole.DisplayRole) or ""
        subtitle = index.data(SUBTITLE_ROLE) or ""
        title_font = QFont(painter.font())
        title_font.setPointSize(15)
        title_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(QColor("#ffffff" if selected else theme.TEXT))
        if subtitle:
            title_rect = rect.adjusted(14, 8, -12, -26)
            sub_rect = rect.adjusted(14, 32, -12, -8)
            painter.drawText(title_rect, Qt.AlignmentFlag.AlignVCenter, title)
            sub_font = QFont(painter.font())
            sub_font.setPointSize(12)
            sub_font.setWeight(QFont.Weight.Normal)
            painter.setFont(sub_font)
            painter.setPen(QColor("#d7e4ff" if selected else theme.TEXT_DIM))
            painter.drawText(sub_rect, Qt.AlignmentFlag.AlignVCenter, subtitle)
        else:
            painter.drawText(rect.adjusted(14, 0, -12, 0), Qt.AlignmentFlag.AlignVCenter, title)
        painter.restore()


class ChoiceList(QListWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("choiceList")
        self.setItemDelegate(ChoiceDelegate(self))
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSpacing(2)
        self.itemSelectionChanged.connect(self.changed.emit)

    def set_items(self, items):
        self.blockSignals(True)
        self.clear()
        for item in items:
            row = QListWidgetItem(item["title"])
            row.setData(ID_ROLE, item["id"])
            row.setData(SUBTITLE_ROLE, item.get("subtitle") or "")
            self.addItem(row)
        self.blockSignals(False)

    @property
    def selected_id(self):
        item = self.currentItem()
        return item.data(ID_ROLE) if item else None

    def select_id(self, item_id):
        for index in range(self.count()):
            if self.item(index).data(ID_ROLE) == item_id:
                self.setCurrentRow(index)
                return


class EventCardDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return EVENT_CARD

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(4, 4, -4, -4)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.BG_RAISED))
        painter.drawRoundedRect(rect, 10, 10)
        if selected:
            painter.setPen(QPen(QColor(theme.ACCENT), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 10, 10)
        elif hovered:
            painter.setPen(QPen(QColor(theme.BORDER), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 10, 10)

        image_rect = rect.adjusted(10, 10, -10, -58)
        pixmap = index.data(PIXMAP_ROLE)
        if isinstance(pixmap, QPixmap) and not pixmap.isNull():
            logo = index.data(IMAGE_KIND_ROLE) == "logo"
            mode = (
                Qt.AspectRatioMode.KeepAspectRatio
                if logo
                else Qt.AspectRatioMode.KeepAspectRatioByExpanding
            )
            scaled = pixmap.scaled(
                image_rect.size(),
                mode,
                Qt.TransformationMode.SmoothTransformation,
            )
            painter.setBrush(QColor(theme.BG_INPUT))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(image_rect, 6, 6)
            painter.setClipRect(image_rect)
            if logo:
                x = image_rect.x() + (image_rect.width() - scaled.width()) // 2
                y = image_rect.y() + (image_rect.height() - scaled.height()) // 2
                painter.drawPixmap(x, y, scaled)
            else:
                x = max(0, (scaled.width() - image_rect.width()) // 2)
                y = max(0, (scaled.height() - image_rect.height()) // 2)
                cropped = scaled.copy(x, y, image_rect.width(), image_rect.height())
                painter.drawPixmap(image_rect.topLeft(), cropped)
            painter.setClipping(False)
        else:
            painter.setBrush(QColor(theme.BG_INPUT))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(image_rect, 6, 6)

        name = index.data(Qt.ItemDataRole.DisplayRole) or ""
        date = index.data(DATE_ROLE) or ""
        name_rect = rect.adjusted(12, rect.height() - 50, -12, -26)
        date_rect = rect.adjusted(12, rect.height() - 26, -12, -8)
        name_font = QFont(painter.font())
        name_font.setPointSize(15)
        name_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(name_font)
        painter.setPen(QColor("#ffffff" if selected else theme.TEXT))
        painter.drawText(
            name_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            painter.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, name_rect.width()),
        )
        date_font = QFont(painter.font())
        date_font.setPointSize(12)
        date_font.setWeight(QFont.Weight.Normal)
        painter.setFont(date_font)
        painter.setPen(QColor("#d7e4ff" if selected else theme.TEXT_DIM))
        painter.drawText(date_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, date)
        painter.restore()


class EventChooser(QWidget):
    changed = Signal()

    def __init__(self, events, parent=None):
        super().__init__(parent)
        self._events = list(events)
        self._items = {}
        self._thumb_ready = queue.Queue()
        self._sort_key = "date"
        self._sort_desc = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search events…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        bar.addWidget(self.search, 1)
        self._sort_group_buttons = []
        for key, label in (("name", "Event name"), ("date", "Event date")):
            btn = QToolButton()
            btn.setText(label)
            btn.setCheckable(True)
            btn.setProperty("chip", True)
            btn.setChecked(key == "date")
            btn.clicked.connect(lambda checked=False, value=key: self._on_sort(value))
            bar.addWidget(btn)
            self._sort_group_buttons.append((key, btn))
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        layout.addLayout(bar)

        self.list = QListWidget()
        self.list.setViewMode(QListWidget.ViewMode.IconMode)
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setMovement(QListWidget.Movement.Static)
        self.list.setWrapping(True)
        self.list.setSpacing(8)
        self.list.setGridSize(EVENT_CARD)
        self.list.setIconSize(EVENT_THUMB)
        self.list.setUniformItemSizes(True)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setItemDelegate(EventCardDelegate(self.list))
        self.list.itemSelectionChanged.connect(self.changed.emit)
        layout.addWidget(self.list, 1)

        self.empty = QLabel("No events match that search.")
        self.empty.setObjectName("dim")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.hide()
        layout.addWidget(self.empty)

        self._fill()
        self._thumb_timer = QTimer(self)
        self._thumb_timer.timeout.connect(self._apply_thumbs)
        self._thumb_timer.start(80)

    @property
    def selected_id(self):
        item = self.list.currentItem()
        if item is None or item.isHidden():
            return None
        return item.data(ID_ROLE)

    def select_id(self, item_id):
        item = self._items.get(item_id)
        if item is None or item.isHidden():
            return
        self.list.setCurrentItem(item)
        self._scroll_to(item)
        QTimer.singleShot(50, lambda: self._scroll_to(item))
        QTimer.singleShot(200, lambda: self._scroll_to(item))

    def _scroll_to(self, item):
        if item is None:
            return
        try:
            self.list.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
        except RuntimeError:
            return

    def _fill(self):
        self.list.clear()
        self._items = {}
        events = list(self._events)
        if self._sort_key == "name":
            events.sort(key=lambda event: event.name.casefold(), reverse=self._sort_desc)
        else:
            events.sort(
                key=lambda event: event.event_date or datetime.min,
                reverse=self._sort_desc,
            )
        for event in events:
            item = QListWidgetItem(event.name)
            date_key = event.event_date or datetime.min
            if getattr(date_key, "tzinfo", None):
                date_key = date_key.replace(tzinfo=None)
            item.setData(ID_ROLE, event.id)
            item.setData(DATE_ROLE, event.date_label or "—")
            item.setData(DATE_KEY_ROLE, date_key)
            item.setData(NAME_KEY_ROLE, event.name.casefold())
            item.setData(IMAGE_KIND_ROLE, event.image_kind)
            item.setSizeHint(EVENT_CARD)
            item.setToolTip(event.name)
            self.list.addItem(item)
            self._items[event.id] = item
            if event.image_ref:
                threading.Thread(
                    target=self._load_thumb,
                    args=(event.id, event.image_ref, event.image_kind == "logo"),
                    daemon=True,
                ).start()
        self._apply_filter()

    def _load_thumb(self, event_id, image_ref, contain=False):
        qimage = load_event_thumb(event_id, image_ref, contain=contain)
        if qimage is not None:
            self._thumb_ready.put((event_id, qimage))

    def _apply_thumbs(self):
        updated = False
        while True:
            try:
                event_id, qimage = self._thumb_ready.get_nowait()
            except queue.Empty:
                break
            item = self._items.get(event_id)
            if item:
                item.setData(PIXMAP_ROLE, QPixmap.fromImage(qimage))
                updated = True
        if updated:
            self.list.viewport().update()

    def _on_sort(self, key):
        if self._sort_key == key:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_key = key
            self._sort_desc = key == "date"
        for sort_key, btn in self._sort_group_buttons:
            btn.setChecked(sort_key == self._sort_key)
        current = self.selected_id
        self._reorder()
        if current:
            self.select_id(current)

    def _reorder(self):
        items = [self.list.takeItem(0) for _ in range(self.list.count())]
        if self._sort_key == "name":
            items.sort(key=lambda item: item.data(NAME_KEY_ROLE) or "", reverse=self._sort_desc)
        else:
            items.sort(key=lambda item: item.data(DATE_KEY_ROLE) or datetime.min, reverse=self._sort_desc)
        for item in items:
            self.list.addItem(item)
        self._apply_filter()

    def _apply_filter(self):
        query = self.search.text().strip().casefold()
        visible = 0
        current = self.selected_id
        self.list.blockSignals(True)
        for index in range(self.list.count()):
            item = self.list.item(index)
            name = (item.text() or "").casefold()
            date = (item.data(DATE_ROLE) or "").casefold()
            match = (not query) or query in name or query in date
            item.setHidden(not match)
            if match:
                visible += 1
        if current:
            self.select_id(current)
        item = self.list.currentItem()
        if item is not None and item.isHidden():
            self.list.setCurrentItem(None)
        self.list.blockSignals(False)
        self.list.setVisible(visible > 0 or not query)
        self.empty.setVisible(bool(query) and visible == 0)
        self.changed.emit()


class FolderDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return FOLDER_CARD

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(4, 4, -4, -4)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.BG_RAISED))
        painter.drawRoundedRect(rect, 10, 10)
        if selected:
            painter.setPen(QPen(QColor(theme.ACCENT), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 10, 10)
        elif hovered:
            painter.setPen(QPen(QColor(theme.BORDER), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 10, 10)

        icon = QRect(rect.x() + 22, rect.y() + 16, rect.width() - 44, 64)
        folder = QColor(theme.ACCENT if selected else "#6d7582")
        tab = QRect(icon.x(), icon.y() + 6, int(icon.width() * 0.42), 16)
        body = QRect(icon.x(), icon.y() + 16, icon.width(), icon.height() - 16)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(folder.darker(118))
        painter.drawRoundedRect(tab, 4, 4)
        painter.setBrush(folder)
        painter.drawRoundedRect(body, 8, 8)

        title = index.data(Qt.ItemDataRole.DisplayRole) or ""
        subtitle = index.data(SUBTITLE_ROLE) or ""
        title_font = QFont(painter.font())
        title_font.setPointSize(15)
        title_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(QColor("#ffffff" if selected else theme.TEXT))
        painter.drawText(
            QRect(rect.x() + 10, rect.y() + 88, rect.width() - 20, 28 if subtitle else 36),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            painter.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, rect.width() - 20),
        )
        if subtitle:
            sub_font = QFont(painter.font())
            sub_font.setPointSize(12)
            sub_font.setWeight(QFont.Weight.Normal)
            painter.setFont(sub_font)
            painter.setPen(QColor("#d7e4ff" if selected else theme.TEXT_DIM))
            painter.drawText(
                QRect(rect.x() + 10, rect.y() + 112, rect.width() - 20, 22),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                painter.fontMetrics().elidedText(subtitle, Qt.TextElideMode.ElideRight, rect.width() - 20),
            )
        painter.restore()


class CollectionChooser(QWidget):
    changed = Signal()

    def __init__(self, collections, parent=None):
        super().__init__(parent)
        self._items = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.list = QListWidget()
        self.list.setViewMode(QListWidget.ViewMode.IconMode)
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setMovement(QListWidget.Movement.Static)
        self.list.setWrapping(True)
        self.list.setSpacing(8)
        self.list.setGridSize(FOLDER_CARD)
        self.list.setUniformItemSizes(True)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setItemDelegate(FolderDelegate(self.list))
        self.list.itemSelectionChanged.connect(self.changed.emit)
        layout.addWidget(self.list, 1)

        if not collections:
            empty = QLabel("This event has no collections yet.")
            empty.setObjectName("dim")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(empty)

        for col in collections:
            item = QListWidgetItem(col.collection_name)
            item.setData(ID_ROLE, col.id)
            item.setData(SUBTITLE_ROLE, col.parent_name)
            item.setSizeHint(FOLDER_CARD)
            item.setToolTip(col.display_name)
            self.list.addItem(item)
            self._items[col.id] = item

    @property
    def selected_id(self):
        item = self.list.currentItem()
        return item.data(ID_ROLE) if item else None

    def select_id(self, item_id):
        item = self._items.get(item_id)
        if item is None:
            return
        self.list.setCurrentItem(item)
        self.list.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
        QTimer.singleShot(50, lambda: self.list.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter))


class CameraStatusPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("cameraCard")
        self.setMinimumHeight(196)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 24)
        layout.setSpacing(8)

        self.glyph = QLabel("·")
        self.glyph.setObjectName("cameraGlyph")
        self.glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.glyph)

        self.title = QLabel("Looking for a camera…")
        self.title.setObjectName("cameraTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setWordWrap(True)
        layout.addWidget(self.title)

        self.detail = QLabel("Keep the camera awake and plugged in.")
        self.detail.setObjectName("dim")
        self.detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)

        self.bar = QProgressBar()
        self.bar.setObjectName("cameraBusy")
        self.bar.setRange(0, 0)
        self.bar.setTextVisible(False)
        layout.addWidget(self.bar)
        layout.addStretch()
        self.set_state("scanning")

    def set_state(self, state, title="", detail=""):
        busy = state in ("scanning", "connecting")
        self.bar.setVisible(busy)
        if state == "connected":
            self.glyph.setText("✓")
            self.glyph.setStyleSheet(f"color: {theme.GOOD};")
            self.title.setText(title or "Connected")
            self.detail.setText(detail or "Ready when you are.")
        elif state == "failed":
            self.glyph.setText("!")
            self.glyph.setStyleSheet(f"color: {theme.BAD};")
            self.title.setText(title or "Could not connect")
            self.detail.setText(detail or no_camera_hint())
        elif state == "connecting":
            self.glyph.setText("…")
            self.glyph.setStyleSheet(f"color: {theme.ACCENT};")
            self.title.setText(title or "Connecting…")
            self.detail.setText(detail or "Talking to the camera.")
        elif state == "lost":
            self.glyph.setText("!")
            self.glyph.setStyleSheet(f"color: {theme.WARN};")
            self.title.setText(title or "Camera disconnected")
            self.detail.setText(detail or "Plug it back in, then Scan again.")
        else:
            self.glyph.setText("·")
            self.glyph.setStyleSheet(f"color: {theme.TEXT_DIM};")
            self.title.setText(title or "Looking for a camera…")
            self.detail.setText(detail or "This will usually take a few seconds.")


class SetupWizard(QWidget):
    finished = Signal(object)
    _camera_ok = Signal(int, object)
    _camera_fail = Signal(int, str, str)
    _camera_try = Signal(int, int, int)

    def __init__(self, events, log_queue=None, parent=None):
        super().__init__(parent)
        self.events = events
        self.log_queue = log_queue
        self.last_session = load_last_session()
        self.host = None

        self.event_id = None
        self.collection = None
        self.parent_path = ""
        self.folder_name = ""
        self.tether_folder = ""
        self.camera = None
        self._resume_choice = None
        self._cameras = {}
        self._folder_custom = False

        self.step_keys = [key for key, _label in STEPS if key != "resume" or self.last_session]
        self.step_index = 0
        self._step_gen = 0
        self._camera_busy = False
        self._host_timer = QTimer(self)
        self._host_timer.timeout.connect(self._poll_host)
        self._camera_ok.connect(self._on_camera_ok)
        self._camera_fail.connect(self._on_camera_fail)
        self._camera_try.connect(self._on_camera_try)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._build_rail(root)
        self._build_body(root)
        self._show_step()
        QTimer.singleShot(400, self._warmup_host)

    def _refresh_rail(self):
        visible = [key for key, _label in STEPS if key in self.step_keys]
        labels = {key: name for key, name in STEPS}
        for index, key in enumerate(visible):
            label = self.step_labels[key]
            name = labels[key]
            if index < self.step_index:
                label.setText(f"✓  {name}")
                label.setObjectName("stepDone")
            elif index == self.step_index:
                label.setText(f"{index + 1}  {name}")
                label.setObjectName("stepActive")
            else:
                label.setText(f"{index + 1}  {name}")
                label.setObjectName("stepIdle")
            label.style().unpolish(label)
            label.style().polish(label)

    def destroy_host(self):
        if self.host:
            try:
                self.host.close()
            except Exception:
                pass
            self.host = None

    def _build_rail(self, root):
        rail = QWidget()
        rail.setObjectName("sidebar")
        rail.setFixedWidth(248)
        layout = QVBoxLayout(rail)
        layout.setContentsMargins(20, 20, 16, 20)
        layout.setSpacing(2)

        title = QLabel("Setup")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        layout.addSpacing(12)

        self.step_labels = {}
        visible = [(key, label) for key, label in STEPS if key in self.step_keys]
        for index, (key, label) in enumerate(visible, start=1):
            lbl = QLabel(f"{index}  {label}")
            lbl.setObjectName("stepIdle")
            layout.addWidget(lbl)
            self.step_labels[key] = lbl

        layout.addSpacing(24)
        hint = QLabel("Complete each step.\nYou can go back anytime.")
        hint.setObjectName("dim")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch()
        root.addWidget(rail)

    def _build_body(self, root):
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(12)

        self.title_label = QLabel()
        self.title_label.setObjectName("pageTitle")
        self.title_label.setWordWrap(True)
        layout.addWidget(self.title_label)
        self.subtitle_label = QLabel()
        self.subtitle_label.setObjectName("dim")
        self.subtitle_label.setWordWrap(True)
        layout.addWidget(self.subtitle_label)

        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(10)
        self.content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.content, 1)

        nav = QHBoxLayout()
        self.back_btn = QPushButton("Back")
        self.back_btn.clicked.connect(self._back)
        self.next_btn = QPushButton("Next")
        self.next_btn.setObjectName("primary")
        self.next_btn.clicked.connect(self._next)
        self.next_hint = QLabel()
        self.next_hint.setObjectName("dim")
        self.next_hint.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        nav.addWidget(self.back_btn)
        nav.addStretch()
        nav.addWidget(self.next_hint)
        nav.addWidget(self.next_btn)
        layout.addLayout(nav)
        root.addWidget(body, 1)

    def _show_step(self):
        self._step_gen += 1
        key = self.step_keys[self.step_index]
        title, subtitle = STEP_COPY.get(key, ("", ""))
        self.title_label.setText(title)
        self.subtitle_label.setText(subtitle)
        self._refresh_rail()
        clear_layout(self.content_layout)
        self._host_timer.stop()
        self.back_btn.setEnabled(self.step_index > 0)
        if key == "resume":
            self._show_resume()
        elif key == "event":
            self._show_event()
        elif key == "collection":
            self._show_collection()
        elif key == "folder":
            self._show_folder()
        elif key == "camera":
            self._show_camera()
        self._refresh_next()

    def _current_event(self):
        for event in self.events:
            if event.id == self.event_id:
                return event
        return None

    def _show_resume(self):
        last = self.last_session or {}
        summary = QLabel(
            f"{last.get('event_name', 'Unknown')}  ›  {last.get('collection_name', '')}\n"
            f"{last.get('tether_folder', '')}"
        )
        summary.setWordWrap(True)
        self.content_layout.addWidget(summary)
        chooser = ChoiceList()
        chooser.set_items([
            {
                "id": "resume",
                "title": "Continue last session",
                "subtitle": "Keeps the same event, collection, and folder. You can still change any step.",
            },
            {
                "id": "new",
                "title": "Start a new session",
                "subtitle": "Pick the event, collection, and folder from scratch.",
            },
        ])
        chooser.changed.connect(self._on_resume_picked)
        self.content_layout.addWidget(chooser, 1)
        self._resume_list = chooser
        if self._resume_choice is True:
            chooser.select_id("resume")
        elif self._resume_choice is False:
            chooser.select_id("new")

    def _on_resume_picked(self):
        choice = self._resume_list.selected_id
        self._resume_choice = choice == "resume"
        if self._resume_choice:
            self._apply_last_session()
        else:
            self.event_id = None
            self.collection = None
            self.parent_path = ""
            self.folder_name = ""
            self.tether_folder = ""
            self.camera = None
            self._folder_custom = False
        self._refresh_next()

    def _apply_last_session(self):
        last = self.last_session or {}
        event, collection = find_collection(self.events, last.get("event_id"), last.get("s3_folder"))
        if event and collection:
            self.event_id = event.id
            self.collection = collection
        self.parent_path = last.get("parent_path") or ""
        self.folder_name = last.get("folder_name") or ""
        self.tether_folder = last.get("tether_folder") or ""
        self._folder_custom = bool(self.folder_name)

    def _show_event(self):
        chooser = EventChooser(self.events)
        chooser.changed.connect(self._on_event_picked)
        self.content_layout.addWidget(chooser, 1)
        self._event_list = chooser
        if self.event_id:
            chooser.select_id(self.event_id)

    def _on_event_picked(self):
        new_id = self._event_list.selected_id
        if new_id is None:
            self._refresh_next()
            return
        if new_id != self.event_id:
            self.event_id = new_id
            if not (self.collection and self.collection.event_id == new_id):
                self.collection = None
            if not self._folder_custom:
                self.folder_name = ""
        self._refresh_next()

    def _show_collection(self):
        event = self._current_event()
        chooser = CollectionChooser(event.collections if event else [])
        chooser.changed.connect(self._on_collection_picked)
        self.content_layout.addWidget(chooser, 1)
        self._collection_list = chooser
        if self.collection:
            chooser.select_id(self.collection.id)

    def _on_collection_picked(self):
        event = self._current_event()
        if not event:
            return
        for col in event.collections:
            if col.id == self._collection_list.selected_id:
                self.collection = col
                break
        if not self._folder_custom:
            self.folder_name = ""
        self._refresh_next()

    def _suggested_folder_name(self):
        return suggested_folder_name(self._current_event(), self.collection)

    def _show_folder(self):
        if not self._folder_custom or not self.folder_name:
            self.folder_name = self._suggested_folder_name()
            self._folder_custom = False
        if not self.parent_path:
            self.parent_path = default_parent_path()

        name_label = QLabel("Folder name")
        name_label.setObjectName("dim")
        self.content_layout.addWidget(name_label)
        self.folder_name_entry = QLineEdit()
        self.folder_name_entry.setText(self.folder_name)
        self.folder_name_entry.setPlaceholderText(self._suggested_folder_name())
        self.folder_name_entry.textChanged.connect(self._on_folder_changed)
        self.content_layout.addWidget(self.folder_name_entry)

        parent_label = QLabel("Save inside this folder")
        parent_label.setObjectName("dim")
        self.content_layout.addWidget(parent_label)
        row = QHBoxLayout()
        self.parent_entry = QLineEdit()
        self.parent_entry.setText(self.parent_path)
        self.parent_entry.textChanged.connect(self._on_folder_changed)
        browse = QPushButton("Browse")
        browse.clicked.connect(self._browse_parent)
        row.addWidget(self.parent_entry, 1)
        row.addWidget(browse)
        self.content_layout.addLayout(row)

        self.folder_preview = QLabel()
        self.folder_preview.setObjectName("dim")
        self.folder_preview.setWordWrap(True)
        self.content_layout.addWidget(self.folder_preview)
        self.content_layout.addStretch()
        self._on_folder_changed()

    def _browse_parent(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder", self.parent_path)
        if folder:
            self.parent_entry.setText(folder)

    def _on_folder_changed(self):
        self.folder_name = self.folder_name_entry.text().strip()
        self.parent_path = self.parent_entry.text().strip()
        suggested = self._suggested_folder_name()
        self._folder_custom = self.folder_name != suggested
        if self.folder_name and self.parent_path:
            self.folder_preview.setText(
                f"Photos will be saved to:\n{os.path.join(self.parent_path, self.folder_name)}"
            )
        else:
            self.folder_preview.setText("Name the folder and choose where it should live.")
        self._refresh_next()

    def _show_camera(self):
        for index, text in enumerate(camera_setup_steps(), start=1):
            step = QLabel(f"{index}.  {text}")
            step.setWordWrap(True)
            self.content_layout.addWidget(step)

        self._camera_panel = CameraStatusPanel()
        self.content_layout.addWidget(self._camera_panel, 1)
        self.camera_status = self._camera_panel.detail

        btn_row = QHBoxLayout()
        self.scan_btn = QPushButton("Scan again")
        self.scan_btn.clicked.connect(self._scan_cameras)
        btn_row.addWidget(self.scan_btn)
        btn_row.addStretch()
        self.content_layout.addLayout(btn_row)
        self._host_timer.start(200)
        QTimer.singleShot(80, lambda gen=self._step_gen: self._scan_cameras(gen))

    def _warmup_host(self):
        try:
            self._ensure_host()
        except Exception as exc:
            if self.log_queue:
                self.log_queue.put(f"Camera host warmup failed: {exc}")

    def _ensure_host(self):
        if self.host and self.host.is_running():
            return self.host
        self.host = CameraHost()
        hello = self.host.start()
        backend = (hello or {}).get("backend", "unknown")
        if self.log_queue:
            self.log_queue.put(f"Camera host started ({backend}).")
        return self.host

    def _scan_cameras(self, gen=None):
        if gen is not None and gen != self._step_gen:
            return
        if self.step_keys[self.step_index] != "camera":
            return
        if self._camera_busy:
            return
        self.camera = None
        self._camera_busy = True
        if hasattr(self, "scan_btn"):
            self.scan_btn.setEnabled(False)
        self._set_camera_state("scanning")
        self._refresh_next()
        try:
            host = self._ensure_host()
        except Exception as exc:
            self._on_camera_fail(self._step_gen, str(exc), getattr(exc, "hint", HINT_USB))
            return
        threading.Thread(
            target=self._camera_worker,
            args=(self._step_gen, host),
            daemon=True,
        ).start()

    def _camera_worker(self, gen, host):
        def progress(attempt, total):
            self._camera_try.emit(gen, attempt, total)

        try:
            folder = create_tether_folder(self.parent_path, self.folder_name)
            data = dict(host.connect("", folder, on_attempt=progress) or {})
            data["save_dir"] = folder
            self._camera_ok.emit(gen, data)
        except Exception as exc:
            self._camera_fail.emit(gen, str(exc), getattr(exc, "hint", "") or HINT_USB)

    def _on_camera_try(self, gen, attempt, total):
        if gen != self._step_gen:
            return
        self._set_camera_state(
            "scanning",
            "Looking for a camera…",
            f"This will usually take a few seconds. Try {attempt} of {total}.",
        )

    def _on_camera_ok(self, gen, data):
        if gen != self._step_gen:
            return
        self._camera_busy = False
        if hasattr(self, "scan_btn"):
            self.scan_btn.setEnabled(True)
        self.tether_folder = (data or {}).get("save_dir") or self.tether_folder
        self.camera = (data or {}).get("camera") or (self.host.camera if self.host else None)
        name = (self.camera or {}).get("name") or (self.camera or {}).get("model") or "Camera"
        self._set_camera_state("connected", f"Connected to {name}", "Click Start shooting when you are ready.")
        if self.log_queue:
            self.log_queue.put(f"Camera connected: {self.camera}")
        self._refresh_next()

    def _on_camera_fail(self, gen, message, hint=""):
        if gen != self._step_gen:
            return
        self._camera_busy = False
        if hasattr(self, "scan_btn"):
            self.scan_btn.setEnabled(True)
        self.camera = None
        detail = message
        if hint and hint not in message:
            detail = f"{message}\n{hint}".strip()
        self._set_camera_state("failed", "Could not connect", detail)
        self._refresh_next()

    def _set_camera_state(self, state, title="", detail=""):
        if hasattr(self, "_camera_panel"):
            self._camera_panel.set_state(state, title, detail)

    def _poll_host(self):
        if self.step_keys[self.step_index] != "camera" or not self.host:
            return
        while True:
            try:
                msg = self.host.event_queue.get_nowait()
            except queue.Empty:
                break
            name = msg.get("name")
            if name == EVENT_CAMERA_FOUND:
                model = msg.get("model") or msg.get("name") or "Sony camera"
                self._set_camera_state("connecting", f"Connecting to {model}…")
            elif name == EVENT_DISCONNECTED:
                self.camera = None
                self._set_camera_state("lost")
                self._refresh_next()
            elif name == EVENT_RECONNECTING:
                attempt = msg.get("attempt", 1)
                self._set_camera_state("connecting", f"Reconnecting…", f"Try {attempt}")
            elif name == EVENT_CONNECTED:
                model = msg.get("model") or msg.get("name") or "Camera"
                self.camera = self.host.camera or self.camera or {"name": model}
                self._set_camera_state(
                    "connected",
                    f"Connected to {model}",
                    "Click Start shooting when you are ready.",
                )
                self._refresh_next()
            elif name == EVENT_ERROR and self.log_queue:
                self.log_queue.put(f"Camera host: {msg.get('message', '')}")

    def _step_valid(self):
        key = self.step_keys[self.step_index]
        if key == "resume":
            return self._resume_choice is not None
        if key == "event":
            return bool(self.event_id)
        if key == "collection":
            return bool(self.collection)
        if key == "folder":
            return bool(self.folder_name and self.parent_path and os.path.isdir(self.parent_path))
        if key == "camera":
            return bool(self.camera and self.host and self.host.connected)
        return False

    def _next_blocker(self):
        key = self.step_keys[self.step_index]
        if key == "resume":
            return "Choose one to continue."
        if key == "event":
            return "Select an event to continue."
        if key == "collection":
            return "Select a collection to continue."
        if key == "folder":
            if not self.folder_name:
                return "Give the folder a name."
            if not self.parent_path:
                return "Choose where the folder should live."
            if not os.path.isdir(self.parent_path):
                return "That folder does not exist. Use Browse to pick one."
            return "Finish the folder details to continue."
        if key == "camera":
            return "Waiting for the camera to connect. Plug it in, then Scan again."
        return ""

    def _refresh_next(self):
        last = self.step_index == len(self.step_keys) - 1
        valid = self._step_valid()
        self.next_btn.setText("Start shooting" if last else "Next")
        self.next_btn.setEnabled(valid)
        self.next_hint.setText("" if valid else self._next_blocker())

    def _back(self):
        if self.step_index == 0:
            return
        key = self.step_keys[self.step_index]
        if key == "camera" and self.host and self.host.connected:
            self.host.disconnect()
            self.camera = None
        self.step_index -= 1
        self._show_step()

    def _next(self):
        if not self._step_valid():
            return
        if self.step_index < len(self.step_keys) - 1:
            self.step_index += 1
            self._show_step()
            return
        try:
            self.tether_folder = create_tether_folder(self.parent_path, self.folder_name)
        except ValueError as exc:
            if hasattr(self, "camera_status"):
                self.camera_status.setText(str(exc))
            return
        self.finished.emit({
            "event_id": self.collection.event_id,
            "event_name": self.collection.event_name,
            "collection": self.collection,
            "tether_folder": self.tether_folder,
            "folder_name": self.folder_name,
            "parent_path": os.path.abspath(self.parent_path),
            "host": self.host,
            "camera": self.camera,
        })
