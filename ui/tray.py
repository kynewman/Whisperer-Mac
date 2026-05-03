"""System tray icon and menu for Whisperer."""

from __future__ import annotations

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QAction, QIcon, QPainter, QColor, QPixmap
from PyQt6.QtWidgets import QSystemTrayIcon, QMenu, QWidget

from ui.app_icon import app_icon_path


class TrayIcon(QSystemTrayIcon):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._parent_window = parent
        self._paused = False
        self._status = "stopped"
        self._mode_name = "Voice"
        self._app_icon = QIcon(app_icon_path())

        self.setIcon(self._app_icon if not self._app_icon.isNull() else self._draw_icon("grey"))
        self.setToolTip("Whisperer — Stopped")

        self._menu = QMenu(parent)
        self._build_menu()
        self.setContextMenu(self._menu)

        self.activated.connect(self._on_activated)

    def _draw_icon(self, color_name: str) -> QIcon:
        size = 64
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color_map = {
            "green": QColor(20, 27, 34),
            "orange": QColor(20, 27, 34),
            "grey": QColor(20, 27, 34),
        }
        color = color_map.get(color_name, color_map["grey"])
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(246, 247, 249))
        painter.drawRoundedRect(6, 6, 52, 52, 14, 14)
        painter.setBrush(color)
        bar_width = 5
        for center, height in ((17, 9), (24, 18), (32, 30), (40, 18), (47, 9)):
            painter.drawRoundedRect(
                center - bar_width // 2,
                32 - height // 2,
                bar_width,
                height,
                bar_width // 2,
                bar_width // 2,
            )
        painter.end()
        return QIcon(pixmap)

    def _build_menu(self):
        self._menu.clear()

        self._status_action = QAction("Status: Stopped", self._menu)
        self._status_action.setEnabled(False)
        self._menu.addAction(self._status_action)

        self._mode_menu = self._menu.addMenu("Mode")
        self._refresh_mode_menu()

        self._menu.addSeparator()

        self._open_action = QAction("Open Whisperer", self._menu)
        self._open_action.triggered.connect(self._show_window)
        self._menu.addAction(self._open_action)

        self._pause_action = QAction("Pause", self._menu)
        self._pause_action.triggered.connect(self._toggle_pause)
        self._menu.addAction(self._pause_action)

        self._menu.addSeparator()

        self._quit_action = QAction("Quit", self._menu)
        self._quit_action.triggered.connect(self._quit)
        self._menu.addAction(self._quit_action)

    def _refresh_mode_menu(self):
        self._mode_menu.clear()
        try:
            from core.modes import list_modes
            for mode in list_modes(enabled_only=True):
                action = QAction(mode.name, self._mode_menu)
                action.setCheckable(True)
                action.setChecked(mode.name == self._mode_name)
                action.setEnabled(False)
                self._mode_menu.addAction(action)
        except Exception:
            action = QAction(self._mode_name, self._mode_menu)
            action.setEnabled(False)
            self._mode_menu.addAction(action)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._show_window()

    def _show_window(self):
        if self._parent_window:
            if hasattr(self._parent_window, "show_window"):
                self._parent_window.show_window()
                return
            self._parent_window.showNormal()
            self._parent_window.raise_()
            self._parent_window.activateWindow()

    def _toggle_pause(self):
        self._paused = not self._paused
        self._pause_action.setText("Resume" if self._paused else "Pause")
        if self._parent_window and hasattr(self._parent_window, "set_paused"):
            self._parent_window.set_paused(self._paused)

    def _quit(self):
        if self._parent_window and hasattr(self._parent_window, "force_quit"):
            self._parent_window.force_quit()
        elif self._parent_window:
            self._parent_window.close()

    def set_status(self, status: str):
        self._status = status.lower()
        if not self._app_icon.isNull():
            self.setIcon(self._app_icon)
        if self._status == "running" or self._status == "ready":
            self.setToolTip("Whisperer — Running")
            self._status_action.setText("Status: Running")
        elif self._status == "loading":
            self.setToolTip("Whisperer — Loading")
            self._status_action.setText("Status: Loading")
        else:
            self.setToolTip("Whisperer — Stopped")
            self._status_action.setText("Status: Stopped")

    def set_mode_name(self, name: str):
        self._mode_name = name
        self._refresh_mode_menu()
