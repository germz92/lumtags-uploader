"""Fullscreen / loupe review — Lumetry-style navigate with arrows."""

import os
import queue
import threading

from PIL import Image, ImageChops, ImageOps
from PySide6.QtCore import QRect, QRectF, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPainterPath, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from image_util import make_thumbnail, pixmap_is_placeholder, pil_to_qimage
from status_events import STATUS_FAILED, STATUS_UPLOADED
import theme

STRIP_W = 96
STRIP_H = 72
CLIP_THRESHOLD = 252
_SETTINGS = QSettings("GalleryUploader", "GalleryUploader")


def _fit_image(path, max_w, max_h):
    image = Image.open(path)
    image = ImageOps.exif_transpose(image)
    image.thumbnail((max(120, max_w), max(120, max_h)), Image.Resampling.LANCZOS)
    return image.convert("RGB")


def highlight_clip_pref():
    return _SETTINGS.value("highlight_clip", False, type=bool)


def set_highlight_clip_pref(on):
    _SETTINGS.setValue("highlight_clip", bool(on))


def apply_highlight_clip(image, threshold=CLIP_THRESHOLD):
    rgb = image.convert("RGB")
    red, green, blue = rgb.split()
    hottest = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    mask = hottest.point(lambda value: 255 if value >= threshold else 0)
    overlay = Image.new("RGB", rgb.size, (255, 48, 88))
    return Image.composite(overlay, rgb, mask)


