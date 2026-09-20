import sys

if "--host-simulator" in sys.argv:
    from crsdk_simulator import main as simulator_main

    simulator_main()
    raise SystemExit(0)

import atexit
import queue
import threading
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QTextCursor
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

from busy import BusyOverlay, LaunchSplash
from logger import add_queue_handler, get_logger
from platform_support import (
    APP_NAME,
    APP_ID,
    WINDOWS_APP_ID,
    app_icon_path,
    app_icon_pixmap,
    kill_stale_camera_hosts,
)
from status_events import is_status_event
import theme

logger = get_logger("main")


class MainWindow(QMainWindow):
    ready = Signal()
    _events_ready = Signal(object, object)

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1200, 860)
        self.setMinimumSize(1000, 720)

        self.log_queue = queue.Queue()
        self.log_batch = []
        self.last_log_update = 0
        self._log_expanded = False
        self._booted = False
        self._ending = False
        self._quit_done = False

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
        self._overlay = BusyOverlay(central)

        add_queue_handler(logger, self.log_queue)
        self._events_ready.connect(self._on_events_ready)
        self._log_timer = QTimer(self)
        self._log_timer.timeout.connect(self.poll_log_queue)
        self._log_timer.start(200)

    def begin_boot(self):
        threading.Thread(target=self._boot_worker, daemon=True).start()

    def closeEvent(self, event):
        self._quit_clean()
        event.accept()

    def _quit_clean(self):
        """Kill the camera host immediately so a leftover PTP session cannot linger."""
        if self._quit_done:
            return
        self._quit_done = True
        if self.workspace:
            try:
                self.workspace.shutdown()
            except Exception:
                pass
        if self.intake:
            try:
                self.intake.shutdown(wait=False)
            except Exception:
                pass
            self.intake = None
        if self.host:
            try:
                self.host.close(force=True)
            except Exception:
                pass
            self.host = None
        if self.wizard:
            try:
                self.wizard.destroy_host()
            except Exception:
                pass
            self.wizard.host = None
        kill_stale_camera_hosts()

    def _build_header(self, root):
        header = QWidget()
        header.setObjectName("headerBar")
        header.setFixedHeight(52)
        self.app_header = header
        layout = QHBoxLayout(header)
        layout.setContentsMargins(16, 0, 16, 0)
        pixmap = app_icon_pixmap(28)
        if not pixmap.isNull():
            mark = QLabel()
            mark.setFixedSize(28, 28)
            mark.setPixmap(pixmap)
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
            self.wizard.shutdown()
            self.stack.removeWidget(self.wizard)
            self.wizard.deleteLater()
            self.wizard = None
        if self.workspace:
            self.workspace.shutdown()
            self.stack.removeWidget(self.workspace)
            self.workspace.deleteLater()
            self.workspace = None

    def _fetch_events(self):
        from concurrent.futures import ThreadPoolExecutor
        from db import get_client_logos, get_events
        from events_model import parse_events

        with ThreadPoolExecutor(max_workers=2) as pool:
            events_future = pool.submit(get_events)
            logos_future = pool.submit(get_client_logos)
            events = parse_events(events_future.result(), logos_future.result())
        self.log_queue.put(f"Loaded {sum(len(e.collections) for e in events)} collections.")
        return events

    def _boot_worker(self):
        try:
            self._events_ready.emit(self._fetch_events(), None)
        except Exception as exc:
            self._events_ready.emit([], exc)

    def _on_events_ready(self, events, error):
        try:
            self._present_wizard(events)
        except Exception:
            logger.exception("Failed to open setup")
        self._overlay.hide_busy()
        self._ending = False
        if not self._booted:
            self._booted = True
            self.ready.emit()
        if error or not events:
            QMessageBox.warning(
                self,
                "No events",
                "Could not load events from the database. Check MongoDB and refresh by restarting setup.",
            )

    def _present_wizard(self, events):
        from wizard import SetupWizard

        self._clear_stage()
        self.app_header.show()
        self.events = events or []
        self.wizard = SetupWizard(events=self.events, log_queue=self.log_queue)
        self.wizard.finished.connect(self._start_session)
        self.stack.addWidget(self.wizard)
        self.stack.setCurrentWidget(self.wizard)

    def _start_session(self, result):
        QTimer.singleShot(10, lambda r=result: self._enter_session(r))

    def _enter_session(self, result):
        from shooting_view import ShootingWorkspace
        from tether_intake import TetherIntake
        from tether_session import write_session

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
        if self._ending:
            return
        self._ending = True
        self._overlay.show_busy(
            "Ending session…",
            "Disconnecting the camera and returning to setup. This will take a few seconds.",
        )
        if self.workspace:
            self.workspace.shutdown()
            self.workspace.setEnabled(False)
        host = self.host
        intake = self.intake
        self.host = None
        self.intake = None
        if self.wizard:
            self.wizard.host = None
        threading.Thread(target=self._end_session_worker, args=(host, intake), daemon=True).start()

    def _end_session_worker(self, host, intake):
        if intake:
            try:
                intake.shutdown(wait=False)
            except Exception:
                pass
        if host:
            try:
                host.disconnect()
                host.close()
            except Exception:
                pass
        try:
            self._events_ready.emit(self._fetch_events(), None)
        except Exception as exc:
            self._events_ready.emit([], exc)

    def _teardown_session(self, wait_uploads=False):
        if self.intake:
            self.intake.shutdown(wait=wait_uploads)
            self.intake = None
        if self.host:
            try:
                self.host.close(force=not wait_uploads)
            except Exception:
                pass
            self.host = None
        if self.wizard:
            self.wizard.host = None
        if not wait_uploads:
            kill_stale_camera_hosts()

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

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_ID)

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName("Lumetry")
    app.setDesktopFileName(APP_ID)
    icon_path = app_icon_path()
    if icon_path:
        icon = QIcon(icon_path)
        app.setWindowIcon(icon)
    app.setStyle("Fusion")
    app.setStyleSheet(theme.STYLESHEET)

    splash = LaunchSplash()
    if icon_path:
        splash.setWindowIcon(app.windowIcon())
    splash.show()
    app.processEvents()

    window = MainWindow()
    if icon_path:
        window.setWindowIcon(app.windowIcon())
    window.ready.connect(lambda: _reveal_window(window, splash))
    app.aboutToQuit.connect(window._quit_clean)
    atexit.register(kill_stale_camera_hosts)
    window.begin_boot()
    sys.exit(app.exec())


def _reveal_window(window, splash):
    window.show()
    window.raise_()
    window.activateWindow()
    splash.close()


if __name__ == "__main__":
    main()
