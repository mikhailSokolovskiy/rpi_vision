"""Параметры камеры через v4l2-ctl (Linux, пакет v4l-utils).

Тот же инструмент, что и в консоли руками:
    v4l2-ctl -d /dev/video0 -c auto_exposure=1
Здесь он читает список доступных контролов и применяет значения из
config.V4L2_CONTROLS (при каждом открытии камеры) и из GUI-диалога
(по кнопке "Settings..." у панели камеры).

На Windows/Linux без v4l2-ctl модуль не вызывает ошибок: available()
вернёт False, а apply_* молча ничего не сделает.
"""

import json
import logging
import re
import shutil
import subprocess
from pathlib import Path

import config

log = logging.getLogger(__name__)

TOOL = "v4l2-ctl"
RUN_TIMEOUT_S = 10

# Пример строки:
#   brightness 0x00980900 (int) : min=0 max=255 step=1 default=0 value=0 flags=inactive
#   auto_exposure 0x009a0901 (menu) : min=0 max=3 default=1 value=1
_CTRL = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9_\-]+)\s+0x[0-9a-fA-F]+\s+"
    r"\((?P<type>[^)]+)\)\s*:\s*(?P<rest>.*)$"
)
_FIELD = re.compile(r"(?P<key>min|max|step|default|value|flags)=(?P<value>-?[\w\-]+)")
# Пункты меню из --list-ctrls-menus:
#   exposure_auto (menu): min=0 max=3 default=1
#     0: Manual Mode
_MENU_HEAD = re.compile(r"^\s*(?P<name>[A-Za-z0-9_\-]+)\s+\(menu\)\s*:\s*(?P<rest>.*)$")
_MENU_ITEM = re.compile(r"^\s*(?P<value>\d+)\s*:\s*(?P<label>.+?)\s*$")

# Файл с тем, что нащёлкали в GUI: перекрывает config.V4L2_CONTROLS
# при следующем запуске. Удалил файл — вернулись значения из config.py.
SETTINGS_PATH = Path(__file__).resolve().parent / getattr(
    config, "V4L2_SETTINGS_FILE", "camera_settings.json")


def available() -> bool:
    return shutil.which(TOOL) is not None


def device_for_index(index) -> str | None:
    """Индекс камеры OpenCV -> узел V4L2 (/dev/videoN)."""
    template = getattr(config, "V4L2_DEVICE_TEMPLATE", "/dev/video{index}")
    try:
        return template.format(index=int(index))
    except (TypeError, ValueError):
        return None


def _run(args: list[str]) -> str:
    result = subprocess.run([TOOL] + args, check=True, capture_output=True,
                            text=True, timeout=RUN_TIMEOUT_S)
    return result.stdout


def _fields(rest: str) -> dict:
    out = {}
    for match in _FIELD.finditer(rest):
        key, value = match.group("key"), match.group("value")
        if key != "flags":
            try:
                out[key] = int(value)
            except ValueError:
                continue
        else:
            out[key] = value
    return out


def parse_controls(text: str) -> dict:
    """Разбор вывода `v4l2-ctl --list-ctrls`. Возвращает {имя: {...}}."""
    controls = {}
    for line in text.splitlines():
        match = _CTRL.match(line)
        if not match:
            continue
        fields = _fields(match.group("rest"))
        if "min" not in fields or "max" not in fields:
            continue
        flags = fields.get("flags", "")
        controls[match.group("name")] = {
            "name": match.group("name"),
            "type": match.group("type"),
            "min": fields["min"],
            "max": fields["max"],
            "step": fields.get("step", 1) or 1,
            "default": fields.get("default", 0),
            "value": fields.get("value", 0),
            "writable": "read-only" not in flags and "inactive" not in flags,
            "choices": [],
        }
    return controls


