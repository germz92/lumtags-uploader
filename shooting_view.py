import os
import queue
import threading
import time

from PySide6.QtCore import QEvent, QRect, QSettings, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from camera_protocol import (
    EVENT_CONNECTED,
    EVENT_DISCONNECTED,
    EVENT_ERROR,
    EVENT_IMAGE_READY,
    EVENT_RECONNECTING,
)
from image_util import make_thumbnail, pil_to_qimage
from loupe_view import LoupeView
from queue_view import QueueView
from status_events import STATUS_UPLOADED, StatusEvent
import theme

GRID_COLUMNS_MIN = 1
GRID_COLUMNS_MAX = 12
GRID_COLUMNS_DEFAULT = 5
NAME_ROW = 28
LOST_MESSAGE = "Camera lost — shots may not reach the app until reconnected."
RECONNECT_MESSAGE = "Reconnecting to the camera…"
_SETTINGS = QSettings("GalleryUploader", "GalleryUploader")

PATH_ROLE = Qt.ItemDataRole.UserRole
STATUS_ROLE = Qt.ItemDataRole.UserRole + 1
REASON_ROLE = Qt.ItemDataRole.UserRole + 2
PIXMAP_ROLE = Qt.ItemDataRole.UserRole + 3


def follow_latest_pref():
    return _SETTINGS.value("follow_latest", True, type=bool)


def set_follow_latest_pref(on):
    _SETTINGS.setValue("follow_latest", bool(on))


def thumb_columns_pref():
    value = _SETTINGS.value("thumb_columns", GRID_COLUMNS_DEFAULT, type=int)
    return max(GRID_COLUMNS_MIN, min(GRID_COLUMNS_MAX, int(value)))


def set_thumb_columns_pref(value):
    _SETTINGS.setValue("thumb_columns", int(value))


