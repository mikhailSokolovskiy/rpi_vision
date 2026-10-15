"""Автозапуск приложения при загрузке системы через systemd.

Включается флагом config.AUTORUN: при старте программа ставит
/etc/systemd/system/rpi-vision.service и делает systemctl enable.
Юнит намеренно описывает текущий запуск (тот же python и тот же каталог),
поэтому "переустановить" можно просто перезапустив программу.

Сразу сервис не стартует — приложение уже запущено, юнит нужен
для следующей загрузки. Снять автозапуск: autorun.disable().
"""

import getpass
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

import config

log = logging.getLogger(__name__)

UNIT_NAME = "rpi-vision.service"
UNIT_PATH = Path("/etc/systemd/system") / UNIT_NAME
APP_PATH = Path(__file__).resolve().parent / "main.py"


def _systemd_available() -> bool:
    return os.name == "posix" and shutil.which("systemctl") is not None


def _sudo() -> list[str]:
    """Префикс sudo, если мы не root. -n: без интерактивного пароля."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return []
    return ["sudo", "-n"]


def _run(cmd: list[str]) -> str:
    """Запускает команду, возвращает stdout; при ошибке кидает CalledProcessError."""
    result = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=30)
    return result.stdout.strip()


def _target_user() -> str:
    """Пользователь, от имени которого должен бежать сервис.

    При запуске через `sudo python main.py` getpass.getuser() вернёт
    'root', а нужен тот, кто вызвал sudo — он и есть владелец X-сессии.
    """
    return (
        os.environ.get("SUDO_USER")
        or os.environ.get("USER")
        or getpass.getuser()
    )


def render_unit() -> str:
    display = os.environ.get("DISPLAY") or getattr(config, "AUTORUN_DISPLAY", ":0")
    user = _target_user()
    home = Path("/home") / user if user != "root" else Path("/root")
    lines = [
        "[Unit]",
        "Description=rpi-vision camera monitor",
        "After=graphical.target",
        "Wants=graphical.target",
        "",
        "[Service]",
        "Type=simple",
        f"User={user}",
        f"Group={user}",
        f"WorkingDirectory={APP_PATH.parent}",
        f"ExecStart={sys.executable} {APP_PATH}",
        "Restart=on-failure",
        "RestartSec=5",
        f"Environment=DISPLAY={display}",
        f"Environment=XAUTHORITY={home}/.Xauthority",
        f"Environment=HOME={home}",
        "",
        "[Install]",
        "WantedBy=graphical.target",
        "",
    ]
    return "\n".join(lines)


def is_enabled() -> bool:
    if not _systemd_available():
        return False
    try:
        return _run(["systemctl", "is-enabled", UNIT_NAME]) == "enabled"
    except (subprocess.SubprocessError, OSError):
        return False


def install() -> bool:
    """Ставит юнит и включает автозапуск. True -> автозапуск включён."""
    if not _systemd_available():
        log.warning("Autorun: systemd не найден, автозапуск не настроен")
        return False
    try:
        content = render_unit()
        current = UNIT_PATH.read_text(encoding="utf-8") if UNIT_PATH.exists() else None
        if current != content:
            UNIT_PATH.write_text(content, encoding="utf-8")
            _run(_sudo() + ["systemctl", "daemon-reload"])
            log.info("Autorun: unit %s записан в %s", UNIT_NAME, UNIT_PATH)
        if is_enabled():
            log.info("Autorun: %s уже включён", UNIT_NAME)
            return True
        _run(_sudo() + ["systemctl", "enable", UNIT_NAME])
        log.info("Autorun: %s включён (запуск при загрузке системы)", UNIT_NAME)
        return True
    except PermissionError:
        log.error("Autorun: нет прав на %s — перезапусти программу с sudo", UNIT_PATH)
    except subprocess.CalledProcessError as exc:
        log.error("Autorun: %s не сработала: %s", " ".join(exc.cmd),
                  (exc.stderr or exc.stdout or "").strip())
    except Exception as exc:  # noqa: BLE001 — автозапуск не должен ронять программу
        log.error("Autorun: непредвиденная ошибка: %s", exc)
    return False


def disable() -> bool:
    """Выключает автозапуск и удаляет юнит. True -> автозапуск снят."""
    if not _systemd_available():
        log.warning("Autorun: systemd не найден")
        return False
    try:
        try:
            _run(_sudo() + ["systemctl", "disable", UNIT_NAME])
        except subprocess.CalledProcessError:
            pass  # юнит мог быть и не включён — это не ошибка
        if UNIT_PATH.exists():
            UNIT_PATH.unlink()
            _run(_sudo() + ["systemctl", "daemon-reload"])
        log.info("Autorun: %s выключен", UNIT_NAME)
        return True
    except PermissionError:
        log.error("Autorun: нет прав на %s — запусти с sudo", UNIT_PATH)
    except subprocess.CalledProcessError as exc:
        log.error("Autorun: %s не сработала: %s", " ".join(exc.cmd),
                  (exc.stderr or exc.stdout or "").strip())
    except Exception as exc:  # noqa: BLE001
        log.error("Autorun: непредвиденная ошибка: %s", exc)
    return False


def setup() -> bool:
    """Точка входа из main(): ставит/обновляет юнит, если config.AUTORUN."""
    if not getattr(config, "AUTORUN", False):
        return False
    return install()