"""Native file/folder picker, run in a short-lived child process.

Tk dialogs are unreliable off the main thread, and Gradio handlers run on worker threads, so
the dialog lives in its own process (spawn - same mechanism as jobs.py, works frozen too).
The paths are plain strings in a text box: no upload, so a 100 MB GTFS zip is never copied.
"""
from __future__ import annotations

import multiprocessing as mp


def _ask(kind: str, q) -> None:
    import tkinter
    from tkinter import filedialog

    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    if kind == "dir":
        path = filedialog.askdirectory(parent=root)
    elif kind == "save":
        path = filedialog.asksaveasfilename(parent=root)
    else:
        path = filedialog.askopenfilename(parent=root)
    root.destroy()
    q.put(path or "")


def pick(kind: str = "file") -> str:
    """Show the dialog; return the chosen path, or "" if cancelled. kind: file | dir | save."""
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    proc = ctx.Process(target=_ask, args=(kind, q))
    proc.start()
    try:
        while proc.is_alive() or not q.empty():  # a crashed dialog process must not hang the UI
            try:
                return q.get(timeout=0.5)
            except Exception:  # queue.Empty
                continue
        return ""
    finally:
        proc.join()
