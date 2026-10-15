"""Диалог правки параметров камеры (V4L2) прямо из программы.

Показывает реальные контролы устройства (`v4l2-ctl --list-ctrls`), их
диапазоны и текущие значения, позволяет поменять и применить — без
консоли. Значения, заданные в config.V4L2_CONTROLS, подставляются
поверх текущих: видно, что именно держит программа.
"""

import logging

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

import v4l2

log = logging.getLogger(__name__)

TYPE_LABELS = {
    "int": "int", "integer": "int", "boolean": "bool", "bitmask": "int", "int64": "int",
    "int32": "int", "ctrl_class": "class", "string": "text",
}


class CameraSettingsDialog(QDialog):
    """Параметры одной камеры. Успех Apply: значения применены и сохранены."""

    def __init__(self, camera_index, device, applied_values, parent=None):
        super().__init__(parent)
        self.camera_index = int(camera_index)
        self.device = device
        self._editors = {}

        self.setWindowTitle(self.tr("Camera settings: {}").format(device))
        layout = QVBoxLayout(self)

        header = QLabel(self.tr("Device: {}").format(device))
        layout.addWidget(header)

        controls = v4l2.list_controls(device)
        if not controls:
            QMessageBox.warning(
                self, self.tr("Camera settings"),
                self.tr("Could not read camera controls: {}.\n"
                        "Check that v4l2-ctl is installed and the camera is free.").format(device))
        else:
            layout.addWidget(self._build_form(controls, applied_values))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.apply_button = QPushButton(self.tr("Apply"))
        self.apply_button.setEnabled(bool(controls))
        self.apply_button.clicked.connect(self._on_apply)
        buttons.addButton(self.apply_button, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def _build_form(self, controls, applied_values) -> QWidget:
        form_host = QWidget()
        form = QFormLayout(form_host)

        for name, info in controls.items():
            editor = self._build_editor(info, applied_values.get(name))
            self._editors[name] = editor
            # Показываем и то, что задано в конфиге, и диапазон устройства.
            label = name
            if info["choices"]:
                label = f"{name} [{TYPE_LABELS.get(info['type'], info['type'])}]"
            elif not info["writable"]:
                label = f"{name} ({self.tr('read-only')})"
            form.addRow(label, editor)

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(form_host)
        area.setMinimumSize(380, 320)
        return area

    def _build_editor(self, info, applied):
        value = applied if applied is not None else info["value"]
        if info["choices"]:
            combo = QComboBox()
            for choice_value, choice_label in info["choices"]:
                combo.addItem(f"{choice_value}: {choice_label}", userData=choice_value)
            position = combo.findData(int(value))
            combo.setCurrentIndex(max(position, 0))
            combo.setEnabled(info["writable"])
            return combo
        spin = QSpinBox()
        spin.setRange(info["min"], info["max"])
        spin.setSingleStep(max(int(info["step"]), 1))
        spin.setValue(int(value) if int(value) is not None else 0)
        spin.setEnabled(info["writable"])
        return spin

    def values(self) -> dict:
        out = {}
        for name, editor in self._editors.items():
            if isinstance(editor, QComboBox):
                out[name] = int(editor.currentData())
            else:
                out[name] = int(editor.value())
        return out

    def _on_apply(self):
        ok, errors = v4l2.apply_values(self.camera_index, self.values())
        if ok:
            self.accept()
        else:
            QMessageBox.warning(self, self.tr("Camera settings"),
                                "\n".join(errors) or self.tr("Failed to apply settings"))