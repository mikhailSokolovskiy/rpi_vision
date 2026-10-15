import logging
import time
from pathlib import Path

import cv2

import camera as camera_module
import config
import detector
import preprocessing
import reference as reference_module

log = logging.getLogger(__name__)

REFERENCE_NOT_SET = "REFERENCE_NOT_SET"
CAMERA_ERROR = "CAMERA_ERROR"


class Controller:
    """Состояние одной камеры: эталон и счётчик подтверждений.

    Экземпляр на камеру — иначе камеры делили бы один эталон (в модуле
    раньше жили глобальные _store/_consecutive_hits, для второй камеры
    это значило бы постоянные ложные тревоги).

    get_roi — функция без аргументов, возвращающая область интереса
    камеры (доли кадра) или None. Нужна только для прямого захвата с
    камеры мимо CaptureThread: там кропим сами, иначе эталон окажется
    в другой геометрии, чем приходящие кадры.
    """

    def __init__(self, get_roi=None):
        self._store = reference_module.ReferenceStore()
        self._consecutive_hits = 0
        self._get_roi = get_roi if get_roi is not None else lambda: None

    def _capture_cropped(self):
        return preprocessing.crop_roi(camera_module.capture_frame(), self._get_roi())

    def set_reference(self, frames=None) -> bool:
        """Формирует эталон из REFERENCE_FRAMES кадров (сырых BGR).

        frames — необязательный список уже захваченных кадров (например, из GUI);
        если не передан, кадры захватываются с камеры напрямую.
        """
        try:
            if frames is None:
                frames = [self._capture_cropped() for _ in range(config.REFERENCE_FRAMES)]
        except camera_module.CameraError as exc:
            log.error("Camera error while setting reference: %s", exc)
            return False

        processed = [preprocessing.preprocess(f) for f in frames]
        self._store.set(reference_module.build_reference(processed))
        self._consecutive_hits = 0
        if config.DEBUG:
            _save_debug("reference.png", self._store.get())
        log.info("Reference created (%d frames)", len(frames))
        return True

    def check_frame(self, frame) -> dict:
        """Одно сравнение сырого BGR-кадра с эталоном + счётчик подтверждений."""
        if not self._store.has():
            # Вызывается на каждый кадр превью (30 FPS) — warning здесь
            # спамил бы лог, поэтому debug.
            log.debug("Check skipped: reference not set")
            return {"changed": False, "confirmed": False, "error": REFERENCE_NOT_SET}

        start = time.perf_counter()
        processed = preprocessing.preprocess(frame)
        result = detector.compare(self._store.get(), processed)
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        if result["changed"]:
            self._consecutive_hits += 1
        else:
            self._consecutive_hits = 0
        confirmed = self._consecutive_hits >= config.CONFIRMATION_FRAMES

        result["confirmed"] = confirmed
        result["processing_time_ms"] = elapsed_ms

        if config.DEBUG:
            _save_debug("current.png", processed)
            _save_debug("difference.png", result["diff"])
            _save_debug("mask.png", result["mask"])
        del result["diff"], result["mask"]

        return result

    def detect_change(self, frame=None, frames=None) -> dict:
        """Полный цикл по внешнему сигналу: CONFIRMATION_FRAMES проверок подряд (ТЗ 12).

        GUI-режим: кадры приходят из Processor._recent (захват уже идёт
        в CaptureThread), камеру здесь не трогаем. Прямой захват с камеры
        оставлен только как фолбэк для тестов/скриптов без GUI.
        """
        if not self._store.has():
            log.warning("detect_change: reference not set")
            return {"changed": False, "error": REFERENCE_NOT_SET}

        self._consecutive_hits = 0
        result = {}
        if frames is not None:
            sequence = list(frames)[: config.CONFIRMATION_FRAMES]
        elif frame is not None:
            sequence = [frame] * config.CONFIRMATION_FRAMES
        else:
            sequence = []
            try:
                for _ in range(config.CONFIRMATION_FRAMES):
                    sequence.append(self._capture_cropped())
            except camera_module.CameraError as exc:
                log.error("Camera error during detect_change: %s", exc)
                return {"changed": False, "error": CAMERA_ERROR}
        for current in sequence:
            result = self.check_frame(current)
            if not result["changed"]:
                break

        confirmed = bool(result.get("confirmed"))
        return {
            "changed": confirmed,
            "change_ratio": result.get("change_ratio", 0.0),
            "changed_pixels": result.get("changed_pixels", 0),
            "boxes": result.get("boxes", []),
            "processing_time_ms": result.get("processing_time_ms", 0.0),
        }

    def get_reference(self):
        return self._store.get()

    def has_reference(self) -> bool:
        return self._store.has()

    def clear_reference(self):
        self._store.clear()
        self._consecutive_hits = 0
        log.info("Reference cleared")

    def reset_confirmation(self):
        self._consecutive_hits = 0


def _save_debug(name, image):
    path = Path(config.DEBUG_DIR)
    path.mkdir(exist_ok=True)
    cv2.imwrite(str(path / name), image)