import logging
import sys
import threading

import cv2
import numpy as np

import config
import v4l2

log = logging.getLogger(__name__)


class CameraError(Exception):
    pass


def _picamera2_available() -> bool:
    try:
        import picamera2  # noqa: F401
        return True
    except Exception:
        return False


def _resolve_backend() -> str:
    """auto: Picamera2 если доступна (RPi), иначе OpenCV (Windows-тесты)."""
    want = str(getattr(config, "CAMERA_BACKEND", "auto")).lower()
    if want in ("picamera2", "opencv", "mindvision"):
        return want
    # auto
    if _picamera2_available() and sys.platform.startswith("linux"):
        return "picamera2"
    # Если picamera2 импортируется и на другой ОС — тоже пробуем её первой,
    # open() сам упадёт обратно на OpenCV при неудаче.
    if _picamera2_available():
        return "picamera2"
    return "opencv"


def _opencv_backend_flags() -> list[int]:
    names = list(getattr(config, "OPENCV_BACKENDS_PREFER", ("DSHOW", "MSMF")))
    names.append("ANY")
    flags: list[int] = []
    for name in names:
        flag = getattr(cv2, f"CAP_{name}", None)
        if flag is None:
            continue
        if flag not in flags:
            flags.append(flag)
    if not flags:
        flags.append(cv2.CAP_ANY)
    return flags


