"""Thin wrapper around the real CLI (``overlay.app.cli``) — kept so the documented invocation
``uv run python examples/mpv_reader.py …`` keeps working. All flags, behaviour, and the demo /
screenshot paths live in the CLI's ``run`` command (see RUNNING.md for the flag contract).

    uv run python examples/mpv_reader.py video.mkv --sub-file jp.srt
    uv run python examples/mpv_reader.py                      # generated demo clip
    uv run python examples/mpv_reader.py --demo-word 読む --screenshot /tmp/reader.png
"""

from overlay.app.cli import main

if __name__ == "__main__":
    main()
