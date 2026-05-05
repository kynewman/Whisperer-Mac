"""Small AppKit bridge for the macOS overlay blur underlay.

The app is primarily PyQt, but AppKit can render a live blurred backdrop below
the waveform. Using the Objective-C runtime directly keeps this bridge optional
and avoids adding PyObjC to the packaged app.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class QtScreenGeometry:
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class QtRect:
    x: float
    y: float
    width: float
    height: float


class _NSPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class _NSSize(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


class _NSRect(ctypes.Structure):
    _fields_ = [("origin", _NSPoint), ("size", _NSSize)]


class _ObjC:
    def __init__(self):
        objc_path = ctypes.util.find_library("objc")
        if not objc_path:
            raise RuntimeError("libobjc not found")
        self.objc = ctypes.CDLL(objc_path)
        appkit_path = ctypes.util.find_library("AppKit") or "/System/Library/Frameworks/AppKit.framework/AppKit"
        ctypes.CDLL(appkit_path)

        self.objc.objc_getClass.argtypes = [ctypes.c_char_p]
        self.objc.objc_getClass.restype = ctypes.c_void_p
        self.objc.sel_registerName.argtypes = [ctypes.c_char_p]
        self.objc.sel_registerName.restype = ctypes.c_void_p

        self._id_msg = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(
            ("objc_msgSend", self.objc)
        )
        self._id_rect_msg = ctypes.CFUNCTYPE(
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, _NSRect
        )(("objc_msgSend", self.objc))
        self._id_rect_ulong_ulong_bool_msg = ctypes.CFUNCTYPE(
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            _NSRect,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_bool,
        )(("objc_msgSend", self.objc))
        self._id_double_double_msg = ctypes.CFUNCTYPE(
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_double,
            ctypes.c_double,
        )(("objc_msgSend", self.objc))
        self._id_double_double_double_double_msg = ctypes.CFUNCTYPE(
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
        )(("objc_msgSend", self.objc))
        self._void_msg = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)(
            ("objc_msgSend", self.objc)
        )
        self._void_id_msg = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(
            ("objc_msgSend", self.objc)
        )
        self._void_bool_msg = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool)(
            ("objc_msgSend", self.objc)
        )
        self._void_long_msg = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long)(
            ("objc_msgSend", self.objc)
        )
        self._void_ulong_msg = ctypes.CFUNCTYPE(
            None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong
        )(("objc_msgSend", self.objc))
        self._void_double_msg = ctypes.CFUNCTYPE(
            None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_double
        )(("objc_msgSend", self.objc))
        self._long_msg = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)(
            ("objc_msgSend", self.objc)
        )
        self._void_rect_bool_msg = ctypes.CFUNCTYPE(
            None, ctypes.c_void_p, ctypes.c_void_p, _NSRect, ctypes.c_bool
        )(("objc_msgSend", self.objc))
        self._void_rect_msg = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, _NSRect)(
            ("objc_msgSend", self.objc)
        )
        self._bool_sel_msg = ctypes.CFUNCTYPE(
            ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
        )(("objc_msgSend", self.objc))

    def cls(self, name: str) -> int:
        return int(self.objc.objc_getClass(name.encode("utf-8")) or 0)

    def sel(self, name: str) -> int:
        return int(self.objc.sel_registerName(name.encode("utf-8")) or 0)

    def alloc_init_window(self, rect: _NSRect) -> int:
        ns_window = self.cls("NSPanel") or self.cls("NSWindow")
        if not ns_window:
            return 0
        style_mask = 128 if self.cls("NSPanel") else 0  # NSWindowStyleMaskNonactivatingPanel
        allocated = self._id_msg(ns_window, self.sel("alloc"))
        return int(
            self._id_rect_ulong_ulong_bool_msg(
                allocated,
                self.sel("initWithContentRect:styleMask:backing:defer:"),
                rect,
                style_mask,
                2,  # NSBackingStoreBuffered
                False,
            )
            or 0
        )

    def alloc_init_view(self, class_name: str, rect: _NSRect) -> int:
        ns_view = self.cls(class_name)
        if not ns_view:
            return 0
        allocated = self._id_msg(ns_view, self.sel("alloc"))
        return int(self._id_rect_msg(allocated, self.sel("initWithFrame:"), rect) or 0)

    def color_with_white(self, white: float, alpha: float) -> int:
        ns_color = self.cls("NSColor")
        if not ns_color:
            return 0
        return int(
            self._id_double_double_msg(
                ns_color,
                self.sel("colorWithWhite:alpha:"),
                float(white),
                float(alpha),
            )
            or 0
        )

    def color_with_rgb(self, red: float, green: float, blue: float, alpha: float) -> int:
        ns_color = self.cls("NSColor")
        if not ns_color:
            return 0
        return int(
            self._id_double_double_double_double_msg(
                ns_color,
                self.sel("colorWithCalibratedRed:green:blue:alpha:"),
                float(red),
                float(green),
                float(blue),
                float(alpha),
            )
            or 0
        )

    def clear_color(self) -> int:
        ns_color = self.cls("NSColor")
        if not ns_color:
            return 0
        return int(self._id_msg(ns_color, self.sel("clearColor")) or 0)

    def send_void(self, obj: int, selector: str):
        if obj:
            self._void_msg(obj, self.sel(selector))

    def send_id(self, obj: int, selector: str, value: int | None):
        if obj:
            self._void_id_msg(obj, self.sel(selector), int(value or 0))

    def send_bool(self, obj: int, selector: str, value: bool):
        if obj:
            self._void_bool_msg(obj, self.sel(selector), bool(value))

    def send_long(self, obj: int, selector: str, value: int):
        if obj:
            self._void_long_msg(obj, self.sel(selector), int(value))

    def send_ulong(self, obj: int, selector: str, value: int):
        if obj:
            self._void_ulong_msg(obj, self.sel(selector), int(value))

    def send_double(self, obj: int, selector: str, value: float):
        if obj:
            self._void_double_msg(obj, self.sel(selector), float(value))

    def send_rect_bool(self, obj: int, selector: str, rect: _NSRect, value: bool):
        if obj:
            self._void_rect_bool_msg(obj, self.sel(selector), rect, bool(value))

    def send_rect(self, obj: int, selector: str, rect: _NSRect):
        if obj:
            self._void_rect_msg(obj, self.sel(selector), rect)

    def responds_to(self, obj: int, selector: str) -> bool:
        if not obj:
            return False
        return bool(self._bool_sel_msg(obj, self.sel("respondsToSelector:"), self.sel(selector)))

    def send_id_return(self, obj: int, selector: str) -> int:
        if not obj or not self.responds_to(obj, selector):
            return 0
        return int(self._id_msg(obj, self.sel(selector)) or 0)

    def send_long_return(self, obj: int, selector: str) -> int:
        if not obj or not self.responds_to(obj, selector):
            return 0
        return int(self._long_msg(obj, self.sel(selector)) or 0)


def _make_ns_rect(x: float, y: float, width: float, height: float) -> _NSRect:
    return _NSRect(_NSPoint(float(x), float(y)), _NSSize(float(width), float(height)))


_INPUT_OBJC: _ObjC | None = None
_MOUSE_APPLICATION_SERVICES = None
GLASS_UNDERLAY_LEVEL = 2
QT_OVERLAY_LEVEL = 3


def set_macos_window_ignores_mouse_events(native_handle: int, ignores: bool) -> bool:
    """Set ``ignoresMouseEvents`` on a Qt-created AppKit window.

    Qt's ``WA_TransparentForMouseEvents`` can still leave a top-level overlay
    in the native hit-test path on macOS. This reaches the owning NSWindow and
    marks it transparent to mouse input while dictation is active.
    """
    if sys.platform != "darwin" or not native_handle:
        return False
    try:
        global _INPUT_OBJC
        if _INPUT_OBJC is None:
            _INPUT_OBJC = _ObjC()
        objc = _INPUT_OBJC
        obj = int(native_handle)
        window = objc.send_id_return(obj, "window") or obj
        if not objc.responds_to(window, "setIgnoresMouseEvents:"):
            return False
        objc.send_bool(window, "setIgnoresMouseEvents:", ignores)
        return True
    except Exception:
        return False


def set_macos_app_activation_policy(*, accessory: bool = True) -> bool:
    """Hide the current process from Dock/Cmd-Tab when it only owns overlays."""
    if sys.platform != "darwin":
        return False
    try:
        global _INPUT_OBJC
        if _INPUT_OBJC is None:
            _INPUT_OBJC = _ObjC()
        objc = _INPUT_OBJC
        ns_app = objc.cls("NSApplication")
        if not ns_app:
            return False
        app = int(objc._id_msg(ns_app, objc.sel("sharedApplication")) or 0)
        if not app or not objc.responds_to(app, "setActivationPolicy:"):
            return False
        objc.send_long(app, "setActivationPolicy:", 1 if accessory else 0)
        return True
    except Exception:
        return False


def configure_macos_overlay_window(native_handle: int, *, order_front: bool = False) -> bool:
    """Keep a Qt-created overlay visible and non-activating across app changes."""
    if sys.platform != "darwin" or not native_handle:
        return False
    try:
        global _INPUT_OBJC
        if _INPUT_OBJC is None:
            _INPUT_OBJC = _ObjC()
        objc = _INPUT_OBJC
        obj = int(native_handle)
        window = objc.send_id_return(obj, "window") or obj
        if not window:
            return False
        if objc.responds_to(window, "setHidesOnDeactivate:"):
            objc.send_bool(window, "setHidesOnDeactivate:", False)
        if objc.responds_to(window, "setCanHide:"):
            objc.send_bool(window, "setCanHide:", False)
        if objc.responds_to(window, "setReleasedWhenClosed:"):
            objc.send_bool(window, "setReleasedWhenClosed:", False)
        if objc.responds_to(window, "setLevel:"):
            objc.send_long(window, "setLevel:", QT_OVERLAY_LEVEL)
        if objc.responds_to(window, "setCollectionBehavior:"):
            objc.send_ulong(window, "setCollectionBehavior:", 1 | 16 | 64 | 256)
        if order_front:
            if objc.responds_to(window, "orderFrontRegardless"):
                objc.send_void(window, "orderFrontRegardless")
            elif objc.responds_to(window, "orderFront:"):
                objc.send_id(window, "orderFront:", None)
        return True
    except Exception:
        return False


def macos_window_number(native_handle: int) -> int:
    """Return the AppKit window number for a Qt-created native window/view."""
    if sys.platform != "darwin" or not native_handle:
        return 0
    try:
        global _INPUT_OBJC
        if _INPUT_OBJC is None:
            _INPUT_OBJC = _ObjC()
        objc = _INPUT_OBJC
        obj = int(native_handle)
        window = objc.send_id_return(obj, "window") or obj
        return objc.send_long_return(window, "windowNumber")
    except Exception:
        return 0


def macos_left_mouse_button_down() -> bool:
    """Return the global left mouse button state from Quartz."""
    if sys.platform != "darwin":
        return False
    try:
        global _MOUSE_APPLICATION_SERVICES
        if _MOUSE_APPLICATION_SERVICES is None:
            services_path = (
                ctypes.util.find_library("ApplicationServices")
                or "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
            )
            services = ctypes.CDLL(services_path)
            services.CGEventSourceButtonState.argtypes = [ctypes.c_int, ctypes.c_uint32]
            services.CGEventSourceButtonState.restype = ctypes.c_bool
            _MOUSE_APPLICATION_SERVICES = services
        # kCGEventSourceStateHIDSystemState = 1, kCGMouseButtonLeft = 0.
        return bool(_MOUSE_APPLICATION_SERVICES.CGEventSourceButtonState(1, 0))
    except Exception:
        return False


class MacLiquidGlassUnderlay:
    """A borderless AppKit window that renders a live native blur pill."""

    def __init__(self):
        self._objc: _ObjC | None = None
        self._window = 0
        self._glass = 0
        self._scrim = 0
        self._visible = False
        self._available = False
        if sys.platform != "darwin":
            return
        try:
            objc = _ObjC()
            effect_class = "NSVisualEffectView" if objc.cls("NSVisualEffectView") else "NSGlassEffectView"
            if not objc.cls(effect_class):
                return
            rect = _make_ns_rect(0, 0, 72, 30)
            window = objc.alloc_init_window(rect)
            glass = objc.alloc_init_view(effect_class, rect)
            scrim = objc.alloc_init_view("NSView", rect)
            if not window or not glass or not scrim:
                return

            # Use a real AppKit blur material, then add only a light scrim so
            # the Gaussian-looking backdrop remains visible behind the waveform.
            tint = objc.color_with_rgb(0.010, 0.012, 0.018, 0.10)
            scrim_color = objc.color_with_rgb(0.0, 0.0, 0.0, 0.28)
            clear = objc.clear_color()
            objc.send_bool(window, "setOpaque:", False)
            objc.send_id(window, "setBackgroundColor:", clear)
            objc.send_bool(window, "setHasShadow:", False)
            objc.send_bool(window, "setIgnoresMouseEvents:", True)
            objc.send_bool(window, "setReleasedWhenClosed:", False)
            # Keep the native glass strictly below the transparent Qt window
            # that paints the waveform. Equal-level panels can reorder, which
            # hides every animation behind the gray/glass pill.
            objc.send_long(window, "setLevel:", GLASS_UNDERLAY_LEVEL)
            objc.send_ulong(window, "setCollectionBehavior:", 1 | 16 | 256)
            objc.send_double(window, "setAlphaValue:", 0.0)

            if effect_class == "NSVisualEffectView":
                if objc.responds_to(glass, "setMaterial:"):
                    objc.send_long(glass, "setMaterial:", 13)  # HUD/window-like dark blur.
                if objc.responds_to(glass, "setBlendingMode:"):
                    objc.send_long(glass, "setBlendingMode:", 0)  # Behind window.
                if objc.responds_to(glass, "setState:"):
                    objc.send_long(glass, "setState:", 1)  # Active.
                if objc.responds_to(glass, "setEmphasized:"):
                    objc.send_bool(glass, "setEmphasized:", False)
            else:
                if objc.responds_to(glass, "setStyle:"):
                    objc.send_long(glass, "setStyle:", 1)  # NSGlassEffectView.Style.clear
                if objc.responds_to(glass, "setTintColor:"):
                    objc.send_id(glass, "setTintColor:", tint)

            self._set_view_corner_radius(objc, glass, 15.0)
            objc.send_ulong(glass, "setAutoresizingMask:", 2 | 16)
            objc.send_bool(scrim, "setWantsLayer:", True)
            self._set_view_background_color(objc, scrim, scrim_color)
            self._set_view_corner_radius(objc, scrim, 15.0)
            objc.send_ulong(scrim, "setAutoresizingMask:", 2 | 16)
            if objc.responds_to(glass, "setContentView:"):
                objc.send_id(glass, "setContentView:", scrim)
            elif objc.responds_to(glass, "addSubview:"):
                objc.send_id(glass, "addSubview:", scrim)
            objc.send_id(window, "setContentView:", glass)

            self._objc = objc
            self._window = window
            self._glass = glass
            self._scrim = scrim
            self._available = True
        except Exception:
            self._objc = None
            self._window = 0
            self._glass = 0
            self._scrim = 0
            self._available = False

    @staticmethod
    def _set_view_background_color(objc: _ObjC, view: int, color: int):
        if objc.responds_to(view, "setBackgroundColor:"):
            objc.send_id(view, "setBackgroundColor:", color)
            return
        objc.send_bool(view, "setWantsLayer:", True)
        layer = objc.send_id_return(view, "layer")
        cg_color = objc.send_id_return(color, "CGColor")
        if layer and cg_color and objc.responds_to(layer, "setBackgroundColor:"):
            objc.send_id(layer, "setBackgroundColor:", cg_color)

    @staticmethod
    def _set_view_corner_radius(objc: _ObjC, view: int, radius: float):
        if objc.responds_to(view, "setCornerRadius:"):
            objc.send_double(view, "setCornerRadius:", radius)
            return
        objc.send_bool(view, "setWantsLayer:", True)
        layer = objc.send_id_return(view, "layer")
        if layer and objc.responds_to(layer, "setCornerRadius:"):
            objc.send_double(layer, "setCornerRadius:", radius)
        if layer and objc.responds_to(layer, "setMasksToBounds:"):
            objc.send_bool(layer, "setMasksToBounds:", True)

    def is_available(self) -> bool:
        return self._available

    def set_alpha(self, alpha: float):
        if not self._available or not self._objc:
            return
        self._objc.send_double(self._window, "setAlphaValue:", max(0.0, min(1.0, float(alpha))))

    def set_frame(
        self,
        rect: QtRect,
        screen: QtScreenGeometry,
        *,
        corner_radius: float,
        visible: bool,
        order_front: bool = False,
    ):
        if not self._available or not self._objc:
            return
        if rect.width <= 1 or rect.height <= 1:
            visible = False

        cocoa_x = rect.x
        cocoa_y = screen.y + screen.height - (rect.y - screen.y) - rect.height
        frame = _make_ns_rect(cocoa_x, cocoa_y, rect.width, rect.height)
        content = _make_ns_rect(0, 0, rect.width, rect.height)
        self._objc.send_rect_bool(self._window, "setFrame:display:", frame, True)
        self._objc.send_rect(self._glass, "setFrame:", content)
        self._objc.send_rect(self._scrim, "setFrame:", content)
        self._set_view_corner_radius(self._objc, self._glass, corner_radius)
        self._set_view_corner_radius(self._objc, self._scrim, corner_radius)

        if visible:
            if order_front or not self._visible:
                if self._objc.responds_to(self._window, "orderFrontRegardless"):
                    self._objc.send_void(self._window, "orderFrontRegardless")
                else:
                    self._objc.send_id(self._window, "orderFront:", None)
            self._visible = True
        else:
            self.hide()

    def hide(self):
        if not self._available or not self._objc:
            return
        self._objc.send_id(self._window, "orderOut:", None)
        self._visible = False

    def close(self):
        if not self._available or not self._objc:
            return
        self.hide()
        self._objc.send_void(self._window, "close")
        self._window = 0
        self._glass = 0
        self._scrim = 0
        self._available = False
