# core/media_controls.py
import time
from ctypes import cast, POINTER

try:
    import pythoncom  # type: ignore
except Exception as e:
    pythoncom = None
    _PYTHONCOM_ERROR = e

try:
    import wmi  # type: ignore
    _HAS_WMI = True
except Exception as e:
    wmi = None
    _HAS_WMI = False
    _WMI_ERROR = e

try:
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume  # type: ignore
    _HAS_PYCAW = True
except Exception as e:
    AudioUtilities = None
    IAudioEndpointVolume = None
    _HAS_PYCAW = False
    _PYCAW_ERROR = e

try:
    import win32api  # type: ignore
    import win32con  # type: ignore
    import win32gui  # type: ignore
    _HAS_WIN32 = True
except Exception as e:
    win32api = None
    win32con = None
    win32gui = None
    _HAS_WIN32 = False
    _WIN32_ERROR = e


VK = {
    "PLAY_PAUSE": 0xB3,
    "NEXT":       0xB0,
    "PREV":       0xB1,
    "VOL_UP":     0xAF,
    "VOL_DOWN":   0xAE,
    "MUTE":       0xAD,
}


def _get_foreground_hwnd() -> int:
    """Vráti handle aktuálne aktívneho okna (alebo 0 ak nič)."""
    if not win32gui:
        print("GetForegroundWindow error: win32gui missing")
        return 0
    try:
        return win32gui.GetForegroundWindow()
    except Exception as e:
        print(f"GetForegroundWindow error: {e}")
        return 0


def minimize_active_window():
    """Minimalizuje aktuálne aktívne okno spoľahlivo cez ShowWindow."""
    if not _HAS_WIN32:
        print("MinimizeWindow error: pywin32 missing")
        return
    try:
        hwnd = _get_foreground_hwnd()
        if hwnd:
            win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
        else:
            print("MinimizeWindow: žiadne aktívne okno")
    except Exception as e:
        print(f"MinimizeWindow error: {e}")