def cell_size_for_width(width, columns):
    columns = max(GRID_COLUMNS_MIN, min(GRID_COLUMNS_MAX, int(columns)))
    cell_w = max(80, (max(width, 80) - 1) // columns)
    image_h = max(60, round(cell_w * 3 / 4))
    return QSize(cell_w, image_h + NAME_ROW)


class ThumbDelegate(QStyledItemDelegate):
    def __init__(self, parent, size_getter):
        super().__init__(parent)
        self._size_getter = size_getter

    def sizeHint(self, option, index):
        return self._size_getter()

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(4, 4, -4, -4)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.BG_RAISED))
        painter.drawRoundedRect(rect, 8, 8)
        if selected:
            painter.setPen(QPen(QColor(theme.ACCENT), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 8, 8)

        image_rect = rect.adjusted(8, 8, -8, -28)
        pixmap = index.data(PIXMAP_ROLE)
        check_rect = image_rect
        if isinstance(pixmap, QPixmap) and not pixmap.isNull():
            scaled = pixmap.scaled(
                image_rect.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = image_rect.x() + (image_rect.width() - scaled.width()) // 2
            y = image_rect.y() + (image_rect.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
            check_rect = QRect(x, y, scaled.width(), scaled.height())
        else:
            painter.setBrush(QColor(theme.BG_INPUT))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(image_rect, 6, 6)

        if index.data(STATUS_ROLE) == STATUS_UPLOADED:
            theme.draw_upload_check(painter, check_rect, size=max(16, check_rect.width() // 9))

        name = index.data(Qt.ItemDataRole.DisplayRole) or ""
        name_rect = rect.adjusted(8, rect.height() - 24, -8, -4)
        painter.setPen(QColor(theme.TEXT_DIM))
        painter.drawText(
            name_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            painter.fontMetrics().elidedText(name, Qt.TextElideMode.ElideMiddle, name_rect.width()),
        )
        painter.restore()


class ShootingWorkspace(QWidget):
    end_session = Signal()

    def __init__(self, session, host, intake, log_queue, parent=None):
        super().__init__(parent)
        self.session = session
        self.host = host
        self.intake = intake
        self.log_queue = log_queue
        self._items = {}
        self._statuses = {}
        self._order = []
        self._thumb_ready = queue.Queue()
        self._loupe = None
        self.follow_latest = follow_latest_pref()
        self._thumb_columns = thumb_columns_pref()
        self._conn_state = "connected"

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._build_header(root)
        self._build_banner(root)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._build_thumbs(splitter)
        self._build_queue_column(splitter)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([820, 400])
        root.addWidget(splitter, 1)

        camera = self.session.get("camera") or {}
        self.set_connection("connected", camera.get("name") or camera.get("model") or "Camera")

        self._host_timer = QTimer(self)
        self._host_timer.timeout.connect(self._poll_host)
        self._host_timer.start(120)
        self._folder_timer = QTimer(self)
        self._folder_timer.timeout.connect(self._scan_tether_folder)
        self._folder_timer.start(1000)

    def apply_status_events(self, events):
        self.queue_view.apply_events(events)
        for event in events:
            if isinstance(event, StatusEvent):
                self._statuses[event.file_path] = (event.status, event.reason)
                item = self._items.get(event.file_path)
                if item:
                    item.setData(STATUS_ROLE, event.status)
                    item.setData(REASON_ROLE, event.reason)
        self.thumbs.viewport().update()
        self._update_progress()
        if self._loupe:
            self._loupe.refresh_status()

    def add_image(self, file_path):
        file_path = os.path.abspath(file_path)
        if file_path in self._items:
            return
        if self._conn_state in ("lost", "reconnecting"):
            self._mark_connected(log_message="Camera link restored.")
        item = QListWidgetItem(os.path.basename(file_path))
        item.setData(PATH_ROLE, file_path)
        item.setData(STATUS_ROLE, None)
        item.setSizeHint(self._card_size())
        self.thumbs.insertItem(0, item)
        self._items[file_path] = item
        self._order.insert(0, file_path)
        first = self.photo_stack.currentWidget() is not self.thumbs
        self.photo_stack.setCurrentWidget(self.thumbs)
        if first:
            QTimer.singleShot(0, self._apply_thumb_size)
        self._update_count()
        if self.follow_latest:
            self._focus_path(file_path)
        if self._loupe:
            self._loupe.set_paths(
                self._order,
                0 if self.follow_latest else None,
            )
        threading.Thread(target=self._load_thumb, args=(file_path,), daemon=True).start()

    def set_connection(self, state, detail=""):
        self._conn_state = state
        colors = {
            "connected": (theme.GOOD, theme.GOOD_BG, "Connected"),
            "reconnecting": (theme.WARN, theme.WARN_BG, "Reconnecting"),
            "lost": (theme.BAD, theme.BAD_BG, "Lost"),
        }
        fg, bg, label = colors.get(state, (theme.TEXT_DIM, theme.BG_RAISED, state))
        text = label if not detail else f"{label}  ·  {detail}"
        self.conn_chip.setText(text)
        self.conn_chip.setStyleSheet(theme.chip_style(fg, bg))
        if state == "lost":
            self.banner.setText(LOST_MESSAGE)
            self.banner.setVisible(True)
        elif state == "reconnecting":
            self.banner.setText(RECONNECT_MESSAGE)
            self.banner.setVisible(True)
        else:
            self.banner.setVisible(False)

    def _camera_label(self):
        camera = (self.host.camera if self.host else None) or self.session.get("camera") or {}
        return camera.get("name") or camera.get("model") or "Camera"

    def _mark_connected(self, detail="", log_message=""):
        self.set_connection("connected", detail or self._camera_label())
        if log_message and self.log_queue:
            self.log_queue.put(log_message)

    def close_loupe(self):
        if self._loupe:
            self._loupe.close()
            self._loupe = None

    def shutdown(self):
        self._host_timer.stop()
        self._folder_timer.stop()
        self.close_loupe()

    def _scan_tether_folder(self):
        folder = (self.session or {}).get("tether_folder") or ""
        if not folder or not os.path.isdir(folder):
            return
        try:
            names = os.listdir(folder)
        except OSError:
            return
        now = time.time()
        found = []
        for name in names:
            if name.startswith("."):
                continue
            if not name.lower().endswith((".jpg", ".jpeg")):
                continue
            path = os.path.abspath(os.path.join(folder, name))
            if path in self._items:
                continue
            try:
                stat = os.stat(path)
            except OSError:
                continue
            if stat.st_size < 1024 or (now - stat.st_mtime) < 0.6:
                continue
            found.append((stat.st_mtime, path))
        found.sort()
        for _mtime, path in found:
            self.add_image(path)
            self.intake.on_image_ready(path)

    def _build_header(self, root):
        header = QWidget()
        header.setObjectName("headerBar")
        header.setFixedHeight(58)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(8)

        collection = self.session["collection"]
        title = QLabel(collection.full_label)
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        self.count_label = QLabel("0 photos")
        self.count_label.setObjectName("dim")
        layout.addWidget(self.count_label)
        layout.addStretch()

        self.pending_pill = QLabel("0 pending")
        self.uploaded_pill = QLabel("0 uploaded")
        self.failed_pill = QLabel("0 failed")
        for pill in (self.pending_pill, self.uploaded_pill, self.failed_pill):
            pill.setStyleSheet(theme.chip_style(theme.TEXT_DIM, theme.BG_INPUT))
            layout.addWidget(pill)

        self.follow_btn = QToolButton()
        self.follow_btn.setText("Follow latest")
        self.follow_btn.setCheckable(True)
        self.follow_btn.setChecked(self.follow_latest)
        self.follow_btn.setProperty("chip", True)
        self.follow_btn.setToolTip("When on, a new shot is selected and opened automatically.")
        self.follow_btn.toggled.connect(self._set_follow_latest)
        layout.addWidget(self.follow_btn)
        self.follow_btn.style().unpolish(self.follow_btn)
        self.follow_btn.style().polish(self.follow_btn)

        if collection.live_gallery_url:
            gallery_btn = QPushButton("Open Live Gallery")
            gallery_btn.setToolTip(collection.live_gallery_url)
            gallery_btn.clicked.connect(self._open_live_gallery)
            layout.addWidget(gallery_btn)

        self.conn_chip = QLabel("Connected")
        layout.addWidget(self.conn_chip)
        if self.host and self.host.using_simulator:
            test = QPushButton("Test JPEG")
            test.clicked.connect(self._simulate_shot)
            layout.addWidget(test)
        end = QPushButton("End session")
        end.setObjectName("danger")
        end.clicked.connect(self._end)
        layout.addWidget(end)
        root.addWidget(header)

    def _build_banner(self, root):
        self.banner = QLabel(LOST_MESSAGE)
        self.banner.setObjectName("lostBanner")
        self.banner.setWordWrap(True)
        self.banner.setVisible(False)
        root.addWidget(self.banner)

    def _build_thumbs(self, splitter):
        card = QWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 12, 12)
        layout.setSpacing(8)

        bar = QHBoxLayout()
        photos = QLabel("PHOTOS")
        photos.setObjectName("sectionHeader")
        hint = QLabel("Click a thumbnail to review  ·  ← → to move  ·  Enter to open")
        hint.setObjectName("dim")
        bar.addWidget(photos)
        bar.addWidget(hint)
        bar.addStretch()
        size_label = QLabel("Size")
        size_label.setObjectName("dim")
        bar.addWidget(size_label)
        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setRange(GRID_COLUMNS_MIN, GRID_COLUMNS_MAX)
        self.size_slider.setValue(self._thumb_columns)
        self.size_slider.setInvertedAppearance(True)
        self.size_slider.setPageStep(1)
        self.size_slider.setSingleStep(1)
        self.size_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.size_slider.setTickInterval(1)
        self.size_slider.setFixedWidth(160)
        self.size_slider.setToolTip("Thumbnail size — snaps to photos per row")
        self.size_slider.valueChanged.connect(self._set_thumb_columns)
        bar.addWidget(self.size_slider)
        layout.addLayout(bar)

        self.photo_stack = QStackedWidget()
        empty = QLabel("Shoot on the camera. Photos appear here as they arrive.")
        empty.setObjectName("dim")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.setWordWrap(True)

        self.thumbs = QListWidget()
        self.thumbs.setViewMode(QListWidget.ViewMode.IconMode)
        self.thumbs.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.thumbs.setMovement(QListWidget.Movement.Static)
        self.thumbs.setWrapping(True)
        self.thumbs.setSpacing(6)
        self.thumbs.setUniformItemSizes(True)
        self.thumbs.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.thumbs.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.thumbs.setItemDelegate(ThumbDelegate(self.thumbs, self._card_size))
        self.thumbs.viewport().installEventFilter(self)
        self._apply_thumb_size()
        self.thumbs.itemActivated.connect(self._open_item)
        self.thumbs.itemClicked.connect(self._open_item)

        self.photo_stack.addWidget(empty)
        self.photo_stack.addWidget(self.thumbs)
        layout.addWidget(self.photo_stack, 1)
        splitter.addWidget(card)

    def _build_queue_column(self, splitter):
        col = QWidget()
        col.setObjectName("uploadRail")
        col.setMinimumWidth(280)
        col.setMaximumWidth(520)
        layout = QVBoxLayout(col)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(8)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)
        self.progress_label = QLabel("0 / 0 uploaded")
        self.progress_label.setObjectName("dim")
        layout.addWidget(self.progress_label)

        self.queue_view = QueueView(on_counts_changed=self._on_counts)
        layout.addWidget(self.queue_view, 1)
        collection = self.session["collection"]
        self.queue_view.register_monitor(
            self.session["session_id"], collection.event_name, collection.full_label
        )
        splitter.addWidget(col)

    def _on_counts(self, counts):
        self.pending_pill.setText(f"{counts.pending} pending")
        self.uploaded_pill.setText(f"{counts.uploaded} uploaded")
        self.failed_pill.setText(f"{counts.failed} failed")
        self._update_progress()

    def _update_progress(self):
        counts = self.queue_view.counts()
        total = counts.pending + counts.uploaded + counts.failed
        self.progress.setValue(int((counts.uploaded / total) * 1000) if total else 0)
        self.progress_label.setText(f"{counts.uploaded} / {total} uploaded")

    def eventFilter(self, obj, event):
        if obj is self.thumbs.viewport() and event.type() == QEvent.Type.Resize:
            self._apply_thumb_size()
        return super().eventFilter(obj, event)

    def _card_size(self):
        return cell_size_for_width(self.thumbs.viewport().width(), self._thumb_columns)

    def _set_thumb_columns(self, value):
        self._thumb_columns = int(value)
        set_thumb_columns_pref(self._thumb_columns)
        self._apply_thumb_size()

    def _apply_thumb_size(self):
        size = self._card_size()
        self.thumbs.setIconSize(QSize(max(40, size.width() - 16), max(40, size.height() - NAME_ROW - 16)))
        self.thumbs.setGridSize(size)
        for index in range(self.thumbs.count()):
            self.thumbs.item(index).setSizeHint(size)
        self.thumbs.doItemsLayout()
        self.thumbs.viewport().update()

    def _update_count(self):
        n = len(self._order)
        self.count_label.setText("1 photo" if n == 1 else f"{n} photos")

    def _load_thumb(self, file_path):
        image = make_thumbnail(file_path, self.session["tether_folder"])
        self._thumb_ready.put((file_path, pil_to_qimage(image)))

    def _apply_ready_thumbs(self):
        while True:
            try:
                file_path, qimage = self._thumb_ready.get_nowait()
            except queue.Empty:
                break
            item = self._items.get(file_path)
            if item:
                pixmap = QPixmap.fromImage(qimage)
                item.setData(PIXMAP_ROLE, pixmap)
                if self._loupe:
                    self._loupe.set_strip_thumb(file_path, pixmap)
        self.thumbs.viewport().update()

    def _simulate_shot(self):
        try:
            self.host.simulate_shot()
        except Exception as exc:
            QMessageBox.warning(self, "Simulator", str(exc))

    def _poll_host(self):
        if not self.host:
            return
        processed = 0
        try:
            while processed < 40:
                msg = self.host.event_queue.get_nowait()
                self._handle_host_event(msg)
                processed += 1
        except Exception:
            pass
        self._apply_ready_thumbs()

    def _handle_host_event(self, msg):
        if msg.get("type") == "hello":
            return
        name = msg.get("name")
        if name == EVENT_IMAGE_READY:
            if self._conn_state in ("lost", "reconnecting"):
                self._mark_connected(log_message="Camera link restored.")
            path = msg.get("path")
            if path:
                self.add_image(path)
                self.intake.on_image_ready(path)
        elif name == EVENT_CONNECTED:
            model = msg.get("model") or msg.get("name") or self._camera_label()
            self._mark_connected(model, "Camera reconnected.")
        elif name == EVENT_RECONNECTING:
            attempt = msg.get("attempt", 1)
            self.set_connection("reconnecting", f"try {attempt}")
            if self.log_queue:
                self.log_queue.put(f"Camera reconnecting (attempt {attempt}).")
        elif name == EVENT_DISCONNECTED:
            self.set_connection("lost")
            if self.log_queue:
                self.log_queue.put(f"Camera disconnected: {msg.get('reason', '')}")
        elif name == EVENT_ERROR:
            if self.log_queue:
                self.log_queue.put(f"Camera host: {msg.get('message', '')}")

    def _open_item(self, item):
        path = item.data(PATH_ROLE)
        if path:
            self._open_path(path)

    def _open_path(self, file_path):
        if not self._order:
            return
        index = self._order.index(file_path) if file_path in self._order else 0
        if self._loupe:
            self._loupe.set_paths(self._order, index)
            self._loupe.raise_()
            self._loupe.activateWindow()
            return
        self._loupe = LoupeView(
            self.window(),
            self._order,
            index,
            self._status_for,
            thumb_lookup=self._thumb_pixmap,
            tether_folder=self.session["tether_folder"],
            follow_latest=self.follow_latest,
        )
        self._loupe.follow_changed.connect(self._set_follow_latest)
        self._loupe.closed.connect(self._loupe_closed)
        self._loupe.show()

    def _focus_path(self, file_path):
        item = self._items.get(file_path)
        if not item:
            return
        self.thumbs.setCurrentItem(item)
        self.thumbs.scrollToItem(item, QAbstractItemView.ScrollHint.EnsureVisible)

    def _thumb_pixmap(self, file_path):
        item = self._items.get(file_path)
        if not item:
            return None
        pixmap = item.data(PIXMAP_ROLE)
        return pixmap if isinstance(pixmap, QPixmap) else None

    def _set_follow_latest(self, on):
        self.follow_latest = bool(on)
        set_follow_latest_pref(self.follow_latest)
        if self.follow_btn.isChecked() != self.follow_latest:
            self.follow_btn.blockSignals(True)
            self.follow_btn.setChecked(self.follow_latest)
            self.follow_btn.blockSignals(False)
        if self._loupe:
            self._loupe.set_follow_latest(self.follow_latest)
        if self.follow_latest and self._order:
            self._focus_path(self._order[0])
            if self._loupe:
                self._loupe.goto(0)

    def _loupe_closed(self):
        self._loupe = None

    def _status_for(self, file_path):
        return self._statuses.get(file_path, (None, None))

    def _open_live_gallery(self):
        collection = self.session.get("collection")
        url = collection.live_gallery_url if collection else ""
        if not url:
            QMessageBox.information(self, "Live Gallery", "No gallery URL is available for this collection.")
            return
        QDesktopServices.openUrl(QUrl(url))

    def _end(self):
        self.close_loupe()
        self.end_session.emit()