def _draw_fitted_pixmap(painter, box, pixmap):
    device = painter.device()
    dpr = device.devicePixelRatioF() if device is not None else 1.0
    scaled = pixmap.scaled(
        max(1, int(box.width() * dpr)),
        max(1, int(box.height() * dpr)),
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    scaled.setDevicePixelRatio(dpr)
    width = max(1, int(scaled.width() / dpr))
    height = max(1, int(scaled.height() / dpr))
    dest = QRect(
        box.x() + (box.width() - width) // 2,
        box.y() + (box.height() - height) // 2,
        width,
        height,
    )
    painter.drawPixmap(dest, scaled)
    return dest


class _StripCell(QWidget):
    clicked = Signal()

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path
        self.pixmap = QPixmap()
        self.status = None
        self.selected = False
        self.setFixedSize(STRIP_W + 10, STRIP_H + 10)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_pixmap(self, pixmap):
        self.pixmap = pixmap if isinstance(pixmap, QPixmap) else QPixmap()
        self.update()

    def set_status(self, status):
        self.status = status
        self.update()

    def set_selected(self, on):
        self.selected = bool(on)
        self.update()

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(2, 2, -2, -2)
        image_rect = rect.adjusted(5, 5, -5, -5)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.BG_INPUT))
        painter.drawRoundedRect(rect, 6, 6)

        check_rect = image_rect
        if not self.pixmap.isNull():
            clip = QPainterPath()
            clip.addRoundedRect(QRectF(image_rect), 4, 4)
            painter.setClipPath(clip)
            check_rect = _draw_fitted_pixmap(painter, image_rect, self.pixmap)
            painter.setClipping(False)

        if self.selected:
            painter.setPen(QPen(QColor(theme.ACCENT), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 6, 6)

        if self.status == STATUS_UPLOADED:
            theme.draw_upload_check(painter, check_rect, size=10)
        painter.end()


class _FilmStrip(QScrollArea):
    item_clicked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("filmStrip")
        self.setWidgetResizable(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMaximumHeight(96)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self._row = QWidget()
        self._layout = QHBoxLayout(self._row)
        self._layout.setContentsMargins(0, 2, 0, 2)
        self._layout.setSpacing(6)
        self._layout.addStretch()
        self.setWidget(self._row)
        self.cells = []

    def clear(self):
        for cell in self.cells:
            self._layout.removeWidget(cell)
            cell.deleteLater()
        self.cells.clear()
        self._relayout()

    def add_cell(self, path, at_front=False):
        cell = _StripCell(path, self._row)
        cell.clicked.connect(lambda: self.item_clicked.emit(self.cells.index(cell)))
        if at_front:
            self._layout.insertWidget(0, cell)
            self.cells.insert(0, cell)
        else:
            self._layout.insertWidget(self._layout.count() - 1, cell)
            self.cells.append(cell)
        self._relayout()
        return cell

    def cell_for_path(self, path):
        for cell in self.cells:
            if cell.path == path:
                return cell
        return None

    def set_selected_index(self, index):
        for i, cell in enumerate(self.cells):
            cell.set_selected(i == index)
        if 0 <= index < len(self.cells):
            self.ensureWidgetVisible(self.cells[index])

    def _relayout(self):
        self._row.adjustSize()
        self._row.resize(self._row.sizeHint())


class LoupeView(QDialog):
    closed = Signal()
    follow_changed = Signal(bool)

    def __init__(
        self,
        parent,
        paths,
        index,
        status_lookup,
        thumb_lookup=None,
        tether_folder="",
        follow_latest=True,
    ):
        super().__init__(parent)
        self.setWindowTitle("Review")
        self.setWindowFlag(Qt.WindowType.Window)
        self.resize(1100, 780)
        self.setMinimumSize(800, 560)

        self.paths = list(paths)
        self.index = max(0, min(index, len(self.paths) - 1)) if self.paths else 0
        self.status_lookup = status_lookup
        self.thumb_lookup = thumb_lookup
        self.tether_folder = tether_folder
        self.follow_latest = follow_latest
        self.highlight_clip = highlight_clip_pref()
        self._fullscreen = False
        self._ready = queue.Queue()
        self._strip_ready = queue.Queue()
        self._pending_thumbs = set()
        self._strip_retries = {}
        self._gen = 0
        self._current_qimage = None
        self._clip_qimage = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._build_chrome(root)
        self._bind_keys()
        QTimer.singleShot(40, self._show_current)

        self._pump_timer = QTimer(self)
        self._pump_timer.timeout.connect(self._pump)
        self._pump_timer.start(80)

    def set_paths(self, paths, index=None):
        previous = self.paths[self.index] if self.paths else None
        new_paths = list(paths)
        old_len = len(self.paths)
        if new_paths[:old_len] == self.paths:
            for path in new_paths[old_len:]:
                self.paths.append(path)
                self._add_strip_item(path)
        elif old_len and new_paths[len(new_paths) - old_len:] == self.paths:
            for path in reversed(new_paths[: len(new_paths) - old_len]):
                self.paths.insert(0, path)
                self._add_strip_item(path, at_front=True)
        else:
            self.paths = new_paths
            self._rebuild_strip()
        if index is not None:
            self.goto(index)
            return
        if previous and previous in self.paths:
            self.index = self.paths.index(previous)
        elif self.paths:
            self.index = max(0, min(self.index, len(self.paths) - 1))
        self._highlight_strip()
        self.meta_label.setText(f"{self.index + 1}  /  {len(self.paths)}" if self.paths else "")

    def set_strip_thumb(self, path, pixmap):
        if path not in self.paths:
            return
        cell = self.strip.cell_for_path(path)
        if cell is None:
            return
        if pixmap is None or pixmap_is_placeholder(pixmap):
            self._pending_thumbs.discard(path)
            self._schedule_strip_retry(path)
            return
        cell.set_pixmap(pixmap)
        self._pending_thumbs.discard(path)
        self._strip_retries.pop(path, None)

    def set_follow_latest(self, on):
        self.follow_latest = bool(on)
        if self.follow_btn.isChecked() != self.follow_latest:
            self.follow_btn.blockSignals(True)
            self.follow_btn.setChecked(self.follow_latest)
            self.follow_btn.blockSignals(False)

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)

    def _build_chrome(self, root):
        header = QWidget()
        header.setObjectName("headerBar")
        header.setFixedHeight(52)
        h = QHBoxLayout(header)
        h.setContentsMargins(16, 0, 16, 0)
        self.title_label = QLabel()
        self.meta_label = QLabel()
        self.meta_label.setObjectName("dim")
        self.status_chip = QLabel()
        theme.style_status_chip(self.status_chip, theme.TEXT_DIM, theme.BG_INPUT)
        h.addWidget(self.title_label)
        h.addWidget(self.meta_label)
        h.addStretch()
        progress = QWidget()
        progress.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        progress_layout = QVBoxLayout(progress)
        progress_layout.setContentsMargins(0, 10, 8, 10)
        progress_layout.setSpacing(3)
        self.upload_label = QLabel("0 / 0 uploaded")
        self.upload_label.setObjectName("dim")
        self.upload_bar = QProgressBar()
        self.upload_bar.setRange(0, 1000)
        self.upload_bar.setValue(0)
        self.upload_bar.setTextVisible(False)
        self.upload_bar.setFixedSize(160, 5)
        progress_layout.addWidget(self.upload_label)
        progress_layout.addWidget(self.upload_bar)
        h.addWidget(progress)
        h.addWidget(self.status_chip)
        self.follow_btn = QToolButton()
        self.follow_btn.setText("Follow latest")
        self.follow_btn.setCheckable(True)
        self.follow_btn.setChecked(self.follow_latest)
        self.follow_btn.setProperty("chip", True)
        self.follow_btn.toggled.connect(self._on_follow_toggled)
        h.addWidget(self.follow_btn)
        self.follow_btn.style().unpolish(self.follow_btn)
        self.follow_btn.style().polish(self.follow_btn)
        self.clip_btn = QToolButton()
        self.clip_btn.setText("Highlights")
        self.clip_btn.setCheckable(True)
        self.clip_btn.setChecked(self.highlight_clip)
        self.clip_btn.setProperty("chip", True)
        self.clip_btn.setToolTip("Show blown highlights in red. Shortcut H.")
        self.clip_btn.toggled.connect(self._on_clip_toggled)
        h.addWidget(self.clip_btn)
        self.clip_btn.style().unpolish(self.clip_btn)
        self.clip_btn.style().polish(self.clip_btn)
        fs = QPushButton("Fullscreen  F")
        fs.clicked.connect(self.toggle_fullscreen)
        close_btn = QPushButton("Close  Esc")
        close_btn.clicked.connect(self.close)
        h.addWidget(fs)
        h.addWidget(close_btn)
        root.addWidget(header)

        stage = QWidget()
        stage_layout = QHBoxLayout(stage)
        stage_layout.setContentsMargins(12, 8, 12, 8)
        self.prev_btn = QPushButton("‹")
        self.prev_btn.setFixedSize(48, 48)
        self.prev_btn.clicked.connect(self.prev_photo)
        self.image_label = QLabel("Loading…")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumSize(200, 200)
        self.next_btn = QPushButton("›")
        self.next_btn.setFixedSize(48, 48)
        self.next_btn.clicked.connect(self.next_photo)
        stage_layout.addWidget(self.prev_btn)
        stage_layout.addWidget(self.image_label, 1)
        stage_layout.addWidget(self.next_btn)
        root.addWidget(stage, 1)

        footer = QWidget()
        footer.setObjectName("statusBar")
        footer.setFixedHeight(136)
        f = QVBoxLayout(footer)
        f.setContentsMargins(12, 6, 12, 8)
        hint = QLabel("← → navigate     click a thumbnail     H highlights     F fullscreen     Esc close")
        hint.setObjectName("dim")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f.addWidget(hint)
        self.strip = _FilmStrip()
        self.strip.item_clicked.connect(self.goto)
        f.addWidget(self.strip)
        root.addWidget(footer)
        self._rebuild_strip()

    def _bind_keys(self):
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, self.prev_photo)
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, self.next_photo)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self.close)
        QShortcut(QKeySequence(Qt.Key.Key_F), self, self.toggle_fullscreen)
        QShortcut(QKeySequence(Qt.Key.Key_F11), self, self.toggle_fullscreen)
        QShortcut(QKeySequence(Qt.Key.Key_H), self, self._toggle_highlight_clip)
        QShortcut(QKeySequence(Qt.Key.Key_Home), self, lambda: self.goto(0))
        QShortcut(QKeySequence(Qt.Key.Key_End), self, lambda: self.goto(len(self.paths) - 1))

    def _on_follow_toggled(self, checked):
        self.follow_latest = checked
        self.follow_changed.emit(checked)

    def _toggle_highlight_clip(self):
        self.clip_btn.setChecked(not self.clip_btn.isChecked())

    def _on_clip_toggled(self, checked):
        self.highlight_clip = bool(checked)
        set_highlight_clip_pref(self.highlight_clip)
        self._apply_pixmap()

    def toggle_fullscreen(self):
        self._fullscreen = not self._fullscreen
        if self._fullscreen:
            self.showFullScreen()
        else:
            self.showNormal()

    def prev_photo(self):
        if self.paths:
            self.goto((self.index - 1) % len(self.paths))

    def next_photo(self):
        if self.paths:
            self.goto((self.index + 1) % len(self.paths))

    def goto(self, index):
        if not self.paths:
            return
        self.index = max(0, min(index, len(self.paths) - 1))
        self._show_current()

    def refresh_status(self):
        self._sync_strip_status()
        if not self.paths:
            return
        status, reason = self.status_lookup(self.paths[self.index])
        self._paint_status(status, reason)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_pixmap()

    def _show_current(self):
        if not self.paths:
            self.title_label.setText("No photos")
            return
        path = self.paths[self.index]
        self.title_label.setText(os.path.basename(path))
        self.meta_label.setText(f"{self.index + 1}  /  {len(self.paths)}")
        status, reason = self.status_lookup(path)
        self._paint_status(status, reason)
        self._highlight_strip()
        self._gen += 1
        gen = self._gen
        self.image_label.setPixmap(QPixmap())
        self.image_label.setText("Loading…")
        max_w = max(200, self.image_label.width() or 900)
        max_h = max(200, self.image_label.height() or 560)
        threading.Thread(target=self._decode, args=(path, max_w, max_h, gen), daemon=True).start()

    def _decode(self, path, max_w, max_h, gen):
        try:
            image = _fit_image(path, max_w, max_h)
        except Exception:
            image = Image.new("RGB", (400, 280), (31, 35, 42))
        self._ready.put((gen, pil_to_qimage(image), pil_to_qimage(apply_highlight_clip(image))))

    def _pump(self):
        try:
            while True:
                gen, qimage, clip_qimage = self._ready.get_nowait()
                if gen != self._gen:
                    continue
                self._current_qimage = qimage
                self._clip_qimage = clip_qimage
                self.image_label.setText("")
                self._apply_pixmap()
                self._fill_strip_from_preview()
        except queue.Empty:
            pass
        try:
            while True:
                path, qimage = self._strip_ready.get_nowait()
                self.set_strip_thumb(path, QPixmap.fromImage(qimage) if qimage is not None else None)
        except queue.Empty:
            pass

    def _apply_pixmap(self):
        source = self._clip_qimage if self.highlight_clip and self._clip_qimage is not None else self._current_qimage
        if source is None or self.image_label.width() < 20:
            return
        pix = QPixmap.fromImage(source)
        scaled = pix.scaled(
            self.image_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.image_label.setPixmap(scaled)

    def _paint_status(self, status, reason):
        if status == STATUS_UPLOADED:
            self.status_chip.setText("Uploaded")
            theme.style_status_chip(self.status_chip, theme.GOOD, theme.GOOD_BG)
        elif status == STATUS_FAILED:
            self.status_chip.setText(reason or "Failed")
            theme.style_status_chip(self.status_chip, theme.BAD, theme.BAD_BG)
        else:
            self.status_chip.setText("Uploading")
            theme.style_status_chip(self.status_chip, theme.WARN, theme.WARN_BG)

    def set_upload_progress(self, uploaded, total, pending=0, failed=0):
        total = max(int(total or 0), 0)
        uploaded = max(int(uploaded or 0), 0)
        self.upload_bar.setValue(int((uploaded / total) * 1000) if total else 0)
        extra = []
        if pending:
            extra.append(f"{pending} pending")
        if failed:
            extra.append(f"{failed} failed")
        suffix = f"  ·  {', '.join(extra)}" if extra else ""
        self.upload_label.setText(f"{uploaded} / {total} uploaded{suffix}")

    def _rebuild_strip(self):
        self.strip.clear()
        self._pending_thumbs.clear()
        self._strip_retries.clear()
        for path in self.paths:
            self._add_strip_item(path)
        self._highlight_strip()

    def _sync_strip_status(self):
        for path, cell in zip(self.paths, self.strip.cells):
            status, _reason = self.status_lookup(path)
            cell.set_status(status)

    def _add_strip_item(self, path, at_front=False):
        cell = self.strip.add_cell(path, at_front=at_front)
        cell.setToolTip(os.path.basename(path))
        status, _reason = self.status_lookup(path)
        cell.set_status(status)
        pixmap = self.thumb_lookup(path) if self.thumb_lookup else None
        if pixmap is not None and not pixmap_is_placeholder(pixmap):
            cell.set_pixmap(pixmap)
        else:
            self._request_strip_thumb(path)

    def _thumb_folder(self):
        if self.tether_folder:
            return self.tether_folder
        if self.paths:
            return os.path.dirname(self.paths[0])
        return ""

    def _request_strip_thumb(self, path):
        if path in self._pending_thumbs or not self._thumb_folder():
            return
        self._pending_thumbs.add(path)
        threading.Thread(target=self._load_strip_thumb, args=(path,), daemon=True).start()

    def _schedule_strip_retry(self, path):
        attempt = self._strip_retries.get(path, 0)
        if attempt >= 12:
            return
        self._strip_retries[path] = attempt + 1
        QTimer.singleShot(400 * (attempt + 1), lambda p=path: self._request_strip_thumb(p))

    def _load_strip_thumb(self, path):
        try:
            image = make_thumbnail(path, self._thumb_folder())
            self._strip_ready.put((path, pil_to_qimage(image) if image is not None else None))
        except Exception:
            self._strip_ready.put((path, None))

    def _fill_strip_from_preview(self):
        if not self.paths or self._current_qimage is None:
            return
        path = self.paths[self.index]
        cell = self.strip.cell_for_path(path)
        if cell is None or (not cell.pixmap.isNull() and not pixmap_is_placeholder(cell.pixmap)):
            return
        cell.set_pixmap(QPixmap.fromImage(self._current_qimage))
        self._pending_thumbs.discard(path)
        self._strip_retries.pop(path, None)

    def _highlight_strip(self):
        self.strip.set_selected_index(self.index)
