import base64
import ctypes
import json
import math
import sys
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from time import monotonic
from ctypes import wintypes
from urllib import error, request

from PyQt6.QtCore import QPoint, QRect, QRectF, Qt, QTimer, QAbstractNativeEventFilter, QObject, pyqtSignal
from PyQt6.QtGui import QActionGroup, QBrush, QColor, QCursor, QFontMetrics, QGuiApplication, QIcon, QPainter, QPen, QPixmap, QPolygon
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget


class ACCENT_POLICY(ctypes.Structure):
    _fields_ = [
        ("AccentState", wintypes.DWORD),
        ("AccentFlags", wintypes.DWORD),
        ("GradientColor", wintypes.DWORD),
        ("AnimationId", wintypes.DWORD),
    ]


class WINDOWCOMPOSITIONATTRDATA(ctypes.Structure):
    _fields_ = [
        ("Attribute", ctypes.c_uint32),
        ("Data", ctypes.POINTER(ACCENT_POLICY)),
        ("SizeOfData", ctypes.c_ulonglong),
    ]


WCA_ACCENT_POLICY = 19
ACCENT_DISABLED = 0
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_BORDER_COLOR = 34
DWMWCP_DONOTROUND = 1
DWMWA_COLOR_NONE = 0xFFFFFFFE
WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
HOTKEY_ID_CAPTURE = 1
OVERLAY_SIZE = 25
OVERLAY_HALF = OVERLAY_SIZE // 2
CAPTURE_SIZE = 200
CAPTURE_HALF = CAPTURE_SIZE // 2
CURSOR_OFFSET_X = 17
CURSOR_OFFSET_Y = 12
UPDATE_INTERVAL_MS = 16
MIN_SCALE = 0.85
MAX_SCALE = 1.15
SCALE_STEP = 0.0001
IDLE_COLOR_PHASE_STEP = 0.000018
ACTIVE_SCALE_STEP = 0.01
ACTIVE_ROTATION_STEP = 4
SEQUENCE_TIMEOUT_SECONDS = 1.5
SENDING_STATE_DELAY_MS = 1000
RESPONSE_TEXT_MAX_CHARS = 160
RESPONSE_MAX_WIDTH = 180
RESPONSE_MIN_WIDTH = 120
RESPONSE_PADDING_X = 8
RESPONSE_PADDING_Y = 6
RESPONSE_BOTTOM_PADDING = 5
RESPONSE_TAIL_HEIGHT = 7
RESPONSE_LINE_SLACK = 4
CONFIG_PATH = Path(__file__).resolve().parent / "config.json"
DEFAULT_CONFIG = {
    "capture_binding": ["F8"],
    "lmstudio_endpoint": "http://10.0.0.37:1234",
    "lmstudio_prompt": "Describe what is visible directly and concisely. Do not say screenshot, image, picture, or photo. Do not start with phrases like this screenshot shows or this image shows.",
}
BINDING_PRESETS = [
    ("F8", ["F8"]),
    ("Ctrl+Shift+S", ["Ctrl+Shift+S"]),
    ("Ctrl+K, then C", ["Ctrl+K", "C"]),
]


class OverlayState(Enum):
    IDLE = "idle"
    SENDING = "sending"
    WAITING = "waiting"
    RESPONDING = "responding"


STATE_COLORS = {
    OverlayState.IDLE: QColor(255, 221, 64, 220),
    OverlayState.SENDING: QColor(64, 210, 96, 220),
    OverlayState.WAITING: QColor(220, 70, 70, 220),
    OverlayState.RESPONDING: QColor(72, 140, 255, 220),
}

VK_ALIASES = {
    "ALT": 0x12,
    "BACKSPACE": 0x08,
    "CAPSLOCK": 0x14,
    "CTRL": 0x11,
    "DEL": 0x2E,
    "DELETE": 0x2E,
    "DOWN": 0x28,
    "END": 0x23,
    "ENTER": 0x0D,
    "ESC": 0x1B,
    "ESCAPE": 0x1B,
    "HOME": 0x24,
    "INS": 0x2D,
    "INSERT": 0x2D,
    "LEFT": 0x25,
    "PAGEDOWN": 0x22,
    "PAGEUP": 0x21,
    "PGDN": 0x22,
    "PGUP": 0x21,
    "RIGHT": 0x27,
    "SHIFT": 0x10,
    "SPACE": 0x20,
    "TAB": 0x09,
    "UP": 0x26,
    "WIN": 0x5B,
}

