import cv2
from PySide6.QtCore import QMetaObject, QThread, Qt, Signal, Slot
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import config
import v4l2
from camera_settings import CameraSettingsDialog
from capture import CaptureThread
from controller import Controller
from processing import STATUS_ALARM, STATUS_NO_REFERENCE, STATUS_NORMAL, Processor
from video_view import VideoView

NO_CAMERA = "NO_CAMERA"


class CameraPanel(QWidget):
    """Одна камера целиком: свой выбор камеры, свой захват, свой эталон,
    своя область интереса, свои кнопки и статусы.

    Панелей столько, сколько config.CAMERA_COUNT; между собой они не
    пересекаются — общий у них только порог тревоги (config) и кнопки
    верхнего уровня (Refresh, слайдер).
    """

    # slot_index, status — чтобы MainWindow знал, где горит тревога
    status_changed = Signal(int, str)

    def __init__(self, slot_index: int, preview_size, cameras, parent=None):
        super().__init__(parent)
        self.slot_index = slot_index
        # Область интереса камеры: доли кадра (x0, y0, x1, y1) или None.
        # Читается захватом и контроллером, меняется только из GUI-потока.
        self.roi = getattr(config, "DEFAULT_ROI", None)
        self._status = NO_CAMERA
        self._reference_pending = False
        self._capture_thread = None
        self.preview_width, self.preview_height = preview_size

        self.controller = Controller(get_roi=lambda: self.roi)
        self._processor = Processor(self.controller)
        self._process_thread = QThread(self)
        self._processor.moveToThread(self._process_thread)
        self._processor.result_ready.connect(self._on_result)
        self._processor.detection_done.connect(self._on_detection_done)
        self._process_thread.start()

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)

        camera_row = QHBoxLayout()
        camera_row.addWidget(QLabel(self.tr("Camera:")))
        self.camera_combo = QComboBox()
        self.camera_combo.activated.connect(self._on_camera_selected)
        self.settings_button = QPushButton(self.tr("Settings..."))
        self.settings_button.clicked.connect(self._on_settings)
        self._fill_combo(cameras)
        camera_row.addWidget(self.camera_combo, stretch=1)
        camera_row.addWidget(self.settings_button)
        column.addLayout(camera_row)

        self.video = VideoView()
        self.video.setFixedSize(self.preview_width, self.preview_height)
        self.video.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video.setStyleSheet(
            "background-color: #202020; color: #aaaaaa; border: 1px solid #444;")
        self.video.setText(self.tr("Camera not available"))
        self.video.roi_selected.connect(self._on_roi_selected)
        column.addWidget(self.video, alignment=Qt.AlignmentFlag.AlignHCenter)

        mono = "font-family: monospace; font-size: 14px;"
        self.status_label = QLabel()
        self.status_label.setStyleSheet(mono + "font-weight: bold;")
        self.reference_label = QLabel()
        self.reference_label.setStyleSheet(mono)
        self.change_label = QLabel()
        self.change_label.setStyleSheet(mono)
        self.last_check_label = QLabel()
        self.last_check_label.setStyleSheet(mono)
        self.trigger_label = QLabel()
        self.trigger_label.setStyleSheet(mono)
        for label in (self.status_label, self.reference_label, self.change_label,
                      self.last_check_label, self.trigger_label):
            column.addWidget(label)

        buttons_row = QHBoxLayout()
        self.reset_roi_button = QPushButton(self.tr("Reset zone"))
        self.reset_roi_button.setEnabled(False)
        self.reset_roi_button.clicked.connect(self._on_reset_roi)
        buttons_row.addWidget(self.reset_roi_button)
        self.set_reference_button = QPushButton(self.tr("Set reference"))
        self.set_reference_button.setEnabled(False)
        self.set_reference_button.clicked.connect(self._on_set_reference)
        buttons_row.addWidget(self.set_reference_button)
        self.detect_button = QPushButton(self.tr("Detect change"))
        self.detect_button.setEnabled(False)
        self.detect_button.clicked.connect(self._on_detect)
        buttons_row.addWidget(self.detect_button)
        column.addLayout(buttons_row)

        self._set_status(NO_CAMERA)
        self.change_label.setText(self.tr("Change: -"))
        self.trigger_label.setText(self.tr("Trigger: -"))

        self.start_capture(self.camera_combo.currentData())

    # --- список камер ------------------------------------------------------

    def _fill_combo(self, cameras):
        current = self.camera_combo.currentData()
        self.camera_combo.clear()
        for index in cameras:
            self.camera_combo.addItem(f"Camera {index}", userData=index)
        # Держим выбранную камеру; если она пропала из списка — берём свою по
        # номеру панели. Нет своей (камер меньше, чем панелей) — панель остаётся
        # пустой и показывает "Camera not available", а не открывает чужую камеру.
        wanted = current
        if wanted is None or self.camera_combo.findData(wanted) < 0:
            wanted = cameras[self.slot_index] if self.slot_index < len(cameras) else None
        position = self.camera_combo.findData(wanted)
        self.camera_combo.setCurrentIndex(position if position >= 0 else -1)
        self.settings_button.setEnabled(self.camera_combo.currentData() is not None)

    def reload_cameras(self, cameras):
        """Пересобрать список после Refresh. True -> у панели есть камера."""
        self.stop_capture()
        self._fill_combo(cameras)
        if self.camera_combo.currentData() is None:
            self._on_camera_error()
            return False
        self._on_camera_selected()
        return True

    def _on_camera_selected(self):
        self.stop_capture()
        self.controller.clear_reference()
        self._reference_pending = False
        self.roi = getattr(config, "DEFAULT_ROI", None)
        self.reference_label.setText(self.tr("Reference: NOT SET"))
        self.trigger_label.setText(self.tr("Trigger: -"))
        self._set_status(NO_CAMERA)
        self.set_reference_button.setEnabled(False)
        self.detect_button.setEnabled(False)
        self.reset_roi_button.setEnabled(False)
        self.start_capture(self.camera_combo.currentData())

    # --- параметры камеры (V4L2) -------------------------------------------

    @Slot()
    def _on_settings(self):
        """Правка экспозиции/яркости/контраста и прочего без консоли."""
        index = self.camera_combo.currentData()
        if index is None:
            return
        device = v4l2.device_for_index(index)
        if not v4l2.available():
            QMessageBox.information(
                self, self.tr("Camera settings"),
                self.tr("v4l2-ctl is not available.\nInstall it with: sudo apt install v4l-utils"))
            return
        dialog = CameraSettingsDialog(index, device, v4l2.controls_for_index(index), self)
        dialog.exec()

    # --- захват -----------------------------------------------------------

    def start_capture(self, camera_index):
        if camera_index is None:
            self._on_camera_error()
            return
        # На случай повторного старта (смена камеры) — сначала тихо глушим старый.
        self.stop_capture()
        # parent=None: объект живёт в главном потоке, но детей в run() больше
        # нет (Бывший QTimer(self) и давал варнинг + отсутствие картинки).
        # Удаление — через finished->deleteLater, ссылку чистим в stop_capture.
        self._capture_thread = CaptureThread(
            camera_index,
            preview_size=(self.preview_width, self.preview_height),
            roi_getter=lambda: self.roi,
        )
        self._capture_thread.camera_error.connect(self._on_camera_error)
        self._capture_thread.frame_captured.connect(self._processor.on_frame)
        self._capture_thread.finished.connect(self._capture_thread.deleteLater)
        self._capture_thread.start()

    def stop_capture(self):
        thread, self._capture_thread = self._capture_thread, None
        if thread is not None:
            # Отключаем error чтобы остановленный поток не перетирал UI
            # уже стартовавшего нового захвата (смена камеры).
            try:
                thread.camera_error.disconnect(self._on_camera_error)
            except Exception:
                pass
            try:
                thread.frame_captured.disconnect(self._processor.on_frame)
            except Exception:
                pass
            thread.stop()
            # CaptureThread дросселирован сверху до CAPTURE_MAX_FPS,
            # поэтому долгое блочирование GUI здесь исключено.
            thread.wait(3000)

    def close_capture(self):
        self.stop_capture()
        self._process_thread.quit()
        self._process_thread.wait(3000)

    # --- область интереса и эталон ----------------------------------------

    @Slot(float, float, float, float)
    def _on_roi_selected(self, x0: float, y0: float, x1: float, y1: float):
        # Выделили — режим рисования больше не нужен.
        self.video.set_selection_enabled(False)
        self.roi = (x0, y0, x1, y1)
        self.controller.clear_reference()
        self.reference_label.setText(self.tr("Reference: NOT SET"))
        self.change_label.setText(self.tr("Change: -"))
        self._set_status(STATUS_NO_REFERENCE)
        self.detect_button.setEnabled(False)
        if self._reference_pending:
            self._reference_pending = False
            self._request_reference()

    @Slot()
    def _on_set_reference(self):
        # Эталон снимается с выбранной области: включаем режим выделения
        # и ждём рамку. Пока область не нарисована — только взводим ожидание.
        self.video.set_selection_enabled(True)
        self._reference_pending = True

    @Slot()
    def _on_reset_roi(self):
        self.video.set_selection_enabled(False)   # ← вместо roi_button.setChecked(False)
        self._reference_pending = False
        self.roi = getattr(config, "DEFAULT_ROI", None)
        self.controller.clear_reference()
        self.controller.reset_confirmation()
        self.reference_label.setText(self.tr("Reference: NOT SET"))
        self.change_label.setText(self.tr("Change: -"))
        self.last_check_label.setText(self.tr("Last check: -"))
        self.trigger_label.setText(self.tr("Trigger: -"))
        self._set_status(STATUS_NO_REFERENCE)
        self.detect_button.setEnabled(False)

    @Slot()
    def reset_roi(self):
        """То же, что кнопка Reset zone (для входов GPIO)."""
        self._on_reset_roi()

    @Slot()
    def arm_reference(self):
        """То же, что кнопка Set reference (используется входами GPIO)."""
        self._on_set_reference()

    @Slot()
    def request_detect(self):
        """То же, что кнопка Detect change (используется входами GPIO)."""
        self._on_detect()

    def _request_reference(self):
        QMetaObject.invokeMethod(self._processor, "on_set_reference",
                                 Qt.ConnectionType.QueuedConnection)

    @Slot()
    def _on_detect(self):
        self.detect_button.setEnabled(False)
        QMetaObject.invokeMethod(self._processor, "on_detect",
                                 Qt.ConnectionType.QueuedConnection)

    # --- результаты --------------------------------------------------------

    def _on_detection_done(self, changed: bool, change_ratio: float,
                           elapsed_ms: float, boxes: list):
        self.detect_button.setEnabled(True)
        if changed:
            self.trigger_label.setText(self.tr("Trigger: CHANGED"))
            self.trigger_label.setStyleSheet(
                "font-family: monospace; font-size: 14px; color: #e33; font-weight: bold;")
        else:
            self.trigger_label.setText(self.tr("Trigger: NO CHANGE"))
            self.trigger_label.setStyleSheet(
                "font-family: monospace; font-size: 14px; color: #3c3;")

    def _on_camera_error(self):
        self.video.setPixmap(QPixmap())
        self.video.setText(self.tr("Camera not available"))
        self._set_status(NO_CAMERA)
        self._reference_pending = False
        self.reference_label.setText(self.tr("Reference: NOT SET"))
        self.change_label.setText(self.tr("Change: -"))
        self.last_check_label.setText(self.tr("Last check: -"))
        self.trigger_label.setText(self.tr("Trigger: -"))
        self.settings_button.setEnabled(False)
        self.set_reference_button.setEnabled(False)
        self.detect_button.setEnabled(False)
        self.reset_roi_button.setEnabled(False)

    def _on_result(self, frame, status: str, elapsed_ms: float, has_reference: bool,
                   change_ratio: float, boxes: list):
        # 30 FPS: тяжёлая обработка уже сделана в Processor, здесь только
        # BGR->RGB + QImage. copy() отвязывает QPixmap от numpy-буфера.

        self._set_status(status)

        try:
            if frame is None or getattr(frame, "size", 0) == 0:
                return
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            # Рамки изменённых областей: координаты в рабочем разрешении
            # (config.IMAGE_WIDTH/HEIGHT), масштабируем на кадр превью.
            if boxes:
                color = (255, 51, 51) if status == STATUS_ALARM else (51, 204, 51)
                sx = rgb.shape[1] / config.IMAGE_WIDTH
                sy = rgb.shape[0] / config.IMAGE_HEIGHT
                for x, y, w, h, _area in boxes:
                    cv2.rectangle(
                        rgb,
                        (int(x * sx), int(y * sy)),
                        (int((x + w) * sx), int((y + h) * sy)),
                        color,
                        2,
                    )
            image = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0],
                           QImage.Format.Format_RGB888)
            self.video.setPixmap(QPixmap.fromImage(image.copy()))
        except Exception:
            return

        # Пока ждём выделения области — показываем подсказку, а не "NOT SET":
        # смена текста на каждом кадре сбила бы её за 30 мс.
        if self._reference_pending:
            self.reference_label.setText(self.tr("Reference: draw the area"))
        else:
            self.reference_label.setText(
                self.tr("Reference: OK") if has_reference else self.tr("Reference: NOT SET")
            )
        if has_reference:
            self.change_label.setText(
                self.tr("Change: {} %").format(round(change_ratio * 100, 2)))
            self.last_check_label.setText(
                self.tr("Last check: {} ms").format(round(elapsed_ms, 1)))
        self.set_reference_button.setEnabled(True)
        self.detect_button.setEnabled(has_reference)
        self.reset_roi_button.setEnabled(True)

    def _set_status(self, status: str):
        texts = {
            STATUS_NORMAL: (self.tr("STATUS: NORMAL"), "#3c3"),
            STATUS_ALARM: (self.tr("STATUS: ALARM"), "#e33"),
            STATUS_NO_REFERENCE: (self.tr("STATUS: NO REFERENCE"), "#aa3"),
            NO_CAMERA: (self.tr("STATUS: NO CAMERA"), "#e33"),
        }
        text, color = texts[status]
        self.status_label.setText(text)
        self.status_label.setStyleSheet(
            f"font-family: monospace; font-size: 14px; font-weight: bold; color: {color};"
        )
        if status != self._status:
            self._status = status
            self.status_changed.emit(self.slot_index, status)

    # --- внешнее -----------------------------------------------------------

    @property
    def status(self) -> str:
        return self._status

    def reset_confirmation(self):
        self.controller.reset_confirmation()