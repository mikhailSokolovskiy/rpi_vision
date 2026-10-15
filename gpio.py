import logging
import threading

import config

log = logging.getLogger(__name__)

try:
    from gpiozero import DigitalOutputDevice, DigitalInputDevice
    _GPIOZERO_AVAILABLE = True
except Exception as _exc:  # noqa: BLE001
    _GPIOZERO_AVAILABLE = False
    _GPIOZERO_IMPORT_ERROR = _exc


class GPIOManager:
    """Обёртка над gpiozero.

    - Выходы адресуются по роли ("alarm", "ready", ...).
    - Входы вешают колбэки на GPIO-события и вызывают функции из
      self._callbacks (задаются снаружи, обычно через MainWindow).
    - Все вызовы set_* потокобезопасны.
    """

    def __init__(self):
        self._outputs: dict[str, DigitalOutputDevice] = {}
        self._inputs: dict[int, DigitalInputDevice] = {}
        self._pulse_timers: dict[str, threading.Timer] = {}
        self._callbacks: dict[str, callable] = {}
        self._alarm_state = False
        self._lock = threading.Lock()

    # --- публичный API ----------------------------------------------------

    def set_callbacks(self, callbacks: dict):
        """callbacks: {"set_reference": fn, "detect": fn, ...} — по action."""
        self._callbacks = dict(callbacks or {})

    def setup(self):
        with self._lock:
            self._release_locked()
            self._alarm_state = False
            if not getattr(config, "GPIO_ENABLED", False):
                log.info("GPIO disabled (config.GPIO_ENABLED=False)")
                return
            if not _GPIOZERO_AVAILABLE:
                log.warning("gpiozero недоступен: %s", _GPIOZERO_IMPORT_ERROR)
                return
            self._setup_outputs_locked()
            self._setup_inputs_locked()

    def set_output(self, role: str, value: bool) -> bool:
        with self._lock:
            dev = self._outputs.get(role)
            if dev is None:
                return False
            try:
                dev.on() if value else dev.off()
                return True
            except Exception as exc:  # noqa: BLE001
                log.warning("GPIO set_output role=%s value=%s failed: %s", role, value, exc)
                return False

    def pulse_output(self, role: str, duration_ms: int) -> bool:
        with self._lock:
            dev = self._outputs.get(role)
            if dev is None:
                return False
            old = self._pulse_timers.pop(role, None)
            if old is not None:
                old.cancel()
            try:
                dev.on()
            except Exception as exc:  # noqa: BLE001
                log.warning("GPIO pulse role=%s failed: %s", role, exc)
                return False
            timer = threading.Timer(duration_ms / 1000.0, self._pulse_off, args=(role,))
            timer.daemon = True
            self._pulse_timers[role] = timer
            timer.start()
            return True

    def set_alarm(self, active: bool):
        """Дёргается из GUI на каждый кадр. С учётом GPIO_PULSE_MS."""
        pulse_ms = int(getattr(config, "GPIO_PULSE_MS", 0))
        if pulse_ms > 0:
            # импульс только на переходе False -> True
            if active and not self._alarm_state:
                self.pulse_output("alarm", pulse_ms)
            self._alarm_state = active
        else:
            self.set_output("alarm", active)

    def set_ready(self, active: bool):
        self.set_output("ready", active)

    def read_input(self, pin: int):
        with self._lock:
            dev = self._inputs.get(int(pin))
            if dev is None:
                return None
            try:
                return bool(dev.value)
            except Exception:  # noqa: BLE001
                return None

    def close(self):
        with self._lock:
            self._release_locked()

    # --- внутреннее -------------------------------------------------------

    def _setup_outputs_locked(self):
        active_high = bool(getattr(config, "GPIO_ACTIVE_HIGH", True))
        for entry in getattr(config, "GPIO_OUTPUTS", []) or []:
            try:
                pin = int(entry["pin"])
                role = str(entry.get("role", "output"))
                dev = DigitalOutputDevice(pin, active_high=active_high,
                                          initial_value=True)
                self._outputs[role] = dev
                log.info("GPIO output pin=%s role=%s active_high=%s", pin, role, active_high)
            except Exception as exc:  # noqa: BLE001
                log.warning("GPIO output setup failed (%s): %s", entry, exc)

    def _setup_inputs_locked(self):
        for entry in getattr(config, "GPIO_INPUTS", []) or []:
            try:
                pin = int(entry["pin"])
                pull = entry.get("pull", "up")
                action = str(entry.get("action", "none"))
                edge = str(entry.get("edge", "falling" if pull == "up" else "rising"))
                pull_up = True if pull == "up" else False
                dev = DigitalInputDevice(pin, pull_up=pull_up, bounce_time=0.05)
                if action != "none":
                    cb = self._make_input_callback(action, pin)
                    if edge == "rising":
                        dev.when_activated = cb
                    else:  # falling
                        dev.when_deactivated = cb
                self._inputs[pin] = dev
                log.info("GPIO input pin=%s pull=%s action=%s edge=%s", pin, pull, action, edge)
            except Exception as exc:  # noqa: BLE001
                log.warning("GPIO input setup failed (%s): %s", entry, exc)

    def _make_input_callback(self, action: str, pin: int):
        def _cb():
            fn = self._callbacks.get(action)
            if fn is None:
                log.debug("GPIO input pin=%s action=%s has no callback", pin, action)
                return
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                log.warning("GPIO input pin=%s action=%s callback failed: %s",
                            pin, action, exc)
        return _cb

    def _pulse_off(self, role: str):
        with self._lock:
            self._pulse_timers.pop(role, None)
            dev = self._outputs.get(role)
            if dev is not None:
                try:
                    dev.off()
                except Exception:  # noqa: BLE001
                    pass

    def _release_locked(self):
        for timer in self._pulse_timers.values():
            try:
                timer.cancel()
            except Exception:  # noqa: BLE001
                pass
        self._pulse_timers.clear()
        for dev in list(self._outputs.values()) + list(self._inputs.values()):
            try:
                dev.close()
            except Exception:  # noqa: BLE001
                pass
        self._outputs.clear()
        self._inputs.clear()


gpio = GPIOManager()