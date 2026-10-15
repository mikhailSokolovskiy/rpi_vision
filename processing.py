from collections import deque

from PySide6.QtCore import QObject, Signal, Slot

import config
from controller import REFERENCE_NOT_SET, Controller

STATUS_NORMAL = "NORMAL"
STATUS_ALARM = "ALARM"
STATUS_NO_REFERENCE = "NO_REFERENCE"


class Processor(QObject):
    """Живёт в отдельном QThread (moveToThread). Все слоты исполняются
    в его потоке через queued-соединения — GUI не блокируется.

    Экземпляр на камеру: свой Controller (свой эталон) и свой поток.

    Единственный источник кадров — CaptureThread через on_frame().
    Прямых чтений камеры отсюда нет: иначе два потока дерутся за
    один VideoCapture/Picamera2 и превью фризится.
    """

    # frame, status, elapsed_ms, has_reference, change_ratio, boxes
    result_ready = Signal(object, str, float, bool, float, list)
    # changed, change_ratio, elapsed_ms, boxes (ручной внешний сигнал, ТЗ 7)
    detection_done = Signal(bool, float, float, list)

    def __init__(self, controller: Controller, parent=None):
        super().__init__(parent)
        self._controller = controller
        self._reference_frames = None
        maxlen = max(
            config.REFERENCE_FRAMES, config.CONFIRMATION_FRAMES * 2, 5
        )
        self._recent: deque = deque(maxlen=maxlen)

    @Slot()
    def on_set_reference(self):
        self._reference_frames = []

    @Slot()
    def on_detect(self):
        # Берём свежие кадры из кольцевого буфера превью, камеру не трогаем.
        frames = list(self._recent)[-config.CONFIRMATION_FRAMES :]
        if not frames:
            self.detection_done.emit(False, 0.0, 0.0, [])
            return
        result = self._controller.detect_change(frames=frames)
        if result.get("error"):
            self.detection_done.emit(False, 0.0, 0.0, [])
            return
        self.detection_done.emit(
            result["changed"],
            result["change_ratio"],
            result["processing_time_ms"],
            result.get("boxes", []),
        )

    @Slot(object)
    def on_frame(self, frame):
        self._recent.append(frame)
        if self._reference_frames is not None:
            self._reference_frames.append(frame)
            if len(self._reference_frames) >= config.REFERENCE_FRAMES:
                self._controller.set_reference(self._reference_frames)
                self._reference_frames = None
            else:
                # Эталон ещё копится — превью не замораживаем.
                self.result_ready.emit(frame, STATUS_NO_REFERENCE, 0.0, False, 0.0, [])
                return

        result = self._controller.check_frame(frame)
        if result.get("error") == REFERENCE_NOT_SET:
            self.result_ready.emit(frame, STATUS_NO_REFERENCE, 0.0, False, 0.0, [])
            return

        status = STATUS_ALARM if result["confirmed"] else STATUS_NORMAL
        self.result_ready.emit(
            frame,
            status,
            result["processing_time_ms"],
            True,
            result["change_ratio"],
            result.get("boxes", []),
        )
