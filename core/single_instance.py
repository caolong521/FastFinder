from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


class SingleInstanceController(QObject):
    """Keep a single FastFinder process and wake the existing one on relaunch."""

    message_received = Signal(str)

    def __init__(self, server_name: str = "SolveTrue.FastFinder", parent=None):
        super().__init__(parent)
        self.server_name = server_name
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._on_new_connection)
        self._is_primary = False

    def acquire_or_notify(self, message: str = "SHOW") -> bool:
        # First try the existing instance.
        if self._send_to_existing(message):
            return False

        # If a process crashed, Windows may leave a stale local-server name.
        QLocalServer.removeServer(self.server_name)
        if self.server.listen(self.server_name):
            self._is_primary = True
            return True

        # Handle a race where another process became primary after our probe.
        if self._send_to_existing(message):
            return False

        # Fail open only if IPC itself is unavailable. This is safer than
        # preventing the app from starting because of a broken local socket.
        return True

    def _send_to_existing(self, message: str) -> bool:
        socket = QLocalSocket()
        socket.connectToServer(self.server_name)
        if not socket.waitForConnected(160):
            socket.abort()
            return False
        try:
            socket.write(message.encode("utf-8"))
            socket.flush()
            socket.waitForBytesWritten(160)
        finally:
            socket.disconnectFromServer()
        return True

    def _on_new_connection(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            if socket is None:
                continue

            def read_message(s=socket):
                try:
                    text = bytes(s.readAll()).decode("utf-8", errors="replace").strip()
                    if text:
                        self.message_received.emit(text)
                finally:
                    s.disconnectFromServer()
                    s.deleteLater()

            socket.readyRead.connect(read_message)
            if socket.bytesAvailable():
                read_message()

    def shutdown(self):
        if self._is_primary:
            self.server.close()
            QLocalServer.removeServer(self.server_name)
            self._is_primary = False
