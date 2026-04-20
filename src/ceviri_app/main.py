from __future__ import annotations

import tkinter as tk

from .ui import LiveCaptionApp


def main() -> None:
    root = tk.Tk()
    LiveCaptionApp(root)
    root.mainloop()
