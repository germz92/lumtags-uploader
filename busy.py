"""Launch splash and in-window busy overlay."""

from PySide6.QtCore import QEvent, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from platform_support import APP_NAME, app_icon_pixmap
import theme


class Spinner(QWidget):
    def __init__(self, parent=None, size=36):
        super().__init__(parent)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet("background: transparent;")

    def start(self):
        if not self._timer.isActive():
            self._timer.start(16)

    def stop(self):
        self._timer.stop()

    def _tick(self):
        self._angle = (self._angle + 8) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        track = QPen(QColor(theme.ACCENT_DIM), 3)
        track.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(track)
        painter.drawArc(box, 0, 360 * 16)
        sweep = QPen(QColor(theme.ACCENT), 3)
        sweep.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(sweep)
        painter.drawArc(box, -self._angle * 16, 110 * 16)
        painter.end()


def _fill_busy_card(layout, title, subtitle, kicker=""):
    row = QHBoxLayout()
    row.setSpacing(14)
    pixmap = app_icon_pixmap(36)
    if not pixmap.isNull():
        mark = QLabel()
        mark.setFixedSize(36, 36)
        mark.setPixmap(pixmap)
        row.addWidget(mark)
    title_col = QVBoxLayout()
    title_col.setSpacing(2)
    brand = QLabel(APP_NAME)
    brand.setObjectName("pageTitle")
    title_col.addWidget(brand)
    kicker_label = QLabel(kicker)
    kicker_label.setObjectName("dim")
    kicker_label.setVisible(bool(kicker))
    title_col.addWidget(kicker_label)
    row.addLayout(title_col, 1)
    layout.addLayout(row)

    spinner = Spinner()
    layout.addWidget(spinner, 0, Qt.AlignmentFlag.AlignHCenter)

    heading = QLabel(title)
    heading.setObjectName("cameraTitle")
    heading.setWordWrap(True)
    layout.addWidget(heading)
    detail = QLabel(subtitle)
    detail.setObjectName("dim")
    detail.setWordWrap(True)
    layout.addWidget(detail)
    bar = QProgressBar()
    bar.setObjectName("cameraBusy")
    bar.setRange(0, 0)
    bar.setTextVisible(False)
    layout.addWidget(bar)
    return spinner, heading, detail, kicker_label


class LaunchSplash(QWidget):
    def __init__(self, title="Starting…", subtitle="Loading galleries and preparing setup. This will take a few seconds."):
        super().__init__()
        self.setObjectName("busySplash")
        self.setWindowTitle(APP_NAME)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.SplashScreen
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setFixedSize(440, 280)
        card = QVBoxLayout(self)
        card.setContentsMargins(28, 28, 28, 28)
        card.setSpacing(14)
        self._spinner, self._title, self._detail, _kicker = _fill_busy_card(
            card,
            title,
            subtitle,
            kicker="The app has started.",
        )
        self._spinner.start()
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.move(geo.center() - self.rect().center())

    def set_message(self, title, subtitle=""):
        self._title.setText(title)
        self._detail.setText(subtitle)
        self._detail.setVisible(bool(subtitle))

    def closeEvent(self, event):
        self._spinner.stop()
        super().closeEvent(event)


class BusyOverlay(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("busyOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.addStretch()
        card = QWidget()
        card.setObjectName("busyCard")
        card.setMaximumWidth(440)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(28, 24, 28, 24)
        card_layout.setSpacing(12)
        self._spinner, self._title, self._detail, self._kicker = _fill_busy_card(
            card_layout,
            "Working…",
            "",
        )
        layout.addWidget(card, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch()
        self.hide()
        parent.installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is self.parent() and event.type() == QEvent.Type.Resize:
            self.resize(obj.size())
        return False

    def show_busy(self, title, subtitle="", kicker="Please wait."):
        self._title.setText(title)
        self._detail.setText(subtitle)
        self._detail.setVisible(bool(subtitle))
        self._kicker.setText(kicker)
        self._kicker.setVisible(bool(kicker))
        if self.parent():
            self.resize(self.parent().size())
        self.raise_()
        self.show()
        self._spinner.start()

    def hide_busy(self):
        self._spinner.stop()
        self.hide()
