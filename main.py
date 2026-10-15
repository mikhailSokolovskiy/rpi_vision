import logging
import sys
from gpio import gpio
from pathlib import Path

from PySide6.QtCore import QLocale, QMetaObject, QTranslator, Qt, QTimer, Slot
from PySide6.QtWidgets import (
    QApplication,
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
from autorun import setup as autorun_setup
from camera_panel import NO_CAMERA, CameraPanel
from processing import STATUS_ALARM

BASE_DIR = Path(__file__).parent
_HB_MUTED_STATUSES = (STATUS_ALARM, STATUS_NO_REFERENCE, "NO_CAMERA")

# Минимальная ширина панели камеры: три кнопки в ряд должны поместиться,
# когда PREVIEW_WIDTH делится между камерами.
MIN_PANEL_WIDTH = 380


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(self.tr("Camera monitor"))
        self._camera_count = max(1, int(getattr(config, "CAMERA_COUNT", 1)))
        # PREVIEW_WIDTH — суммарная ширина полей превью; при двух камерах
        # делим её, но не ниже MIN_PANEL_WIDTH, иначе окно ужимается сильнее,
        # чем нужно ради превью.
        self._panel_width = max(config.PREVIEW_WIDTH // self._camera_count, MIN_PANEL_WIDTH)
        self.setFixedSize(self._panel_width * self._camera_count + 150,
                          config.PREVIEW_HEIGHT + 330)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        # Список камер общий для всех панелей, поэтому и Refresh один.
        cameras = camera_module.list_cameras()

        top_row = QHBoxLayout()
        top_row.addStretch(1)
        self.refresh_button = QPushButton(self.tr("Refresh"))
        self.refresh_button.clicked.connect(self._on_refresh_cameras)
        top_row.addWidget(self.refresh_button)
        layout.addLayout(top_row)

        cameras_row = QHBoxLayout()
        self._panels = []
        self._statuses = [NO_CAMERA] * self._camera_count
        for slot in range(self._camera_count):
            panel = CameraPanel(slot, (self._panel_width, config.PREVIEW_HEIGHT), cameras)
            panel.status_changed.connect(self._on_panel_status)
            self._panels.append(panel)
            cameras_row.addWidget(panel)
        cameras_row.addLayout(self._build_threshold_slider())
        layout.addLayout(cameras_row)

        self._heartbeat_state = False
        self._hb_timer = QTimer(self)
        self._hb_timer.setInterval(200)
        self._hb_timer.timeout.connect(self._tick_heartbeat)
        self._hb_timer.start()

        # GPIO: связываем входные пины с действиями и инициализируем железо.
        # Колбэки зовут те же методы, что и кнопки (arm_reference /
        # request_detect у каждой панели); QueuedConnection — безопасно
        # из чужого потока gpiozero.
        gpio.set_callbacks({
            "set_reference": self._on_gpio_action("arm_reference"),
            "detect": self._on_gpio_action("request_detect"),
        })
        gpio.setup()
        gpio.set_ready(False)

    def _build_threshold_slider(self) -> QVBoxLayout:
        # Вертикальный слайдер порога тревоги (доля изменившихся пикселей).
        # Маппинг: значение слайдера 1..200 -> 0.1%..20.0% -> ratio 0.001..0.2.
        # Порог общий для всех камер — config.CHANGE_RATIO_THRESHOLD.
        slider_col = QVBoxLayout()
        slider_col.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        slider_col.addWidget(QLabel(self.tr("Alarm %")),
                             alignment=Qt.AlignmentFlag.AlignHCenter)
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
        return slider_col

    @Slot()
    def _on_refresh_cameras(self):
        self.refresh_button.setEnabled(False)
        try:
            # list_cameras() для picamera2/mindvision переоткрывает общий
            # camera.open()/close() — живые захваты это убьёт, глушим заранее.
            for panel in self._panels:
                panel.stop_capture()
            cameras = camera_module.list_cameras()
            for panel in self._panels:
                panel.reload_cameras(cameras)
        finally:
            self.refresh_button.setEnabled(True)

    def _on_panel_status(self, slot: int, status: str):
        self._statuses[slot] = status
        if status == NO_CAMERA:
            # Камера отвалилась: тревогу этой панели гасим, готовность снимаем.
            gpio.set_alarm(False)
            gpio.set_ready(False)

<<<<<<< HEAD
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
        self._heartbeat_state = True
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

        self._start_capture(self.camera_combo.currentData())

    def _tick_heartbeat(self):
        if self._current_status in _HB_MUTED_STATUSES:
            self._heartbeat_state = True
=======
    def _on_gpio_action(self, method: str):
        def _call():
            for panel in self._panels:
                QMetaObject.invokeMethod(panel, method, Qt.ConnectionType.QueuedConnection)
        return _call

    def _tick_heartbeat(self):
        # Тревога по любой из камер: светодиод не мигает.
        if STATUS_ALARM in self._statuses:
            self._heartbeat_state = False
>>>>>>> fb63d8a4fd28f6730383129a296e46da55f138c6
        else:
            self._heartbeat_state = not self._heartbeat_state
        gpio.set_output("heartbeat", self._heartbeat_state)
        print(self._heartbeat_state)

    def _update_threshold_label(self, slider_value: int):
        percent = slider_value / 10.0
        self.threshold_value_label.setText(f"{percent:.1f} %")

    @Slot(int)
    def _on_threshold_changed(self, slider_value: int):
        # Живое изменение порога: detector.compare читает
        # config.CHANGE_RATIO_THRESHOLD при каждом кадре.
        config.CHANGE_RATIO_THRESHOLD = slider_value / 1000.0
        self._update_threshold_label(slider_value)
<<<<<<< HEAD
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
        QMetaObject.invokeMethod(self._processor, "on_set_reference",
                                 Qt.ConnectionType.QueuedConnection)

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
        self.reference_label.setText(self.tr("Reference: NOT SET"))
        self.change_label.setText(self.tr("Change: -"))
        self.last_check_label.setText(self.tr("Last check: -"))
        self.trigger_label.setText(self.tr("Trigger: -"))
        self.set_reference_button.setEnabled(False)
        self.detect_button.setEnabled(False)
        self._current_status = "NO_CAMERA"
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

        self.reference_label.setText(
            self.tr("Reference: OK") if has_reference else self.tr("Reference: NOT SET")
        )
        if has_reference:
            self.change_label.setText(self.tr("Change: {} %").format(round(change_ratio * 100, 2)))
            self.last_check_label.setText(self.tr("Last check: {} ms").format(round(elapsed_ms, 1)))
        self.set_reference_button.setEnabled(True)
        self.detect_button.setEnabled(has_reference)
=======
        for panel in self._panels:
            panel.reset_confirmation()
>>>>>>> fb63d8a4fd28f6730383129a296e46da55f138c6

    def closeEvent(self, event):
        self._hb_timer.stop()
        gpio.set_output("heartbeat", False)
        gpio.set_ready(False)
        gpio.close()
        for panel in self._panels:
            panel.close_capture()
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

<<<<<<< HEAD
    gpio.setup()

=======
    # config.AUTORUN -> юнит systemd на автозапуск. Ошибки не фатальны:
    # autorun сам логирует и возвращает False.
    autorun_setup()
>>>>>>> fb63d8a4fd28f6730383129a296e46da55f138c6

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