MODIFIER_VKS = {0x10, 0x11, 0x12, 0x5B, 0x5C}
MODIFIER_CANONICAL = {
    0x10: 0x10,
    0xA0: 0x10,
    0xA1: 0x10,
    0x11: 0x11,
    0xA2: 0x11,
    0xA3: 0x11,
    0x12: 0x12,
    0xA4: 0x12,
    0xA5: 0x12,
    0x5B: 0x5B,
    0x5C: 0x5B,
}
HOTKEY_MODIFIERS = {
    0x10: MOD_SHIFT,
    0x11: MOD_CONTROL,
    0x12: MOD_ALT,
    0x5B: MOD_WIN,
}


def canonical_vk(vk_code):
    return MODIFIER_CANONICAL.get(vk_code, vk_code)


def token_to_vk(token):
    upper = token.strip().upper()
    if upper in VK_ALIASES:
        return VK_ALIASES[upper]
    if len(upper) == 1 and "A" <= upper <= "Z":
        return ord(upper)
    if len(upper) == 1 and "0" <= upper <= "9":
        return ord(upper)
    if upper.startswith("F") and upper[1:].isdigit():
        value = int(upper[1:])
        if 1 <= value <= 24:
            return 0x6F + value
    raise ValueError(f"Unsupported key token: {token}")


@dataclass(frozen=True)
class BindingStep:
    key_vk: int
    modifiers: frozenset[int]
    label: str


class CaptureBinding:
    def __init__(self, steps):
        if len(steps) not in (1, 2):
            raise ValueError("capture_binding must have one or two steps")
        self.steps = steps

    @classmethod
    def from_config(cls, raw_steps):
        if not isinstance(raw_steps, list) or not raw_steps:
            raise ValueError("capture_binding must be a non-empty list")
        return cls([parse_binding_step(value) for value in raw_steps])

    def label(self):
        return ", then ".join(step.label for step in self.steps)


def parse_binding_step(raw_step):
    if not isinstance(raw_step, str) or not raw_step.strip():
        raise ValueError("binding step must be a non-empty string")
    tokens = [token.strip() for token in raw_step.split("+") if token.strip()]
    if not tokens:
        raise ValueError("binding step must include a key")

    key_vk = None
    modifiers = set()
    labels = []
    for token in tokens:
        vk_code = canonical_vk(token_to_vk(token))
        labels.append(token.strip().upper())
        if vk_code in MODIFIER_VKS:
            modifiers.add(vk_code)
            continue
        if key_vk is not None:
            raise ValueError(f"binding step has multiple non-modifier keys: {raw_step}")
        key_vk = vk_code

    if key_vk is None:
        raise ValueError(f"binding step requires one non-modifier key: {raw_step}")
    return BindingStep(key_vk=key_vk, modifiers=frozenset(modifiers), label="+".join(labels))


def load_config():
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
        return DEFAULT_CONFIG.copy()

    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    merged = DEFAULT_CONFIG.copy()
    merged.update(config)
    return merged


def save_config(config):
    CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")


