import cv2

import config


def crop_roi(frame, roi):
    """Вырезает область интереса из сырого кадра.

    roi — доли кадра (x0, y0, x1, y1) или None (= весь кадр). Хранится в
    панели камеры, а не в config: у каждой камеры своя область.
    Вызывается на всех путях, где кадр идёт в алгоритм: и в превью
    (capture.py), и при прямом capture_frame() из controller — иначе
    эталон и текущий кадр оказались бы в разной геометрии.
    Кропим до resize — так в выбранную область попадает максимум деталей.
    """
    if not roi:
        return frame
    height, width = frame.shape[:2]
    # Правая/нижняя границы — исключающие (срез их не включает),
    # поэтому их можно брать вплотную до размера кадра.
    x0 = min(max(int(roi[0] * width), 0), width - 1)
    y0 = min(max(int(roi[1] * height), 0), height - 1)
    x1 = min(max(int(roi[2] * width), 0), width)
    y1 = min(max(int(roi[3] * height), 0), height)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return frame
    return frame[y0:y1, x0:x1]


def preprocess(frame):
    """Приводит сырой BGR-кадр к рабочему виду: resize -> gray (опц.) -> blur."""
    out = cv2.resize(frame, (config.IMAGE_WIDTH, config.IMAGE_HEIGHT),
                     interpolation=cv2.INTER_AREA)
    if config.USE_GRAYSCALE:
        out = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
    if config.BLUR_KERNEL > 1:
        k = config.BLUR_KERNEL
        out = cv2.GaussianBlur(out, (k, k), 0)
    return out


def normalize_brightness(current, reference):
    """Компенсирует небольшой глобальный сдвиг яркости (ТЗ 11.2)."""
    delta = float(reference.mean()) - float(current.mean())
    if 0 < abs(delta) <= config.BRIGHTNESS_TOLERANCE:
        current = cv2.add(current, int(round(delta)))
    return current
