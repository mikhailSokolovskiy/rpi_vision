import time

import cv2
from PySide6.QtCore import QThread, Signal

import camera as camera_module
import config
from preprocessing import crop_roi


class CaptureThread(QThread):
    """Поток захвата — единственный читатель своей камеры.

    Раньше здесь был QTimer(self), созданный внутри run(): родитель
    жил в главном потоке -> "Cannot create children for a parent that
    is in a different thread", таймер не фаерился, картинки не было.
    Теперь обычный цикл без Qt-детей в рабочем потоке: читаем камеру
    с её собственной скоростью и только дросселируем выдачу сверху
    до CAPTURE_MAX_FPS (камере FPS не навязываем).

    Экземпляр на камеру: каждая панель получает свой Camera и свой
    поток, иначе вторая open() закрыла бы хендл первой.
    """

    camera_error = Signal()
    frame_captured = Signal(object)

    def __init__(self, camera_index: int = 0, preview_size=None, roi_getter=None, parent=None):
        super().__init__(parent)
        self._camera_index = camera_index
        self._preview_size = preview_size or (config.PREVIEW_WIDTH, config.PREVIEW_HEIGHT)
        self._get_roi = roi_getter if roi_getter is not None else lambda: None

    def run(self):
        cam = camera_module.Camera()
        if not cam.open(self._camera_index):
            self.camera_error.emit()
            return
        max_fps = float(getattr(config, "CAPTURE_MAX_FPS", 30))
        min_interval_ms = 1000.0 / max_fps if max_fps > 0 else 0.0
        while not self.isInterruptionRequested():
            started = time.perf_counter()
            try:
                frame = cam.read()
            except camera_module.CameraError:
                self.camera_error.emit()
                break
            try:
                preview = cv2.resize(
                    crop_roi(frame, self._get_roi()),
                    self._preview_size,
                    interpolation=cv2.INTER_AREA,
                )
            except Exception:
                continue
            self.frame_captured.emit(preview)
            # Только ограничение сверху: если камера быстрее max_fps —
            # досыпаем до минимального интервала, если медленнее —
            # сразу идём за следующим кадром.
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            sleep_ms = int(min_interval_ms - elapsed_ms)
            if sleep_ms >= 1:
                self.msleep(sleep_ms)
        cam.close()

    def stop(self):
        self.requestInterruption()
        self.quit()