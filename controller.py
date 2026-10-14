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

_store = reference_module.ReferenceStore()
_consecutive_hits = 0


def _save_debug(name, image):
    path = Path(config.DEBUG_DIR)
    path.mkdir(exist_ok=True)
    cv2.imwrite(str(path / name), image)


def set_reference(frames=None) -> bool:
    """Формирует эталон из REFERENCE_FRAMES кадров (сырых BGR).

    frames — необязательный список уже захваченных кадров (например, из GUI);
    если не передан, кадры захватываются с камеры напрямую.
    """
    global _consecutive_hits
    try:
        if frames is None:
            frames = [camera_module.capture_frame() for _ in range(config.REFERENCE_FRAMES)]
    except camera_module.CameraError as exc:
        log.error("Camera error while setting reference: %s", exc)
        return False

    processed = [preprocessing.preprocess(f) for f in frames]
    _store.set(reference_module.build_reference(processed))
    _consecutive_hits = 0
    if config.DEBUG:
        _save_debug("reference.png", _store.get())
    log.info("Reference created (%d frames)", len(frames))
    return True


def check_frame(frame) -> dict:
    """Одно сравнение сырого BGR-кадра с эталоном + счётчик подтверждений."""
    global _consecutive_hits
    if not _store.has():
        # Вызывается на каждый кадр превью (30 FPS) — warning здесь
        # спамил бы лог, поэтому debug.
        log.debug("Check skipped: reference not set")
        return {"changed": False, "confirmed": False, "error": REFERENCE_NOT_SET}

    start = time.perf_counter()
    processed = preprocessing.preprocess(frame)
    result = detector.compare(_store.get(), processed)
    elapsed_ms = (time.perf_counter() - start) * 1000.0

    if result["changed"]:
        _consecutive_hits += 1
    else:
        _consecutive_hits = 0
    confirmed = _consecutive_hits >= config.CONFIRMATION_FRAMES

    result["confirmed"] = confirmed
    result["processing_time_ms"] = elapsed_ms

    if config.DEBUG:
        _save_debug("current.png", processed)
        _save_debug("difference.png", result["diff"])
        _save_debug("mask.png", result["mask"])
    del result["diff"], result["mask"]

    # log.info(
    #     "Check: changed=%s confirmed=%s ratio=%.4f pixels=%d time=%.1fms",
    #     result["changed"], confirmed, result["change_ratio"],
    #     result["changed_pixels"], elapsed_ms,
    # )
    return result


def detect_change(frame=None, frames=None) -> dict:
    """Полный цикл по внешнему сигналу: CONFIRMATION_FRAMES проверок подряд (ТЗ 12).

    GUI-режим: кадры приходят из Processor._recent (захват уже идёт
    в CaptureThread), камеру здесь не трогаем. Прямой захват с камеры
    оставлен только как фолбэк для тестов/скриптов без GUI.
    """
    global _consecutive_hits
    if not _store.has():
        log.warning("detect_change: reference not set")
        return {"changed": False, "error": REFERENCE_NOT_SET}

    _consecutive_hits = 0
    result = {}
    if frames is not None:
        sequence = list(frames)[: config.CONFIRMATION_FRAMES]
    elif frame is not None:
        sequence = [frame] * config.CONFIRMATION_FRAMES
    else:
        sequence = []
        try:
            for _ in range(config.CONFIRMATION_FRAMES):
                sequence.append(camera_module.capture_frame())
        except camera_module.CameraError as exc:
            log.error("Camera error during detect_change: %s", exc)
            return {"changed": False, "error": CAMERA_ERROR}
    for current in sequence:
        result = check_frame(current)
        if not result["changed"]:
            break

    confirmed = bool(result.get("confirmed"))
    # log.info("Change %s", "detected" if confirmed else "not detected")
    return {
        "changed": confirmed,
        "change_ratio": result.get("change_ratio", 0.0),
        "changed_pixels": result.get("changed_pixels", 0),
        "boxes": result.get("boxes", []),
        "processing_time_ms": result.get("processing_time_ms", 0.0),
    }


def get_reference():
    return _store.get()


def has_reference() -> bool:
    return _store.has()


def clear_reference():
    global _consecutive_hits
    _store.clear()
    _consecutive_hits = 0
    log.info("Reference cleared")


def reset_confirmation():
    global _consecutive_hits
    _consecutive_hits = 0
