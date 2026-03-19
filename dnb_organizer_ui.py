"""
╔══════════════════════════════════════════════════════════════╗
║       DnB Music Library Organizer — UI  v1.5                 ║
╚══════════════════════════════════════════════════════════════╝

Dependencies:
    pip install mutagen customtkinter

Usage:
    python dnb_organizer_ui.py
"""

import threading
import queue
from pathlib import Path
import tkinter as tk
from tkinter import filedialog

import customtkinter as ctk

# Import core logic from the sibling script
from dnb_organizer import (
    organize_library,
    SUPPORTED_EXTENSIONS,
)


# ─── Theme ────────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

ACCENT   = "#1DB954"   # green accent (Spotify-ish)
BG_DARK  = "#1a1a2e"
BG_MID   = "#16213e"
BG_CARD  = "#0f3460"
TEXT     = "#e0e0e0"
MUTED    = "#888888"
FONT     = ("Segoe UI", 13)
FONT_SM  = ("Segoe UI", 11)
FONT_LG  = ("Segoe UI", 15, "bold")
MONO     = ("Consolas", 11)


# ─── Main App Window ──────────────────────────────────────────────────────────

class DnBOrganizerApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("DnB Music Library Organizer  v1.5")
        self.geometry("720x740")
        self.minsize(620, 680)
        self.configure(fg_color=BG_DARK)

        # State
        self._running      = False
        self._log_queue    = queue.Queue()
        self._total        = 0
        self._done         = 0

        self._build_ui()
        self._poll_queue()

    # ── UI Construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        pad = {"padx": 20, "pady": 8}

        # ── Header ────────────────────────────────────────────────────────
        header = ctk.CTkFrame(self, fg_color=BG_MID, corner_radius=0)
        header.pack(fill="x")
        ctk.CTkLabel(
            header,
            text="🎵  DnB Music Library Organizer",
            font=("Segoe UI", 17, "bold"),
            text_color=ACCENT,
        ).pack(pady=14)

        # ── Folder selection ──────────────────────────────────────────────
        folders_frame = ctk.CTkFrame(self, fg_color=BG_MID, corner_radius=12)
        folders_frame.pack(fill="x", **pad)

        ctk.CTkLabel(folders_frame, text="FOLDERS", font=("Segoe UI", 10, "bold"),
                     text_color=MUTED).pack(anchor="w", padx=16, pady=(12, 4))

        self._source_var = tk.StringVar()
        self._dest_var   = tk.StringVar()

        self._build_folder_row(folders_frame, "Source", self._source_var,
                               "Where your files are now")
        self._build_folder_row(folders_frame, "Destination", self._dest_var,
                               "Where to put the organised files")

        # ── Lookup options ────────────────────────────────────────────────
        options_frame = ctk.CTkFrame(self, fg_color=BG_MID, corner_radius=12)
        options_frame.pack(fill="x", **pad)

        ctk.CTkLabel(options_frame, text="ONLINE LOOKUP  (label + genre)",
                     font=("Segoe UI", 10, "bold"), text_color=MUTED
                     ).pack(anchor="w", padx=16, pady=(12, 6))

        # Toggle row
        toggles = ctk.CTkFrame(options_frame, fg_color="transparent")
        toggles.pack(fill="x", padx=16, pady=(0, 4))

        self._online_var   = tk.BooleanVar(value=True)
        self._beatport_var = tk.BooleanVar(value=True)
        self._mb_var       = tk.BooleanVar(value=True)

        self._online_switch = ctk.CTkSwitch(
            toggles, text="Enable online lookup",
            variable=self._online_var, font=FONT,
            command=self._on_online_toggle,
        )
        self._online_switch.pack(side="left", padx=(0, 20))

        self._bp_switch = ctk.CTkSwitch(
            toggles, text="Beatport",
            variable=self._beatport_var, font=FONT,
        )
        self._bp_switch.pack(side="left", padx=(0, 20))

        self._mb_switch = ctk.CTkSwitch(
            toggles, text="MusicBrainz",
            variable=self._mb_var, font=FONT,
        )
        self._mb_switch.pack(side="left")

        # Discogs token
        discogs_row = ctk.CTkFrame(options_frame, fg_color="transparent")
        discogs_row.pack(fill="x", padx=16, pady=(6, 12))
        ctk.CTkLabel(discogs_row, text="Discogs token:", font=FONT,
                     text_color=TEXT).pack(side="left")
        self._discogs_entry = ctk.CTkEntry(
            discogs_row, placeholder_text="optional — paste token here",
            width=320, font=FONT_SM, show="•",
        )
        self._discogs_entry.pack(side="left", padx=(10, 8))
        ctk.CTkButton(
            discogs_row, text="👁", width=32, font=FONT_SM,
            fg_color="transparent", hover_color=BG_CARD,
            command=self._toggle_token_visibility,
        ).pack(side="left")

        # ── Structure preview ─────────────────────────────────────────────
        preview_frame = ctk.CTkFrame(self, fg_color=BG_MID, corner_radius=12)
        preview_frame.pack(fill="x", **pad)

        ctk.CTkLabel(preview_frame, text="OUTPUT STRUCTURE",
                     font=("Segoe UI", 10, "bold"), text_color=MUTED
                     ).pack(anchor="w", padx=16, pady=(12, 2))
        ctk.CTkLabel(
            preview_frame,
            text="[Genre]  /  [Label]  /  [Artist]  /  [Artist] - [Title].ext",
            font=("Consolas", 12), text_color=ACCENT,
        ).pack(anchor="w", padx=16, pady=(0, 12))

        # ── Progress ──────────────────────────────────────────────────────
        progress_frame = ctk.CTkFrame(self, fg_color=BG_MID, corner_radius=12)
        progress_frame.pack(fill="x", **pad)

        top_row = ctk.CTkFrame(progress_frame, fg_color="transparent")
        top_row.pack(fill="x", padx=16, pady=(12, 4))
        self._progress_label = ctk.CTkLabel(top_row, text="Ready", font=FONT,
                                             text_color=TEXT)
        self._progress_label.pack(side="left")
        self._count_label = ctk.CTkLabel(top_row, text="", font=FONT_SM,
                                          text_color=MUTED)
        self._count_label.pack(side="right")

        self._progress_bar = ctk.CTkProgressBar(progress_frame, height=10,
                                                  progress_color=ACCENT)
        self._progress_bar.pack(fill="x", padx=16, pady=(0, 12))
        self._progress_bar.set(0)

        # ── Log output ────────────────────────────────────────────────────
        log_frame = ctk.CTkFrame(self, fg_color=BG_MID, corner_radius=12)
        log_frame.pack(fill="both", expand=True, **pad)

        ctk.CTkLabel(log_frame, text="LOG", font=("Segoe UI", 10, "bold"),
                     text_color=MUTED).pack(anchor="w", padx=16, pady=(10, 4))

        self._log_box = ctk.CTkTextbox(
            log_frame, font=MONO, fg_color=BG_DARK,
            text_color=TEXT, wrap="none",
        )
        self._log_box.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self._log_box.configure(state="disabled")

        # ── Action button ─────────────────────────────────────────────────
        self._run_btn = ctk.CTkButton(
            self,
            text="▶   Organise Library",
            font=("Segoe UI", 14, "bold"),
            height=46,
            fg_color=ACCENT,
            hover_color="#17a845",
            text_color="#000000",
            corner_radius=10,
            command=self._on_run,
        )
        self._run_btn.pack(fill="x", padx=20, pady=(4, 16))

    def _build_folder_row(self, parent, label, var, hint):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=4)

        ctk.CTkLabel(row, text=f"{label}:", font=FONT, width=88,
                     anchor="w", text_color=TEXT).pack(side="left")

        entry = ctk.CTkEntry(row, textvariable=var,
                             placeholder_text=hint, font=FONT_SM)
        entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

        ctk.CTkButton(
            row, text="📁", width=36, font=FONT,
            fg_color=BG_CARD, hover_color="#1a4a7a",
            command=lambda v=var: self._browse(v),
        ).pack(side="left")

    # ── Event Handlers ────────────────────────────────────────────────────────

    def _browse(self, var: tk.StringVar):
        path = filedialog.askdirectory(title="Select folder")
        if path:
            var.set(path)

    def _on_online_toggle(self):
        state = "normal" if self._online_var.get() else "disabled"
        self._bp_switch.configure(state=state)
        self._mb_switch.configure(state=state)
        self._discogs_entry.configure(state=state)

    def _toggle_token_visibility(self):
        current = self._discogs_entry.cget("show")
        self._discogs_entry.configure(show="" if current == "•" else "•")

    def _on_run(self):
        if self._running:
            return

        source_raw = self._source_var.get().strip()
        dest_raw   = self._dest_var.get().strip()

        if not source_raw or not dest_raw:
            self._log("⚠  Please set both Source and Destination folders.", color="warning")
            return

        source_dir = Path(source_raw)
        dest_dir   = Path(dest_raw)

        if not source_dir.is_dir():
            self._log(f"⚠  Source folder not found: {source_dir}", color="warning")
            return
        if not dest_dir.is_dir():
            try:
                dest_dir.mkdir(parents=True)
                self._log(f"ℹ  Created destination folder: {dest_dir}")
            except Exception as e:
                self._log(f"⚠  Cannot create destination: {e}", color="warning")
                return

        use_online    = self._online_var.get()
        use_beatport  = self._beatport_var.get() and use_online
        discogs_token = self._discogs_entry.get().strip() if use_online else ""

        self._start_run(source_dir, dest_dir, use_online, use_beatport, discogs_token)

    # ── Background Worker ─────────────────────────────────────────────────────

    def _start_run(self, source_dir, dest_dir, use_online, use_beatport, discogs_token):
        self._running = True
        self._done    = 0
        self._total   = 0
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")
        self._progress_bar.set(0)
        self._progress_label.configure(text="Scanning…")
        self._count_label.configure(text="")
        self._run_btn.configure(state="disabled", text="⏳  Running…")

        thread = threading.Thread(
            target=self._worker,
            args=(source_dir, dest_dir, use_online, use_beatport, discogs_token),
            daemon=True,
        )
        thread.start()

    def _worker(self, source_dir, dest_dir, use_online, use_beatport, discogs_token):
        """Runs organize_library in a background thread, capturing output."""

        # Count files first so the progress bar is accurate
        all_files = [
            p for p in source_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
        ]
        self._total = len(all_files)

        if self._total == 0:
            self._log_queue.put(("⚠  No supported audio files found.", "warning"))
            self._log_queue.put(("__DONE__", None))
            return

        self._log_queue.put((f"Found {self._total} audio file(s). Starting…\n", "info"))

        # Patch organize_library's print output via a custom stdout-like object
        import io, sys

        class QueueWriter(io.TextIOBase):
            def __init__(self, q, app):
                self._q   = q
                self._app = app
            def write(self, s):
                if s.strip():
                    self._q.put((s.rstrip(), "log"))
                    # Detect progress by counting "→" lines
                    if "→" in s:
                        self._app._done += 1
                return len(s)
            def flush(self):
                pass

        old_stdout = sys.stdout
        sys.stdout = QueueWriter(self._log_queue, self)

        try:
            organize_library(
                source_dir=source_dir,
                dest_dir=dest_dir,
                discogs_token=discogs_token,
                use_online=use_online,
                use_beatport=use_beatport,
            )
        except Exception as e:
            self._log_queue.put((f"ERROR: {e}", "error"))
        finally:
            sys.stdout = old_stdout
            self._log_queue.put(("__DONE__", None))

    # ── Queue Polling (runs on UI thread) ─────────────────────────────────────

    def _poll_queue(self):
        try:
            while True:
                msg, kind = self._log_queue.get_nowait()
                if msg == "__DONE__":
                    self._on_done()
                else:
                    self._log(msg, color=kind)
                    self._update_progress()
        except queue.Empty:
            pass
        self.after(80, self._poll_queue)

    def _update_progress(self):
        if self._total > 0:
            pct = min(self._done / self._total, 1.0)
            self._progress_bar.set(pct)
            self._progress_label.configure(text=f"Processing…  {int(pct*100)}%")
            self._count_label.configure(text=f"{self._done} / {self._total}")

    def _on_done(self):
        self._running = False
        self._progress_bar.set(1.0)
        self._progress_label.configure(text="✔  Done!", text_color=ACCENT)
        self._count_label.configure(text=f"{self._total} files")
        self._run_btn.configure(state="normal", text="▶   Organise Library")

    # ── Log Helper ────────────────────────────────────────────────────────────

    def _log(self, text: str, color: str = "log"):
        color_map = {
            "log":     TEXT,
            "info":    "#a0c4ff",
            "warning": "#ffdd57",
            "error":   "#ff6b6b",
        }
        self._log_box.configure(state="normal")
        self._log_box.insert("end", text + "\n")
        self._log_box.configure(state="disabled")
        self._log_box.see("end")


# ─── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = DnBOrganizerApp()
    app.mainloop()
