"""Диалог правки параметров камеры (V4L2) прямо из программы.

Показывает РЕАЛЬНЫЕ контролы устройства (`v4l2-ctl --list-ctrls`), их
диапазоны и текущие значения. Значения из config.V4L2_CONTROLS
показываются рядом как подсказка и помечаются ✓ / ≠.

Apply сначала применяет, затем ПЕРЕЧИТЫВАЕТ камеру и показывает,
что реально встало, а что нет. Диалог не закрывается, пока есть
неприменённые значения — чтобы не было иллюзии «применилось».
"""

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

import config
import v4l2

log = logging.getLogger(__name__)

TYPE_LABELS = {
    "int": "int", "integer": "int", "boolean": "bool", "bitmask": "int",
    "int64": "int", "int32": "int", "ctrl_class": "class", "string": "text",
}


class CameraSettingsDialog(QDialog):
    """Параметры одной камеры. Apply: значения применены и проверены."""

    def __init__(self, camera_index, device, applied_values, parent=None):
        super().__init__(parent)
        self.camera_index = int(camera_index)
        self.device = device
        self._editors = {}
        self._config_values = dict(applied_values or {})

        self.setWindowTitle(self.tr("Camera settings: {}").format(device))
        # Тач-режим: диалог крупный, кнопки — палец.
        self.setMinimumSize(720, 620)
        self._apply_touch_style()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        header = QLabel(self.tr("Device: {}").format(device))
        header.setStyleSheet("font-size: 22px; font-weight: 600;")
        layout.addWidget(header)

        controls = v4l2.list_controls(device)
        if not controls:
            QMessageBox.warning(
                self, self.tr("Camera settings"),
                self.tr("Could not read camera controls: {}.\n"
                        "Check that v4l2-ctl is installed and the camera is free.").format(device))
            controls = {}
        else:
            layout.addWidget(self._build_form(controls))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.apply_button = QPushButton(self.tr("Apply"))
        self.apply_button.setEnabled(bool(controls))
        self.apply_button.clicked.connect(self._on_apply)
        buttons.addButton(self.apply_button, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ---------- UI ----------

    def _apply_touch_style(self):
        self.setStyleSheet("""
            * { font-size: 22px; }
            QPushButton {
                min-height: 72px;
                min-width: 140px;
                padding: 12px 20px;
                border-radius: 12px;
                border: 2px solid #888;
                background: #ffffff;
            }
            QPushButton:pressed { background: #d8d8d8; }
            QPushButton:disabled { color: #999; border-color: #ccc; }
            QSpinBox, QComboBox {
                min-height: 56px;
                padding: 6px 10px;
                font-size: 22px;
                border-radius: 8px;
                border: 2px solid #888;
            }
        """)

    def _build_form(self, controls) -> QWidget:
        form_host = QWidget()
        form = QFormLayout(form_host)
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        for name, info in controls.items():
            editor = self._build_editor(info)
            self._editors[name] = editor
            form.addRow(self._build_label(name, info), editor)

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(form_host)
        area.setMinimumSize(620, 380)
        return area

    def _build_label(self, name, info) -> QLabel:
        suffix = ""
        if info["choices"]:
            suffix = f" [{TYPE_LABELS.get(info['type'], info['type'])}]"
        elif not info["writable"]:
            suffix = f" ({self.tr('read-only')})"

        hint = ""
        want = self._config_values.get(name)
        if want is not None:
            actual = info["value"]
            if want == actual:
                hint = f"\n{self.tr('config')}: {want} ✓"
            else:
                hint = f"\n{self.tr('config')}: {want} ≠ {self.tr('actual')} {actual}"

        label = QLabel(f"{name}{suffix}{hint}")
        label.setTextFormat(Qt.TextFormat.PlainText)
        base = "font-size: 20px;"
        if hint.endswith("✓"):
            label.setStyleSheet(base + " color: #202020;")
        elif "≠" in hint:
            label.setStyleSheet(base + " color: #b03030;")
        else:
            label.setStyleSheet(base)
        return label

    def _build_editor(self, info):
        # ВАЖНО: всегда реальное значение с камеры, не из конфига.
        value = info["value"]

        if info["choices"]:
            combo = QComboBox()
            for choice_value, choice_label in info["choices"]:
                combo.addItem(f"{choice_value}: {choice_label}", userData=choice_value)
            pos = combo.findData(int(value))
            combo.setCurrentIndex(max(pos, 0))
            combo.setEnabled(info["writable"])
            return combo

        spin = QSpinBox()
        spin.setRange(int(info["min"]), int(info["max"]))
        spin.setSingleStep(max(int(info["step"]), 1))
        try:
            spin.setValue(int(value))
        except (TypeError, ValueError):
            spin.setValue(int(info["min"]))
        spin.setEnabled(info["writable"])
        return spin

    # ---------- data ----------

    def values(self) -> dict:
        out = {}
        for name, editor in self._editors.items():
            if isinstance(editor, QComboBox):
                data = editor.currentData()
                if data is not None:
                    out[name] = int(data)
            else:
                out[name] = int(editor.value())
        return out

    def _refresh_editors(self, controls):
        for name, info in controls.items():
            editor = self._editors.get(name)
            if editor is None:
                continue
            if isinstance(editor, QComboBox):
                pos = editor.findData(int(info["value"]))
                if pos >= 0:
                    editor.setCurrentIndex(pos)
            else:
                try:
                    editor.blockSignals(True)
                    editor.setValue(int(info["value"]))
                finally:
                    editor.blockSignals(False)

    # ---------- apply ----------

    def _on_apply(self):
        requested = self.values()
        if not requested:
            self.accept()
            return

        try:
            v4l2.apply_values(self.camera_index, requested)
        except Exception as exc:  # noqa: BLE001 — диалог не должен падать
            log.warning("v4l2 apply failed: %s", exc)
            QMessageBox.warning(
                self, self.tr("Camera settings"),
                self.tr("Failed to apply settings: {}").format(exc))
            return

        # Перечитываем РЕАЛЬНОЕ состояние камеры
        controls_after = v4l2.list_controls(self.device)
        if not controls_after:
            QMessageBox.warning(
                self, self.tr("Camera settings"),
                self.tr("Applied, but could not re-read camera to verify."))
            self.accept()
            return

        actual = {name: info["value"] for name, info in controls_after.items()}
        not_supported = []
        stuck = []
        for name, wanted in requested.items():
            if name not in actual:
                not_supported.append(name)
                continue
            
            actual_val = actual[name]
            
            # Читаем шаг контрола (например, 5 или 10). Если шаг не указан, берем 1.
            step = max(1, int(controls_after[name].get("step", 1)))
            
            # Если разница БОЛЬШЕ шага — только тогда это реальная ошибка.
            # Если разница 5 (5005 -> 5000), а шаг 5 — всё ок, молчим.
            if abs(actual_val - wanted) > step:
                stuck.append(f"{name}: {self.tr('asked')} {wanted}, "
                             f"{self.tr('has')} {actual_val}")

        if not_supported or stuck:
            parts = []
            if not_supported:
                parts.append(
                    self.tr("Not supported by camera:") + "\n  " + "\n  ".join(not_supported))
            if stuck:
                parts.append(
                    self.tr("Requested but not applied:") + "\n  " + "\n  ".join(stuck))
            QMessageBox.warning(self, self.tr("Camera settings"), "\n\n".join(parts))

            # Показать реальные значения, оставить диалог открытым
            self._refresh_editors(controls_after)
            return

        log.info("Camera settings applied to %s: %d control(s)", self.device, len(requested))
        self.accept()