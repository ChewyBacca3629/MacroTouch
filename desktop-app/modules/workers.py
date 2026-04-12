from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal


class UploaderWorker(QObject):
    finished: pyqtSignal = pyqtSignal()
    error: pyqtSignal = pyqtSignal(str)
    progress: pyqtSignal = pyqtSignal(str)

    def __init__(self, sketch_dir: str, upload_func):
        super().__init__()
        self.sketch_dir = sketch_dir
        self.upload_func = upload_func

    def run(self) -> None:
        try:
            self.upload_func(self.sketch_dir, self.progress.emit)
            self.finished.emit()
        except Exception as exc:
            self.error.emit(str(exc))


class TaskWorker(QObject):
    finished: pyqtSignal = pyqtSignal()
    error: pyqtSignal = pyqtSignal(str)
    progress: pyqtSignal = pyqtSignal(str)

    def __init__(self, func, *args, **kwargs):
        super().__init__()
        self._func = func
        self._args = args
        self._kwargs = kwargs

    def run(self) -> None:
        try:
            self._func(*self._args, **self._kwargs)
            self.finished.emit()
        except Exception as exc:
            self.error.emit(str(exc))
