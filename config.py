# Все параметры алгоритма — только здесь (см. ТЗ, раздел 16).

# Камера
CAMERA_INDEX = 0
# Сколько камер показывать одновременно: 1 или 2. Для каждой — своё поле
# превью, свой эталон, свои кнопки (в т.ч. Set reference для своей области).
# Если камер меньше, чем панелей, лишние панели просто показывают
# "Camera not available" и оживают по кнопке Refresh, когда камера появится.
CAMERA_COUNT = 1
# auto: Picamera2 если доступна (RPi), иначе OpenCV (Windows-тесты).
# Можно явно зафиксировать: "opencv" или "picamera2".
CAMERA_BACKEND = "opencv"
MV_SDK_PATH="/home/elegir/Desktop/sdk camera/demo/python_demo"

# на Windows/MSMF фиксация экспозиции роняет FPS до ~1; включать на RPi (Picamera2)
LOCK_CAMERA_PARAMS = True

# === Настройки камеры через V4L2 (v4l2-ctl, пакет v4l-utils) ===
# Те же команды, что и руками в консоли, только при открытии камеры:
#   v4l2-ctl -d /dev/video0 -c auto_exposure=1
# Ключ — индекс камеры (как в списке камер), значение — {контрол: значение}.
# Ключа для камеры нет -> её настройки не трогаем. Контрола, которого нет
# на этой камере, будет пропущен с предупреждением в лог.
# ПОРЯДОК ВАЖЕН: авторежимы (auto_exposure, white_balance_temperature_auto)
# ставятся ПЕРВЫМИ, иначе камера сразу вернёт авторежим и затрёт ручные
# экспозицию/баланс белого.
#
# Типичные контролы UVC-камер (раскомментируй нужные):
#   brightness, contrast, saturation, hue, sharpness, gamma, gain,
#   backlight_compensation, focus_absolute, focus_auto,
#   exposure_absolute, exposure_auto_priority, auto_exposure (меню),
#   white_balance_temperature, white_balance_temperature_auto,
#   power_line_frequency (меню: 1 = 50 Гц, 2 = 60 Гц — от мерцания ламп).
V4L2_APPLY_ON_OPEN = True
V4L2_CONTROLS = {
    0: {
        "auto_exposure": 1,        # 0 = Auto, 1 = Manual (фиксированная экспозиция)
        "exposure_absolute": 300,  # единицы V4L2, по 100 мкс: 300 = 30 мс
        "power_line_frequency": 1, # 1 = 50 Гц
        "white_balance_temperature_auto": 0,
        "white_balance_temperature": 4200,
        # "brightness": 0,
        # "contrast": 0,
        # "gamma": 0,
        # "saturation": 64,
        # "hue": 0,
        # "sharpness": 0,
        # "gain": 0,
        # "backlight_compensation": 0,
        # "exposure_auto_priority": 0,
        # "focus_absolute": 0,
    },
}
# Узел V4L2 для индекса камеры (индекс OpenCV = номер узла).
V4L2_DEVICE_TEMPLATE = "/dev/video{index}"
# Сюда пишутся правки из GUI-диалога; перекрывают V4L2_CONTROLS при старте.
# Удалил файл — снова действуют значения из этого файла.
V4L2_SETTINGS_FILE = "camera_settings.json"

# Ограничение выдачи сверху: не чаще 30 FPS. Самой камере FPS
# не навязываем (CAP_PROP_FPS не трогаем) — только дросселируем
# выдачу кадров: если камера медленнее, идём с её скоростью.
CAPTURE_MAX_FPS = 30
# Порядок перебора VideoCapture-бэкендов на Windows (headless-пакета достаточно,
# здесь важен именно флаг, а не замена на opencv-python).
# OPENCV_BACKENDS_PREFER = ("DSHOW", "MSMF")
OPENCV_BACKENDS_PREFER = ("V4L2")

# Предпросмотр в GUI. PREVIEW_WIDTH — суммарная ширина всех полей превью:
# при CAMERA_COUNT = 2 делится пополам (но не ниже 380 px на панель, иначе
# три кнопки в ряд не помещаются — см. MIN_PANEL_WIDTH в main.py).
PREVIEW_WIDTH = 640
PREVIEW_HEIGHT = 480

# Рабочее разрешение алгоритма
IMAGE_WIDTH = 256
IMAGE_HEIGHT = 256

# Эталон
REFERENCE_FRAMES = 5

# Сравнение
PIXEL_THRESHOLD = 25          # изменение яркости пикселя ниже — шум
MIN_AREA = 50                 # мин. площадь области (пикселей рабочего кадра)
CHANGE_RATIO_THRESHOLD = 0.01  # мин. доля изменившихся пикселей
CONFIRMATION_FRAMES = 2       # сколько подряд "изменено" = подтверждённое событие

# Предобработка
USE_GRAYSCALE = True          # переключаемо (ТЗ 5.2)
BLUR_KERNEL = 3               # Gaussian Blur, нечётное; 1 = выкл
BRIGHTNESS_TOLERANCE = 15     # компенсировать сдвиг средней яркости в этих пределах

# Отладка (ТЗ 17)
DEBUG = False
DEBUG_DIR = "debug"

# Лог
LOG_FILE = "app.log"


# === Автозапуск (systemd) ===
# True -> при старте программа ставит systemd-юнит и включает его,
# т.е. приложение само поднимается при загрузке системы.
# Нужны права root (запись в /etc/systemd/system + systemctl enable).
# Снять уже установленный автозапуск:
#   uv run python -c "import autorun; autorun.disable()"
AUTORUN = False
# DISPLAY для юнита. На Raspberry Pi OS (X11) обычно ":0".
AUTORUN_DISPLAY = ":0"


# === GPIO (Raspberry Pi 5 через gpiozero+lgpio) ===
# Выключено по умолчанию — на Windows/dev ничего не тянем.
GPIO_ENABLED = False

# Выходные пины. Роли:
#   "alarm" — активен при STATUS_ALARM (подтверждённое изменение);
#   "ready" — активен, когда задан эталон и камера работает;
#   можно завести произвольные роли и дёргать их из кода через gpio.set_output(role, True/False).
GPIO_OUTPUTS = [
    {"pin": 17, "role": "alarm"},    # по желанию — как было
    {"pin": 5,  "role": "heartbeat"},# ← мигает, пока всё ОК
]

# Входные пины. Поля:
#   pull   — "up" | "down" | None (внешняя подтяжка)
#   action — "set_reference" | "detect" | "none"
#   edge   — "falling" | "rising" (что считаем срабатыванием)
GPIO_INPUTS = [
    # {"pin": 22, "pull": "up",   "action": "set_reference", "edge": "falling"},
    # {"pin": 23, "pull": "up",   "action": "detect",        "edge": "falling"},
]

# Полярность активного уровня выходов (True: высокий уровень = активен).
GPIO_ACTIVE_HIGH = True

# Длительность импульса тревоги, мс.
#   0  — держим уровень, пока статус ALARM;
#   >0 — импульс на КАЖДЫЙ переход NORMAL -> ALARM (удобно для "звонка").
GPIO_PULSE_MS = 0