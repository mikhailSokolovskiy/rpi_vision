import logging
import sys
from gpio import gpio
from pathlib import Path

import cv2
from PySide6.QtCore import QLocale, QMetaObject, QThread, QTranslator, Qt, QTimer, Slot
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

import camera as camera_module
import config
import controller
from autorun import setup as autorun_setup
from capture import CaptureThread
from processing import STATUS_ALARM, STATUS_NO_REFERENCE, STATUS_NORMAL, Processor
from video_view import VideoView

BASE_DIR = Path(__file__).parent


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(self.tr("Camera monitor"))
        self.setFixedSize(config.PREVIEW_WIDTH + 150, config.PREVIEW_HEIGHT + 330)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        camera_row = QHBoxLayout()
        camera_row.addWidget(QLabel(self.tr("Camera:")))
        self.camera_combo = QComboBox()
        for index in camera_module.list_cameras():
            self.camera_combo.addItem(f"Camera {index}", userData=index)
        self.camera_combo.activated.connect(self._on_camera_selected)
        camera_row.addWidget(self.camera_combo, stretch=1)
        self.refresh_button = QPushButton(self.tr("Refresh"))
        self.refresh_button.clicked.connect(self._on_refresh_cameras)
        camera_row.addWidget(self.refresh_button)
        layout.addLayout(camera_row)

        video_row = QHBoxLayout()
        self.video_label = VideoView()
        self.video_label.setFixedSize(config.PREVIEW_WIDTH, config.PREVIEW_HEIGHT)
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setStyleSheet("background-color: #202020; color: #aaaaaa; border: 1px solid #444;")
        self.video_label.setText(self.tr("Camera not available"))
        self.video_label.roi_selected.connect(self._on_roi_selected)
        video_row.addWidget(self.video_label, alignment=Qt.AlignmentFlag.AlignHCenter)

        # Вертикальный слайдер порога тревоги (доля изменившихся пикселей).
        # Маппинг: значение слайдера 1..200 -> 0.1%..20.0% -> ratio 0.001..0.2.
        slider_col = QVBoxLayout()
        slider_col.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        slider_col.addWidget(QLabel(self.tr("Alarm %")), alignment=Qt.AlignmentFlag.AlignHCenter)
        self.threshold_slider = QSlider(Qt.Orientation.Vertical)
        self.threshold_slider.setRange(1, 200)
        self.threshold_slider.setValue(int(round(config.CHANGE_RATIO_THRESHOLD * 1000)))
        self.threshold_slider.setTickPosition(QSlider.TickPosition.TicksRight)
        self.threshold_slider.setTickInterval(20)
        self.threshold_slider.setSingleStep(1)
        self.threshold_slider.setPageStep(10)
        self.threshold_slider.setFixedHeight(config.PREVIEW_HEIGHT)
        self.threshold_slider.valueChanged.connect(self._on_threshold_changed)
        slider_col.addWidget(self.threshold_slider, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.threshold_value_label = QLabel()
        self.threshold_value_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._update_threshold_label(self.threshold_slider.value())
        slider_col.addWidget(self.threshold_value_label, alignment=Qt.AlignmentFlag.AlignHCenter)
        video_row.addLayout(slider_col)
        layout.addLayout(video_row)

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
        layout.addWidget(self.status_label)
        layout.addWidget(self.reference_label)
        layout.addWidget(self.change_label)
        layout.addWidget(self.last_check_label)
        layout.addWidget(self.trigger_label)

        buttons_row = QHBoxLayout()
        self.roi_button = QPushButton(self.tr("Select area"))
        self.roi_button.setCheckable(True)
        self.roi_button.setEnabled(False)
        self.roi_button.toggled.connect(self._on_roi_toggled)
        buttons_row.addWidget(self.roi_button)
        self.set_reference_button = QPushButton(self.tr("Set reference"))
        self.set_reference_button.setEnabled(False)
        self.set_reference_button.clicked.connect(self._on_set_reference)
        buttons_row.addWidget(self.set_reference_button)
        self.detect_button = QPushButton(self.tr("Detect change"))
        self.detect_button.setEnabled(False)
        self.detect_button.clicked.connect(self._on_detect)
        buttons_row.addWidget(self.detect_button)
        layout.addLayout(buttons_row)

        self._set_status("NO_CAMERA")
        self.change_label.setText(self.tr("Change: -"))
        self.trigger_label.setText(self.tr("Trigger: -"))

        self._capture_thread = None
        self._process_thread = QThread(self)
        self._processor = Processor()
        self._processor.moveToThread(self._process_thread)
        self._processor.result_ready.connect(self._on_result)
        self._processor.detection_done.connect(self._on_detection_done)
        self._process_thread.start()

        self._current_status = "NO_CAMERA"
        # Эталон ждёт выделенной области: Set reference взводит флаг,
        # а снимается он уже на первом нарисованном ROI.
        self._reference_pending = False
        self._heartbeat_state = False
        self._hb_timer = QTimer(self)
        self._hb_timer.setInterval(200)
        self._hb_timer.timeout.connect(self._tick_heartbeat)
        self._hb_timer.start()

        # GPIO: связываем входные пины с действиями и инициализируем железо.
        # Колбэки зовут те же методы, что и кнопки; они используют
        # QMetaObject.invokeMethod(QueuedConnection) — безопасно из чужого потока.
        gpio.set_callbacks({
            "set_reference": lambda: QMetaObject.invokeMethod(
                 self, "_on_set_reference", Qt.ConnectionType.QueuedConnection),
            "detect": lambda: QMetaObject.invokeMethod(
                 self, "_on_detect", Qt.ConnectionType.QueuedConnection),
        })
        gpio.setup()

        self._start_capture(self.camera_combo.currentData())

    def _tick_heartbeat(self):
        if self._current_status == STATUS_ALARM:
            self._heartbeat_state = False
        else:
            self._heartbeat_state = not self._heartbeat_state
        gpio.set_output("heartbeat", self._heartbeat_state)

    def _start_capture(self, camera_index):
        if camera_index is None:
            self._on_camera_error()
            return
        # На случай повторного старта (смена камеры) — сначала тихо глушим старый.
        self._stop_capture()
        # parent=None: объект живёт в главном потоке, но детей в run() больше
        # нет (бывший QTimer(self) и давал варнинг + отсутствие картинки).
        # Удаление — через finished->deleteLater, ссылку чистим в _stop_capture.
        self._capture_thread = CaptureThread(camera_index)
        self._capture_thread.camera_error.connect(self._on_camera_error)
        self._capture_thread.frame_captured.connect(self._processor.on_frame)
        self._capture_thread.finished.connect(self._capture_thread.deleteLater)
        self._capture_thread.start()

    def _stop_capture(self):
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
            # поэтому долгое блочивание GUI здесь исключено.
            thread.wait(3000)

    @Slot()
    def _on_refresh_cameras(self):
        self.refresh_button.setEnabled(False)
        try:
            current = self.camera_combo.currentData()
            # list_cameras() для picamera2/mindvision переоткрывает общий
            # camera.open()/close() — живой захват это убьёт, глушим заранее.
            self._stop_capture()
            cameras = camera_module.list_cameras()
            self.camera_combo.clear()
            for index in cameras:
                self.camera_combo.addItem(f"Camera {index}", userData=index)
            if not cameras:
                self._on_camera_error()
                return
            # Камера могла сменить индекс/отвалиться — берём ближайшую.
            position = self.camera_combo.findData(current)
            self.camera_combo.setCurrentIndex(position if position >= 0 else 0)
            self._on_camera_selected()
        finally:
            self.refresh_button.setEnabled(True)

    def _on_camera_selected(self):
        self._stop_capture()
        controller.clear_reference()
        self._reference_pending = False
        self.reference_label.setText(self.tr("Reference: NOT SET"))
        self.trigger_label.setText(self.tr("Trigger: -"))
        self._set_status("NO_CAMERA")
        self._current_status = "NO_CAMERA"
        self.roi_button.setEnabled(False)
        self.set_reference_button.setEnabled(False)
        self.detect_button.setEnabled(False)
        self._start_capture(self.camera_combo.currentData())
        gpio.set_ready(False)

    @Slot(bool)
    def _on_roi_toggled(self, checked: bool):
        self.video_label.set_selection_enabled(checked)
        if not checked:
            # Выключили режим, не выделив область — ждать эталона больше нечего.
            self._reference_pending = False

    @Slot(float, float, float, float)
    def _on_roi_selected(self, x0: float, y0: float, x1: float, y1: float):
        config.ROI = (x0, y0, x1, y1)
        # Сменилась геометрия кадра — прежний эталон больше не годится.
        controller.clear_reference()
        self.reference_label.setText(self.tr("Reference: NOT SET"))
        self.change_label.setText(self.tr("Change: -"))
        self._set_status(STATUS_NO_REFERENCE)
        self._current_status = STATUS_NO_REFERENCE
        self.detect_button.setEnabled(False)
        if self._reference_pending:
            self._reference_pending = False
            QMetaObject.invokeMethod(self._processor, "on_set_reference",
                                     Qt.ConnectionType.QueuedConnection)


    def _update_threshold_label(self, slider_value: int):
        percent = slider_value / 10.0
        self.threshold_value_label.setText(f"{percent:.1f} %")

    def _on_threshold_changed(self, slider_value: int):
        # Живое изменение порога: detector.compare читает
        # config.CHANGE_RATIO_THRESHOLD при каждом кадре.
        config.CHANGE_RATIO_THRESHOLD = slider_value / 1000.0
        self._update_threshold_label(slider_value)
        controller.reset_confirmation()

    def _set_status(self, status: str):
        texts = {
            STATUS_NORMAL: (self.tr("STATUS: NORMAL"), "#3c3"),
            STATUS_ALARM: (self.tr("STATUS: ALARM"), "#e33"),
            STATUS_NO_REFERENCE: (self.tr("STATUS: NO REFERENCE"), "#aa3"),
            "NO_CAMERA": (self.tr("STATUS: NO CAMERA"), "#e33"),
        }
        text, color = texts[status]
        self.status_label.setText(text)
        self.status_label.setStyleSheet(
            f"font-family: monospace; font-size: 14px; font-weight: bold; color: {color};"
        )

    @Slot()
    def _on_set_reference(self):
        # Эталон снимается с выбранной области: включаем режим выделения
        # и ждём рамку. Пока область не нарисована — только взводим ожидание.
        if not self.roi_button.isChecked():
            self.roi_button.setChecked(True)
        self._reference_pending = True

    @Slot()
    def _on_detect(self):
        self.detect_button.setEnabled(False)
        QMetaObject.invokeMethod(self._processor, "on_detect",
                                 Qt.ConnectionType.QueuedConnection)

    def _on_detection_done(self, changed: bool, change_ratio: float, elapsed_ms: float, boxes: list):
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
        self.video_label.setPixmap(QPixmap())
        self.video_label.setText(self.tr("Camera not available"))
        self._set_status("NO_CAMERA")
        self._reference_pending = False
        self.reference_label.setText(self.tr("Reference: NOT SET"))
        self.change_label.setText(self.tr("Change: -"))
        self.last_check_label.setText(self.tr("Last check: -"))
        self.trigger_label.setText(self.tr("Trigger: -"))
        self.roi_button.setEnabled(False)
        self.set_reference_button.setEnabled(False)
        self.detect_button.setEnabled(False)
        self._current_status = "NO_CAMERA"
        gpio.set_alarm(False)
        gpio.set_ready(False)

    def _on_result(self, frame, status: str, elapsed_ms: float, has_reference: bool,
                   change_ratio: float, boxes: list):
        # 30 FPS: тяжёлая обработка уже сделана в Processor, здесь только
        # BGR->RGB + QImage. copy() отвязывает QPixmap от numpy-буфера.

        self._current_status = status
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
            image = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format.Format_RGB888)
            self.video_label.setPixmap(QPixmap.fromImage(image.copy()))
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
            self.change_label.setText(self.tr("Change: {} %").format(round(change_ratio * 100, 2)))
            self.last_check_label.setText(self.tr("Last check: {} ms").format(round(elapsed_ms, 1)))
        self.roi_button.setEnabled(True)
        self.set_reference_button.setEnabled(True)
        self.detect_button.setEnabled(has_reference)

    def closeEvent(self, event):
        self._hb_timer.stop()
        gpio.set_output("heartbeat", False)
        gpio.set_alarm(False)
        gpio.set_ready(False)
        gpio.close()
        self._stop_capture()
        self._process_thread.quit()
        self._process_thread.wait(3000)
        camera_module.release_camera()
        super().closeEvent(event)


def main():
    (BASE_DIR / config.LOG_FILE).write_text("", encoding="utf-8")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(BASE_DIR / config.LOG_FILE, encoding="utf-8"),
        ],
    )

    # config.AUTORUN -> юнит systemd на автозапуск. Ошибки не фатальны:
    # autorun сам логирует и возвращает False.
    autorun_setup()

    app = QApplication(sys.argv)

    translator = QTranslator(app)
    locale = QLocale.system().name().split("_")[0]
    if translator.load(f"app_{locale}", str(BASE_DIR / "i18n")):
        app.installTranslator(translator)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