class LMStudioClient:
    def __init__(self, endpoint, prompt):
        self.endpoint = endpoint.rstrip("/")
        self.prompt = prompt

    def describe_image(self, image_path):
        model = self._select_model()
        image_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
        payload = {
            "model": model,
            "input": [
                {"type": "text", "content": self.prompt},
                {"type": "image", "data_url": f"data:image/png;base64,{image_b64}"},
            ],
        }
        response = self._post_json("/api/v1/chat", payload)
        return self._extract_text(response)

    def _select_model(self):
        models_response = self._get_json("/api/v1/models")
        models = models_response.get("models", [])
        for model in models:
            capabilities = model.get("capabilities", {})
            if capabilities.get("vision") and model.get("loaded_instances"):
                return model["key"]
        for model in models:
            capabilities = model.get("capabilities", {})
            if capabilities.get("vision"):
                return model["key"]
        raise RuntimeError("No vision-capable LM Studio model found.")

    def _extract_text(self, response):
        if isinstance(response, dict):
            if isinstance(response.get("text"), str):
                return self._clean_response_text(response["text"])
            output = response.get("output")
            if isinstance(output, list):
                for item in output:
                    if isinstance(item, dict) and item.get("type") == "message":
                        content = item.get("content")
                        if isinstance(content, str) and content.strip():
                            return self._clean_response_text(content)
            choices = response.get("choices")
            if isinstance(choices, list) and choices:
                message = choices[0].get("message", {})
                content = message.get("content")
                if isinstance(content, str):
                    return self._clean_response_text(content)
                if isinstance(content, list):
                    text_parts = [
                        item.get("text", "")
                        for item in content
                        if isinstance(item, dict) and item.get("type") == "text"
                    ]
                    if text_parts:
                        return self._clean_response_text(" ".join(part for part in text_parts if part))
        return "LM Studio returned no text."

    def _clean_response_text(self, text):
        cleaned = " ".join(text.split())
        replacements = (
            ("This screenshot shows ", ""),
            ("This screenshot is ", ""),
            ("This screenshot contains ", ""),
            ("This image shows ", ""),
            ("This image is ", ""),
            ("This image contains ", ""),
            ("The screenshot shows ", ""),
            ("The image shows ", ""),
        )
        for prefix, replacement in replacements:
            if cleaned.lower().startswith(prefix.lower()):
                cleaned = replacement + cleaned[len(prefix):]
                break
        return cleaned.strip()

    def _get_json(self, path):
        req = request.Request(
            f"{self.endpoint}{path}",
            headers={"Accept": "application/json"},
            method="GET",
        )
        return self._read_json(req)

    def _post_json(self, path, payload):
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            f"{self.endpoint}{path}",
            data=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        return self._read_json(req)

    def _read_json(self, req):
        try:
            with request.urlopen(req, timeout=60) as response:
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"LM Studio HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"LM Studio unavailable: {exc.reason}") from exc

        if not raw.strip():
            return {}
        return json.loads(raw)


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
        ("lPrivate", wintypes.DWORD),
    ]


class HotkeyEventFilter(QAbstractNativeEventFilter):
    def __init__(self, on_capture):
        super().__init__()
        self.on_capture = on_capture

    def nativeEventFilter(self, event_type, message):
        if event_type != b"windows_generic_MSG":
            return False, 0
        msg = MSG.from_address(int(message))
        if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID_CAPTURE:
            self.on_capture()
            return True, 0
        return False, 0


class UiBridge(QObject):
    state_changed = pyqtSignal(object)
    lmstudio_success = pyqtSignal(str, str)
    lmstudio_failure = pyqtSignal(str)


class PolledKeySequence:
    def __init__(self, binding, on_match):
        self.binding = binding
        self.on_match = on_match
        self.sequence_index = 0
        self.sequence_deadline = 0.0
        self.step_active = [False] * len(self.binding.steps)

    def install(self):
        return None

    def uninstall(self):
        return None

    def poll(self):
        now = monotonic()
        if self.sequence_index and now > self.sequence_deadline:
            self._reset_sequence()

        expected = self.binding.steps[self.sequence_index]
        expected_index = self.sequence_index
        step_is_down = self._step_is_down(expected)
        if step_is_down and not self.step_active[expected_index]:
            self.step_active[expected_index] = True
            self.sequence_index += 1
            if self.sequence_index == len(self.binding.steps):
                self._reset_sequence()
                self.on_match()
            else:
                self.sequence_deadline = now + SEQUENCE_TIMEOUT_SECONDS
            return

        if not step_is_down:
            self.step_active[expected_index] = False

    def _step_is_down(self, step):
        if not self._is_vk_down(step.key_vk):
            return False
        active_modifiers = frozenset(
            modifier for modifier in {0x10, 0x11, 0x12, 0x5B}
            if self._is_vk_down(modifier)
        )
        return active_modifiers == step.modifiers

    def _is_vk_down(self, vk_code):
        return bool(ctypes.windll.user32.GetAsyncKeyState(vk_code) & 0x8000)

    def _reset_sequence(self):
        self.sequence_index = 0
        self.sequence_deadline = 0.0
        self.step_active = [False] * len(self.binding.steps)


