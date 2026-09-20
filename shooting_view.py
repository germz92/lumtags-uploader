import os
import queue
import threading
import time

from PySide6.QtCore import QEvent, QRect, QSettings, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSplitter,
    QStackedWidget,
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
from image_util import make_thumbnail, pixmap_is_placeholder, pil_to_qimage, read_jpeg_info
from loupe_view import LoupeView
from queue_view import QueueView
from status_events import STATUS_UPLOADED, StatusEvent
import theme

GRID_COLUMNS_MIN = 1
GRID_COLUMNS_MAX = 12
GRID_COLUMNS_DEFAULT = 5
NAME_ROW = 28
LOST_MESSAGE = "Camera lost. Turn it on, then click Reconnect if it does not come back."
RECONNECT_MESSAGE = "Waiting for the camera. Turn it on — the app will keep trying."
_SETTINGS = QSettings("GalleryUploader", "GalleryUploader")

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


GRID_GAP = 6


class PhotoInfoPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("photoInfo")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)
        self.name = QLabel("Select a photo")
        self.name.setObjectName("photoInfoName")
        self.meta = QLabel("Time and file size appear here.")
        self.meta.setObjectName("photoInfoMeta")
        self.settings = QLabel("Click a thumbnail · double-click to review")
        self.settings.setObjectName("photoInfoSettings")
        for label in (self.name, self.meta, self.settings):
            label.setWordWrap(True)
            layout.addWidget(label)

    def show_info(self, info):
        if not info:
            self.name.setText("Select a photo")
            self.meta.setText("Time and file size appear here.")
            self.settings.setText("Click a thumbnail · double-click to review")
            return
        self.name.setText(info.get("name") or "Photo")
        bits = [part for part in (info.get("time"), info.get("size"), info.get("camera")) if part]
        self.meta.setText("  ·  ".join(bits) or "—")
        self.settings.setText(info.get("settings") or "No camera settings in this file")


