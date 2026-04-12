# core/system_stats.py
from __future__ import annotations

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
import psutil
import time

from .logging import get_logger

try:
    import pynvml
    _HAS_NVML = True
except ImportError:
    pynvml = None
    _HAS_NVML = False


logger = get_logger(__name__)


class SystemStatsProvider(QObject):
    """
    Periodické systémové štatistiky pre SystemMonitorWidget.
    Posiela dict cez signal stats_updated:
      cpu_percent, cpu_ghz, cpu_cores, cpu_threads,
      ram_used_gb, ram_total_gb, ram_percent,
      gpu_percent, gpu_temp,
      fps,
      disk_mb_s, net_mb_s
    """
    stats_updated = pyqtSignal(dict)

    def __init__(self, parent: QObject | None = None, interval_ms: int = 500):
        """Configure timers and optional NVML for GPU stats."""
        super().__init__(parent)

        # timer
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._poll)

        self._last_cpu_call = 0.0

        self._last_disk = None
        self._last_net = None
        self._last_io_time = None

        # FPS môžeš dopĺňať zvonka (napr. zo seriálu)
        self._fps: float | None = None

        # GPU (NVML – NVIDIA only)
        self._gpu_supported = False
        self._gpu_handle = None

        if _HAS_NVML:
            try:
                pynvml.nvmlInit()
                self._gpu_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                self._gpu_supported = True
            except Exception:
                self._gpu_supported = False
                self._gpu_handle = None

        # pre výpočet MB/s – posledný sample
        self._last_sample_time: float | None = None
        self._last_disk: tuple[int, int] | None = None   # (read_bytes, write_bytes)
        self._last_net: tuple[int, int] | None = None    # (sent_bytes, recv_bytes)

    # ----- public API -----

    def set_fps(self, value: float | None) -> None:
        """Externý zdroj FPS (napr. zo seriálu)."""
        self._fps = value

    def start(self) -> None:
        """Start periodic polling timer."""
        self._timer.start()

    def stop(self) -> None:
        """Stop periodic polling timer."""
        self._timer.stop()
        
    def is_running(self) -> bool:
        """Vracia, či timer beží."""
        return self._timer.isActive()

    # ----- interné: hlavný poll -----
    def _poll(self) -> None:
        """Collect stats sample and emit via signal."""
        try:
            now = time.monotonic()

            # --- CPU ---
            cpu_percent = psutil.cpu_percent(interval=None)
            freq = psutil.cpu_freq()
            cpu_ghz = (freq.current / 1000.0) if freq else 0.0
            cpu_cores = psutil.cpu_count(logical=False) or 0
            cpu_threads = psutil.cpu_count(logical=True) or cpu_cores

            # --- RAM ---
            vm = psutil.virtual_memory()
            ram_used_gb = vm.used / (1024 ** 3)
            ram_total_gb = vm.total / (1024 ** 3)
            ram_percent = vm.percent

            # --- GPU (NVIDIA, ak je) ---
            gpu_percent = None
            gpu_temp = None
            if self._gpu_supported and self._gpu_handle is not None:
                try:
                    util = pynvml.nvmlDeviceGetUtilizationRates(self._gpu_handle)
                    gpu_percent = float(util.gpu)
                    temp = pynvml.nvmlDeviceGetTemperature(
                        self._gpu_handle,
                        pynvml.NVML_TEMPERATURE_GPU,
                    )
                    gpu_temp = float(temp)
                except Exception:
                    gpu_percent = None
                    gpu_temp = None

            # --- Disk a sieť – MB/s (jedna normálna implementácia) ---
            disk = psutil.disk_io_counters()
            net = psutil.net_io_counters()

            disk_mb_s = 0.0
            net_mb_s = 0.0

            if self._last_sample_time is not None and self._last_disk is not None and self._last_net is not None:
                dt = max(1e-3, now - self._last_sample_time)

                # disk delta
                d_read_prev, d_write_prev = self._last_disk
                d_read_cur = disk.read_bytes
                d_write_cur = disk.write_bytes
                d_delta_bytes = (d_read_cur - d_read_prev) + (d_write_cur - d_write_prev)
                disk_mb_s = (d_delta_bytes / dt) / (1024 ** 2)

                # net delta
                n_sent_prev, n_recv_prev = self._last_net
                n_sent_cur = net.bytes_sent
                n_recv_cur = net.bytes_recv
                n_delta_bytes = (n_sent_cur - n_sent_prev) + (n_recv_cur - n_recv_prev)
                net_mb_s = (n_delta_bytes / dt) / (1024 ** 2)

            # uložiť current sample ako „last“
            self._last_sample_time = now
            self._last_disk = (disk.read_bytes, disk.write_bytes)
            self._last_net = (net.bytes_sent, net.bytes_recv)

            # normalizácia pre ESP (ak niečo nemám, dám -1.0)
            gpu_pct_for_esp = float(gpu_percent) if gpu_percent is not None else -1.0
            fps_for_esp = float(self._fps) if self._fps is not None else -1.0

            # --- finálny balík dát ---
            data = {
                "cpu_percent": cpu_percent,
                "cpu_ghz": cpu_ghz,
                "cpu_cores": cpu_cores,
                "cpu_threads": cpu_threads,
                "ram_used_gb": ram_used_gb,
                "ram_total_gb": ram_total_gb,
                "ram_percent": ram_percent,
                "gpu_percent": gpu_percent,
                "gpu_temp": gpu_temp,
                "fps": self._fps,
                "disk_mb_s": disk_mb_s,
                "net_mb_s": net_mb_s,

                # pre ESP
                "gpu_pct_for_esp": gpu_pct_for_esp,
                "fps_for_esp": fps_for_esp,
            }


            self.stats_updated.emit(data)

        except Exception as e:
            logger.exception("SystemStatsProvider._poll error")
            return
