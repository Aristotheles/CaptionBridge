from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from queue import Empty, Queue
import re
import threading
import tempfile
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
import webbrowser

from .audio import (
    LABEL_TO_MODE,
    MODE_CHOICES,
    MODE_MICROPHONE,
    MODE_SYSTEM,
    MODE_VIRTUAL,
    MODE_LABELS,
    AudioLevelMonitor,
    AudioSource,
    clear_audio_debug_log,
    capture_audio_sample,
    find_audio_source,
    get_audio_debug_log_path,
    list_audio_sources,
    mode_hint,
    probe_audio_source,
    write_audio_debug_snapshot,
    to_azure_pcm16,
    write_pcm16_wav,
)
from .config import AppConfig, load_config, save_config
from .i18n import PRODUCT_NAME, UI_LANGUAGE_OPTIONS, translate
from .languages import LANGUAGES, LANGUAGE_BY_LABEL, source_label_for_locale, target_label_for_code
from .translator import LiveTranslationSession, run_azure_translation_test
from .ai_assistant import generate_interview_answer


COLORS = {
    "bg": "#eef2f1",
    "surface": "#ffffff",
    "panel": "#0f1720",
    "panel_alt": "#111827",
    "panel_answer": "#0d2a26",
    "ink": "#13212b",
    "muted": "#5b6b73",
    "line": "#d7dfdc",
    "accent": "#0f766e",
    "text_on_dark": "#ecf3f1",
}


@dataclass
class TranscriptPanel:
    key: str
    title: str
    frame: ttk.LabelFrame
    widget: ScrolledText
    font_size_var: tk.IntVar = None  # type: ignore[assignment]
    detach_button: ttk.Button | None = None
    copy_button: ttk.Button | None = None
    clear_button: ttk.Button | None = None
    gen_button: ttk.Button | None = None


class DetachedPanel(tk.Toplevel):
    def __init__(
        self,
        master: tk.Misc,
        title: str,
        font_size: int,
        on_generate: object = None,
        gen_label: str = "Cevap Uret",
        header_color: str = "#1e3a2f",
    ) -> None:
        super().__init__(master)
        self.title(title)
        self.geometry("760x460")
        self.resizable(True, True)
        self.minsize(320, 220)
        self.configure(bg=COLORS["bg"])
        self.attributes("-topmost", True)

        self._font_size_var = tk.IntVar(value=font_size)

        # Renkli başlık şeridi (Almanca=kırmızı, Türkçe=mavi)
        header_strip = tk.Frame(self, bg=header_color, height=36)
        header_strip.pack(fill="x")
        header_strip.pack_propagate(False)
        self._header_label = tk.Label(
            header_strip,
            text=title,
            bg=header_color,
            fg="white",
            font=("Bahnschrift SemiBold", 11),
            anchor="w",
        )
        self._header_label.pack(side="left", padx=12, pady=7)

        toolbar = ttk.Frame(self, style="Toolbar.TFrame")
        toolbar.pack(fill="x", padx=10, pady=(8, 0))

        # Font küçült / büyüt
        ttk.Button(toolbar, text="-", style="Soft.TButton", width=2,
                   command=self._make_font_step(-1)).pack(side="left", padx=(0, 2))
        ttk.Button(toolbar, text="+", style="Soft.TButton", width=2,
                   command=self._make_font_step(1)).pack(side="left", padx=(0, 8))

        self.gen_button: ttk.Button | None = None
        if on_generate is not None:
            self.gen_button = ttk.Button(toolbar, text=gen_label, style="Accent.TButton", command=on_generate)
            self.gen_button.pack(side="right")

        self.text = ScrolledText(
            self,
            wrap="word",
            bg=COLORS["panel"],
            fg=COLORS["text_on_dark"],
            insertbackground=COLORS["text_on_dark"],
            relief="flat",
            borderwidth=0,
            padx=16,
            pady=14,
            font=("Bahnschrift", font_size),
        )
        self.text.pack(fill="both", expand=True, padx=10, pady=10)
        self.text.configure(state="disabled")

    def _make_font_step(self, delta: int):
        def _step() -> None:
            new_size = max(10, min(48, self._font_size_var.get() + delta))
            self._font_size_var.set(new_size)
            self.text.configure(font=("Bahnschrift", new_size))
        return _step

    def set_gen_label(self, text: str) -> None:
        if self.gen_button is not None:
            self.gen_button.configure(text=text)

    def set_title(self, text: str) -> None:
        self.title(text)
        self._header_label.configure(text=text)

    def update_text(self, content: str, font_size: int | None = None, *, append: bool = False) -> None:
        current_view = self.text.yview()
        was_near_bottom = current_view[1] >= 0.98
        if font_size is not None:
            self._font_size_var.set(font_size)
        self.text.configure(state="normal", font=("Bahnschrift", self._font_size_var.get()))
        if append:
            self.text.insert("end", content)
        else:
            self.text.delete("1.0", "end")
            self.text.insert("1.0", content)
        if append or was_near_bottom:
            self.text.see("end")
        else:
            self.text.yview_moveto(current_view[0])
        self.text.configure(state="disabled")


class LiveCaptionApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(PRODUCT_NAME)
        self.root.geometry("1380x940")
        self.root.resizable(True, True)
        self.root.minsize(1160, 780)
        self.root.configure(bg=COLORS["bg"])

        self.config = load_config()
        self.event_queue: Queue = Queue()
        self.session: LiveTranslationSession | None = None
        self.monitor_stop_event: threading.Event | None = None
        self.monitor_thread: AudioLevelMonitor | None = None
        self.azure_test_running = False

        self.history: list[dict[str, str]] = []
        self.panels: dict[str, TranscriptPanel] = {}
        self.detached: dict[str, DetachedPanel] = {}
        self.available_sources: list[AudioSource] = []
        self.source_by_label: dict[str, AudioSource] = {}

        self.topmost_var = tk.BooleanVar(value=self.config.topmost)
        self.font_size_var = tk.IntVar(value=self.config.font_size)
        self.ui_language_code = self.config.ui_language if self.config.ui_language in UI_LANGUAGE_OPTIONS else "tr"
        self.ui_language_var = tk.StringVar(value=UI_LANGUAGE_OPTIONS[self.ui_language_code])
        self.status_var = tk.StringVar(value=self._t("ready"))
        self.key_var = tk.StringVar(value=self.config.speech_key)
        self.region_var = tk.StringVar(value=self.config.speech_region)
        self.gemini_key_var = tk.StringVar(value=self.config.gemini_key)
        self.gemini_model_var = tk.StringVar(value=self.config.gemini_model)
        self.ai_profile = self.config.ai_profile
        self.last_question_source = ""
        self.last_question_target = ""
        self.ai_busy = False
        self.source_lang_var = tk.StringVar(value=source_label_for_locale(self.config.source_locale))
        self.target_lang_var = tk.StringVar(value=target_label_for_code(self.config.target_language))
        initial_mode = self.config.capture_mode or MODE_SYSTEM
        self.capture_mode_var = tk.StringVar()
        self.audio_source_var = tk.StringVar(value=self.config.input_device_name)
        self.device_hint_var = tk.StringVar(value="")
        self.meter_text_var = tk.StringVar(value=self._t("level_empty"))
        self.meter_value_var = tk.IntVar(value=0)
        self.capture_mode_code = initial_mode
        self.mode_labels_by_code: dict[str, str] = {}
        self.mode_codes_by_label: dict[str, str] = {}
        self.ui_language_labels = [UI_LANGUAGE_OPTIONS[code] for code in UI_LANGUAGE_OPTIONS]

        self._configure_style()
        self._build_layout()
        self._apply_language(initial_mode)
        self._refresh_audio_sources(startup=True)
        self._apply_topmost()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self._poll_events)
        self.root.after(250, self._startup_device_test)

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=COLORS["bg"], foreground=COLORS["ink"])
        style.configure("Card.TFrame", background=COLORS["surface"])
        style.configure("Toolbar.TFrame", background=COLORS["surface"])
        style.configure(
            "Title.TLabel",
            background=COLORS["surface"],
            foreground=COLORS["ink"],
            font=("Bahnschrift SemiBold", 23),
        )
        style.configure(
            "Muted.TLabel",
            background=COLORS["surface"],
            foreground=COLORS["muted"],
            font=("Aptos", 10),
        )
        style.configure(
            "Hint.TLabel",
            background=COLORS["surface"],
            foreground=COLORS["muted"],
            font=("Aptos", 9),
        )
        style.configure(
            "Section.TLabelframe",
            background=COLORS["surface"],
            bordercolor=COLORS["line"],
            relief="solid",
            borderwidth=1,
        )
        style.configure(
            "Section.TLabelframe.Label",
            background=COLORS["surface"],
            foreground=COLORS["ink"],
            font=("Bahnschrift SemiBold", 12),
        )
        style.configure(
            "Accent.TButton",
            background=COLORS["accent"],
            foreground="#ffffff",
            borderwidth=0,
            focusthickness=0,
            font=("Bahnschrift SemiBold", 10),
            padding=(12, 7),
        )
        style.map(
            "Accent.TButton",
            background=[("active", "#0b625c"), ("disabled", "#9fb8b5")],
            foreground=[("disabled", "#f7fbfa")],
        )
        style.configure(
            "Soft.TButton",
            background="#ffffff",
            foreground=COLORS["ink"],
            bordercolor=COLORS["line"],
            lightcolor="#ffffff",
            darkcolor="#ffffff",
            font=("Aptos", 10),
            padding=(10, 6),
        )
        style.configure("TCheckbutton", background=COLORS["surface"], foreground=COLORS["ink"])
        style.configure("TCombobox", padding=5)
        style.configure("TEntry", padding=5)
        style.configure("TSpinbox", padding=5)
        style.configure(
            "Level.Horizontal.TProgressbar",
            troughcolor="#dfe8e5",
            background=COLORS["accent"],
            bordercolor="#dfe8e5",
            lightcolor=COLORS["accent"],
            darkcolor=COLORS["accent"],
        )

    def _t(self, key: str, **kwargs) -> str:
        return translate(self.ui_language_code, key, **kwargs)

    def _mode_label(self, mode: str) -> str:
        return {
            MODE_SYSTEM: self._t("mode_system"),
            MODE_VIRTUAL: self._t("mode_virtual"),
            MODE_MICROPHONE: self._t("mode_microphone"),
        }.get(mode, mode)

    def _mode_hint(self, mode: str, has_sources: bool) -> str:
        if has_sources:
            return {
                MODE_SYSTEM: self._t("hint_system_ok"),
                MODE_VIRTUAL: self._t("hint_virtual_ok"),
                MODE_MICROPHONE: self._t("hint_microphone_ok"),
            }[mode]
        return {
            MODE_SYSTEM: self._t("hint_system_missing"),
            MODE_VIRTUAL: self._t("hint_virtual_missing"),
            MODE_MICROPHONE: self._t("hint_microphone_missing"),
        }[mode]

    def _apply_language(self, preferred_mode: str | None = None) -> None:
        self.root.title(self._t("window_title"))
        self.header_title_label.configure(text=self._t("app_title"))
        self.header_subtitle_label.configure(text=self._t("app_subtitle"))

        self.ui_language_label.configure(text=self._t("ui_language"))
        self.azure_key_label.configure(text=self._t("azure_key"))
        self.region_label.configure(text=self._t("region"))
        self.save_button.configure(text=self._t("save"))
        self.preflight_button.configure(text=self._t("preflight"))
        self.azure_test_button.configure(text=self._t("azure_test"))
        self.azure_costs_button.configure(text=self._t("azure_costs"))

        self.capture_mode_label.configure(text=self._t("capture_mode"))
        self.audio_source_label.configure(text=self._t("audio_source"))
        self.refresh_button.configure(text=self._t("refresh"))
        self.device_test_button.configure(text=self._t("device_test"))
        self.debug_audio_button.configure(text=self._t("debug_audio"))

        self.gemini_key_label.configure(text=self._t("gemini_key"))
        self.gemini_model_label.configure(text=self._t("gemini_model"))
        self.ai_profile_button.configure(text=self._t("ai_profile"))
        self.generate_answer_button.configure(text=self._t("generate_answer"))

        self.source_language_label.configure(text=self._t("source_language"))
        self.target_language_label.configure(text=self._t("target_language"))
        self.start_button.configure(text=self._t("start"))
        self.stop_button.configure(text=self._t("stop"))
        self.clear_button.configure(text=self._t("clear"))
        self.export_button.configure(text=self._t("export"))
        self.import_button.configure(text=self._t("import"))
        self.topmost_checkbutton.configure(text=self._t("topmost"))
        self.zoom_label.configure(text=self._t("zoom"))

        panel_titles = {
            "partial_source": self._t("partial_source"),
            "partial_target": self._t("partial_target"),
            "final_source": self._t("final_source"),
            "final_target": self._t("final_target"),
            "answer_source": self._t("answer_source"),
            "answer_target": self._t("answer_target"),
        }
        for key, panel in self.panels.items():
            panel.title = panel_titles[key]
            panel.frame.configure(text=panel.title)
            if panel.detach_button is not None:
                panel.detach_button.configure(text=self._t("detach"))
            if panel.copy_button is not None:
                panel.copy_button.configure(text=self._t("copy"))
            if panel.clear_button is not None:
                panel.clear_button.configure(text=self._t("clear"))
            if panel.gen_button is not None:
                panel.gen_button.configure(text=self._t("generate_answer"))
        for key, window in list(self.detached.items()):
            if window.winfo_exists():
                window.set_title(self.panels[key].title)
                window.set_gen_label(self._t("generate_answer"))

        self.mode_labels_by_code = {mode: self._mode_label(mode) for mode, _ in MODE_CHOICES}
        self.mode_codes_by_label = {label: mode for mode, label in self.mode_labels_by_code.items()}
        self.mode_combo.configure(values=list(self.mode_labels_by_code.values()))
        current_mode = preferred_mode or self.capture_mode_code or MODE_SYSTEM
        self.capture_mode_var.set(self.mode_labels_by_code.get(current_mode, self._mode_label(MODE_SYSTEM)))
        self.ui_language_combo.configure(values=self.ui_language_labels)
        self.ui_language_var.set(UI_LANGUAGE_OPTIONS[self.ui_language_code])
        self._update_meter(self.meter_value_var.get() / 100 if self.meter_value_var.get() else 0.0)

    def _on_ui_language_changed(self) -> None:
        selected_label = self.ui_language_var.get()
        selected_code = next(
            (code for code, label in UI_LANGUAGE_OPTIONS.items() if label == selected_label),
            "tr",
        )
        self.ui_language_code = selected_code
        self.config.ui_language = selected_code
        self._apply_language(self.capture_mode_code)
        self._refresh_audio_sources()
        self._persist_minor_settings()

    def _build_layout(self) -> None:
        shell = ttk.Frame(self.root, style="Card.TFrame", padding=18)
        shell.pack(fill="both", expand=True, padx=16, pady=16)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(1, weight=1)

        header = ttk.Frame(shell, style="Card.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        header.columnconfigure(0, weight=1)
        self.header_title_label = ttk.Label(header, text="", style="Title.TLabel")
        self.header_title_label.grid(row=0, column=0, sticky="w")
        self.header_subtitle_label = ttk.Label(
            header,
            text="",
            style="Muted.TLabel",
        )
        self.header_subtitle_label.grid(row=1, column=0, sticky="w", pady=(4, 0))

        controls = ttk.Frame(shell, style="Card.TFrame", padding=(14, 14, 14, 10))
        controls.grid(row=1, column=0, sticky="nsew")
        controls.columnconfigure(0, weight=1)
        controls.rowconfigure(5, weight=1)

        self._build_credentials_row(controls)
        self._build_audio_row(controls)
        self._build_ai_row(controls)
        self._build_action_row(controls)
        self._build_board(controls)

        status_bar = ttk.Frame(shell, style="Card.TFrame", padding=(2, 10, 2, 0))
        status_bar.grid(row=2, column=0, sticky="ew")
        ttk.Label(status_bar, textvariable=self.status_var, style="Muted.TLabel").pack(anchor="w")

    def _bind_right_click_paste(self, widget: tk.Widget) -> None:
        """Entry ve ScrolledText icin sag tik kopyala/yapistir menusu baglar."""
        def show_menu(event: tk.Event) -> None:
            menu = tk.Menu(self.root, tearoff=0)
            menu.configure(bg=COLORS["surface"], fg=COLORS["ink"])
            try:
                has_sel = bool(widget.selection_get())
            except tk.TclError:
                has_sel = False
            menu.add_command(
                label=self._t("ctx_cut"),
                command=lambda: widget.event_generate("<<Cut>>"),
                state="normal" if has_sel else "disabled",
            )
            menu.add_command(
                label=self._t("ctx_copy"),
                command=lambda: widget.event_generate("<<Copy>>"),
                state="normal" if has_sel else "disabled",
            )
            menu.add_command(
                label=self._t("ctx_paste"),
                command=lambda: widget.event_generate("<<Paste>>"),
            )
            menu.tk_popup(event.x_root, event.y_root)
        widget.bind("<Button-3>", show_menu)

    def _build_credentials_row(self, parent: ttk.Frame) -> None:
        row = ttk.Frame(parent, style="Toolbar.TFrame")
        row.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        for column in range(10):
            row.columnconfigure(column, weight=1 if column in (1, 3) else 0)

        self.azure_key_label = ttk.Label(row, text="")
        self.azure_key_label.grid(row=0, column=0, sticky="w")
        key_entry = ttk.Entry(row, textvariable=self.key_var, show="*")
        key_entry.grid(row=0, column=1, sticky="ew", padx=(6, 12))
        self._bind_right_click_paste(key_entry)
        self.region_label = ttk.Label(row, text="")
        self.region_label.grid(row=0, column=2, sticky="w")
        region_entry = ttk.Entry(row, textvariable=self.region_var, width=18)
        region_entry.grid(row=0, column=3, sticky="ew", padx=(6, 12))
        self._bind_right_click_paste(region_entry)
        self.ui_language_label = ttk.Label(row, text="")
        self.ui_language_label.grid(row=0, column=4, sticky="e", padx=(0, 6))
        self.ui_language_combo = ttk.Combobox(
            row,
            textvariable=self.ui_language_var,
            state="readonly",
            values=self.ui_language_labels,
            width=10,
        )
        self.ui_language_combo.grid(row=0, column=5, sticky="w", padx=(0, 8))
        self.ui_language_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_ui_language_changed())
        self.save_button = ttk.Button(row, text="", style="Soft.TButton", command=self.save_settings)
        self.save_button.grid(row=0, column=6, padx=(0, 8))
        self.preflight_button = ttk.Button(row, text="", style="Soft.TButton", command=self.run_preflight_check)
        self.preflight_button.grid(row=0, column=7, padx=(0, 8))
        self.azure_test_button = ttk.Button(
            row,
            text="",
            style="Soft.TButton",
            command=self.run_azure_test,
        )
        self.azure_test_button.grid(row=0, column=8)
        self.azure_costs_button = ttk.Button(
            row,
            text="",
            style="Soft.TButton",
            command=self.open_azure_costs,
        )
        self.azure_costs_button.grid(row=0, column=9, padx=(8, 0))

    def _build_audio_row(self, parent: ttk.Frame) -> None:
        row = ttk.Frame(parent, style="Toolbar.TFrame")
        row.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        for column in range(11):
            row.columnconfigure(column, weight=1 if column in (1, 3, 7) else 0)

        self.capture_mode_label = ttk.Label(row, text="")
        self.capture_mode_label.grid(row=0, column=0, sticky="w")
        self.mode_combo = ttk.Combobox(
            row,
            textvariable=self.capture_mode_var,
            state="readonly",
            values=[],
        )
        self.mode_combo.grid(row=0, column=1, sticky="ew", padx=(6, 12))
        self.mode_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_mode_changed())

        self.audio_source_label = ttk.Label(row, text="")
        self.audio_source_label.grid(row=0, column=2, sticky="w")
        self.source_combo = ttk.Combobox(row, textvariable=self.audio_source_var, state="readonly")
        self.source_combo.grid(row=0, column=3, sticky="ew", padx=(6, 12))
        self.source_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_source_changed())

        self.refresh_button = ttk.Button(row, text="", style="Soft.TButton", command=self._refresh_audio_sources)
        self.refresh_button.grid(row=0, column=4, padx=(0, 8))
        self.device_test_button = ttk.Button(row, text="", style="Soft.TButton", command=self._probe_selected_source)
        self.device_test_button.grid(row=0, column=5, padx=(0, 8))
        self.debug_audio_button = ttk.Button(row, text="", style="Soft.TButton", command=self.run_audio_debug)
        self.debug_audio_button.grid(row=0, column=6, padx=(0, 8))

        meter_frame = ttk.Frame(row, style="Toolbar.TFrame")
        meter_frame.grid(row=0, column=7, sticky="ew", padx=(4, 8))
        meter_frame.columnconfigure(0, weight=1)
        ttk.Progressbar(
            meter_frame,
            style="Level.Horizontal.TProgressbar",
            variable=self.meter_value_var,
            maximum=100,
        ).grid(row=0, column=0, sticky="ew")
        ttk.Label(meter_frame, textvariable=self.meter_text_var, style="Muted.TLabel").grid(
            row=0, column=1, sticky="w", padx=(8, 0)
        )

        ttk.Label(parent, textvariable=self.device_hint_var, style="Hint.TLabel").grid(
            row=2, column=0, sticky="w", pady=(0, 12)
        )

    def _build_ai_row(self, parent: ttk.Frame) -> None:
        row = ttk.Frame(parent, style="Toolbar.TFrame")
        row.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        for column in range(6):
            row.columnconfigure(column, weight=1 if column == 1 else 0)

        self.gemini_key_label = ttk.Label(row, text="")
        self.gemini_key_label.grid(row=0, column=0, sticky="w")
        gemini_key_entry = ttk.Entry(row, textvariable=self.gemini_key_var, show="*")
        gemini_key_entry.grid(row=0, column=1, sticky="ew", padx=(6, 12))
        self._bind_right_click_paste(gemini_key_entry)
        self.gemini_model_label = ttk.Label(row, text="")
        self.gemini_model_label.grid(row=0, column=2, sticky="w")
        gemini_model_entry = ttk.Entry(row, textvariable=self.gemini_model_var, width=22)
        gemini_model_entry.grid(row=0, column=3, sticky="ew", padx=(6, 12))
        self._bind_right_click_paste(gemini_model_entry)
        self.ai_profile_button = ttk.Button(
            row,
            text="",
            style="Soft.TButton",
            command=self.edit_ai_profile,
        )
        self.ai_profile_button.grid(row=0, column=4, padx=(0, 8))
        self.generate_answer_button = ttk.Button(
            row,
            text="",
            style="Accent.TButton",
            command=self.generate_ai_answer,
        )
        self.generate_answer_button.grid(row=0, column=5, padx=(0, 8))

    def _build_action_row(self, parent: ttk.Frame) -> None:
        row = ttk.Frame(parent, style="Toolbar.TFrame")
        row.grid(row=4, column=0, sticky="ew", pady=(0, 14))
        for column in range(12):
            row.columnconfigure(column, weight=1 if column in (1, 3) else 0)

        self.source_language_label = ttk.Label(row, text="")
        self.source_language_label.grid(row=0, column=0, sticky="w")
        self.source_lang_combo = ttk.Combobox(
            row,
            textvariable=self.source_lang_var,
            state="readonly",
            values=[item.label for item in LANGUAGES],
        )
        self.source_lang_combo.grid(row=0, column=1, sticky="ew", padx=(6, 12))

        self.target_language_label = ttk.Label(row, text="")
        self.target_language_label.grid(row=0, column=2, sticky="w")
        self.target_lang_combo = ttk.Combobox(
            row,
            textvariable=self.target_lang_var,
            state="readonly",
            values=[item.label for item in LANGUAGES],
        )
        self.target_lang_combo.grid(row=0, column=3, sticky="ew", padx=(6, 12))

        self.start_button = ttk.Button(row, text="", style="Accent.TButton", command=self.start_session)
        self.start_button.grid(row=0, column=4, padx=(0, 8))
        self.stop_button = ttk.Button(
            row,
            text="",
            style="Soft.TButton",
            command=self.stop_session,
            state="disabled",
        )
        self.stop_button.grid(row=0, column=5, padx=(0, 8))
        self.clear_button = ttk.Button(row, text="", style="Soft.TButton", command=self.clear_all)
        self.clear_button.grid(row=0, column=6, padx=(0, 8))
        self.export_button = ttk.Button(row, text="", style="Soft.TButton", command=self.export_history)
        self.export_button.grid(row=0, column=7, padx=(0, 8))
        self.import_button = ttk.Button(row, text="", style="Soft.TButton", command=self.import_history)
        self.import_button.grid(row=0, column=8, padx=(0, 8))
        self.topmost_checkbutton = ttk.Checkbutton(
            row,
            text="",
            variable=self.topmost_var,
            command=self._apply_topmost,
        )
        self.topmost_checkbutton.grid(row=0, column=9, padx=(8, 10))
        self.zoom_label = ttk.Label(row, text="")
        self.zoom_label.grid(row=0, column=10, sticky="e")
        ttk.Spinbox(
            row,
            from_=12,
            to=34,
            textvariable=self.font_size_var,
            width=5,
            command=self._apply_font_size,
        ).grid(row=0, column=11, sticky="w", padx=(6, 0))

    def _build_board(self, parent: ttk.Frame) -> None:
        board = ttk.Frame(parent, style="Card.TFrame")
        board.grid(row=5, column=0, sticky="nsew")
        board.columnconfigure(0, weight=1)
        board.columnconfigure(1, weight=1)
        board.rowconfigure(0, weight=1)
        board.rowconfigure(1, weight=1)
        board.rowconfigure(2, weight=1)

        self._create_panel(board, 0, 0, "partial_source", "Anlik Orijinal")
        self._create_panel(board, 0, 1, "partial_target", "Anlik Ceviri")
        self._create_panel(board, 1, 0, "final_source", "Final Orijinal")
        self._create_panel(board, 1, 1, "final_target", "Final Ceviri")
        self._create_panel(board, 2, 0, "answer_source", "Onerilen Cevap (Soylenecek)")
        self._create_panel(board, 2, 1, "answer_target", "Cevabin Anlami")

    def _create_panel(self, parent: ttk.Frame, row: int, column: int, key: str, title: str) -> None:
        frame = ttk.LabelFrame(parent, text=title, style="Section.TLabelframe", padding=(10, 10, 10, 10))
        frame.grid(row=row, column=column, sticky="nsew", padx=6, pady=6)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        actions = ttk.Frame(frame, style="Toolbar.TFrame")
        actions.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        actions.columnconfigure(0, weight=1)

        action_column = 1
        detach_button = None
        # Partial ve answer panelleri ayrilabilir; final paneller sabit kalir
        if not key.startswith("final"):
            detach_button = ttk.Button(actions, text="", style="Soft.TButton", command=lambda: self.toggle_detached(key))
            detach_button.grid(row=0, column=action_column, padx=(0, 6))
            action_column += 1
        copy_button = ttk.Button(actions, text="", style="Soft.TButton", command=lambda: self.copy_panel(key))
        copy_button.grid(row=0, column=action_column, padx=(0, 6))
        action_column += 1
        clear_button = None
        if not key.startswith("partial"):
            clear_button = ttk.Button(actions, text="", style="Soft.TButton", command=lambda: self.set_panel_text(key, ""))
            clear_button.grid(row=0, column=action_column, padx=(0, 6))
            action_column += 1
        # Cevap Uret butonu: soru panellerinde (source/target) goster, answer panellerinde goster ma
        gen_button = None
        if not key.startswith("answer"):
            gen_button = ttk.Button(actions, text="", style="Soft.TButton", command=self.generate_ai_answer)
            gen_button.grid(row=0, column=action_column, padx=(0, 6))
            action_column += 1

        # Her panele kendi font buyutme/kucultme butonlari (placeholder — widget sonra bağlanır)
        panel_font_var = tk.IntVar(value=self.font_size_var.get())
        minus_btn = ttk.Button(actions, text="-", style="Soft.TButton", width=2)
        minus_btn.grid(row=0, column=action_column, padx=(0, 2))
        plus_btn = ttk.Button(actions, text="+", style="Soft.TButton", width=2)
        plus_btn.grid(row=0, column=action_column + 1, padx=(0, 6))

        if key.startswith("partial"):
            panel_bg = COLORS["panel"]
        elif key.startswith("answer"):
            panel_bg = COLORS["panel_answer"]
        else:
            panel_bg = COLORS["panel_alt"]
        widget = ScrolledText(
            frame,
            wrap="word",
            height=10,
            bg=panel_bg,
            fg=COLORS["text_on_dark"],
            insertbackground=COLORS["text_on_dark"],
            relief="flat",
            borderwidth=0,
            padx=16,
            pady=16,
            font=("Bahnschrift", panel_font_var.get()),
        )
        widget.grid(row=1, column=0, sticky="nsew")
        widget.configure(state="disabled")
        self._bind_right_click_paste(widget)

        # Widget hazir, font adim komutlarini bagla
        def _make_step(delta: int) -> object:
            def _step() -> None:
                new_size = max(10, min(48, panel_font_var.get() + delta))
                panel_font_var.set(new_size)
                widget.configure(font=("Bahnschrift", new_size))
                dp = self.detached.get(key)
                if dp and dp.winfo_exists():
                    dp.update_text(self.get_panel_text(key), new_size)
            return _step

        minus_btn.configure(command=_make_step(-1))
        plus_btn.configure(command=_make_step(1))

        self.panels[key] = TranscriptPanel(
            key=key,
            title=title,
            frame=frame,
            widget=widget,
            font_size_var=panel_font_var,
            detach_button=detach_button,
            copy_button=copy_button,
            clear_button=clear_button,
            gen_button=gen_button,
        )

    def _selected_source(self) -> AudioSource | None:
        return self.source_by_label.get(self.audio_source_var.get())

    def _on_mode_changed(self) -> None:
        self.capture_mode_code = self.mode_codes_by_label.get(self.capture_mode_var.get(), MODE_SYSTEM)
        self._refresh_audio_sources()
        self._persist_minor_settings()

    def _on_source_changed(self) -> None:
        self._restart_monitor()
        self._probe_selected_source(silent=True)

    def _refresh_audio_sources(self, startup: bool = False) -> None:
        selected_before = self.audio_source_var.get()
        mode = self.capture_mode_code
        self.available_sources = list_audio_sources(mode)
        self.source_by_label = {source.display_name: source for source in self.available_sources}

        labels = [source.display_name for source in self.available_sources]
        self.source_combo.configure(values=labels, state="readonly" if labels else "disabled")

        source = find_audio_source(self.available_sources, selected_before or self.config.input_device_name)
        if source:
            self.audio_source_var.set(source.display_name)
        else:
            self.audio_source_var.set("")

        self.device_hint_var.set(self._mode_hint(mode, bool(self.available_sources)))
        if startup or not self.session:
            self._restart_monitor()

    def _startup_device_test(self) -> None:
        self._probe_selected_source(silent=True)

    def _restart_monitor(self) -> None:
        self._stop_monitor()
        if self.session is not None:
            return

        source = self._selected_source()
        if source is None:
            self.meter_value_var.set(0)
            self.meter_text_var.set(self._t("level_empty"))
            return

        self.monitor_stop_event = threading.Event()
        self.monitor_thread = AudioLevelMonitor(
            source=source,
            stop_event=self.monitor_stop_event,
            on_level=lambda value: self.event_queue.put({"type": "meter", "value": value}),
            on_status=lambda message: self.event_queue.put({"type": "status", "message": message}),
            on_error=lambda message: self.event_queue.put({"type": "monitor_error", "message": message}),
        )
        self.monitor_thread.start()

    def _stop_monitor(self) -> None:
        if self.monitor_stop_event is not None:
            self.monitor_stop_event.set()
        if self.monitor_thread is not None:
            self.monitor_thread.join(timeout=0.5)
        self.monitor_stop_event = None
        self.monitor_thread = None

    def _probe_selected_source(self, silent: bool = False) -> None:
        source = self._selected_source()
        if source is None:
            if not silent:
                messagebox.showerror(self._t("no_audio_source_title"), self._t("no_audio_source_message"))
            return

        self._stop_monitor()

        def worker() -> None:
            try:
                ok, message, level = probe_audio_source(source)
                self.event_queue.put(
                    {"type": "probe", "ok": ok, "message": message, "level": level, "silent": silent}
                )
            finally:
                self.event_queue.put({"type": "probe_done"})

        threading.Thread(target=worker, daemon=True).start()

    def _collect_preflight_issues(self) -> tuple[list[str], list[str]]:
        errors: list[str] = []
        warnings: list[str] = []

        key = self.key_var.get().strip()
        region = self.region_var.get().strip().lower()
        source = self._selected_source()

        if not key:
            errors.append(self._t("azure_key_empty"))
        if not region:
            errors.append(self._t("region_empty"))
        elif not re.fullmatch(r"[a-z0-9-]+", region):
            errors.append(self._t("region_invalid"))

        if source is None:
            mode = self.capture_mode_code
            errors.append(self._mode_hint(mode, False))
        else:
            self._stop_monitor()
            try:
                ok, message, level = probe_audio_source(source, seconds=0.18)
            finally:
                if self.session is None and not self.azure_test_running:
                    self._restart_monitor()
            if not ok:
                errors.append(f"Ses kaynagi testi basarisiz: {message}")
            elif level <= 0.01:
                warnings.append("Audio source opened, but the current signal level is low or silent." if self.ui_language_code == "en" else "Audioquelle geoeffnet, aber das aktuelle Signal ist zu niedrig oder still." if self.ui_language_code == "de" else "Ses kaynagi acildi ama su anda anlamli bir sinyal yok. Kaynak sessiz olabilir.")

        try:
            from azure.cognitiveservices import speech as speechsdk

            if key and region:
                speechsdk.translation.SpeechTranslationConfig(subscription=key, region=region)
        except Exception:
            errors.append(
                "Azure Speech SDK could not be loaded. Make sure you are running the correct build from the dist folder." if self.ui_language_code == "en" else "Azure Speech SDK konnte nicht geladen werden. Stellen Sie sicher, dass Sie den richtigen Build aus dem dist-Ordner starten." if self.ui_language_code == "de" else "Azure Speech SDK yuklenemedi. Dogru build'i kullandiginizdan ve dist klasorundeki exe'yi calistirdiginizdan emin olun."
            )

        return errors, warnings

    def run_preflight_check(self) -> None:
        errors, warnings = self._collect_preflight_issues()
        if errors:
            messagebox.showerror(
                self._t("preflight_failed"),
                "\n".join(f"- {item}" for item in errors),
            )
            return

        if warnings:
            messagebox.showwarning(
                self._t("preflight_warning"),
                "\n".join(f"- {item}" for item in warnings),
            )
        else:
            messagebox.showinfo(self._t("preflight_ok"), self._t("preflight_ok_message"))

    def open_azure_costs(self) -> None:
        webbrowser.open("https://portal.azure.com/")
        messagebox.showinfo(
            self._t("azure_costs_title"),
            self._t("azure_costs_message"),
        )

    def run_audio_debug(self) -> None:
        source = self._selected_source()
        log_path = clear_audio_debug_log()
        snapshot_path = write_audio_debug_snapshot(
            selected_source=source,
            available_sources=self.available_sources,
            note="UI debug request",
        )
        self._stop_monitor()
        self.status_var.set(self._t("audio_debug_running"))

        if source is None:
            messagebox.showinfo(
                self._t("audio_debug_title"),
                self._t("audio_debug_result_no_source", snapshot=snapshot_path, log=log_path),
            )
            if self.session is None and not self.azure_test_running:
                self._restart_monitor()
            return

        def worker() -> None:
            try:
                ok, message, level = probe_audio_source(source, seconds=0.3)
                final_snapshot = write_audio_debug_snapshot(
                    selected_source=source,
                    available_sources=self.available_sources,
                    note=f"probe ok={ok} level={level:.6f} message={message}",
                )
                self.event_queue.put(
                    {
                        "type": "audio_debug_result",
                        "ok": ok,
                        "message": message,
                        "level": level,
                        "snapshot_path": final_snapshot,
                        "log_path": get_audio_debug_log_path(),
                    }
                )
            except Exception as exc:
                self.event_queue.put(
                    {
                        "type": "audio_debug_error",
                        "message": str(exc),
                        "snapshot_path": snapshot_path,
                        "log_path": get_audio_debug_log_path(),
                    }
                )
            finally:
                self.event_queue.put({"type": "audio_debug_done"})

        threading.Thread(target=worker, daemon=True).start()

    def run_azure_test(self) -> None:
        if self.session and self.session.running:
            messagebox.showinfo(self._t("azure_test_title"), self._t("azure_test_running"))
            return
        if self.azure_test_running:
            return

        source = self._selected_source()
        key = self.key_var.get().strip()
        region = self.region_var.get().strip().lower()
        if not key:
            messagebox.showerror(self._t("azure_test_title"), self._t("azure_key_empty"))
            return
        if not region:
            messagebox.showerror(self._t("azure_test_title"), self._t("region_empty"))
            return
        if not re.fullmatch(r"[a-z0-9-]+", region):
            messagebox.showerror(self._t("azure_test_title"), self._t("region_invalid"))
            return
        if source is None:
            messagebox.showerror(self._t("azure_test_title"), self._t("azure_test_no_source"))
            return

        self._stop_monitor()
        self.azure_test_running = True
        self.azure_test_button.configure(state="disabled")
        self.status_var.set(self._t("azure_test_capturing"))

        selected_source_lang = LANGUAGE_BY_LABEL[self.source_lang_var.get()]
        selected_target_lang = LANGUAGE_BY_LABEL[self.target_lang_var.get()]

        def worker() -> None:
            try:
                self.event_queue.put({"type": "status", "message": self._t("azure_test_sampling")})
                sample = capture_audio_sample(
                    source,
                    seconds=4.0,
                    on_level=lambda value: self.event_queue.put({"type": "meter", "value": value}),
                )
                pcm16 = to_azure_pcm16(sample, source.samplerate)

                sample_dir = os.path.join(tempfile.gettempdir(), "CeviriApp")
                os.makedirs(sample_dir, exist_ok=True)
                sample_path = os.path.join(
                    sample_dir,
                    f"azure-test-{datetime.now().strftime('%Y%m%d-%H%M%S')}.wav",
                )
                write_pcm16_wav(sample_path, pcm16)

                self.event_queue.put({"type": "status", "message": self._t("azure_test_sending")})
                result = run_azure_translation_test(
                    speech_key=key,
                    speech_region=region,
                    source_locale=selected_source_lang.speech_locale,
                    target_language=selected_target_lang.translation_code,
                    audio_bytes=pcm16.tobytes(),
                )
                self.event_queue.put(
                    {
                        "type": "azure_test_result",
                        "result": result,
                        "sample_path": sample_path,
                        "source_label": selected_source_lang.label,
                        "target_label": selected_target_lang.label,
                    }
                )
            except Exception as exc:
                self.event_queue.put({"type": "azure_test_error", "message": str(exc)})
            finally:
                self.event_queue.put({"type": "azure_test_done"})

        threading.Thread(target=worker, daemon=True).start()

    def save_settings(self) -> None:
        source_option = LANGUAGE_BY_LABEL[self.source_lang_var.get()]
        target_option = LANGUAGE_BY_LABEL[self.target_lang_var.get()]
        self.config = AppConfig(
            speech_key=self.key_var.get().strip(),
            speech_region=self.region_var.get().strip(),
            gemini_key=self.gemini_key_var.get().strip(),
            gemini_model=self.gemini_model_var.get().strip(),
            ai_profile=self.ai_profile,
            source_locale=source_option.speech_locale,
            target_language=target_option.translation_code,
            ui_language=self.ui_language_code,
            capture_mode=self.capture_mode_code,
            input_device_name=self.audio_source_var.get(),
            topmost=self.topmost_var.get(),
            font_size=self.font_size_var.get(),
        )
        save_config(self.config)
        self.status_var.set(self._t("settings_saved"))

    def _persist_minor_settings(self) -> None:
        self.config.ui_language = self.ui_language_code
        self.config.capture_mode = self.capture_mode_code
        self.config.input_device_name = self.audio_source_var.get()
        self.config.topmost = self.topmost_var.get()
        self.config.font_size = self.font_size_var.get()
        save_config(self.config)

    def _apply_topmost(self) -> None:
        keep_top = self.topmost_var.get()
        self.root.attributes("-topmost", keep_top)
        if keep_top:
            # "Yumusak" ustekal: baska uygulamaya gecildiginde arkaplanlasmaya izin ver.
            # focus_get() None donunce focus bizim disimizda demektir.
            self.root.bind_all("<FocusOut>", self._on_focus_change)
            self.root.bind_all("<FocusIn>", self._on_focus_change)
        else:
            self.root.unbind_all("<FocusOut>")
            self.root.unbind_all("<FocusIn>")
        for panel in self.detached.values():
            if panel.winfo_exists():
                panel.attributes("-topmost", keep_top)
        self._persist_minor_settings()

    def _on_focus_change(self, _event: tk.Event) -> None:
        self.root.after(60, self._sync_topmost_to_focus)

    def _sync_topmost_to_focus(self) -> None:
        if not self.topmost_var.get():
            return
        # focus_get() None ise hicbir widget'imiz focus'ta degil = baska uygulamada
        has_focus = self.root.focus_get() is not None
        self.root.attributes("-topmost", has_focus)

    def _apply_font_size(self) -> None:
        font_size = self.font_size_var.get()
        for panel in self.panels.values():
            # Global zoom: panel'in kendi font_size_var'ini da senkronize et
            if panel.font_size_var is not None:
                panel.font_size_var.set(font_size)
            panel.widget.configure(font=("Bahnschrift", font_size))
        for key, window in list(self.detached.items()):
            if window.winfo_exists():
                window.update_text(self.get_panel_text(key), font_size)
        self._persist_minor_settings()

    def start_session(self) -> None:
        if self.session and self.session.running:
            return

        selected_source = self._selected_source()
        self._stop_monitor()
        errors, warnings = self._collect_preflight_issues()
        if errors:
            self._restart_monitor()
            messagebox.showerror(
                self._t("start_check_failed"),
                "\n".join(f"- {item}" for item in errors),
            )
            return
        if warnings:
            self.status_var.set(warnings[0])

        self._stop_monitor()
        self.save_settings()

        selected_source_lang = LANGUAGE_BY_LABEL[self.source_lang_var.get()]
        selected_target_lang = LANGUAGE_BY_LABEL[self.target_lang_var.get()]

        try:
            self.session = LiveTranslationSession(
                speech_key=self.key_var.get(),
                speech_region=self.region_var.get().strip().lower(),
                source_locale=selected_source_lang.speech_locale,
                target_language=selected_target_lang.translation_code,
                source=selected_source,
                event_queue=self.event_queue,
            )
            self.session.start()
        except Exception as exc:
            self.session = None
            self._restart_monitor()
            messagebox.showerror(self._t("start_error"), self._friendly_runtime_error(str(exc)))
            return

        self.stop_button.configure(state="normal")
        self.status_var.set(self._t("working_status", source=selected_source_lang.label, target=selected_target_lang.label, device=selected_source.name))

    def stop_session(self) -> None:
        if self.session is None:
            return
        self.session.stop()
        self.session = None
        self.stop_button.configure(state="disabled")
        self._restart_monitor()

    def clear_all(self) -> None:
        self.history.clear()
        self.last_question_source = ""
        self.last_question_target = ""
        for key in self.panels:
            self.set_panel_text(key, "")
        self.status_var.set(self._t("cleared"))

    def edit_ai_profile(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title(self._t("ai_profile_title"))
        dialog.configure(bg=COLORS["surface"])
        dialog.transient(self.root)
        dialog.geometry("560x420")
        dialog.attributes("-topmost", self.topmost_var.get())

        container = ttk.Frame(dialog, style="Card.TFrame", padding=16)
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(1, weight=1)

        ttk.Label(container, text=self._t("ai_profile_hint"), style="Hint.TLabel", wraplength=520).grid(
            row=0, column=0, sticky="w", pady=(0, 10)
        )
        text_box = ScrolledText(
            container,
            wrap="word",
            bg=COLORS["panel_alt"],
            fg=COLORS["text_on_dark"],
            insertbackground=COLORS["text_on_dark"],
            relief="flat",
            borderwidth=0,
            padx=12,
            pady=12,
            font=("Bahnschrift", 12),
        )
        text_box.grid(row=1, column=0, sticky="nsew")
        text_box.insert("1.0", self.ai_profile)
        text_box.focus_set()

        button_row = ttk.Frame(container, style="Toolbar.TFrame")
        button_row.grid(row=2, column=0, sticky="e", pady=(12, 0))

        def save_and_close() -> None:
            self.ai_profile = text_box.get("1.0", "end-1c").strip()
            self.config.ai_profile = self.ai_profile
            save_config(self.config)
            self.status_var.set(self._t("ai_profile_saved"))
            dialog.destroy()

        ttk.Button(button_row, text=self._t("save"), style="Accent.TButton", command=save_and_close).grid(
            row=0, column=0
        )

    def generate_ai_answer(self) -> None:
        if self.ai_busy:
            return

        api_key = self.gemini_key_var.get().strip()
        if not api_key:
            messagebox.showerror(self._t("ai_error_title"), self._t("ai_key_empty"))
            return

        question = self.last_question_source.strip()
        if not question:
            self.status_var.set(self._t("ai_no_question"))
            return

        question_text = question
        if self.last_question_target.strip():
            question_text = (
                f"{question}\n\n(Translation for context: {self.last_question_target.strip()})"
            )

        answer_language = LANGUAGE_BY_LABEL[self.source_lang_var.get()].label
        understand_language = LANGUAGE_BY_LABEL[self.target_lang_var.get()].label
        model = self.gemini_model_var.get().strip()
        profile = self.ai_profile

        self.ai_busy = True
        self.generate_answer_button.configure(state="disabled")
        self.status_var.set(self._t("ai_thinking"))

        def worker() -> None:
            try:
                result = generate_interview_answer(
                    api_key=api_key,
                    question=question_text,
                    answer_language=answer_language,
                    understand_language=understand_language,
                    model=model,
                    profile=profile,
                )
                self.event_queue.put(
                    {
                        "type": "ai_answer",
                        "answer": result["answer"],
                        "answer_translation": result["answer_translation"],
                    }
                )
            except Exception as exc:  # noqa: BLE001 - kullaniciya hata mesaji gosterilecek
                self.event_queue.put({"type": "ai_error", "message": str(exc)})

        threading.Thread(target=worker, daemon=True).start()

    def get_panel_text(self, key: str) -> str:
        return self.panels[key].widget.get("1.0", "end-1c")

    def set_panel_text(self, key: str, text: str, *, append: bool = False) -> None:
        panel = self.panels[key]
        panel.widget.configure(state="normal")
        if append:
            panel.widget.insert("end", text)
            panel.widget.see("end")
        else:
            panel.widget.delete("1.0", "end")
            panel.widget.insert("1.0", text)
        panel.widget.configure(state="disabled")

        floating = self.detached.get(key)
        if floating and floating.winfo_exists():
            floating.update_text(self.get_panel_text(key) if not append else text, append=append)

    def toggle_detached(self, key: str) -> None:
        if key.startswith("final"):
            return  # final paneller ayrilmaz
        current = self.detached.get(key)
        if current and current.winfo_exists():
            current.destroy()
            self.detached.pop(key, None)
            return

        # Answer panellerinde regenerate mantikli degil; diger panellerde "Cevap Uret" butonu goster
        on_gen = None if key.startswith("answer") else self.generate_ai_answer
        # Almanca (source) paneller kirmizi, Türkçe (target) paneller mavi
        header_color = "#6b1010" if key.endswith("_source") else "#10306b"
        panel = DetachedPanel(
            self.root,
            self.panels[key].title,
            self.font_size_var.get(),
            on_generate=on_gen,
            gen_label=self._t("generate_answer"),
            header_color=header_color,
        )
        panel.attributes("-topmost", self.topmost_var.get())
        panel.update_text(self.get_panel_text(key))
        panel.protocol("WM_DELETE_WINDOW", lambda: self._close_detached(key))
        self.detached[key] = panel

    def _close_detached(self, key: str) -> None:
        panel = self.detached.pop(key, None)
        if panel and panel.winfo_exists():
            panel.destroy()

    def copy_panel(self, key: str) -> None:
        content = self.get_panel_text(key)
        self.root.clipboard_clear()
        self.root.clipboard_append(content)
        self.status_var.set(self._t("copy_status", title=self.panels[key].title))

    def export_history(self) -> None:
        if not self.history:
            messagebox.showinfo(self._t("empty_export_title"), self._t("empty_export_message"))
            return

        file_path = filedialog.asksaveasfilename(
            title=self._t("export_dialog"),
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("Text", "*.txt")],
        )
        if not file_path:
            return

        payload = {
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "source_locale": LANGUAGE_BY_LABEL[self.source_lang_var.get()].speech_locale,
            "target_language": LANGUAGE_BY_LABEL[self.target_lang_var.get()].translation_code,
            "items": self.history,
        }

        if file_path.lower().endswith(".txt"):
            blocks = []
            for item in self.history:
                blocks.append(
                    f"[{item['timestamp']}]\nSOURCE: {item['source']}\nTARGET: {item['target']}\n"
                )
            with open(file_path, "w", encoding="utf-8") as handle:
                handle.write("\n".join(blocks))
        else:
            with open(file_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)

        self.status_var.set(self._t("export_done"))

    def import_history(self) -> None:
        file_path = filedialog.askopenfilename(title=self._t("import_dialog"), filetypes=[("JSON", "*.json")])
        if not file_path:
            return

        try:
            with open(file_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            items = payload["items"]
        except Exception as exc:
            messagebox.showerror(self._t("import_error"), str(exc))
            return

        self.history = list(items)
        self.set_panel_text("final_source", "")
        self.set_panel_text("final_target", "")
        for item in self.history:
            self.set_panel_text("final_source", f"{item['source']}\n\n", append=True)
            self.set_panel_text("final_target", f"{item['target']}\n\n", append=True)
        self.status_var.set(self._t("import_done"))

    def _friendly_runtime_error(self, message: str) -> str:
        lowered = message.lower()
        if "key" in lowered and "region" in lowered:
            return self._t("azure_key_empty") + " / " + self._t("region_invalid")
        if "sdk yuklenemedi" in lowered:
            return message
        if "loopback" in lowered:
            return message
        if "permission" in lowered:
            return "Audio device permission was denied. Check Windows privacy settings." if self.ui_language_code == "en" else "Zugriff auf das Audiogeraet wurde verweigert. Pruefen Sie die Windows-Datenschutzeinstellungen." if self.ui_language_code == "de" else "Ses aygitina erisim izni reddedildi. Windows gizlilik ayarlarini kontrol edin."
        return message

    def _update_meter(self, value: float) -> None:
        percent = int(max(0.0, min(1.0, value)) * 100)
        self.meter_value_var.set(percent)
        self.meter_text_var.set(self._t("level", percent=percent))

    def _poll_events(self) -> None:
        try:
            while True:
                event = self.event_queue.get_nowait()
                event_type = event["type"]

                if event_type == "partial":
                    self.set_panel_text("partial_source", event["source"])
                    self.set_panel_text("partial_target", event["target"])
                elif event_type == "final":
                    self.set_panel_text("final_source", f"{event['source']}\n\n", append=True)
                    self.set_panel_text("final_target", f"{event['target']}\n\n", append=True)
                    self.set_panel_text("partial_source", "")
                    self.set_panel_text("partial_target", "")
                    if event["source"].strip():
                        self.last_question_source = event["source"].strip()
                        self.last_question_target = event["target"].strip()
                    self.history.append(
                        {
                            "timestamp": datetime.now().isoformat(timespec="seconds"),
                            "source": event["source"],
                            "target": event["target"],
                        }
                    )
                elif event_type == "status":
                    self.status_var.set(event["message"])
                elif event_type == "ai_answer":
                    self.ai_busy = False
                    self.generate_answer_button.configure(state="normal")
                    self.set_panel_text("answer_source", event["answer"])
                    self.set_panel_text("answer_target", event["answer_translation"])
                    self.status_var.set(self._t("ai_ready"))
                elif event_type == "ai_error":
                    self.ai_busy = False
                    self.generate_answer_button.configure(state="normal")
                    self.status_var.set(self._t("ai_error_title"))
                    messagebox.showerror(self._t("ai_error_title"), event["message"])
                elif event_type == "canceled":
                    prefix = "Azure canceled: " if self.ui_language_code == "en" else "Azure abgebrochen: " if self.ui_language_code == "de" else "Azure iptal etti: "
                    self.status_var.set(f"{prefix}{event['message']}")
                    self.stop_session()
                elif event_type == "no_match":
                    self.status_var.set(event["message"])
                elif event_type == "error":
                    prefix = "Audio error: " if self.ui_language_code == "en" else "Audiofehler: " if self.ui_language_code == "de" else "Ses hatasi: "
                    self.status_var.set(f"{prefix}{event['message']}")
                    self.stop_session()
                elif event_type == "monitor_error":
                    self.status_var.set(event["message"])
                    self._update_meter(0.0)
                elif event_type == "meter":
                    self._update_meter(event["value"])
                elif event_type == "probe":
                    self._update_meter(event["level"])
                    if event["ok"]:
                        self.status_var.set(event["message"])
                    elif not event.get("silent"):
                        messagebox.showerror(self._t("device_test_failed"), event["message"])
                elif event_type == "probe_done":
                    if self.session is None and not self.azure_test_running:
                        self._restart_monitor()
                elif event_type == "audio_debug_result":
                    self._update_meter(event["level"])
                    self.status_var.set(event["message"])
                    messagebox.showinfo(
                        self._t("audio_debug_title"),
                        self._t(
                            "audio_debug_result",
                            message=event["message"],
                            level=event["level"],
                            snapshot=event["snapshot_path"],
                            log=event["log_path"],
                        ),
                    )
                elif event_type == "audio_debug_error":
                    self.status_var.set(self._t("audio_debug_failed"))
                    messagebox.showerror(
                        self._t("audio_debug_title"),
                        self._t(
                            "audio_debug_error",
                            message=self._friendly_runtime_error(event["message"]),
                            snapshot=event["snapshot_path"],
                            log=event["log_path"],
                        ),
                    )
                elif event_type == "audio_debug_done":
                    if self.session is None and not self.azure_test_running:
                        self._restart_monitor()
                elif event_type == "azure_test_result":
                    result = event["result"]
                    sample_path = event["sample_path"]
                    status = result.get("status", "empty")
                    source_text = result.get("source_text", "")
                    target_text = result.get("target_text", "")
                    summary = (
                        self._t(
                            "azure_test_summary",
                            sample_path=sample_path,
                            message=result.get("message", ""),
                            source_label=event["source_label"],
                            target_label=event["target_label"],
                            source_text=source_text or "-",
                            target_text=target_text or "-",
                        )
                    )

                    if source_text or target_text:
                        self.set_panel_text("partial_source", source_text)
                        self.set_panel_text("partial_target", target_text)
                    if status == "ok":
                        if source_text:
                            self.set_panel_text("final_source", f"{source_text}\n\n", append=True)
                        if target_text:
                            self.set_panel_text("final_target", f"{target_text}\n\n", append=True)
                        self.status_var.set(self._t("azure_test_success"))
                        messagebox.showinfo(self._t("azure_test_title"), summary)
                    elif status == "no_match":
                        self.status_var.set(result.get("message", self._t("azure_test_empty")))
                        messagebox.showwarning(self._t("azure_test_title"), summary)
                    else:
                        self.status_var.set(result.get("message", self._t("azure_test_empty")))
                        messagebox.showwarning(self._t("azure_test_title"), summary)
                elif event_type == "azure_test_error":
                    self.status_var.set(self._t("azure_test_failed"))
                    messagebox.showerror(self._t("azure_test_title"), self._friendly_runtime_error(event["message"]))
                elif event_type == "azure_test_done":
                    self.azure_test_running = False
                    self.azure_test_button.configure(state="normal")
                    if self.session is None:
                        self._restart_monitor()
        except Empty:
            pass
        finally:
            self.root.after(100, self._poll_events)

    def on_close(self) -> None:
        self._stop_monitor()
        if self.session is not None:
            self.session.stop()
            self.session = None
        self.save_settings()
        self.root.destroy()