class PhotoGrid(QWidget):
    pathSelected = Signal(str)
    pathActivated = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._paths = []
        self._pixmaps = {}
        self._statuses = {}
        self._selected = None
        self._columns = GRID_COLUMNS_DEFAULT
        self._cell = QSize(120, 118)
        self._hits = []

    def set_columns(self, columns, cell):
        self._columns = max(GRID_COLUMNS_MIN, min(GRID_COLUMNS_MAX, int(columns)))
        self._cell = QSize(cell)
        self._relayout()

    def set_paths(self, paths):
        self._paths = list(paths)
        if self._selected and self._selected not in self._paths:
            self._selected = self._paths[0] if self._paths else None
        self._relayout()

    def set_selected(self, path):
        if path == self._selected:
            return
        self._selected = path
        self._relayout()

    def selected_path(self):
        return self._selected

    def set_thumb(self, path, pixmap):
        self._pixmaps[path] = pixmap
        self.update()

    def set_status(self, path, status):
        self._statuses[path] = status
        self.update()

    def thumb_pixmap(self, path):
        pixmap = self._pixmaps.get(path)
        if not isinstance(pixmap, QPixmap) or pixmap_is_placeholder(pixmap):
            return None
        return pixmap

    def ensure_selected_visible(self, scroller):
        if not scroller:
            return
        for rect, path, _highlight in self._hits:
            if path == self._selected:
                scroller.ensureVisible(rect.center().x(), rect.center().y(), 40, 40)
                return

    def _relayout(self):
        cell_w = max(80, self._cell.width())
        cell_h = max(80, self._cell.height())
        columns = self._columns
        highlight = self._selected if self._selected in self._paths else None
        span_c = 2 if highlight and columns >= 2 else (1 if highlight else 0)
        span_r = 2 if highlight else 0
        reserved = {(c, r) for c in range(span_c) for r in range(span_r)} if span_c else set()
        others = [path for path in self._paths if path != highlight]
        hits = []
        if highlight:
            high_w = cell_w * span_c + GRID_GAP * max(0, span_c - 1)
            high_h = cell_h * span_r + GRID_GAP * max(0, span_r - 1)
            hits.append((QRect(0, 0, high_w, high_h), highlight, True))
        placed = 0
        row = 0
        while placed < len(others):
            for col in range(columns):
                if placed >= len(others):
                    break
                if (col, row) in reserved:
                    continue
                x = col * (cell_w + GRID_GAP)
                y = row * (cell_h + GRID_GAP)
                hits.append((QRect(x, y, cell_w, cell_h), others[placed], False))
                placed += 1
            row += 1
            if row > 4000:
                break
        self._hits = hits
        bottom = max((rect.bottom() for rect, _path, _high in hits), default=0)
        height = max(bottom + 8, 80)
        self.setMinimumHeight(height)
        self.resize(max(self.width(), 80), height)
        self.update()

    def resizeEvent(self, event):
        self._relayout()
        super().resizeEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        visible = event.rect()
        for rect, path, highlight in self._hits:
            if not rect.intersects(visible):
                continue
            self._paint_card(painter, rect, path, highlight)
        painter.end()

    def _paint_card(self, painter, rect, path, highlight):
        box = rect.adjusted(3, 3, -3, -3)
        selected = path == self._selected
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.BG_RAISED))
        painter.drawRoundedRect(box, 8, 8)
        if selected:
            painter.setPen(QPen(QColor(theme.ACCENT), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(box.adjusted(1, 1, -1, -1), 8, 8)
        name_h = 22 if not highlight else 26
        image_rect = box.adjusted(7, 7, -7, -name_h)
        pixmap = self._pixmaps.get(path)
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
        if self._statuses.get(path) == STATUS_UPLOADED:
            theme.draw_upload_check(painter, check_rect, size=max(16, check_rect.width() // 9))
        name = os.path.basename(path)
        name_rect = box.adjusted(8, box.height() - name_h, -8, -4)
        painter.setPen(QColor(theme.TEXT_DIM))
        painter.drawText(
            name_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            painter.fontMetrics().elidedText(name, Qt.TextElideMode.ElideMiddle, name_rect.width()),
        )

    def _path_at(self, pos):
        for rect, path, _highlight in self._hits:
            if rect.contains(pos):
                return path
        return None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            path = self._path_at(event.position().toPoint())
            if path:
                self.set_selected(path)
                self.pathSelected.emit(path)
                self.setFocus(Qt.FocusReason.MouseFocusReason)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        path = self._path_at(event.position().toPoint())
        if path:
            self.pathActivated.emit(path)
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        if not self._paths:
            return super().keyPressEvent(event)
        current = self._selected if self._selected in self._paths else self._paths[0]
        index = self._paths.index(current)
        key = event.key()
        if key in (Qt.Key.Key_Right, Qt.Key.Key_Down):
            index = min(len(self._paths) - 1, index + 1)
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            index = max(0, index - 1)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.pathActivated.emit(current)
            return
        else:
            return super().keyPressEvent(event)
        path = self._paths[index]
        self.set_selected(path)
        self.pathSelected.emit(path)


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
        self._thumb_retries = {}
        self._thumb_ready = queue.Queue()
        self._info_ready = queue.Queue()
        self._info_cache = {}
        self._selected = None
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
                self.thumbs.set_status(event.file_path, event.status)
        self._update_progress()
        if self._loupe:
            self._loupe.refresh_status()

    def add_image(self, file_path):
        file_path = os.path.abspath(file_path)
        if file_path in self._items:
            return
        if self._conn_state in ("lost", "reconnecting"):
            self._mark_connected(log_message="Camera link restored.")
        self._items[file_path] = True
        self._order.insert(0, file_path)
        first = self.photo_stack.currentWidget() is not self.thumb_scroll
        self.thumbs.set_paths(self._order)
        self.photo_stack.setCurrentWidget(self.thumb_scroll)
        if first:
            QTimer.singleShot(0, self._apply_thumb_size)
        self._update_count()
        if self.follow_latest or not self._selected:
            self._select_path(file_path, from_user=False)
        if self._loupe:
            self._loupe.set_paths(
                self._order,
                0 if self.follow_latest else None,
            )
        threading.Thread(target=self._load_thumb, args=(file_path,), daemon=True).start()
        threading.Thread(target=self._load_info, args=(file_path,), daemon=True).start()

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
        theme.style_status_chip(self.conn_chip, fg, bg)
        if state == "lost":
            self.banner.setText(LOST_MESSAGE)
            self.banner.setVisible(True)
        elif state == "reconnecting":
            self.banner.setText(RECONNECT_MESSAGE)
            self.banner.setVisible(True)
        else:
            self.banner.setVisible(False)
        if hasattr(self, "reconnect_btn"):
            self.reconnect_btn.setVisible(state in ("lost", "reconnecting"))

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
            theme.style_status_chip(pill, theme.TEXT_DIM, theme.BG_INPUT)
            layout.addWidget(pill)

        self.follow_btn = QToolButton()
        self.follow_btn.setText("Follow latest")
        self.follow_btn.setCheckable(True)
        self.follow_btn.setChecked(self.follow_latest)
        self.follow_btn.setProperty("chip", True)
        self.follow_btn.setToolTip("When on, the newest shot is the highlight.")
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
        self.reconnect_btn = QPushButton("Reconnect")
        self.reconnect_btn.setToolTip("Start a fresh camera host and try USB again.")
        self.reconnect_btn.clicked.connect(self._manual_reconnect)
        self.reconnect_btn.hide()
        layout.addWidget(self.reconnect_btn)
        if self.host and self.host.using_simulator:
            test = QPushButton("Test JPEG")
            test.clicked.connect(self._simulate_shot)
            layout.addWidget(test)
        self.end_btn = QPushButton("End session")
        self.end_btn.setObjectName("danger")
        self.end_btn.clicked.connect(self._end)
        layout.addWidget(self.end_btn)
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
        hint = QLabel("Click for info  ·  Double-click to review  ·  ← → to move")
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

        self.thumbs = PhotoGrid()
        self.thumbs.pathSelected.connect(self._on_thumb_clicked)
        self.thumbs.pathActivated.connect(self._open_path)
        self.thumb_scroll = QScrollArea()
        self.thumb_scroll.setWidgetResizable(False)
        self.thumb_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.thumb_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.thumb_scroll.setWidget(self.thumbs)
        self.thumb_scroll.viewport().installEventFilter(self)
        self._apply_thumb_size()

        self.photo_stack.addWidget(empty)
        self.photo_stack.addWidget(self.thumb_scroll)
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

        self.photo_info = PhotoInfoPanel()
        layout.addWidget(self.photo_info)

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
        total = counts.total or (counts.pending + counts.uploaded + counts.failed)
        self.progress.setValue(int((counts.uploaded / total) * 1000) if total else 0)
        self.progress_label.setText(f"{counts.uploaded} / {total} uploaded")
        if self._loupe:
            self._loupe.set_upload_progress(counts.uploaded, total, counts.pending, counts.failed)

    def eventFilter(self, obj, event):
        if obj is self.thumb_scroll.viewport() and event.type() == QEvent.Type.Resize:
            self._apply_thumb_size()
        return super().eventFilter(obj, event)

    def _set_thumb_columns(self, value):
        self._thumb_columns = int(value)
        set_thumb_columns_pref(self._thumb_columns)
        self._apply_thumb_size()

    def _apply_thumb_size(self):
        width = self.thumb_scroll.viewport().width()
        size = cell_size_for_width(width, self._thumb_columns)
        self.thumbs.setFixedWidth(max(80, width))
        self.thumbs.set_columns(self._thumb_columns, size)

    def _update_count(self):
        n = len(self._order)
        self.count_label.setText("1 photo" if n == 1 else f"{n} photos")

    def _load_thumb(self, file_path):
        image = make_thumbnail(file_path, self.session["tether_folder"])
        self._thumb_ready.put((file_path, pil_to_qimage(image) if image is not None else None))

    def _load_info(self, file_path):
        try:
            info = read_jpeg_info(file_path)
        except Exception:
            info = {"name": os.path.basename(file_path), "time": "", "size": "", "camera": "", "settings": ""}
        self._info_ready.put((file_path, info))

    def _retry_thumb(self, file_path):
        if file_path not in self._items:
            return
        current = self.thumbs.thumb_pixmap(file_path)
        if current is not None:
            return
        threading.Thread(target=self._load_thumb, args=(file_path,), daemon=True).start()

    def _apply_ready_thumbs(self):
        while True:
            try:
                file_path, qimage = self._thumb_ready.get_nowait()
            except queue.Empty:
                break
            if file_path not in self._items:
                continue
            if qimage is None:
                attempt = self._thumb_retries.get(file_path, 0)
                if attempt < 12:
                    self._thumb_retries[file_path] = attempt + 1
                    QTimer.singleShot(400 * (attempt + 1), lambda path=file_path: self._retry_thumb(path))
                continue
            pixmap = QPixmap.fromImage(qimage)
            if pixmap_is_placeholder(pixmap):
                attempt = self._thumb_retries.get(file_path, 0)
                if attempt < 12:
                    self._thumb_retries[file_path] = attempt + 1
                    QTimer.singleShot(400 * (attempt + 1), lambda path=file_path: self._retry_thumb(path))
                continue
            self._thumb_retries.pop(file_path, None)
            self.thumbs.set_thumb(file_path, pixmap)
            if self._loupe:
                self._loupe.set_strip_thumb(file_path, pixmap)

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
        self._apply_ready_info()

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
            model = msg.get("model") or self._camera_label()
            self._mark_connected(model, "Camera reconnected.")
        elif name == EVENT_RECONNECTING:
            attempt = msg.get("attempt", 1)
            self.set_connection("reconnecting")
            if self.log_queue and (attempt <= 3 or attempt % 10 == 0):
                self.log_queue.put(f"Camera reconnecting (attempt {attempt}).")
        elif name == EVENT_DISCONNECTED:
            self.set_connection("reconnecting", "waiting for camera")
            if self.log_queue:
                self.log_queue.put("Camera disconnected. Waiting for it to come back.")
        elif name == EVENT_ERROR:
            if self.log_queue:
                self.log_queue.put(f"Camera host: {msg.get('message', '')}")

    def _apply_ready_info(self):
        while True:
            try:
                file_path, info = self._info_ready.get_nowait()
            except queue.Empty:
                break
            self._info_cache[file_path] = info
            if file_path == self._selected:
                self.photo_info.show_info(info)

    def _on_thumb_clicked(self, file_path):
        latest = self._order[0] if self._order else None
        if self.follow_latest and file_path != latest:
            self._set_follow_latest(False)
        self._select_path(file_path, from_user=True)

    def _select_path(self, file_path, from_user=False):
        if not file_path or file_path not in self._items:
            return
        self._selected = file_path
        self.thumbs.set_selected(file_path)
        info = self._info_cache.get(file_path)
        if info:
            self.photo_info.show_info(info)
        else:
            self.photo_info.show_info({"name": os.path.basename(file_path), "time": "Reading…", "size": "", "camera": "", "settings": ""})
            threading.Thread(target=self._load_info, args=(file_path,), daemon=True).start()
        if from_user:
            self.thumbs.ensure_selected_visible(self.thumb_scroll)
        elif self.follow_latest:
            self.thumb_scroll.verticalScrollBar().setValue(0)

    def _open_path(self, file_path):
        if not self._order:
            return
        self._select_path(file_path, from_user=True)
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
        self._update_progress()
        self._loupe.show()

    def _thumb_pixmap(self, file_path):
        return self.thumbs.thumb_pixmap(file_path)

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
            self._select_path(self._order[0], from_user=False)
            if self._loupe:
                self._loupe.goto(0)

    def _loupe_closed(self):
        self._loupe = None

    def _status_for(self, file_path):
        return self._statuses.get(file_path, (None, None))

    def _manual_reconnect(self):
        if not self.host:
            return
        self.set_connection("reconnecting", "manual")
        if self.log_queue:
            self.log_queue.put("Reconnect requested.")
        self.host.request_reconnect()

    def _open_live_gallery(self):
        collection = self.session.get("collection")
        url = collection.live_gallery_url if collection else ""
        if not url:
            QMessageBox.information(self, "Live Gallery", "No gallery URL is available for this collection.")
            return
        QDesktopServices.openUrl(QUrl(url))

    def _end(self):
        self.end_btn.setEnabled(False)
        self.end_btn.setText("Ending…")
        self.close_loupe()
        self.end_session.emit()