def parse_menus(text: str) -> dict:
    """Разбор вывода `v4l2-ctl --list-ctrls-menus` -> {имя: [(значение, метка)]}."""
    menus = {}
    current = None
    for line in text.splitlines():
        head = _MENU_HEAD.match(line)
        if head and "min" in _fields(head.group("rest")):
            current = head.group("name")
            menus.setdefault(current, [])
            continue
        item = _MENU_ITEM.match(line)
        if item and current:
            menus[current].append((int(item.group("value")), item.group("label")))
    return menus


def list_controls(device: str) -> dict:
    """Все контролы устройства с текущими значениями (пусто, если не вышло)."""
    if not available():
        return {}
    controls = {}
    try:
        controls = parse_controls(_run(["-d", device, "--list-ctrls"]))
        for name, choices in parse_menus(_run(["-d", device, "--list-ctrls-menus"])).items():
            if name in controls and choices:
                controls[name]["choices"] = choices
    except (subprocess.SubprocessError, OSError) as exc:
        log.warning("v4l2: не удалось прочитать контролы %s: %s", device, exc)
        return {}
    return controls


def set_controls(device: str, values: dict) -> tuple[bool, list[str]]:
    """Прописывает значения одной командой (порядок словаря = порядок применения).

    Возвращает (успех, список ошибок). Ошибка по одному контролу не мешает
    остальным: недоступные имена просто пропускаются с предупреждением.
    """
    if not values:
        return True, []
    args = ["-d", device]
    for name, value in values.items():
        args += ["-c", f"{name}={int(value)}"]
    try:
        _run(args)
        return True, []
    except subprocess.CalledProcessError as exc:
        message = (exc.stderr or exc.stdout or "").strip() or "v4l2-ctl вернул ошибку"
        log.warning("v4l2: %s для %s: %s", values, device, message)
        return False, [message]
    except (subprocess.SubprocessError, OSError) as exc:
        log.warning("v4l2: не удалось применить %s на %s: %s", values, device, exc)
        return False, [str(exc)]


def controls_for_index(index) -> dict:
    """Значения для камеры: из GUI-файла, иначе из config.V4L2_CONTROLS."""
    index = int(index)
    values = dict(getattr(config, "V4L2_CONTROLS", {}).get(index, {}))
    values.update(load_saved().get(str(index), {}))
    return values


def apply_for_index(index) -> bool:
    """Прописывает настройки камеры. Вызывается при каждом её открытии."""
    if not getattr(config, "V4L2_APPLY_ON_OPEN", True) or not available():
        return False
    values = controls_for_index(index)
    if not values:
        return False
    device = device_for_index(index)
    if not device:
        return False
    # auto_exposure/white_balance_temperature_auto идут первыми (порядок
    # словаря в config), иначе камера тут же вернёт авторежим и затрёт
    # ручные экспозицию/баланс белого.
    ok, errors = set_controls(device, values)
    if ok:
        log.info("v4l2: %s <- %s", device, values)
    else:
        for error in errors:
            log.warning("v4l2: %s <- %s: %s", device, values, error)
    return ok


def apply_values(index, values: dict) -> tuple[bool, list[str]]:
    """Применяет значения из GUI: сразу на камеру + в config + в файл."""
    index = int(index)
    device = device_for_index(index)
    ok, errors = set_controls(device, values) if device else (False, ["нет узла V4L2"])
    if ok:
        config.V4L2_CONTROLS.setdefault(index, {}).update(values)
        saved = load_saved()
        saved[str(index)] = dict(values)
        save_saved(saved)
    return ok, errors


def load_saved() -> dict:
    """Настройки, сохранённые из GUI: {"0": {"brightness": 10}}."""
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        log.warning("v4l2: не прочитан %s: %s", SETTINGS_PATH, exc)
        return {}


def save_saved(data: dict) -> None:
    try:
        SETTINGS_PATH.write_text(json.dumps(data, indent=2, sort_keys=True),
                                 encoding="utf-8")
        log.info("v4l2: настройки сохранены в %s", SETTINGS_PATH)
    except OSError as exc:
        log.warning("v4l2: не записан %s: %s", SETTINGS_PATH, exc)