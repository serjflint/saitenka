"""Bolt the rendered panel to mpv via the JSON IPC ``overlay-add`` command.

``overlay-add`` uploads a raw BGRA bitmap into mpv's **own OSD surface**, composited inside mpv's
video swapchain. There is no second top-level window, so the Windows airspace / MPO / exclusive-
fullscreen bug class (the reason we're leaving Electron) cannot occur — this is the "single surface"
cure at the simplest possible cost (no GL, no FFI, no Rust).
"""
