from __future__ import annotations

import sys
from pathlib import Path

# Resolve the persistent application directory before importing Qt.  In a
# packaged build this is the directory that contains FastFinder.exe.
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

from utils.startup_profiler import StartupProfiler

profiler = StartupProfiler(BASE_DIR / "logs" / "startup.log")

# Qt import cost is now visible in startup.log instead of being hidden before
# the profiler started.
from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

profiler.mark("PySide6 core imports")

DB_PATH = BASE_DIR / "data" / "fastfinder.db"
SETTINGS_PATH = BASE_DIR / "config" / "settings.json"


def main():
    from utils.app_info import APP_NAME, APP_USER_MODEL_ID, APP_VERSION, ORGANIZATION_NAME
    from utils.windows_integration import apply_native_window_icon, configure_windows_app_identity

    configure_windows_app_identity(APP_USER_MODEL_ID)
    profiler.mark("Windows app identity")

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(ORGANIZATION_NAME)
    app.setQuitOnLastWindowClosed(False)

    # Do NOT install a global Qt stylesheet on the critical startup path.
    # Applying QSS before creating dozens of widgets makes every widget pay the
    # style-polish cost during construction.  On some Windows machines this was
    # responsible for several seconds of MainWindow._build_ui().
    app.setFont(QFont("Microsoft YaHei UI", 10))
    profiler.mark("QApplication + startup font")

    # Single-instance support is required before creating the main window, but
    # importing QtNetwork no longer happens before startup profiling begins.
    from core.single_instance import SingleInstanceController

    single_instance = SingleInstanceController("SolveTrue.FastFinder", app)
    if not single_instance.acquire_or_notify("SHOW"):
        profiler.mark("secondary instance forwarded")
        profiler.flush()
        return 0
    app.aboutToQuit.connect(single_instance.shutdown)
    profiler.mark("single-instance guard")

    from utils.resource_path import resource_path

    icon_path = resource_path("resources", "fastfinder.ico")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    from database.database import Database

    database = Database(DB_PATH)
    database.initialize()
    profiler.mark("SQLite fast initialize")

    from utils.settings import SettingsStore

    settings_store = SettingsStore(SETTINGS_PATH)

    # Import the main window only now. app.main_window itself uses lazy imports
    # for history/favorites/settings/quick-search/watchdog/index scheduling.
    from app.main_window import MainWindow

    profiler.mark("MainWindow module imported")
    window = MainWindow(database, settings_store, startup_profiler=profiler)
    profiler.mark("MainWindow constructed")

    if icon_path.exists():
        apply_native_window_icon(window, icon_path)

    def handle_instance_message(message: str):
        if message.strip().upper() == "SHOW":
            window.restore_from_tray()

    single_instance.message_received.connect(handle_instance_message)

    if (
        window.settings.get("start_hidden_to_tray", False)
        and QSystemTrayIcon.isSystemTrayAvailable()
    ):
        window.hide()
    else:
        window.show()
        window.focus_search()

    # Everything expensive after this point is deferred by MainWindow timers.
    profiler.mark("window shown / startup handoff")
    profiler.flush()

    # Apply the visual theme only AFTER the first paint.  This keeps the window
    # responsive/visible as early as possible while preserving the FastFinder UI.
    # The optimized stylesheet intentionally avoids a global QWidget selector.
    def apply_deferred_theme():
        try:
            from app.style import APP_STYLESHEET
            app.setStyleSheet(APP_STYLESHEET)
            window._update_responsive_ui()
        except Exception:
            pass

    QTimer.singleShot(450, apply_deferred_theme)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
