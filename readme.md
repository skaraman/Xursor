# Xursor

Xursor is a Windows desktop prototype for a secondary on-screen assistant. The current codebase implements the overlay shell: a PyQt6 app that opens a transparent always-on-top overlay window, follows the cursor, and runs from the system tray. The intended next subsystem is a hover-driven UI explainer that captures the hovered region, sends it to a local model server, and reflects request state through the overlay.

## Current Status

Implemented now:
- Transparent always-on-top overlay window that follows the cursor
- Animated overlay pulse and rotation
- Visual state contract with color and shape variants
- Rebindable global capture trigger with single-step and two-step presets
- Automatic post-capture send to LM Studio for screenshot description
- Blue in-app response bubble that persists until the next capture
- Full LM Studio response saved to disk for later review
- Frameless PyQt6 window with input transparency
- Windows-specific shadow removal through `ctypes` and DWM composition attributes
- System tray app shell with Exit and manual state testing actions

Planned, not implemented yet:
- Hover dwell detection over buttons, images, and other UI elements
- Delayed screenshot capture after hover
- Settings/configuration for appearance and behavior

## How It Works

Current runtime flow:

1. Launch the app.
2. Create a tray-backed PyQt6 application.
3. Show a transparent overlay window that stays above other windows and ignores mouse input.
4. Reposition and animate the overlay around the cursor.
5. Trigger capture with the configured global binding for a 100x100 screenshot around the cursor.
6. Save the capture into the local `captures/` directory.
7. Send the capture to LM Studio at `http://10.0.0.37:1234`.
8. Save the full LM Studio response into the local `responses/` directory.
9. Show a truncated response inside the blue overlay bubble until the next capture.
10. Reflect send/wait/respond state through the overlay and tray icon.
11. Change capture binding from the tray between single-step and two-step presets.
12. Allow manual switching between idle, sending, waiting, and responding visual states from the tray menu.

Intended next flow:

1. Detect a stable hover over a UI target.
2. Wait several seconds before capture.
3. Take a screenshot of the hovered region.
4. Send the screenshot and user question to the local model server at `http://10.0.0.37:1234`.
5. Update overlay state automatically while sending, waiting, and responding.

## Visual States

The README-level visual contract is:

- Yellow: highlight or idle attention state
- Green: request is being sent
- Red: waiting for model response
- Blue: responding or presenting an answer

The current prototype supports the color/state contract and shape switching. In the responding state, the overlay becomes a larger blue text bubble, stops spinning, shows a truncated answer, and stays visible until the next capture.

## Local Model API

Current model integration targets a local HTTP service at `http://10.0.0.37:1234`.

- `GET /api/v1/models`: list available models
- `POST /api/v1/chat`: send a screenshot description request using `input` items with `text` and `image`
- `POST /api/v1/models/load`: load a model into memory
- `POST /api/v1/models/download`: download a model
- `GET /api/v1/models/download/status/:job_id`: check download status

The current implementation selects a loaded vision-capable model when available, otherwise the first vision-capable model.

## Tech Stack

- Python
- PyQt6 for the overlay window and tray app
- Windows `ctypes` integration for DWM window composition control
- System tray application model with a single-file entrypoint in `main.py`

## Run

Minimum current startup path:

```powershell
python main.py
```

Current controls:

- Tray `Capture Binding` menu: switch between `F8`, `Ctrl+Shift+S`, and `Ctrl+K, then C`
- Active global binding: capture a 100x100 screenshot around the mouse cursor
- `config.json`: set `lmstudio_endpoint` and `lmstudio_prompt`
- `responses/`: full LM Studio response text files keyed to each capture

Environment assumptions:

- Windows desktop environment
- Python with `PyQt6` installed

## Roadmap

- Add hover timing and readiness rules for deciding when a target is stable enough to inspect
- Add screenshot capture for the hovered UI region
- Move appearance and timing options into settings/config
