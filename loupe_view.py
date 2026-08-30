"""Fullscreen / loupe review — Lumetry-style navigate with arrows."""

import os
import queue
import threading

from PIL import Image, ImageOps
from PySide6.QtCore import QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from image_util import make_thumbnail, pil_to_qimage
from status_events import STATUS_FAILED, STATUS_UPLOADED
import theme

STRIP_W = 96
STRIP_H = 64
STATUS_ROLE = Qt.ItemDataRole.UserRole + 1


def _fit_image(path, max_w, max_h):
    image = Image.open(path)
    image = ImageOps.exif_transpose(image)
    image.thumbnail((max(120, max_w), max(120, max_h)), Image.Resampling.LANCZOS)
    return image.convert("RGB")


def _placeholder_icon():
    pix = QPixmap(STRIP_W, STRIP_H)
    pix.fill(QColor(theme.BG_INPUT))
    return QIcon(pix)


class StripDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(2, 2, -2, -2)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.BG_INPUT))
        painter.drawRoundedRect(rect, 6, 6)
        if selected:
            painter.setPen(QPen(QColor(theme.ACCENT), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 6, 6)

        image_rect = rect.adjusted(4, 4, -4, -4)
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(icon, QIcon) and not icon.isNull():
            pixmap = icon.pixmap(image_rect.size())
            if not pixmap.isNull():
                x = image_rect.x() + (image_rect.width() - pixmap.width()) // 2
                y = image_rect.y() + (image_rect.height() - pixmap.height()) // 2
                painter.drawPixmap(x, y, pixmap)
                image_rect = QRect(x, y, pixmap.width(), pixmap.height())

        if index.data(STATUS_ROLE) == STATUS_UPLOADED:
            theme.draw_upload_check(painter, image_rect, size=14)
        painter.restore()


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
        self._fullscreen = False
        self._ready = queue.Queue()
        self._strip_ready = queue.Queue()
        self._pending_thumbs = set()
        self._placeholder = _placeholder_icon()
        self._gen = 0
        self._current_qimage = None

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
        if path not in self.paths or pixmap is None or pixmap.isNull():
            return
        item = self.strip.item(self.paths.index(path))
        if item:
            item.setIcon(QIcon(pixmap))
            self._pending_thumbs.discard(path)

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
        h.addWidget(self.title_label)
        h.addWidget(self.meta_label)
        h.addStretch()
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
        footer.setFixedHeight(128)
        f = QVBoxLayout(footer)
        f.setContentsMargins(12, 6, 12, 8)
        hint = QLabel("← → navigate     click a thumbnail     F fullscreen     Esc close")
        hint.setObjectName("dim")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f.addWidget(hint)
        self.strip = QListWidget()
        self.strip.setObjectName("filmStrip")
        self.strip.setViewMode(QListWidget.ViewMode.IconMode)
        self.strip.setFlow(QListWidget.Flow.LeftToRight)
        self.strip.setWrapping(False)
        self.strip.setMovement(QListWidget.Movement.Static)
        self.strip.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.strip.setIconSize(QSize(STRIP_W, STRIP_H))
        self.strip.setGridSize(QSize(STRIP_W + 12, STRIP_H + 12))
        self.strip.setUniformItemSizes(True)
        self.strip.setSpacing(4)
        self.strip.setMaximumHeight(88)
        self.strip.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.strip.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.strip.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.strip.setItemDelegate(StripDelegate(self.strip))
        self.strip.itemClicked.connect(self._strip_clicked)
        f.addWidget(self.strip)
        root.addWidget(footer)
        self._rebuild_strip()

    def _bind_keys(self):
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, self.prev_photo)
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, self.next_photo)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self.close)
        QShortcut(QKeySequence(Qt.Key.Key_F), self, self.toggle_fullscreen)
        QShortcut(QKeySequence(Qt.Key.Key_F11), self, self.toggle_fullscreen)
        QShortcut(QKeySequence(Qt.Key.Key_Home), self, lambda: self.goto(0))
        QShortcut(QKeySequence(Qt.Key.Key_End), self, lambda: self.goto(len(self.paths) - 1))

    def _on_follow_toggled(self, checked):
        self.follow_latest = checked
        self.follow_changed.emit(checked)

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
        self._ready.put((gen, pil_to_qimage(image)))

    def _pump(self):
        try:
            while True:
                gen, qimage = self._ready.get_nowait()
                if gen != self._gen:
                    continue
                self._current_qimage = qimage
                self.image_label.setText("")
                self._apply_pixmap()
        except queue.Empty:
            pass
        try:
            while True:
                path, qimage = self._strip_ready.get_nowait()
                self.set_strip_thumb(path, QPixmap.fromImage(qimage))
        except queue.Empty:
            pass

    def _apply_pixmap(self):
        if self._current_qimage is None or self.image_label.width() < 20:
            return
        pix = QPixmap.fromImage(self._current_qimage)
        scaled = pix.scaled(
            self.image_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.image_label.setPixmap(scaled)

    def _paint_status(self, status, reason):
        if status == STATUS_UPLOADED:
            self.status_chip.setText("Uploaded")
            self.status_chip.setStyleSheet(theme.chip_style(theme.GOOD, theme.GOOD_BG))
        elif status == STATUS_FAILED:
            self.status_chip.setText(reason or "Failed")
            self.status_chip.setStyleSheet(theme.chip_style(theme.BAD, theme.BAD_BG))
        else:
            self.status_chip.setText("Uploading")
            self.status_chip.setStyleSheet(theme.chip_style(theme.WARN, theme.WARN_BG))

    def _rebuild_strip(self):
        self.strip.clear()
        self._pending_thumbs.clear()
        for path in self.paths:
            self._add_strip_item(path)
        self._highlight_strip()

    def _sync_strip_status(self):
        for index, path in enumerate(self.paths):
            item = self.strip.item(index)
            if item is None:
                continue
            status, _reason = self.status_lookup(path)
            item.setData(STATUS_ROLE, status)
        self.strip.viewport().update()

    def _add_strip_item(self, path, at_front=False):
        item = QListWidgetItem()
        item.setToolTip(os.path.basename(path))
        item.setSizeHint(QSize(STRIP_W + 12, STRIP_H + 12))
        status, _reason = self.status_lookup(path)
        item.setData(STATUS_ROLE, status)
        pixmap = self.thumb_lookup(path) if self.thumb_lookup else None
        if pixmap is not None and not pixmap.isNull():
            item.setIcon(QIcon(pixmap))
        else:
            item.setIcon(self._placeholder)
            self._request_strip_thumb(path)
        if at_front:
            self.strip.insertItem(0, item)
        else:
            self.strip.addItem(item)

    def _request_strip_thumb(self, path):
        if not self.tether_folder or path in self._pending_thumbs:
            return
        self._pending_thumbs.add(path)
        threading.Thread(target=self._load_strip_thumb, args=(path,), daemon=True).start()

    def _load_strip_thumb(self, path):
        try:
            image = make_thumbnail(path, self.tether_folder)
            self._strip_ready.put((path, pil_to_qimage(image)))
        except Exception:
            self._pending_thumbs.discard(path)

    def _highlight_strip(self):
        if 0 <= self.index < self.strip.count():
            self.strip.setCurrentRow(self.index)
            item = self.strip.item(self.index)
            if item:
                self.strip.scrollToItem(item, QAbstractItemView.ScrollHint.EnsureVisible)

    def _strip_clicked(self, item):
        self.goto(self.strip.row(item))