class CaptureTrigger:
    def __init__(self, app, binding, on_match):
        self.app = app
        self.binding = binding
        self.on_match = on_match
        self.polled_sequence = None
        self.hotkey_filter = None

    def install(self):
        if len(self.binding.steps) == 1:
            self._install_hotkey()
            return
        self.polled_sequence = PolledKeySequence(self.binding, self.on_match)

    def uninstall(self):
        if self.hotkey_filter is not None:
            self.app.removeNativeEventFilter(self.hotkey_filter)
            self.hotkey_filter = None
            ctypes.windll.user32.UnregisterHotKey(None, HOTKEY_ID_CAPTURE)
        self.polled_sequence = None

    def poll(self):
        if self.polled_sequence is not None:
            self.polled_sequence.poll()

    def _install_hotkey(self):
        step = self.binding.steps[0]
        modifiers = MOD_NOREPEAT
        for modifier in step.modifiers:
            modifiers |= HOTKEY_MODIFIERS[modifier]
        if not ctypes.windll.user32.RegisterHotKey(None, HOTKEY_ID_CAPTURE, modifiers, step.key_vk):
            raise OSError(f"Failed to register hotkey: {self.binding.label()}")
        self.hotkey_filter = HotkeyEventFilter(self.on_match)
        self.app.installNativeEventFilter(self.hotkey_filter)


