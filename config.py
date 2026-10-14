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
CAPTURE_MAX_FPS = 30
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