def toggle_maximize_active_window():
    """
    Maximalizuje / obnoví aktívne okno:
    - ak je normálne -> maximalizuj
    - ak je už maximalizované -> obnov (toggle)
    """
    if not _HAS_WIN32:
        print("MaximizeWindow error: pywin32 missing")
        return
    try:
        hwnd = _get_foreground_hwnd()
        if not hwnd:
            print("MaximizeWindow: žiadne aktívne okno")
            return

        if win32gui.IsZoomed(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        else:
            win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
    except Exception as e:
        print(f"MaximizeWindow error: {e}")


class WindowsMediaController:
    """Windows-specific helper for media keys, volume, and brightness."""
    def __init__(self):
        """Initialize COM audio and brightness controllers."""
        self._init_audio()
        self._init_brightness()
        self._soft_vol = 0.5  # mäkký odhad volume, používame keď nie je pycaw

        try:
            if getattr(self, "volume_interface", None):
                self._soft_vol = float(self.volume_interface.GetMasterVolumeLevelScalar())
        except Exception:
            pass

        self._last_osd_ms = 0
        self._osd_min_interval_ms = 250  # aspoň 250 ms medzi OSD ťuknutiami

    # --- interné pomocné metódy ---

    def _show_volume_osd(self):
        """Trigger system volume OSD and restore previous level."""
        if not _HAS_WIN32:
            return
        try:
            now_ms = int(time.monotonic() * 1000)
            if now_ms - getattr(self, "_last_osd_ms", 0) < getattr(self, "_osd_min_interval_ms", 250):
                return
            self._last_osd_ms = now_ms

            cur = None
            if self.volume_interface:
                try:
                    cur = float(self.volume_interface.GetMasterVolumeLevelScalar())
                except Exception:
                    cur = None

            win32api.keybd_event(VK["VOL_UP"],   0, 0, 0)
            win32api.keybd_event(VK["VOL_UP"],   0, win32con.KEYEVENTF_KEYUP, 0)
            win32api.keybd_event(VK["VOL_DOWN"], 0, 0, 0)
            win32api.keybd_event(VK["VOL_DOWN"], 0, win32con.KEYEVENTF_KEYUP, 0)

            if cur is not None:
                time.sleep(0.02)
                try:
                    self.volume_interface.SetMasterVolumeLevelScalar(cur, None)
                    self._soft_vol = cur
                except Exception:
                    pass
        except Exception as e:
            print(f"OSD hint error: {e}")

    def _init_audio(self):
        """Inicializuje pycaw audio endpoint alebo nastaví fallback."""
        if pythoncom is not None:
            try:
                pythoncom.CoInitialize()
            except Exception:
                pass

        if not _HAS_PYCAW:
            print("Audio init skipped: pycaw missing")
            self.volume_interface = None
            return

        try:
            from comtypes import CLSCTX_ALL  # type: ignore
            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            self.volume_interface = cast(interface, POINTER(IAudioEndpointVolume))
            print("Audio controller initialized (pycaw)")
        except Exception as e:
            print(f"Audio init (pycaw) failed: {e} -> fallback WM_APPCOMMAND")
            self.volume_interface = None

    def _init_brightness(self):
        """Inicializuje WMI prístup k jasu displeja."""
        self.brightness_initialized = False
        if not _HAS_WMI:
            print("Brightness init skipped: wmi missing")
            return
        if pythoncom is not None:
            try:
                pythoncom.CoInitialize()
            except Exception:
                pass
        try:
            self.wmi_conn = wmi.WMI(namespace='wmi')
            self.brightness_initialized = True
            print("Brightness controller initialized successfully")
        except Exception as e:
            print(f"Brightness init error: {e}")

    # --- public API: media keys & volume ---

    def send_media_vk(self, vk_code: int):
        """Odošle mediálnu klávesu (play/pause/next/prev)."""
        if not _HAS_WIN32:
            print("send_media_vk skipped: pywin32 missing")
            return
        win32api.keybd_event(vk_code, 0, 0, 0)
        win32api.keybd_event(vk_code, 0, win32con.KEYEVENTF_KEYUP, 0)

    def play_pause(self) -> None:
        """Toggle play/pause using media key."""
        self.send_media_vk(VK["PLAY_PAUSE"])

    def next(self) -> None:
        """Skip to next track using media key."""
        self.send_media_vk(VK["NEXT"])

    def previous(self) -> None:
        """Skip to previous track using media key."""
        self.send_media_vk(VK["PREV"])

    def change_volume(self, delta_percent: float) -> bool:
        """Zmení hlasitosť o delta (0-1). Vracia True, ak obslúžené."""
        delta = float(delta_percent)

        if self.volume_interface:
            try:
                try:
                    if delta > 0 and self.volume_interface.GetMute():
                        self.volume_interface.SetMute(0, None)
                except Exception:
                    pass

                try:
                    current = float(self.volume_interface.GetMasterVolumeLevelScalar())
                except Exception:
                    current = float(self._soft_vol)

                target = max(0.0, min(1.0, current + delta))
                MIN_EFF = 0.015

                if abs(target - current) < MIN_EFF:
                    if delta > 0:
                        self.volume_interface.VolumeStepUp(None)
                    else:
                        self.volume_interface.VolumeStepDown(None)
                    new_vol = float(self.volume_interface.GetMasterVolumeLevelScalar())
                else:
                    self.volume_interface.SetMasterVolumeLevelScalar(target, None)
                    new_vol = float(self.volume_interface.GetMasterVolumeLevelScalar())
                    if (delta > 0 and new_vol <= current) or (delta < 0 and new_vol >= current):
                        if delta > 0:
                            self.volume_interface.VolumeStepUp(None)
                        else:
                            self.volume_interface.VolumeStepDown(None)
                        new_vol = float(self.volume_interface.GetMasterVolumeLevelScalar())

                self._soft_vol = new_vol
                print(f"Volume: {current:.2f} -> {new_vol:.2f} ({delta:+.3f})")
                self._show_volume_osd()
                return True
            except Exception as e:
                print(f"Volume change error: {e}")

        self._show_volume_osd()

        step_unit = 0.02
        max_steps_per_call = 5
        delta = max(-0.10, min(0.10, delta))
        steps = int(round(abs(delta) / step_unit))
        if steps == 0 and abs(delta) > 0:
            steps = 1
        steps = min(steps, max_steps_per_call)

        vk = VK["VOL_UP"] if delta > 0 else VK["VOL_DOWN"]
        for _ in range(steps):
            self.send_media_vk(vk)

        signed_step = step_unit * (1 if delta > 0 else -1)
        self._soft_vol = max(0.0, min(1.0, self._soft_vol + signed_step * steps))
        print(f"(Fallback) Volume ~= {self._soft_vol:.2f} (steps {steps}, dir {'+' if delta>0 else '-'})")
        return True

    def set_volume(self, level: float) -> bool:
        """Nastaví absolútnu hlasitosť (0-1)."""
        level = max(0.0, min(1.0, float(level)))
        if self.volume_interface:
            try:
                self.volume_interface.SetMasterVolumeLevelScalar(level, None)
                self._soft_vol = level
                self._show_volume_osd()
                return True
            except Exception as e:
                print(f"Volume set error: {e}")

        diff = level - self._soft_vol
        if abs(diff) < 0.01:
            self._soft_vol = level
            self._show_volume_osd()
            return True

        step_towards = max(-0.10, min(0.10, diff))
        return self.change_volume(step_towards)

    def get_volume(self) -> float:
        """Vráti aktuálnu hlasitosť (0-1) alebo soft cache."""
        if self.volume_interface:
            try:
                v = float(self.volume_interface.GetMasterVolumeLevelScalar())
                self._soft_vol = v
                return v
            except Exception:
                pass
        return float(self._soft_vol)

    def toggle_mute(self) -> bool:
        """Prepne mute; ak COM zlyhá, pošle mediálnu klávesu MUTE."""
        if self.volume_interface:
            try:
                is_muted = self.volume_interface.GetMute()
                self.volume_interface.SetMute(0 if is_muted else 1, None)
                return True
            except Exception as e:
                print(f"Mute error: {e}")
        self.send_media_vk(VK["MUTE"])
        return True

    # --- brightness ---

    def set_brightness(self, level: int) -> bool:
        """Nastaví jas cez WMI, ak je dostupný."""
        if not self.brightness_initialized:
            return False
        try:
            level = max(0, min(100, level))
            methods = self.wmi_conn.WmiMonitorBrightnessMethods()
            if methods:
                methods[0].WmiSetBrightness(level, 0)
                print(f"Brightness set to: {level}%")
                return True
        except Exception as e:
            print(f"Brightness error: {e}")
        return False

    def change_brightness(self, delta_percent: int) -> bool:
        """Relatívne upraví jas cez WMI, ak je dostupný."""
        if not self.brightness_initialized:
            return False
        try:
            brightness = self.wmi_conn.WmiMonitorBrightness()[0]
            current = brightness.CurrentBrightness
            new_brightness = max(0, min(100, current + delta_percent))
            methods = self.wmi_conn.WmiMonitorBrightnessMethods()
            if methods:
                methods[0].WmiSetBrightness(new_brightness, 0)
                print(f"Brightness: {current}% -> {new_brightness}% ({delta_percent:+d})")
                return True
        except Exception as e:
            print(f"Brightness change error: {e}")
        return False

    def cleanup(self):
        """Ukončí WMI/COM zdroje, ak existujú."""
        try:
            if hasattr(self, 'wmi_conn'):
                if pythoncom is not None:
                    pythoncom.CoUninitialize()
        except Exception:
            pass
