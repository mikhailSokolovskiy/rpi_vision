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

    def _on_gpio_action(self, method: str):
        def _call():
            for panel in self._panels:
                QMetaObject.invokeMethod(panel, method, Qt.ConnectionType.QueuedConnection)
        return _call

    def _tick_heartbeat(self):
        # Тревога по любой из камер: светодиод не мигает.
        if STATUS_ALARM in self._statuses:
            self._heartbeat_state = False
        else:
            self._heartbeat_state = not self._heartbeat_state
        gpio.set_output("heartbeat", self._heartbeat_state)

    def _update_threshold_label(self, slider_value: int):
        percent = slider_value / 10.0
        self.threshold_value_label.setText(f"{percent:.1f} %")

    @Slot(int)
    def _on_threshold_changed(self, slider_value: int):
        # Живое изменение порога: detector.compare читает
        # config.CHANGE_RATIO_THRESHOLD при каждом кадре.
        config.CHANGE_RATIO_THRESHOLD = slider_value / 1000.0
        self._update_threshold_label(slider_value)
        for panel in self._panels:
            panel.reset_confirmation()

    def closeEvent(self, event):
        self._hb_timer.stop()
        gpio.set_output("heartbeat", False)
        gpio.set_alarm(False)
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