class AnimatedCursor(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.resize(OVERLAY_SIZE, OVERLAY_SIZE)

        self.state = OverlayState.IDLE
        self.angle = 0
        self.scale = 1.0
        self.growing = True
        self.response_preview = ""
        self.color_phase = 0.0
        self.resize(OVERLAY_SIZE, OVERLAY_SIZE)

    def showEvent(self, event):
        super().showEvent(event)
        self.remove_window_shadow()

    def remove_window_shadow(self):
        accent_policy = ACCENT_POLICY()
        accent_policy.AccentState = ACCENT_DISABLED
        data_ptr = ctypes.cast(
            ctypes.pointer(accent_policy),
            ctypes.POINTER(ACCENT_POLICY),
        )
        composition_attr = WINDOWCOMPOSITIONATTRDATA(
            WCA_ACCENT_POLICY,
            data_ptr,
            ctypes.sizeof(ACCENT_POLICY),
        )
        hwnd = int(self.winId())
        if hwnd != 0:
            ctypes.windll.user32.SetWindowCompositionAttribute(
                hwnd,
                ctypes.byref(composition_attr),
            )
            corner_preference = ctypes.c_int(DWMWCP_DONOTROUND)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd,
                DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(corner_preference),
                ctypes.sizeof(corner_preference),
            )
            border_color = wintypes.DWORD(DWMWA_COLOR_NONE)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd,
                DWMWA_BORDER_COLOR,
                ctypes.byref(border_color),
                ctypes.sizeof(border_color),
            )

    def set_state(self, state):
        if state == self.state:
            return
        self.state = state
        self._update_size_for_state()
        self.update()

    def set_response_preview(self, text):
        self.response_preview = self._truncate_response(text)
        self._update_size_for_state()
        self.update()

    def clear_response_preview(self):
        if not self.response_preview:
            return
        self.response_preview = ""
        self._update_size_for_state()
        self.update()

    def tick(self):
        cursor_pos = QCursor.pos()
        self.move(
            cursor_pos.x() + CURSOR_OFFSET_X,
            cursor_pos.y() + CURSOR_OFFSET_Y,
        )

        if self.state == OverlayState.RESPONDING:
            self.update()
            return

        scale_step = SCALE_STEP if self.state == OverlayState.IDLE else ACTIVE_SCALE_STEP
        if self.growing:
            self.scale += scale_step
            if self.scale >= MAX_SCALE:
                self.scale = MAX_SCALE
                self.growing = False
        else:
            self.scale -= scale_step
            if self.scale <= MIN_SCALE:
                self.scale = MIN_SCALE
                self.growing = True
        if self.state == OverlayState.IDLE:
            self.color_phase = (self.color_phase + IDLE_COLOR_PHASE_STEP) % 1.0
        else:
            self.angle = (self.angle + ACTIVE_ROTATION_STEP) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.state == OverlayState.RESPONDING:
            self._paint_response_bubble(painter)
            return
        painter.translate(OVERLAY_HALF, OVERLAY_HALF)
        painter.scale(self.scale, self.scale)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(self._current_fill_color()))
        if self.state == OverlayState.IDLE:
            radius = 8
            painter.drawEllipse(QRectF(-radius, -radius, radius * 2, radius * 2))
            return
        painter.drawPolygon(self._build_shape())

    def _paint_response_bubble(self, painter):
        bubble_rect = QRectF(0, 0, self.width(), self.height() - RESPONSE_TAIL_HEIGHT)
        painter.setPen(QPen(QColor(210, 230, 255, 190), 1.5))
        painter.setBrush(QBrush(STATE_COLORS[self.state]))
        painter.drawRoundedRect(bubble_rect, 8, 8)

        tail = QPolygon(
            [
                QPoint(13, int(bubble_rect.bottom()) - 1),
                QPoint(24, int(bubble_rect.bottom()) - 1),
                QPoint(9, self.height() - 1),
            ]
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(tail)

        painter.setPen(QColor(245, 250, 255))
        text_rect = QRect(
            RESPONSE_PADDING_X,
            RESPONSE_PADDING_Y,
            self.width() - (RESPONSE_PADDING_X * 2),
            int(bubble_rect.height()) - RESPONSE_PADDING_Y - RESPONSE_BOTTOM_PADDING,
        )
        flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap)
        painter.drawText(text_rect, flags, self.response_preview or "No response.")

    def _truncate_response(self, text):
        normalized = " ".join(text.split())
        if len(normalized) <= RESPONSE_TEXT_MAX_CHARS:
            return normalized
        return normalized[: RESPONSE_TEXT_MAX_CHARS - 3].rstrip() + "..."

    def _update_size_for_state(self):
        if self.state != OverlayState.RESPONDING or not self.response_preview:
            self.resize(OVERLAY_SIZE, OVERLAY_SIZE)
            return

        metrics = QFontMetrics(self.font())
        text_flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap)
        max_text_width = RESPONSE_MAX_WIDTH - (RESPONSE_PADDING_X * 2)
        min_text_width = RESPONSE_MIN_WIDTH - (RESPONSE_PADDING_X * 2)

        natural_rect = metrics.boundingRect(
            QRect(0, 0, max_text_width, 2000),
            text_flags,
            self.response_preview,
        )
        text_width = max(min_text_width, min(max_text_width, natural_rect.width()))
        width = text_width + (RESPONSE_PADDING_X * 2)

        wrapped_rect = metrics.boundingRect(
            QRect(0, 0, text_width, 2000),
            text_flags,
            self.response_preview,
        )
        height = (
            wrapped_rect.height()
            + RESPONSE_PADDING_Y
            + RESPONSE_BOTTOM_PADDING
            + RESPONSE_TAIL_HEIGHT
            + RESPONSE_LINE_SLACK
        )
        self.resize(width, height)

    def _current_fill_color(self):
        if self.state != OverlayState.IDLE:
            return STATE_COLORS[self.state]

        phase = self.color_phase
        red = int((math.sin(phase * math.tau * 1.0) * 0.5 + 0.5) * 255)
        green = int((math.sin((phase + 0.19) * math.tau * 1.37) * 0.5 + 0.5) * 255)
        blue = int((math.sin((phase + 0.43) * math.tau * 1.91) * 0.5 + 0.5) * 255)
        alpha = int(150 + ((math.sin((phase + 0.67) * math.tau * 0.73) * 0.5 + 0.5) * 105))
        return QColor(red, green, blue, alpha)

    def _build_shape(self):
        if self.state == OverlayState.SENDING:
            return self._diamond_polygon(radius=14)
        if self.state == OverlayState.WAITING:
            return self._hexagon_polygon(radius=13)
        return self._speech_polygon(width=27, height=19, tail=5)

    def _diamond_polygon(self, radius):
        return QPolygon(
            [
                QPoint(0, -radius),
                QPoint(radius, 0),
                QPoint(0, radius),
                QPoint(-radius, 0),
            ]
        )

    def _hexagon_polygon(self, radius):
        return QPolygon(
            [
                QPoint(
                    int(radius * math.cos(math.radians(index * 60 - 30))),
                    int(radius * math.sin(math.radians(index * 60 - 30))),
                )
                for index in range(6)
            ]
        )

    def _speech_polygon(self, width, height, tail):
        half_width = width // 2
        half_height = height // 2
        return QPolygon(
            [
                QPoint(-half_width, -half_height),
                QPoint(half_width, -half_height),
                QPoint(half_width, half_height - tail),
                QPoint(10, half_height - tail),
                QPoint(0, half_height),
                QPoint(-10, half_height - tail),
                QPoint(-half_width, half_height - tail),
            ]
        )


