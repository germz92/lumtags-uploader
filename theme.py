"""LumTags Uploader theme tokens and stylesheet."""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainterPath, QPen

ACCENT = "#4f8cff"
ACCENT_HOVER = "#6ba1ff"
ACCENT_DIM = "#2c4570"

BG = "#121417"
BG_PANEL = "#191c21"
BG_RAISED = "#1f232a"
BG_INPUT = "#23262d"
BG_HOVER = "#2a2e36"
BORDER = "#2b2f37"
TEXT = "#e8ebf0"
TEXT_DIM = "#98a1b0"
GOOD = "#4ecf7f"
GOOD_BG = "#163526"
WARN = "#f0b44c"
WARN_BG = "#3d3014"
BAD = "#f06a6a"
BAD_BG = "#3a1a1a"
UPLOADING = "#4f8cff"
UPLOADING_BG = "#2c4570"


def draw_upload_check(painter, image_rect, size=22):
    margin = max(4, int(size * 0.28))
    badge = QRectF(
        image_rect.right() - size - margin,
        image_rect.bottom() - size - margin,
        size,
        size,
    )
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(GOOD))
    painter.drawEllipse(badge)
    path = QPainterPath()
    cx, cy = badge.center().x(), badge.center().y()
    scale = size / 22
    path.moveTo(cx - 5.2 * scale, cy + 0.4 * scale)
    path.lineTo(cx - 1.4 * scale, cy + 4.6 * scale)
    path.lineTo(cx + 5.8 * scale, cy - 4.6 * scale)
    painter.setPen(
        QPen(
            QColor("#ffffff"),
            max(1.6, 2.2 * scale),
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
    )
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(path)


def chip_style(fg, bg) -> str:
    return (
        f"color: {fg}; background-color: {bg}; border-radius: 10px; "
        f"padding: 4px 10px; font-weight: 600; font-size: 11px;"
    )


STYLESHEET = f"""
* {{
    font-family: "Segoe UI", "SF Pro Text", "Helvetica Neue", sans-serif;
    font-size: 13px;
    color: {TEXT};
}}
QMainWindow, QDialog, QWidget {{
    background-color: {BG};
}}

QLabel {{
    background: transparent;
}}
QLabel#pageTitle {{
    font-size: 22px;
    font-weight: 700;
}}
QLabel#headerTitle {{
    font-size: 15px;
    font-weight: 700;
}}
QLabel#sectionHeader {{
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
    color: {TEXT_DIM};
    padding: 2px 0;
}}
QLabel#queueTitle {{
    font-size: 14px;
    font-weight: 700;
    color: {TEXT};
    padding: 2px 0;
}}
QLabel#dim {{
    color: {TEXT_DIM};
}}
QLabel#stepActive {{
    font-size: 16px;
    font-weight: 700;
    color: {TEXT};
    padding: 6px 0;
}}
QLabel#stepDone {{
    font-size: 16px;
    font-weight: 600;
    color: {GOOD};
    padding: 6px 0;
}}
QLabel#stepIdle {{
    font-size: 16px;
    color: {TEXT_DIM};
    padding: 6px 0;
}}
QLabel#lostBanner {{
    background-color: {BAD_BG};
    color: {BAD};
    padding: 8px 16px;
    border-radius: 8px;
}}
QWidget#cameraCard {{
    background-color: {BG_RAISED};
    border: 1px solid {BORDER};
    border-radius: 14px;
}}
QLabel#cameraGlyph {{
    font-size: 40px;
    font-weight: 700;
}}
QLabel#cameraTitle {{
    font-size: 16px;
    font-weight: 700;
}}
QProgressBar#cameraBusy {{
    max-height: 4px;
    min-height: 4px;
    background-color: {BG_INPUT};
}}

QWidget#headerBar {{
    background-color: {BG_PANEL};
    border-bottom: 1px solid {BORDER};
}}
QWidget#sidebar {{
    background-color: {BG_PANEL};
    border-right: 1px solid {BORDER};
}}
QWidget#uploadRail {{
    background-color: {BG_PANEL};
    border-left: 1px solid {BORDER};
}}
QWidget#statusBar {{
    background-color: {BG_PANEL};
    border-top: 1px solid {BORDER};
}}
QWidget#logPanel {{
    background-color: {BG_PANEL};
    border-top: 1px solid {BORDER};
}}
QWidget#queueRow {{
    background-color: {BG_RAISED};
    border-radius: 8px;
}}

QLineEdit, QPlainTextEdit, QTextEdit {{
    background-color: {BG_INPUT};
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 7px 12px;
    selection-background-color: {ACCENT};
}}
QLineEdit:hover, QPlainTextEdit:hover {{
    background-color: {BG_HOVER};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {{
    border-color: {ACCENT};
    background-color: {BG_INPUT};
}}
QTextEdit#logText {{
    font-family: "Cascadia Mono", "Menlo", "Consolas", monospace;
    font-size: 11px;
    background-color: {BG_RAISED};
}}

QPushButton {{
    background-color: {BG_INPUT};
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 8px 16px;
    font-weight: 600;
}}
QPushButton:hover {{
    background-color: {BG_HOVER};
}}
QPushButton:pressed {{
    background-color: {BG_PANEL};
}}
QPushButton:disabled {{
    color: {TEXT_DIM};
    background-color: {BG_PANEL};
}}
QPushButton#primary {{
    background-color: {ACCENT};
    color: white;
}}
QPushButton#primary:hover {{
    background-color: {ACCENT_HOVER};
}}
QPushButton#primary:disabled {{
    background-color: {ACCENT_DIM};
    color: #a9bcdf;
}}
QPushButton#danger {{
    color: {BAD};
    background: transparent;
    border: 1px solid {BAD};
}}
QPushButton#danger:hover {{
    background-color: {BAD_BG};
}}
QPushButton[chip="true"] {{
    background-color: {BG_INPUT};
    border: 1px solid transparent;
    border-radius: 14px;
    padding: 6px 10px;
    font-weight: 600;
    color: {TEXT_DIM};
}}
QPushButton[chip="true"]:hover {{
    background-color: {BG_HOVER};
    color: {TEXT};
}}
QPushButton[chip="true"]:checked {{
    background-color: {ACCENT_DIM};
    border-color: {ACCENT};
    color: white;
}}
QSlider::groove:horizontal {{
    height: 4px;
    background: {BG_INPUT};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    width: 14px;
    height: 14px;
    margin: -6px 0;
    background: {ACCENT};
    border-radius: 7px;
}}
QSlider::handle:horizontal:hover {{
    background: {ACCENT_HOVER};
}}

QToolButton {{
    background: transparent;
    border: none;
    border-radius: 8px;
    padding: 6px;
}}
QToolButton:hover {{
    background-color: {BG_HOVER};
}}
QToolButton[chip="true"] {{
    background-color: {BG_INPUT};
    border: 1px solid transparent;
    border-radius: 14px;
    padding: 5px 12px;
    font-weight: 600;
    color: {TEXT_DIM};
}}
QToolButton[chip="true"]:hover {{
    background-color: {BG_HOVER};
    color: {TEXT};
}}
QToolButton[chip="true"]:checked {{
    background-color: {ACCENT_DIM};
    border-color: {ACCENT};
    color: white;
}}

QListWidget, QListView {{
    background-color: transparent;
    border: none;
    outline: none;
}}
QListWidget::item {{
    background: transparent;
    border: none;
}}
QListWidget::item:selected {{
    background: transparent;
}}
QListWidget#filmStrip {{
    background: transparent;
}}
QListWidget#filmStrip::item {{
    background-color: {BG_INPUT};
    border: 2px solid transparent;
    border-radius: 8px;
    padding: 2px;
    margin: 2px;
}}
QListWidget#filmStrip::item:selected {{
    background-color: {ACCENT_DIM};
    border-color: {ACCENT};
}}

QTableWidget {{
    background-color: transparent;
    border: none;
    outline: none;
    gridline-color: transparent;
}}
QTableWidget::item {{
    padding: 8px 12px;
    border: none;
    border-bottom: 1px solid {BORDER};
}}
QTableWidget::item:hover {{
    background-color: {BG_HOVER};
}}
QTableWidget::item:selected {{
    background-color: {ACCENT};
    color: white;
}}
QHeaderView::section {{
    background-color: {BG_RAISED};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 8px 12px;
    font-weight: 600;
    color: {TEXT_DIM};
}}
QHeaderView::section:hover {{
    color: {TEXT};
}}
QTableCornerButton::section {{
    background: {BG_RAISED};
    border: none;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 16px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: #363c47;
    border-radius: 6px;
    min-height: 40px;
}}
QScrollBar::handle:vertical:hover {{
    background: #475060;
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0; width: 0;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 16px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: #363c47;
    border-radius: 6px;
    min-width: 40px;
}}
QScrollBar::handle:horizontal:hover {{
    background: #475060;
}}

QProgressBar {{
    background-color: {BG_INPUT};
    border: none;
    border-radius: 3px;
    text-align: center;
    color: transparent;
    max-height: 6px;
}}
QProgressBar::chunk {{
    background-color: {ACCENT};
    border-radius: 3px;
}}
QSplitter::handle {{
    background-color: {BORDER};
    width: 1px;
}}
QToolTip {{
    background-color: {BG_RAISED};
    color: {TEXT};
    border: 1px solid {BORDER};
    padding: 5px 8px;
}}
QScrollArea {{
    border: none;
    background: transparent;
}}
"""
