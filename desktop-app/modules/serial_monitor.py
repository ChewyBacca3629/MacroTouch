# core/serial_monitor.py
from __future__ import annotations

import threading
import queue
from collections import deque
import serial
from serial.tools import list_ports
import time

from .logging import get_logger
from .serial_ports import pick_best_serial_port

class SerialMonitor:
    """Simple serial monitor with auto-detect and outgoing queue."""
    def __init__(self, app=None, status_callback=None):
        """Init state; call start() to spawn the reader thread."""
        self.app = app
        self._status_callback = status_callback
        self.logger = get_logger(__name__)
        self.serial_port: str | None = None
        self.ser: serial.Serial | None = None

        self.serial_thread: threading.Thread | None = None
        self.keep_reading_serial: bool = True
        self._open_error_count: int = 0

        self.serial_monitor_output = deque(maxlen=2000)
        self.serial_monitor_enabled: bool = True
        self.verbose: bool = False

        # Odosielacia fronta
        self._max_outgoing = 300
        self._outgoing: queue.Queue[str] = queue.Queue(maxsize=self._max_outgoing)
        self._stop_event = threading.Event()

    def _log(self, message: str, verbose_only: bool = False) -> None:
        if verbose_only and not self.verbose:
            return
        self.logger.info(message)

    def set_status_callback(self, callback) -> None:
        self._status_callback = callback

    def _notify_status(self, message: str, timeout_ms: int = 0) -> None:
        callback = self._status_callback
        if callable(callback):
            try:
                callback(message, timeout_ms)
                return
            except TypeError:
                try:
                    callback(message)
                    return
                except Exception:
                    self.logger.exception("[SerialMonitor] Status callback failed")
            except Exception:
                self.logger.exception("[SerialMonitor] Status callback failed")

        if self.app is not None and hasattr(self.app, "statusBar"):
            try:
                self.app.statusBar().showMessage(message, timeout_ms)
            except Exception:
                self.logger.exception("[SerialMonitor] statusBar().showMessage failed")

    # ----------------- detekcia portu -----------------
    def _port_present(self, port: str) -> bool:
        """Return True if the port still exists in OS list_ports."""
        try:
            return any(p.device == port for p in list_ports.comports())
        except Exception as exc:
            self.logger.exception("[SerialMonitor] Port presence check failed")
            return True

    def _port_alive(self) -> bool:
        """Return True if the current serial handle is still usable."""
        if not self.ser:
            return False
        try:
            _ = self.ser.in_waiting
            _ = self.ser.cts
            return True
        except Exception as exc:
            self.logger.exception("[SerialMonitor] Port liveness check failed")
            return False

    def detect_esp32_port(self) -> str | None:
        """Heuristically pick an ESP32 serial port from available COM ports."""
        self._log("[SerialMonitor] Hľadám ESP32 port...", verbose_only=True)
        ports = list(list_ports.comports())

        if not ports:
            self._log("[SerialMonitor] ŽIADNE COM PORTY – OS nič nevidí")
            return None

        for p in ports:
            if self.verbose:
                print("----------")
                print(f"device      = {p.device}")
                print(f"description = {p.description}")
                print(f"manufacturer= {p.manufacturer}")
                print(f"hwid        = {p.hwid}")
                print(f"vid         = {hex(p.vid) if p.vid is not None else None}")
                print(f"pid         = {hex(p.pid) if p.pid is not None else None}")
        best = pick_best_serial_port(ports, current_port=self.serial_port)
        if best:
            self._log(f"[SerialMonitor] Vybral som: {best}")
            return best
        return None

    def _detect_esp32_port_quiet(self) -> str | None:
        """Silent ESP32 port detection (no console spam)."""
        return pick_best_serial_port(list_ports.comports(), current_port=self.serial_port)


    # ----------------- verejné API -----------------
    def start(self):
        """Detect port and start serial reader thread."""
        self.serial_port = self.detect_esp32_port()
        self._log(f"[SerialMonitor] Pouzity port: {self.serial_port}")
        if self.serial_port:
            self._stop_event.clear()
            self._start_serial_thread()
        else:
            self._log("[SerialMonitor] NO PORT - serial thread not started")

    def stop(self):
        """Stop serial reader thread if running."""
        self._stop_event.set()
        self._stop_serial_thread()

    def refresh_port(self):
        """Re-detect port and restart serial thread if needed."""
        new_port = self._detect_esp32_port_quiet()

        if not new_port:
            if self.serial_port:
                self._notify_status("ESP32 disconnected")
                self._stop_serial_thread()
                self.serial_port = None
            return

        if new_port != self.serial_port:
            self.serial_port = new_port
            self._notify_status(f"ESP32 na {self.serial_port}")
            self._stop_serial_thread()
            self._start_serial_thread()
            return

        if not self.ser or not self.ser.is_open or not self._port_alive():
            self._stop_serial_thread()
            self._start_serial_thread()

    def send_line(self, line: str):
        """Pridá správu do fronty na odoslanie - JEDINÝ SPÔSOB ODOSIELANIA"""
        try:
            self._outgoing.put_nowait(line)
        except queue.Full:
            try:
                dropped = self._outgoing.get_nowait()
                self._log(f"[SerialMonitor] FRONT PLNA, dropujem najstaršie: {dropped}")
            except queue.Empty:
                pass
            try:
                self._outgoing.put_nowait(line)
            except queue.Full:
                pass
        self._log(f"[SerialMonitor] Správa pridaná do fronty: {line}", verbose_only=True)

    def write_line(self, text: str):
        """Alias pre send_line (zpätne kompatibilné)."""
        self.send_line(text)

    # ----------------- interné veci (thread) -----------------
    def _start_serial_thread(self):
        """Spustí reader thread, ak ešte nebeží a máme port."""
        if self.serial_thread and self.serial_thread.is_alive():
            return
        if not self.serial_port:
            return

        self.keep_reading_serial = True
        self._stop_event.clear()
        self.serial_thread = threading.Thread(
            target=self._read_serial_output,
            daemon=True,
        )
        self.serial_thread.start()

    def _stop_serial_thread(self):
        """Bezpečne zastaví reader thread a zavrie port."""
        try:
            self.keep_reading_serial = False
            self._stop_event.set()
            if self.ser and self.ser.is_open:
                try:
                    self.ser.close()
                except Exception:
                    pass
            if self.serial_thread and self.serial_thread.is_alive():
                self.serial_thread.join(timeout=1.0)
        except Exception:
            pass
        finally:
            self.ser = None
            self.serial_thread = None

    def _read_serial_output(self):
        """Hlavná slučka - čítanie a obmedzené odosielanie"""
        import time  # PRIDAŤ AK CHÝBA
        opened_port: str | None = None
        
        try:
            if not self.serial_port:
                return
            self.ser = serial.Serial(
                port=self.serial_port,
                baudrate=115200,
                timeout=0.1,
                write_timeout=1.0,
            )
            self._open_error_count = 0
            # ESP32 auto-boot býva naviazané na DTR/RTS – vypneme ich, nech držíme normálny beh
            try:
                self.ser.dtr = False
                self.ser.rts = False
                time.sleep(0.05)
            except Exception:
                self.logger.exception("[SerialMonitor] DTR/RTS init failed")
            self._log(f"[SerialMonitor] Port otvorený: {self.serial_port}")
            opened_port = self.serial_port
            if hasattr(self.app, "serialConnected"):
                try:
                    self.app.serialConnected.emit(self.serial_port)
                except Exception:
                    self.logger.exception("[SerialMonitor] serialConnected emit failed")
            self._log(f"[SerialMonitor] Seriový objekt: {self.ser}", verbose_only=True)
        except Exception as e:
            self._open_error_count += 1
            self._log(f"Chyba pri otváraní portu {self.serial_port}: {e}")
            # Pri opakovaných chybách vrátime None na detekciu iného portu
            if self._open_error_count >= 3:
                self._log(f"[SerialMonitor] too many open errors, resetting serial_port")
                self.serial_port = None
            self.ser = None
            return

        buffer = ""
        failed_attempts = 0
        MAX_FAILED_ATTEMPTS = 3

        while self.keep_reading_serial and not self._stop_event.is_set() and self.ser and self.ser.is_open:
            try:
                # --- ČÍTANIE ---
                data = self.ser.read_until(b"\n")
                if data:
                    buffer += data.decode(errors="ignore")
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if line:
                            self._log(f"[SerialMonitor] Prijaté: {line}", verbose_only=True)
                            if hasattr(self.app, "serialLine"):
                                self.app.serialLine.emit(line)

                # --- OBMEDZENÉ ODOSIELANIE ---
                try:
                    msg = self._outgoing.get_nowait()
                except queue.Empty:
                    msg = None

                if msg is not None:
                    try:
                        self._log(f"[SerialMonitor] Pokúšam sa odoslať: {msg}", verbose_only=True)
                        encoded_msg = (msg + "\n").encode("utf-8")

                        written = self.ser.write(encoded_msg)
                        self.ser.flush()

                        self._log(f"[SerialMonitor] Úspešne odoslané {written} bajtov: {msg}", verbose_only=True)
                        failed_attempts = 0  # Resetovať počítadlo chýb

                    except serial.SerialTimeoutException:
                        failed_attempts += 1
                        self._log(f"[SerialMonitor] TIMEOUT #{failed_attempts} pri: {msg}")

                        if failed_attempts >= MAX_FAILED_ATTEMPTS:
                            self._log("[SerialMonitor] PRÍLIŠ VEĽA CHÝB - VYPÚŠŤAM FRONTU")
                            while True:
                                try:
                                    self._outgoing.get_nowait()
                                except queue.Empty:
                                    break
                            failed_attempts = 0
                            time.sleep(2)  # Počkať pred obnovením
                        else:
                            try:
                                self._outgoing.put_nowait(msg)
                            except queue.Full:
                                pass
                            time.sleep(0.5)  # Krátky spánok

                    except Exception as e:
                        self._log(f"[SerialMonitor] FATÁLNA CHYBA: {e}")
                        break

            except Exception as e:
                self._log(f"Chyba v seriovom threade: {e}")
                break

        # Cleanup
        try:
            if self.ser and self.ser.is_open:
                self.ser.close()
        except Exception:
            pass
        finally:
            self.ser = None
            if opened_port and hasattr(self.app, "serialDisconnected"):
                try:
                    self.app.serialDisconnected.emit(opened_port)
                except Exception:
                    self.logger.exception("[SerialMonitor] serialDisconnected emit failed")
            self._log("[SerialMonitor] Thread ukončený.")
