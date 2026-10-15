# Все параметры алгоритма — только здесь (см. ТЗ, раздел 16).

# Камера
CAMERA_INDEX = 0
# auto: Picamera2 если доступна (RPi), иначе OpenCV (Windows-тесты).
# Можно явно зафиксировать: "opencv" или "picamera2".
CAMERA_BACKEND = "opencv"
MV_SDK_PATH="/home/elegir/Desktop/sdk camera/demo/python_demo"

# на Windows/MSMF фиксация экспозиции роняет FPS до ~1; включать на RPi (Picamera2)
LOCK_CAMERA_PARAMS = True

# Ограничение выдачи сверху: не чаще 30 FPS. Самой камере FPS
# не навязываем (CAP_PROP_FPS не трогаем) — только дросселируем
# выдачу кадров: если камера медленнее, идём с её скоростью.
CAPTURE_MAX_FPS = 60
# Порядок перебора VideoCapture-бэкендов на Windows (headless-пакета достаточно,
# здесь важен именно флаг, а не замена на opencv-python).
# OPENCV_BACKENDS_PREFER = ("DSHOW", "MSMF")
OPENCV_BACKENDS_PREFER = ("V4L2")

# Предпросмотр в GUI
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


# === GPIO (Raspberry Pi 5 через gpiozero+lgpio) ===
# Выключено по умолчанию — на Windows/dev ничего не тянем.
GPIO_ENABLED = True

# Выходные пины. Роли:
#   "alarm" — активен при STATUS_ALARM (подтверждённое изменение);
#   "ready" — активен, когда задан эталон и камера работает;
#   можно завести произвольные роли и дёргать их из кода через gpio.set_output(role, True/False).
GPIO_OUTPUTS = [
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