class Camera:
    """Фасад над бэкендами OpenCV / Picamera2.

    Единственный читатель в GUI-режиме — CaptureThread. Прямые вызовы
    read() из Processor запрещены архитектурой (см. processing.py),
    блокировка оставлена только для безопасного open/close.
    """

    def __init__(self):
        self._cap = None
        self._picam2 = None
        self._mv = None
        self._backend: str | None = None
        self._lock = threading.Lock()

    def open(self, index=None) -> bool:
        index = config.CAMERA_INDEX if index is None else index
        want = _resolve_backend()
        with self._lock:
            self._release_locked()
            if want == "mindvision":
                if self._open_mindvision_locked(index):
                    return True
                log.error("Camera erro: cannot open Mindvision camera %", index)
                return False
            if want == "picamera2":
                if self._open_picamera2_locked(index):
                    return True
                # auto: фолбэк на OpenCV; явный picamera2 — ошибка.
                if str(getattr(config, "CAMERA_BACKEND", "auto")).lower() == "picamera2":
                    log.error("Camera error: cannot open Picamera2 (index=%s)", index)
                    return False
                log.warning("Picamera2 unavailable, fallback to OpenCV (index=%s)", index)
            if self._open_opencv_locked(index):
                return True
            log.error("Camera error: cannot open camera %s", index)
            return False

    def _open_mindvision_locked(self, index) -> bool:
        try:
            path = getattr(config, "MV_SDK_PATH", None)
            if path and path not in sys.path:
                sys.path.insert(0, path)
            import mvsdk

            devs = mvsdk.CameraEnumerateDevice()
            if index >= len(devs):
                log.warning("Mindvision: camera %s not found (%s total)", index, len(devs))
                return False
            h = mvsdk.CameraInit(devs[index], -1, -1)
            cap = mvsdk.CameraGetCapability(h)
            mono = cap.sIspCapacity.bMonoSensor != 0
            mvsdk.CameraSetIspOutFormat(
                h, mvsdk.CAMERA_MEDIA_TYPE_MONO8 if mono else mvsdk.CAMERA_MEDIA_TYPE_BGR8
            )
            mvsdk.CameraSetTriggerMode(h, 0)
            if config.LOCK_CAMERA_PARAMS:
                mvsdk.CameraSetAeState(h, 0)
                mvsdk.CameraSetExposureTime(h, getattr(config, "MV_EXPOSURE_US", 30000))
            else:
                mvsdk.CameraSetAeState(h, 1)
            mvsdk.CameraPlay(h)
            size = cap.sResolutionRange.iWidthMax * cap.sResolutionRange.iHeightMax * (1 if mono else 3)
            buf = mvsdk.CameraAlignMalloc(size, 16)
            self._mv = (mvsdk, h, buf, mono)
            self._backend = "mindvision"
            log.info("Camera initialized (index=%s, backend=mindvision)", index)
            return True
        except Exception as exc:
            log.warning("Mindvision open failed (index=%s): %s", index, exc)
            return False
                

    def _open_opencv_locked(self, index) -> bool:
        # Явный бэкенд важен: без него Windows уходит в FFMPEG
        # (WARN ... libavdevice) и open/проба камер виснет.
        # Настройки V4L2 — до открытия: драйвер отдаёт текущие значения
        # только пока устройство не стримит.
        applied = v4l2.apply_for_index(index)
        for flag in _opencv_backend_flags():
            cap = cv2.VideoCapture(index, flag)
            try:
                if cap.isOpened():
                    if config.LOCK_CAMERA_PARAMS and not applied:
                        self._fix_params(cap)
                    elif applied:
                        # _fix_params включает автоэкспозицию через OpenCV и
                        # затёрла бы экспозицию из config.V4L2_CONTROLS.
                        log.debug("OpenCV exposure fix skipped (V4L2_CONTROLS)")
                    self._cap = cap
                    self._backend = "opencv"
                    log.info("Camera initialized (index=%s, backend=opencv flag=%s)", index, flag)
                    return True
            except Exception:
                log.warning("OpenCV open failed (index=%s flag=%s)", index, flag)
            try:
                cap.release()
            except Exception:
                pass
        return False

    def _open_picamera2_locked(self, index) -> bool:
        try:
            from picamera2 import Picamera2
        except Exception as exc:
            log.warning("Picamera2 import failed: %s", exc)
            return False
        try:
            try:
                picam2 = Picamera2(camera_num=index)
            except TypeError:
                picam2 = Picamera2()
            size = (config.PREVIEW_WIDTH, config.PREVIEW_HEIGHT)
            try:
                cfg = picam2.create_preview_configuration(
                    main={"size": size, "format": "RGB888"}
                )
            except Exception:
                cfg = picam2.create_preview_configuration(main={"size": size})
            picam2.configure(cfg)
            picam2.start()
            self._picam2 = picam2
            self._backend = "picamera2"
            if config.LOCK_CAMERA_PARAMS:
                try:
                    picam2.set_controls({"AeEnable": False, "AwbEnable": False})
                except Exception:
                    log.warning("Could not lock Picamera2 exposure/WB")
            log.info("Camera initialized (index=%s, backend=picamera2)", index)
            return True
        except Exception as exc:
            log.warning("Picamera2 open failed (index=%s): %s", index, exc)
            try:
                if "picam2" in locals() and picam2 is not None:
                    picam2.close()
            except Exception:
                pass
            return False

    @staticmethod
    def _fix_params(cap):
        # На Windows через DirectShow/MSMF работает не везде — best-effort.
        try:
            if sys.platform.startswith("linux"):
                cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1)
                cap.set(cv2.CAP_PROP_EXPOSURE, 150)
                cap.set(cv2.CAP_PROP_AUTO_WB, 0)
            else:
                cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)
                cap.set(cv2.CAP_PROP_AUTO_WB, 0)
        except Exception:
            log.warning("Could not lock camera parameters (exposure/WB)")

    def read(self):
        with self._lock:
            backend = self._backend
            cap = self._cap
            picam2 = self._picam2
            mv = self._mv
            if backend is None or (cap is None and picam2 is None and mv is None):
                raise CameraError("CAMERA_NOT_INITIALIZED")
        if backend == "mindvision":
            sdk, h, buf, mono = mv
            try:
                raw, head = sdk.CameraGetImageBuffer(h, 500)
                try:
                    sdk.CameraImageProcess(h, raw, buf, head)
                finally:
                    sdk.CameraReleaseImageBuffer(h, raw)
                data = (sdk.c_ubyte * head.uBytes).from_address(buf)
                arr = np.frombuffer(data, dtype=np.uint8)
                if mono:
                    gray = arr.reshape(head.iHeight, head.iWidth)
                    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
                return arr.reshape(head.iHeight, head.iWidth, 3).copy()
            except Exception as exc:
                log.error("Camera error: Mindvision capture failed: %s", exc)
                raise CameraError("FRAME_CAPTURE_FAILED")
        if backend == "picamera2":
            try:
                frame_rgb = picam2.capture_array()
            except Exception as exc:
                log.error("Camera error: Picamera2 capture failed: %s", exc)
                raise CameraError("FRAME_CAPTURE_FAILED")
            if frame_rgb is None:
                raise CameraError("FRAME_CAPTURE_FAILED")
            try:
                if frame_rgb.ndim == 3 and frame_rgb.shape[2] > 3:
                    return frame_rgb[:, :, :3].copy()
                return frame_rgb
            except Exception as exc:
                log.error("Camera error: Picamera2 convert failed: %s", exc)
                raise CameraError("FRAME_CAPTURE_FAILED")
        # OpenCV: read вне долгого удержания? cap.read сам потокобезопасен
        # относительно close под lock'ом чтения хэндла выше; повторная
        # проверка is_open не нужна — единственный читатель CaptureThread.
        ok, frame = cap.read()
        if not ok or frame is None:
            log.error("Camera error: frame capture failed")
            raise CameraError("FRAME_CAPTURE_FAILED")
        return frame

    def is_open(self) -> bool:
        with self._lock:
            if self._backend == "mindvision":
                return self._mv is not None
            if self._backend == "picamera2":
                return self._picam2 is not None
            return self._cap is not None and self._cap.isOpened()

    @property
    def backend(self) -> str | None:
        with self._lock:
            return self._backend

    def close(self):
        with self._lock:
            self._release_locked()

    def _release_locked(self):
        if self._mv is not None:
            sdk, h, buf, _ = self._mv
            try:
                sdk.CameraUnInit(h)
            except Exception:
                pass
            try:
                sdk.CameraAlignFree(buf)
            except Exception:
                pass
            self._mv = None
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
        if self._picam2 is not None:
            try:
                self._picam2.stop()
            except Exception:
                pass
            try:
                self._picam2.close()
            except Exception:
                pass
            self._picam2 = None
        self._backend = None


