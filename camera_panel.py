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
    """

    status_changed = Signal(int, str)

    def __init__(self, slot_index: int, preview_size, cameras, parent=None):
        super().__init__(parent)
        self.slot_index = slot_index
        self.roi = getattr(config, "DEFAULT_ROI", None)
        self._status = NO_CAMERA
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
        # РАЗРЕШАЕМ РИСОВАТЬ РАМКУ ВСЕГДА
        self.video.set_selection_enabled(True)
        column.addWidget(self.video, alignment=Qt.AlignmentFlag.AlignHCenter)

        transparent_bg = "background: transparent;"
        mono = "font-family: monospace; font-size: 14px; " + transparent_bg
        
        self.status_label = QLabel()
        self.status_label.setStyleSheet("font-family: monospace; font-size: 14px; font-weight: bold; " + transparent_bg)
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
        
        column.addLayout(buttons_row)

        self._set_status(NO_CAMERA)
        self.change_label.setText(self.tr("Change: -"))
        self.trigger_label.setText(self.tr("Trigger: -"))

        self.start_capture(self.camera_combo.currentData())

    def _fill_combo(self, cameras):
        current = self.camera_combo.currentData()
        self.camera_combo.clear()
        for index in cameras:
            self.camera_combo.addItem(f"Camera {index}", userData=index)
        wanted = current
        if wanted is None or self.camera_combo.findData(wanted) < 0:
            wanted = cameras[self.slot_index] if self.slot_index < len(cameras) else None
        position = self.camera_combo.findData(wanted)
        self.camera_combo.setCurrentIndex(position if position >= 0 else -1)
        self.settings_button.setEnabled(self.camera_combo.currentData() is not None)

    def reload_cameras(self, cameras):
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
        self.roi = getattr(config, "DEFAULT_ROI", None)
        self.reference_label.setText(self.tr("Reference: NOT SET"))
        self.trigger_label.setText(self.tr("Trigger: -"))
        self._set_status(NO_CAMERA)
        self.set_reference_button.setEnabled(False)
        self.reset_roi_button.setEnabled(False)
        self.start_capture(self.camera_combo.currentData())

    @Slot()
    def _on_settings(self):
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

    def start_capture(self, camera_index):
        if camera_index is None:
            self._on_camera_error()
            return
        self.stop_capture()
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
            try:
                thread.camera_error.disconnect(self._on_camera_error)
            except Exception:
                pass
            try:
                thread.frame_captured.disconnect(self._processor.on_frame)
            except Exception:
                pass
            thread.stop()
            thread.wait(3000)

    def close_capture(self):
        self.stop_capture()
        self._process_thread.quit()
        self._process_thread.wait(3000)

    @Slot(float, float, float, float)
    def _on_roi_selected(self, x0: float, y0: float, x1: float, y1: float):
        # Запоминаем новую рамку. Старый эталон сбрасывается, так как изменилась зона.
        self.roi = (x0, y0, x1, y1)
        self.controller.clear_reference()
        self.reference_label.setText(self.tr("Reference: NOT SET"))
        self.change_label.setText(self.tr("Change: -"))
        self._set_status(STATUS_NO_REFERENCE)

    @Slot()
    def _on_set_reference(self):
        # Берем текущую рамку (или весь кадр, если рамки нет) и сразу ставим эталон.
        self.reference_label.setText(self.tr("Reference: setting..."))
        self._request_reference()

    @Slot()
    def _on_reset_roi(self):
        # Сбрасываем рамку на весь кадр
        self.roi = getattr(config, "DEFAULT_ROI", None)
        self.controller.clear_reference()
        self.controller.reset_confirmation()
        self.reference_label.setText(self.tr("Reference: NOT SET"))
        self.change_label.setText(self.tr("Change: -"))
        self.last_check_label.setText(self.tr("Last check: -"))
        self.trigger_label.setText(self.tr("Trigger: -"))
        self._set_status(STATUS_NO_REFERENCE)

    @Slot()
    def reset_roi(self):
        self._on_reset_roi()

    @Slot()
    def arm_reference(self):
        self._on_set_reference()

    @Slot()
    def request_detect(self):
        QMetaObject.invokeMethod(self._processor, "on_detect",
                                 Qt.ConnectionType.QueuedConnection)

    def _request_reference(self):
        QMetaObject.invokeMethod(self._processor, "on_set_reference",
                                 Qt.ConnectionType.QueuedConnection)

    def _on_detection_done(self, changed: bool, change_ratio: float,
                           elapsed_ms: float, boxes: list):
        if changed:
            self.trigger_label.setText(self.tr("Trigger: CHANGED"))
            self.trigger_label.setStyleSheet(
                "font-family: monospace; font-size: 14px; color: #e33; font-weight: bold; background: transparent;")
        else:
            self.trigger_label.setText(self.tr("Trigger: NO CHANGE"))
            self.trigger_label.setStyleSheet(
                "font-family: monospace; font-size: 14px; color: #3c3; background: transparent;")

    def _on_camera_error(self):
        self.video.setPixmap(QPixmap())
        self.video.setText(self.tr("Camera not available"))
        self._set_status(NO_CAMERA)
        self.reference_label.setText(self.tr("Reference: NOT SET"))
        self.change_label.setText(self.tr("Change: -"))
        self.last_check_label.setText(self.tr("Last check: -"))
        self.trigger_label.setText(self.tr("Trigger: -"))
        self.settings_button.setEnabled(False)
        self.set_reference_button.setEnabled(False)
        self.reset_roi_button.setEnabled(False)

    def _on_result(self, frame, status: str, elapsed_ms: float, has_reference: bool,
                   change_ratio: float, boxes: list):
        self._set_status(status)

        try:
            if frame is None or getattr(frame, "size", 0) == 0:
                return
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
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

        self.reference_label.setText(
            self.tr("Reference: OK") if has_reference else self.tr("Reference: NOT SET")
        )
        if has_reference:
            self.change_label.setText(
                self.tr("Change: {} %").format(round(change_ratio * 100, 2)))
            self.last_check_label.setText(
                self.tr("Last check: {} ms").format(round(elapsed_ms, 1)))
        
        self.set_reference_button.setEnabled(True)
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
            f"font-family: monospace; font-size: 14px; font-weight: bold; color: {color}; background: transparent;"
        )
        if status != self._status:
            self._status = status
            self.status_changed.emit(self.slot_index, status)

    @property
    def status(self) -> str:
        return self._status

    def reset_confirmation(self):
        self.controller.reset_confirmation()