class TrayApp:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        self.config = load_config()
        self.capture_binding = CaptureBinding.from_config(self.config["capture_binding"])
        self.lmstudio = LMStudioClient(
            self.config["lmstudio_endpoint"],
            self.config["lmstudio_prompt"],
        )
        self.capture_dir = Path(__file__).resolve().parent / "captures"
        self.capture_dir.mkdir(exist_ok=True)
        self.response_dir = Path(__file__).resolve().parent / "responses"
        self.response_dir.mkdir(exist_ok=True)
        self.capture_counter = 0
        self.capture_in_flight = False
        self.ui_bridge = UiBridge()
        self.ui_bridge.state_changed.connect(self.set_overlay_state)
        self.ui_bridge.lmstudio_success.connect(self._handle_lmstudio_success)
        self.ui_bridge.lmstudio_failure.connect(self._handle_lmstudio_failure)

        self.overlay = AnimatedCursor()
        self.overlay.show()

        self.timer = QTimer()
        self.timer.timeout.connect(self._tick)
        self.timer.start(UPDATE_INTERVAL_MS)

        self.capture_trigger = CaptureTrigger(
            self.app,
            self.capture_binding,
            self.capture_around_cursor,
        )
        self.capture_trigger.install()

        self.tray = QSystemTrayIcon()
        self._update_tray_icon(self.overlay.state)
        self.menu = QMenu()
        self.capture_action = self.menu.addAction(f"Capture ({self.capture_binding.label()})")
        self.capture_action.triggered.connect(self.capture_around_cursor)
        self._add_binding_actions()
        self.menu.addSeparator()
        self._add_state_actions()
        self.menu.addSeparator()
        exit_action = self.menu.addAction("Exit")
        exit_action.triggered.connect(self.quit_app)
        self.tray.setContextMenu(self.menu)
        self.tray.show()

    def _add_state_actions(self):
        for label, state in (
            ("Idle", OverlayState.IDLE),
            ("Sending", OverlayState.SENDING),
            ("Waiting", OverlayState.WAITING),
            ("Responding", OverlayState.RESPONDING),
        ):
            action = self.menu.addAction(label)
            action.triggered.connect(
                lambda checked=False, value=state: self.set_overlay_state(value)
            )

    def _add_binding_actions(self):
        binding_menu = self.menu.addMenu("Capture Binding")
        self.binding_action_group = QActionGroup(binding_menu)
        self.binding_action_group.setExclusive(True)
        active_binding = self.config["capture_binding"]

        for label, binding_steps in BINDING_PRESETS:
            action = binding_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(binding_steps == active_binding)
            action.triggered.connect(
                lambda checked=False, value=binding_steps: self.set_capture_binding(value)
            )
            self.binding_action_group.addAction(action)

    def set_capture_binding(self, binding_steps):
        self.config["capture_binding"] = binding_steps
        save_config(self.config)
        self.capture_binding = CaptureBinding.from_config(binding_steps)
        self.capture_trigger.uninstall()
        self.capture_trigger = CaptureTrigger(
            self.app,
            self.capture_binding,
            self.capture_around_cursor,
        )
        self.capture_trigger.install()
        self.capture_action.setText(f"Capture ({self.capture_binding.label()})")
        self._update_tray_icon(self.overlay.state)

    def _tick(self):
        self.overlay.tick()
        self.capture_trigger.poll()

    def set_overlay_state(self, state):
        self.overlay.set_state(state)
        self._update_tray_icon(state)

    def _update_tray_icon(self, state):
        size = 32
        margin = 4
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(STATE_COLORS[state]))
        painter.drawEllipse(margin, margin, size - (margin * 2), size - (margin * 2))
        painter.end()

        self.tray.setIcon(QIcon(pixmap))
        self.tray.setToolTip(f"Xursor: {state.value} | capture {self.capture_binding.label()}")

    def quit_app(self):
        self.capture_trigger.uninstall()
        self.app.quit()

    def run(self):
        sys.exit(self.app.exec())

    def capture_around_cursor(self):
        if self.capture_in_flight:
            self.tray.showMessage("Xursor", "Capture already in progress.")
            return
        if self.overlay.state == OverlayState.RESPONDING:
            self.overlay.clear_response_preview()
            self.set_overlay_state(OverlayState.IDLE)
            self.tray.setToolTip(
                f"Xursor: idle | capture {self.capture_binding.label()}"
            )
            return
        self.overlay.clear_response_preview()
        cursor_pos = QCursor.pos()
        screen = QGuiApplication.screenAt(cursor_pos) or QGuiApplication.primaryScreen()
        if screen is None:
            self.tray.showMessage("Xursor", "Capture failed: no screen found.")
            return

        left = cursor_pos.x() - CAPTURE_HALF
        top = cursor_pos.y() - CAPTURE_HALF
        screenshot = screen.grabWindow(0, left, top, CAPTURE_SIZE, CAPTURE_SIZE)
        output_path = self._next_capture_path()
        if not screenshot.save(str(output_path), "PNG"):
            self.tray.showMessage("Xursor", "Capture failed: image could not be saved.")
            return
        self.capture_in_flight = True
        self.set_overlay_state(OverlayState.SENDING)
        self.tray.setToolTip(
            f"Xursor: sending {output_path.name} | capture {self.capture_binding.label()}"
        )
        self.tray.showMessage("Xursor", f"Saved {output_path.name}, sending to LM Studio")
        threading.Thread(
            target=self._send_capture_to_lmstudio,
            args=(output_path,),
            daemon=True,
        ).start()

    def _next_capture_path(self):
        while True:
            self.capture_counter += 1
            candidate = self.capture_dir / f"capture_{self.capture_counter:03d}.png"
            if not candidate.exists():
                return candidate

    def _send_capture_to_lmstudio(self, output_path):
        threading.Event().wait(SENDING_STATE_DELAY_MS / 1000)
        self.ui_bridge.state_changed.emit(OverlayState.WAITING)
        try:
            response_text = self.lmstudio.describe_image(output_path)
        except Exception as exc:
            self.ui_bridge.lmstudio_failure.emit(str(exc))
            return
        response_path = self._response_path_for_capture(output_path)
        response_path.write_text(response_text, encoding="utf-8")
        self.ui_bridge.lmstudio_success.emit(output_path.name, response_text)

    def _handle_lmstudio_success(self, capture_name, response_text):
        self.capture_in_flight = False
        self.overlay.set_response_preview(response_text)
        self.set_overlay_state(OverlayState.RESPONDING)
        self.tray.setToolTip(
            f"Xursor: responded for {capture_name} | capture {self.capture_binding.label()}"
        )
        self.tray.showMessage("Xursor", f"Response saved for {capture_name}")

    def _handle_lmstudio_failure(self, error_message):
        self.capture_in_flight = False
        self.overlay.clear_response_preview()
        self.set_overlay_state(OverlayState.IDLE)
        self.tray.setToolTip(
            f"Xursor: send failed | capture {self.capture_binding.label()}"
        )
        self.tray.showMessage("Xursor", error_message[:240])

    def _response_path_for_capture(self, capture_path):
        return self.response_dir / f"{capture_path.stem}.txt"


if __name__ == "__main__":
    TrayApp().run()
