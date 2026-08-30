import sys

if "--host-simulator" in sys.argv:
    from crsdk_simulator import main as simulator_main

    simulator_main()
    raise SystemExit(0)

import queue
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon, QPixmap, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from db import get_client_logos, get_events
from events_model import parse_events
from logger import add_queue_handler, get_logger
from platform_support import APP_NAME, APP_ID, app_icon_path
from shooting_view import ShootingWorkspace
from status_events import is_status_event
from tether_intake import TetherIntake
from tether_session import write_session
import theme
from wizard import SetupWizard

logger = get_logger("main")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1200, 860)
        self.setMinimumSize(1000, 720)

        self.log_queue = queue.Queue()
        self.log_batch = []
        self.last_log_update = 0
        self._log_expanded = False

        self.wizard = None
        self.workspace = None
        self.intake = None
        self.host = None
        self.events = []

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._build_header(root)
        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self._build_log(root)
        self._show_wizard()

        add_queue_handler(logger, self.log_queue)
        self._log_timer = QTimer(self)
        self._log_timer.timeout.connect(self.poll_log_queue)
        self._log_timer.start(200)

    def closeEvent(self, event):
        if self.workspace:
            self.workspace.shutdown()
        self._teardown_session(wait_uploads=False)
        if self.wizard:
            self.wizard.destroy_host()
        event.accept()

    def _build_header(self, root):
        header = QWidget()
        header.setObjectName("headerBar")
        header.setFixedHeight(52)
        self.app_header = header
        layout = QHBoxLayout(header)
        layout.setContentsMargins(16, 0, 16, 0)
        icon_path = app_icon_path()
        if icon_path:
            mark = QLabel()
            mark.setPixmap(
                QPixmap(icon_path).scaled(
                    28,
                    28,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            layout.addWidget(mark)
        title = QLabel(APP_NAME)
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        layout.addStretch()
        root.addWidget(header)

    def _build_log(self, root):
        self.log_panel = QWidget()
        self.log_panel.setObjectName("logPanel")
        layout = QVBoxLayout(self.log_panel)
        layout.setContentsMargins(16, 8, 16, 10)
        header_row = QHBoxLayout()
        log_label = QLabel("LOG")
        log_label.setObjectName("sectionHeader")
        header_row.addWidget(log_label)
        header_row.addStretch()
        self.log_toggle = QPushButton("Show log")
        self.log_toggle.clicked.connect(self.toggle_log)
        header_row.addWidget(self.log_toggle)
        layout.addLayout(header_row)
        self.log_text = QTextEdit()
        self.log_text.setObjectName("logText")
        self.log_text.setReadOnly(True)
        self.log_text.setFixedHeight(140)
        self.log_text.hide()
        layout.addWidget(self.log_text)
        root.addWidget(self.log_panel)

    def toggle_log(self):
        self._log_expanded = not self._log_expanded
        self.log_text.setVisible(self._log_expanded)
        self.log_toggle.setText("Hide log" if self._log_expanded else "Show log")

    def _clear_stage(self):
        if self.wizard:
            self.stack.removeWidget(self.wizard)
            self.wizard.deleteLater()
            self.wizard = None
        if self.workspace:
            self.workspace.shutdown()
            self.stack.removeWidget(self.workspace)
            self.workspace.deleteLater()
            self.workspace = None

    def _load_events(self):
        self.events = parse_events(get_events(), get_client_logos())
        self.log_queue.put(f"Loaded {sum(len(e.collections) for e in self.events)} collections.")

    def _show_wizard(self):
        self._clear_stage()
        self.app_header.show()
        self._load_events()
        self.wizard = SetupWizard(events=self.events, log_queue=self.log_queue)
        self.wizard.finished.connect(self._start_session)
        self.stack.addWidget(self.wizard)
        self.stack.setCurrentWidget(self.wizard)
        if not self.events:
            QMessageBox.warning(
                self,
                "No events",
                "Could not load events from the database. Check MongoDB and refresh by restarting setup.",
            )

    def _start_session(self, result):
        QTimer.singleShot(10, lambda r=result: self._enter_session(r))

    def _enter_session(self, result):
        collection = result["collection"]
        tether_folder = result["tether_folder"]
        host = result["host"]
        camera = result.get("camera") or {}
        session_id = f"{collection.event_id}_{collection.s3_folder}_{tether_folder}"

        write_session(tether_folder, {
            "event_id": collection.event_id,
            "event_name": collection.event_name,
            "collection_name": collection.collection_name,
            "parent_name": collection.parent_name,
            "s3_folder": collection.s3_folder,
            "folder_name": result["folder_name"],
            "parent_path": result["parent_path"],
            "camera_id": camera.get("id"),
            "camera_model": camera.get("model"),
            "camera_serial": camera.get("serial"),
        })

        self.host = host
        self.intake = TetherIntake(session_id, collection.s3_folder, tether_folder, self.log_queue)
        session = {
            "session_id": session_id,
            "collection": collection,
            "tether_folder": tether_folder,
            "camera": camera,
        }

        wizard = self.wizard
        self.wizard = None
        if wizard:
            self.stack.removeWidget(wizard)
            wizard.deleteLater()

        self.workspace = ShootingWorkspace(
            session=session,
            host=host,
            intake=self.intake,
            log_queue=self.log_queue,
        )
        self.workspace.end_session.connect(self._end_session)
        self.stack.addWidget(self.workspace)
        self.stack.setCurrentWidget(self.workspace)
        self.app_header.hide()

        existing = self.intake.import_existing()
        for path in existing:
            self.workspace.add_image(path)
        self.log_queue.put(f"Session started: {collection.full_label} → {tether_folder}")

    def _end_session(self):
        self._teardown_session(wait_uploads=False)
        self._show_wizard()

    def _teardown_session(self, wait_uploads=False):
        if self.intake:
            self.intake.shutdown(wait=wait_uploads)
            self.intake = None
        if self.host:
            try:
                self.host.disconnect()
                self.host.close()
            except Exception:
                pass
            self.host = None
        if self.wizard:
            self.wizard.host = None

    def poll_log_queue(self):
        current_time = time.time()
        messages_processed = 0
        status_events = []
        try:
            while messages_processed < 50:
                message = self.log_queue.get_nowait()
                if is_status_event(message):
                    status_events.append(message)
                else:
                    self.log_batch.append(str(message))
                messages_processed += 1
        except queue.Empty:
            pass

        if status_events and self.workspace:
            self.workspace.apply_status_events(status_events)

        if self.log_batch and (messages_processed > 0 or current_time - self.last_log_update > 1.0):
            self.log_text.append("\n".join(self.log_batch))
            document = self.log_text.document()
            if document.blockCount() > 1000:
                cursor = self.log_text.textCursor()
                cursor.movePosition(QTextCursor.MoveOperation.Start)
                cursor.movePosition(
                    QTextCursor.MoveOperation.Down,
                    QTextCursor.MoveMode.KeepAnchor,
                    document.blockCount() - 800,
                )
                cursor.removeSelectedText()
            self.log_text.verticalScrollBar().setValue(self.log_text.verticalScrollBar().maximum())
            self.log_batch = []
            self.last_log_update = current_time

        self._log_timer.setInterval(50 if messages_processed else 200)


def main():
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    icon_path = app_icon_path()
    if icon_path:
        icon = QIcon(icon_path)
        app.setWindowIcon(icon)
    app.setStyle("Fusion")
    app.setStyleSheet(theme.STYLESHEET)
    window = MainWindow()
    if icon_path:
        window.setWindowIcon(QIcon(icon_path))
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
