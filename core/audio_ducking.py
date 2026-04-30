"""System-wide Windows speaker audio ducking during recording."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from uuid import UUID


HRESULT = ctypes.c_long
LPVOID = ctypes.c_void_p
CLSCTX_ALL = 0x17
DEVICE_STATE_ACTIVE = 0x1
ERENDER = 0
EMULTIMEDIA = 1


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    def __init__(self, value: str):
        uuid = UUID(value)
        data = uuid.bytes_le
        super().__init__(
            int.from_bytes(data[0:4], "little"),
            int.from_bytes(data[4:6], "little"),
            int.from_bytes(data[6:8], "little"),
            (ctypes.c_ubyte * 8).from_buffer_copy(data[8:16]),
        )


class IMMDeviceEnumerator(ctypes.Structure):
    pass


class IMMDevice(ctypes.Structure):
    pass


class IAudioEndpointVolume(ctypes.Structure):
    pass


LP_IMMDeviceEnumerator = ctypes.POINTER(IMMDeviceEnumerator)
LP_IMMDevice = ctypes.POINTER(IMMDevice)
LP_IAudioEndpointVolume = ctypes.POINTER(IAudioEndpointVolume)


class IMMDeviceEnumeratorVtbl(ctypes.Structure):
    _fields_ = [
        ("QueryInterface", ctypes.WINFUNCTYPE(HRESULT, LP_IMMDeviceEnumerator, ctypes.POINTER(GUID), ctypes.POINTER(LPVOID))),
        ("AddRef", ctypes.WINFUNCTYPE(wintypes.ULONG, LP_IMMDeviceEnumerator)),
        ("Release", ctypes.WINFUNCTYPE(wintypes.ULONG, LP_IMMDeviceEnumerator)),
        ("EnumAudioEndpoints", ctypes.WINFUNCTYPE(HRESULT, LP_IMMDeviceEnumerator, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(LPVOID))),
        ("GetDefaultAudioEndpoint", ctypes.WINFUNCTYPE(HRESULT, LP_IMMDeviceEnumerator, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(LP_IMMDevice))),
        ("GetDevice", ctypes.WINFUNCTYPE(HRESULT, LP_IMMDeviceEnumerator, wintypes.LPCWSTR, ctypes.POINTER(LP_IMMDevice))),
        ("RegisterEndpointNotificationCallback", ctypes.WINFUNCTYPE(HRESULT, LP_IMMDeviceEnumerator, LPVOID)),
        ("UnregisterEndpointNotificationCallback", ctypes.WINFUNCTYPE(HRESULT, LP_IMMDeviceEnumerator, LPVOID)),
    ]


class IMMDeviceVtbl(ctypes.Structure):
    _fields_ = [
        ("QueryInterface", ctypes.WINFUNCTYPE(HRESULT, LP_IMMDevice, ctypes.POINTER(GUID), ctypes.POINTER(LPVOID))),
        ("AddRef", ctypes.WINFUNCTYPE(wintypes.ULONG, LP_IMMDevice)),
        ("Release", ctypes.WINFUNCTYPE(wintypes.ULONG, LP_IMMDevice)),
        ("Activate", ctypes.WINFUNCTYPE(HRESULT, LP_IMMDevice, ctypes.POINTER(GUID), wintypes.DWORD, LPVOID, ctypes.POINTER(LPVOID))),
        ("OpenPropertyStore", ctypes.WINFUNCTYPE(HRESULT, LP_IMMDevice, wintypes.DWORD, ctypes.POINTER(LPVOID))),
        ("GetId", ctypes.WINFUNCTYPE(HRESULT, LP_IMMDevice, ctypes.POINTER(wintypes.LPWSTR))),
        ("GetState", ctypes.WINFUNCTYPE(HRESULT, LP_IMMDevice, ctypes.POINTER(wintypes.DWORD))),
    ]


class IAudioEndpointVolumeVtbl(ctypes.Structure):
    _fields_ = [
        ("QueryInterface", ctypes.WINFUNCTYPE(HRESULT, LP_IAudioEndpointVolume, ctypes.POINTER(GUID), ctypes.POINTER(LPVOID))),
        ("AddRef", ctypes.WINFUNCTYPE(wintypes.ULONG, LP_IAudioEndpointVolume)),
        ("Release", ctypes.WINFUNCTYPE(wintypes.ULONG, LP_IAudioEndpointVolume)),
        ("RegisterControlChangeNotify", ctypes.WINFUNCTYPE(HRESULT, LP_IAudioEndpointVolume, LPVOID)),
        ("UnregisterControlChangeNotify", ctypes.WINFUNCTYPE(HRESULT, LP_IAudioEndpointVolume, LPVOID)),
        ("GetChannelCount", ctypes.WINFUNCTYPE(HRESULT, LP_IAudioEndpointVolume, ctypes.POINTER(wintypes.UINT))),
        ("SetMasterVolumeLevel", ctypes.WINFUNCTYPE(HRESULT, LP_IAudioEndpointVolume, ctypes.c_float, ctypes.POINTER(GUID))),
        ("SetMasterVolumeLevelScalar", ctypes.WINFUNCTYPE(HRESULT, LP_IAudioEndpointVolume, ctypes.c_float, ctypes.POINTER(GUID))),
        ("GetMasterVolumeLevel", ctypes.WINFUNCTYPE(HRESULT, LP_IAudioEndpointVolume, ctypes.POINTER(ctypes.c_float))),
        ("GetMasterVolumeLevelScalar", ctypes.WINFUNCTYPE(HRESULT, LP_IAudioEndpointVolume, ctypes.POINTER(ctypes.c_float))),
    ]


IMMDeviceEnumerator._fields_ = [("lpVtbl", ctypes.POINTER(IMMDeviceEnumeratorVtbl))]
IMMDevice._fields_ = [("lpVtbl", ctypes.POINTER(IMMDeviceVtbl))]
IAudioEndpointVolume._fields_ = [("lpVtbl", ctypes.POINTER(IAudioEndpointVolumeVtbl))]

CLSID_MMDEVICE_ENUMERATOR = GUID("BCDE0395-E52F-467C-8E3D-C4579291692E")
IID_IMMDEVICE_ENUMERATOR = GUID("A95664D2-9614-4F35-A746-DE8DB63617E6")
IID_IAUDIO_ENDPOINT_VOLUME = GUID("5CDF2C82-841E-4546-9722-0CF74078229A")


def _hr_failed(hr: int) -> bool:
    return ctypes.c_long(hr).value < 0


def _check_hr(hr: int, action: str) -> None:
    if _hr_failed(hr):
        raise OSError(f"{action} failed with HRESULT 0x{ctypes.c_ulong(hr).value:08X}")


def _co_initialize() -> bool:
    hr = ctypes.oledll.ole32.CoInitialize(None)
    if hr in (0, 1):  # S_OK, S_FALSE
        return True
    # RPC_E_CHANGED_MODE means COM is already initialized differently on this thread.
    if ctypes.c_ulong(hr).value == 0x80010106:
        return False
    _check_hr(hr, "CoInitialize")
    return False


def _with_default_endpoint_volume(fn):
    should_uninitialize = _co_initialize()
    enumerator = LP_IMMDeviceEnumerator()
    device = LP_IMMDevice()
    endpoint_raw = LPVOID()
    endpoint = LP_IAudioEndpointVolume()

    try:
        hr = ctypes.oledll.ole32.CoCreateInstance(
            ctypes.byref(CLSID_MMDEVICE_ENUMERATOR),
            None,
            CLSCTX_ALL,
            ctypes.byref(IID_IMMDEVICE_ENUMERATOR),
            ctypes.byref(enumerator),
        )
        _check_hr(hr, "CoCreateInstance(IMMDeviceEnumerator)")

        hr = enumerator.contents.lpVtbl.contents.GetDefaultAudioEndpoint(
            enumerator,
            ERENDER,
            EMULTIMEDIA,
            ctypes.byref(device),
        )
        _check_hr(hr, "GetDefaultAudioEndpoint")

        state = wintypes.DWORD()
        hr = device.contents.lpVtbl.contents.GetState(device, ctypes.byref(state))
        _check_hr(hr, "IMMDevice.GetState")
        if not (state.value & DEVICE_STATE_ACTIVE):
            raise RuntimeError("Default speaker endpoint is not active")

        hr = device.contents.lpVtbl.contents.Activate(
            device,
            ctypes.byref(IID_IAUDIO_ENDPOINT_VOLUME),
            CLSCTX_ALL,
            None,
            ctypes.byref(endpoint_raw),
        )
        _check_hr(hr, "IMMDevice.Activate(IAudioEndpointVolume)")
        endpoint = ctypes.cast(endpoint_raw, LP_IAudioEndpointVolume)
        return fn(endpoint)
    finally:
        if endpoint:
            endpoint.contents.lpVtbl.contents.Release(endpoint)
        if device:
            device.contents.lpVtbl.contents.Release(device)
        if enumerator:
            enumerator.contents.lpVtbl.contents.Release(enumerator)
        if should_uninitialize:
            ctypes.oledll.ole32.CoUninitialize()


class AudioDucker:
    def __init__(self, enabled: bool, duck_percent: int, behavior: str = "lower"):
        self.enabled = enabled
        self.duck_percent = self._snap_percent(duck_percent)
        self.behavior = behavior
        self._original_volume: float | None = None
        self._active = False

    @staticmethod
    def _snap_percent(value: int) -> int:
        return max(0, min(100, int(round(int(value) / 25)) * 25))

    @classmethod
    def from_settings(cls, settings: dict) -> "AudioDucker":
        audio = settings.get("audio", {})
        sound = settings.get("sound", {})
        behavior = sound.get("playback_when_recording", "lower")
        enabled = bool(audio.get("ducking_enabled", False))
        if not enabled:
            return cls(False, 0, behavior)
        if behavior == "keep_playing":
            return cls(False, 0, behavior)
        if behavior == "mute":
            return cls(True, 100, behavior)
        if behavior == "pause":
            # Full per-session transport pause is app-specific. Treat it as no-op
            # until a session control backend is added, rather than risking volume state.
            return cls(False, 0, behavior)
        return cls(
            enabled=True,
            duck_percent=int(audio.get("ducking_percent", 75)),
            behavior=behavior,
        )

    def duck(self) -> bool:
        if not self.enabled or self.duck_percent <= 0 or self._active:
            return False

        try:
            def apply(endpoint: LP_IAudioEndpointVolume) -> tuple[float, float]:
                original = ctypes.c_float()
                hr = endpoint.contents.lpVtbl.contents.GetMasterVolumeLevelScalar(
                    endpoint,
                    ctypes.byref(original),
                )
                _check_hr(hr, "GetMasterVolumeLevelScalar")

                scale = max(0.0, 1.0 - (self.duck_percent / 100.0))
                ducked = max(0.0, min(1.0, float(original.value) * scale))
                hr = endpoint.contents.lpVtbl.contents.SetMasterVolumeLevelScalar(
                    endpoint,
                    ctypes.c_float(ducked),
                    None,
                )
                _check_hr(hr, "SetMasterVolumeLevelScalar")
                return float(original.value), ducked

            original, ducked = _with_default_endpoint_volume(apply)
            self._original_volume = original
            self._active = True
            print(
                f"System audio ducking applied: {self.duck_percent}% "
                f"({original:.2f} -> {ducked:.2f})",
                flush=True,
            )
            return True
        except Exception as exc:
            print(f"System audio ducking failed: {exc}", flush=True)
            self._original_volume = None
            self._active = False
            return False

    def restore(self):
        if self._original_volume is None:
            self._active = False
            return

        original = max(0.0, min(1.0, float(self._original_volume)))
        try:
            def apply(endpoint: LP_IAudioEndpointVolume) -> None:
                hr = endpoint.contents.lpVtbl.contents.SetMasterVolumeLevelScalar(
                    endpoint,
                    ctypes.c_float(original),
                    None,
                )
                _check_hr(hr, "SetMasterVolumeLevelScalar")

            _with_default_endpoint_volume(apply)
            print(f"System audio restored: {original:.2f}", flush=True)
        except Exception as exc:
            print(f"System audio restore failed: {exc}", flush=True)
        finally:
            self._original_volume = None
            self._active = False