camera = Camera()


def initialize_camera(index=None) -> bool:
    return camera.open(index)


def _picamera2_indices(max_index: int = 4) -> list[int]:
    """Индексы камер, которые видит Picamera2 (CSI и USB через libcamera).

    Нужно для CAMERA_COUNT > 1. Если определить не удалось — пустой список,
    вызывающий код откатится на старую проверку открытием.
    """
    try:
        from picamera2 import Picamera2
    except Exception as exc:  # noqa: BLE001
        log.debug("Picamera2 import failed: %s", exc)
        return []
    try:
        infos = Picamera2.global_camera_info()
        if infos:
            return list(range(len(infos)))
    except Exception as exc:  # noqa: BLE001 — на старых версиях метода нет
        log.debug("global_camera_info unavailable: %s", exc)
        return []
    return []


def list_cameras(max_index: int = 24) -> list[int]:
    """Возвращает индексы доступных камер.

    На Picamera2-бэкенде проба через VideoCapture бессмысленна — там
    перечисляем камеры средствами Picamera2, а если это не вышло,
    возвращаем [CAMERA_INDEX] если Picamera2 вообще открывается.
    """
    want = _resolve_backend()
    if want == "mindvision":
        camera.close()
        ok = camera.open(config.CAMERA_INDEX)
        log.info("Available cameras: %s (mindvision)", [config.CAMERA_INDEX] if ok else [])
        return [config.CAMERA_INDEX] if ok else []
    if want == "picamera2":
        indices = _picamera2_indices()
        if indices:
            log.info("Available cameras: %s (picamera2)", indices)
            return indices
        try:
            if camera.open(config.CAMERA_INDEX):
                log.info("Available cameras: [%s] (picamera2)", config.CAMERA_INDEX)
                return [config.CAMERA_INDEX]
        except Exception:
            pass
        log.info("Available cameras: [] (picamera2 unavailable)")
        return []

    if sys.platform.startswith("linux"):
        log.info("Available cameras: [%s] (fixed)", config.CAMERA_INDEX)
        return [config.CAMERA_INDEX]

    available = []
    for i in range(max_index):
        for flag in _opencv_backend_flags():
            cap = None
            try:
                cap = cv2.VideoCapture(i, flag)
                if cap.isOpened():
                    available.append(i)
                    break
            except Exception:
                continue
            finally:
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        pass
    log.info("Available cameras: %s", available)
    return available


def capture_frame():
    return camera.read()


def release_camera():
    camera.close()
