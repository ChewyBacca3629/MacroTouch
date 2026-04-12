"""High-level serial service wrapper for MacroTouch."""
from .serial_monitor import SerialMonitor
from .logging import get_logger


class SerialService:
    """Abstraction around SerialMonitor with broader UI-independent API."""

    def __init__(self, app=None, status_callback=None):
        self.logger = get_logger(__name__)
        try:
            self.monitor = SerialMonitor(app=app, status_callback=status_callback)
        except TypeError:
            self.monitor = SerialMonitor(app=app)
            if status_callback is not None and hasattr(self.monitor, "set_status_callback"):
                self.monitor.set_status_callback(status_callback)

    def start(self):
        self.logger.debug("Starting serial monitor")
        self.monitor.start()

    def stop(self):
        self.logger.debug("Stopping serial monitor")
        self.monitor.stop()

    def refresh_port(self):
        self.logger.debug("Refreshing serial port")
        self.monitor.refresh_port()

    def send_line(self, line: str):
        self.monitor.send_line(line)

    def write_line(self, text: str):
        self.monitor.write_line(text)

    def detect_esp32_port(self):
        return self.monitor.detect_esp32_port()

    @property
    def serial_port(self):
        return self.monitor.serial_port

    @property
    def ser(self):
        return self.monitor.ser

    @property
    def is_connected(self) -> bool:
        return bool(self.ser and self.ser.is_open)

    def set_app(self, app):
        self.monitor.app = app

    def set_status_callback(self, callback) -> None:
        self.monitor.set_status_callback(callback)
