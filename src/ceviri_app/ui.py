# -*- coding: utf-8 -*-
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
from .ai_assistant import generate_interview_answer, test_gemini_connection, is_potential_question_or_prompt, format_prompter_card


FONT_FAMILY = "Segoe UI"

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
    """Tek kuşağa entegre kontrollü, serbest boyutlandırılabilir, bağımsız taşınabilir/sabitlenebilir pencere."""

    BORDER_MARGIN = 6

    def __init__(
        self,
        master: tk.Misc,
        title: str,
        font_size: int,
        on_generate: object = None,
        gen_label: str = "Cevap Üret",
        header_color: str = "#1e3a2f",
        on_close_cb: object = None,
    ) -> None:
        super().__init__(master)
        self.title(title)
        self.geometry("760x460")
        self.minsize(260, 160)
        self.configure(bg=COLORS["panel"])

        # Çift başlık çubuğunu önlemek için işletim sistemi başlığını kaldırıyoruz
        self.overrideredirect(True)

        # İlk açılışta Sabit Olmayan (Bırak / normal) modunda başlar
        self._is_pinned = False
        self.attributes("-topmost", False)
        self._is_maximized = False
        self._prev_geom = "760x460+100+100"
        self._font_size_var = tk.IntVar(value=font_size)
        self._on_close_cb = on_close_cb
        self._header_color = header_color

        # Yeniden boyutlandırma durumu
        self._resize_mode: str | None = None
        self._start_x = 0
        self._start_y = 0
        self._start_w = 760
        self._start_h = 460
        self._start_win_x = 100
        self._start_win_y = 100

        # Dış çerçeve (1px sınır)
        self.border_frame = tk.Frame(self, bg=header_color, bd=1)
        self.border_frame.pack(fill="both", expand=True)

        # 1. Entegre Renkli Başlık Şeridi (Tek Kuşak!)
        self.header_strip = tk.Frame(self.border_frame, bg=header_color, height=30)
        self.header_strip.pack(fill="x")
        self.header_strip.pack_propagate(False)

        self._header_label = tk.Label(
            self.header_strip,
            text=title,
            bg=header_color,
            fg="white",
            font=(FONT_FAMILY, 10, "bold"),
            anchor="w",
        )
        self._header_label.pack(side="left", padx=8, pady=2)

        # Başlık üstündeki pencere kontrol butonları
        btn_frame = tk.Frame(self.header_strip, bg=header_color)
        btn_frame.pack(side="right", padx=2)

        # Sabitle / Bırak Butonu (İlk açılışta serbest moddadır, butonda '📌 Sabitle' yazar)
        self.pin_btn = tk.Button(
            btn_frame,
            text="📌 Sabitle",
            bg=header_color,
            fg="white",
            activebackground="#0b625c",
            activeforeground="white",
            relief="flat",
            bd=0,
            font=(FONT_FAMILY, 9),
            padx=6,
            command=self._toggle_pin,
        )
        self.pin_btn.pack(side="left", padx=2)

        # Küçült butonu
        self.min_btn = tk.Button(
            btn_frame,
            text=" — ",
            bg=header_color,
            fg="white",
            activebackground="#333333",
            activeforeground="white",
            relief="flat",
            bd=0,
            font=(FONT_FAMILY, 9),
            command=self.withdraw,
        )
        self.min_btn.pack(side="left", padx=1)

        # Büyüt / Geri Al butonu
        self.max_btn = tk.Button(
            btn_frame,
            text=" 🗖 ",
            bg=header_color,
            fg="white",
            activebackground="#333333",
            activeforeground="white",
            relief="flat",
            bd=0,
            font=(FONT_FAMILY, 9),
            command=self._toggle_maximize,
        )
        self.max_btn.pack(side="left", padx=1)

        # Kapat butonu (✕)
        self.close_btn = tk.Button(
            btn_frame,
            text=" ✕ ",
            bg=header_color,
            fg="white",
            activebackground="#c53030",
            activeforeground="white",
            relief="flat",
            bd=0,
            font=(FONT_FAMILY, 9, "bold"),
            command=self._do_close,
        )
        self.close_btn.pack(side="left", padx=1)

        # Başlık çubuğundan taşıma (Sürükle - Bırak)
        self.header_strip.bind("<ButtonPress-1>", self._start_drag)
        self.header_strip.bind("<B1-Motion>", self._on_drag)
        self._header_label.bind("<ButtonPress-1>", self._start_drag)
        self._header_label.bind("<B1-Motion>", self._on_drag)

        # 2. İnce ve Kompakt Araç Çubuğu
        toolbar = tk.Frame(self.border_frame, bg=COLORS["surface"], height=26)
        toolbar.pack(fill="x", padx=4, pady=2)

        # Font - / +
        tk.Button(
            toolbar,
            text=" - ",
            relief="flat",
            bg=COLORS["bg"],
            fg=COLORS["ink"],
            command=self._make_font_step(-1),
            font=(FONT_FAMILY, 9),
            padx=4,
            pady=1,
        ).pack(side="left", padx=(2, 2))
        tk.Button(
            toolbar,
            text=" + ",
            relief="flat",
            bg=COLORS["bg"],
            fg=COLORS["ink"],
            command=self._make_font_step(1),
            font=(FONT_FAMILY, 9),
            padx=4,
            pady=1,
        ).pack(side="left", padx=(0, 6))

        # Kopyala
        tk.Button(
            toolbar,
            text="Kopyala",
            relief="flat",
            bg=COLORS["bg"],
            fg=COLORS["ink"],
            command=self._copy_text,
            font=(FONT_FAMILY, 9),
            padx=6,
            pady=1,
        ).pack(side="left", padx=(0, 6))

        # Temizle
        tk.Button(
            toolbar,
            text="Temizle",
            relief="flat",
            bg=COLORS["bg"],
            fg=COLORS["ink"],
            command=self._clear_text,
            font=(FONT_FAMILY, 9),
            padx=6,
            pady=1,
        ).pack(side="left", padx=(0, 6))

        if on_generate is not None:
            self.gen_button = tk.Button(
                toolbar,
                text=gen_label,
                relief="flat",
                bg=COLORS["accent"],
                fg="white",
                command=on_generate,
                font=(FONT_FAMILY, 9, "bold"),
                padx=8,
                pady=1,
            )
            self.gen_button.pack(side="right", padx=(0, 4))
        else:
            self.gen_button = None

        # 3. Metin Alanı
        self.text = ScrolledText(
            self.border_frame,
            wrap="word",
            bg=COLORS["panel"],
            fg=COLORS["text_on_dark"],
            insertbackground=COLORS["text_on_dark"],
            relief="flat",
            borderwidth=0,
            padx=12,
            pady=10,
            font=(FONT_FAMILY, font_size),
        )
        self.text.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        self.text.configure(state="disabled")

        # 4. Serbest Kenar ve Köşe Boyutlandırma Bağlantıları (Tüm Kenarlardan)
        self._bind_resize_events()

        # 5. Sağ Alt Tutamaç (Sizegrip)
        self.sizegrip = ttk.Sizegrip(self.border_frame)
        self.sizegrip.place(relx=1.0, rely=1.0, anchor="se")

    def _bind_resize_events(self) -> None:
        self.bind("<Motion>", self._check_resize_cursor)
        self.bind("<ButtonPress-1>", self._start_resize)
        self.bind("<B1-Motion>", self._on_resize)
        self.bind("<ButtonRelease-1>", self._stop_resize)

    def _get_resize_zone(self, event: tk.Event) -> str:
        w = self.winfo_width()
        h = self.winfo_height()
        x = event.x
        y = event.y
        m = self.BORDER_MARGIN

        on_top = y <= m
        on_bottom = y >= h - m
        on_left = x <= m
        on_right = x >= w - m

        if on_top and on_left:
            return "tl"
        if on_top and on_right:
            return "tr"
        if on_bottom and on_left:
            return "bl"
        if on_bottom and on_right:
            return "br"
        if on_top:
            return "t"
        if on_bottom:
            return "b"
        if on_left:
            return "l"
        if on_right:
            return "r"
        return ""

    def _check_resize_cursor(self, event: tk.Event) -> None:
        if self._is_maximized:
            return
        zone = self._get_resize_zone(event)
        cursor_map = {
            "tl": "size_nw_se",
            "br": "size_nw_se",
            "tr": "size_ne_sw",
            "bl": "size_ne_sw",
            "t": "size_ns",
            "b": "size_ns",
            "l": "size_we",
            "r": "size_we",
            "": "arrow",
        }
        self.configure(cursor=cursor_map.get(zone, "arrow"))

    def _start_resize(self, event: tk.Event) -> None:
        if self._is_maximized:
            return
        zone = self._get_resize_zone(event)
        if zone:
            self._resize_mode = zone
            self._start_x = event.x_root
            self._start_y = event.y_root
            self._start_w = self.winfo_width()
            self._start_h = self.winfo_height()
            self._start_win_x = self.winfo_x()
            self._start_win_y = self.winfo_y()

    def _on_resize(self, event: tk.Event) -> None:
        if not self._resize_mode or self._is_maximized:
            return

        dx = event.x_root - self._start_x
        dy = event.y_root - self._start_y
        mode = self._resize_mode

        new_w = self._start_w
        new_h = self._start_h
        new_x = self._start_win_x
        new_y = self._start_win_y

        min_w, min_h = 260, 160

        if "r" in mode:
            new_w = max(min_w, self._start_w + dx)
        elif "l" in mode:
            possible_w = self._start_w - dx
            if possible_w >= min_w:
                new_w = possible_w
                new_x = self._start_win_x + dx

        if "b" in mode:
            new_h = max(min_h, self._start_h + dy)
        elif "t" in mode:
            possible_h = self._start_h - dy
            if possible_h >= min_h:
                new_h = possible_h
                new_y = self._start_win_y + dy

        self.geometry(f"{new_w}x{new_h}+{new_x}+{new_y}")

    def _stop_resize(self, _event: tk.Event) -> None:
        self._resize_mode = None

    def _toggle_pin(self) -> None:
        self._is_pinned = not self._is_pinned
        self.attributes("-topmost", self._is_pinned)
        if self._is_pinned:
            self.pin_btn.configure(text="📌 Bırak", bg="#0f766e")
        else:
            self.pin_btn.configure(text="📌 Sabitle", bg=self._header_color)

    def _toggle_maximize(self) -> None:
        if self._is_maximized:
            self.geometry(self._prev_geom)
            self._is_maximized = False
            self.max_btn.configure(text=" 🗖 ")
        else:
            self._prev_geom = self.geometry()
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight() - 40
            self.geometry(f"{sw}x{sh}+0+0")
            self._is_maximized = True
            self.max_btn.configure(text=" 🗗 ")

    def _start_drag(self, event: tk.Event) -> None:
        self._drag_x = event.x
        self._drag_y = event.y

    def _on_drag(self, event: tk.Event) -> None:
        if self._is_maximized:
            return
        dx = event.x - self._drag_x
        dy = event.y - self._drag_y
        new_x = self.winfo_x() + dx
        new_y = self.winfo_y() + dy
        self.geometry(f"+{new_x}+{new_y}")

    def _do_close(self) -> None:
        if self._on_close_cb is not None:
            self._on_close_cb()
        self.destroy()

    def _copy_text(self) -> None:
        content = self.text.get("1.0", "end-1c")
        self.clipboard_clear()
        self.clipboard_append(content)

    def _clear_text(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    def _make_font_step(self, delta: int):
        def _step() -> None:
            new_size = max(10, min(48, self._font_size_var.get() + delta))
            self._font_size_var.set(new_size)
            self.text.configure(font=(FONT_FAMILY, new_size))

        return _step

    def set_gen_label(self, text: str) -> None:
        if self.gen_button is not None:
            self.gen_button.configure(text=text)

    def set_title(self, text: str) -> None:
        self._header_label.configure(text=text)

    def update_text(self, content: str, font_size: int | None = None, *, append: bool = False) -> None:
        current_view = self.text.yview()
        was_near_bottom = current_view[1] >= 0.98
        if font_size is not None:
            self._font_size_var.set(font_size)
        self.text.configure(state="normal", font=(FONT_FAMILY, self._font_size_var.get()))
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

    def insert_top(self, content: str) -> None:
        """Yeni gelen kartı en başa ekler ve en üstte gösterir (Latest-on-top)."""
        self.text.configure(state="normal", font=(FONT_FAMILY, self._font_size_var.get()))
        self.text.insert("1.0", content)
        self.text.yview_moveto(0.0)
        self.text.configure(state="disabled")


class SettingsDialog(tk.Toplevel):
    """Modern ve sekmeli ayarlar penceresi."""

    def __init__(self, app: LiveCaptionApp) -> None:
        super().__init__(app.root)
        self.app = app
        self.title(self.app._t("settings_title"))
        self.geometry("740x580")
        self.resizable(True, True)
        self.minsize(580, 480)
        self.configure(bg=COLORS["bg"])
        self.transient(self.app.root)
        self.attributes("-topmost", self.app.topmost_var.get())

        self.protocol("WM_DELETE_WINDOW", self.on_close)

        shell = ttk.Frame(self, style="Card.TFrame", padding=14)
        shell.pack(fill="both", expand=True, padx=12, pady=12)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(0, weight=1)

        notebook = ttk.Notebook(shell)
        notebook.grid(row=0, column=0, sticky="nsew")

        # 1. Sekme: Azure & Dil
        tab_azure = ttk.Frame(notebook, style="Card.TFrame", padding=16)
        notebook.add(tab_azure, text=f" 🌐 {self.app._t('settings_tab_azure')} ")
        self._build_azure_tab(tab_azure)

        # 2. Sekme: Ses & Giriş
        tab_audio = ttk.Frame(notebook, style="Card.TFrame", padding=16)
        notebook.add(tab_audio, text=f" 🎙️ {self.app._t('settings_tab_audio')} ")
        self._build_audio_tab(tab_audio)

        # 3. Sekme: Yapay Zeka (Gemini) & Profil
        tab_ai = ttk.Frame(notebook, style="Card.TFrame", padding=16)
        notebook.add(tab_ai, text=f" 🤖 {self.app._t('settings_tab_ai')} ")
        self._build_ai_tab(tab_ai)

        # Alt Buton Barı
        btn_bar = ttk.Frame(shell, style="Toolbar.TFrame", padding=(0, 12, 0, 0))
        btn_bar.grid(row=1, column=0, sticky="ew")
        btn_bar.columnconfigure(0, weight=1)

        ttk.Button(
            btn_bar,
            text=self.app._t("save"),
            style="Accent.TButton",
            command=self.save_and_close,
        ).pack(side="right", padx=(6, 0))

    def _build_azure_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(1, weight=1)

        # Azure Key
        ttk.Label(parent, text=self.app._t("azure_key"), font=(FONT_FAMILY, 10, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )
        key_entry = ttk.Entry(parent, textvariable=self.app.key_var, show="*")
        key_entry.grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=(0, 8))
        self.app._bind_right_click_paste(key_entry)

        # Region
        ttk.Label(parent, text=self.app._t("region"), font=(FONT_FAMILY, 10, "bold")).grid(
            row=1, column=0, sticky="w", pady=(0, 12)
        )
        region_entry = ttk.Entry(parent, textvariable=self.app.region_var)
        region_entry.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(0, 12))
        self.app._bind_right_click_paste(region_entry)

        # Arayüz Dili
        ttk.Label(parent, text=self.app._t("ui_language"), font=(FONT_FAMILY, 10, "bold")).grid(
            row=2, column=0, sticky="w", pady=(0, 16)
        )
        ui_lang_combo = ttk.Combobox(
            parent,
            textvariable=self.app.ui_language_var,
            state="readonly",
            values=self.app.ui_language_labels,
            width=16,
        )
        ui_lang_combo.grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(0, 16))
        ui_lang_combo.bind("<<ComboboxSelected>>", lambda _e: self.app._on_ui_language_changed())

        # Test Butonları Grubu
        grp = ttk.LabelFrame(parent, text="Azure Kontrolleri", style="Section.TLabelframe", padding=12)
        grp.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 0))

        btn_row = ttk.Frame(grp, style="Toolbar.TFrame")
        btn_row.pack(fill="x")

        ttk.Button(
            btn_row, text=self.app._t("preflight"), style="Soft.TButton", command=self.app.run_preflight_check
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            btn_row, text=self.app._t("azure_test"), style="Soft.TButton", command=self.app.run_azure_test
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            btn_row, text=self.app._t("azure_costs"), style="Soft.TButton", command=self.app.open_azure_costs
        ).pack(side="left")

    def _build_audio_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(1, weight=1)

        # Yakalama Modu
        ttk.Label(parent, text=self.app._t("capture_mode"), font=(FONT_FAMILY, 10, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )
        mode_combo = ttk.Combobox(
            parent,
            textvariable=self.app.capture_mode_var,
            state="readonly",
            values=list(self.app.mode_labels_by_code.values()),
        )
        mode_combo.grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=(0, 8))
        mode_combo.bind("<<ComboboxSelected>>", lambda _e: self.app._on_mode_changed())

        # Ses Kaynağı
        ttk.Label(parent, text=self.app._t("audio_source"), font=(FONT_FAMILY, 10, "bold")).grid(
            row=1, column=0, sticky="w", pady=(0, 12)
        )
        source_combo = ttk.Combobox(parent, textvariable=self.app.audio_source_var, state="readonly")
        source_combo.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(0, 12))
        source_combo.bind("<<ComboboxSelected>>", lambda _e: self.app._on_source_changed())

        # İpucu Metni
        ttk.Label(parent, textvariable=self.app.device_hint_var, style="Hint.TLabel", wraplength=520).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(0, 16)
        )

        # Ses İşlem Butonları
        grp = ttk.LabelFrame(parent, text="Ses Testi & Teşhis", style="Section.TLabelframe", padding=12)
        grp.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        btn_row = ttk.Frame(grp, style="Toolbar.TFrame")
        btn_row.pack(fill="x")

        ttk.Button(
            btn_row, text=self.app._t("refresh"), style="Soft.TButton", command=self.app._refresh_audio_sources
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            btn_row, text=self.app._t("device_test"), style="Soft.TButton", command=self.app._probe_selected_source
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            btn_row, text=self.app._t("debug_audio"), style="Soft.TButton", command=self.app.run_audio_debug
        ).pack(side="left")

    def _build_ai_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(1, weight=1)
        parent.rowconfigure(4, weight=1)

        # Gemini Key
        ttk.Label(parent, text=self.app._t("gemini_key"), font=(FONT_FAMILY, 10, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )
        gkey_entry = ttk.Entry(parent, textvariable=self.app.gemini_key_var, show="*")
        gkey_entry.grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=(0, 8))
        self.app._bind_right_click_paste(gkey_entry)

        # Model & Gemini Test
        ttk.Label(parent, text=self.app._t("gemini_model"), font=(FONT_FAMILY, 10, "bold")).grid(
            row=1, column=0, sticky="w", pady=(0, 12)
        )
        model_row = ttk.Frame(parent, style="Card.TFrame")
        model_row.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(0, 12))
        model_row.columnconfigure(0, weight=1)

        gmodel_entry = ttk.Entry(model_row, textvariable=self.app.gemini_model_var)
        gmodel_entry.grid(row=0, column=0, sticky="ew")
        self.app._bind_right_click_paste(gmodel_entry)

        ttk.Button(
            model_row,
            text=self.app._t("gemini_test"),
            style="Soft.TButton",
            command=self.app.run_gemini_test,
        ).grid(row=0, column=1, padx=(8, 0))

        # Aday Profili
        ttk.Label(parent, text=self.app._t("ai_profile_title"), font=(FONT_FAMILY, 10, "bold")).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(6, 4)
        )
        ttk.Label(parent, text=self.app._t("ai_profile_hint"), style="Hint.TLabel", wraplength=520).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(0, 6)
        )

        self.profile_text = ScrolledText(
            parent,
            wrap="word",
            height=8,
            bg=COLORS["panel_alt"],
            fg=COLORS["text_on_dark"],
            insertbackground=COLORS["text_on_dark"],
            relief="flat",
            borderwidth=0,
            padx=10,
            pady=10,
            font=(FONT_FAMILY, 10),
        )
        self.profile_text.grid(row=4, column=0, columnspan=2, sticky="nsew")
        self.profile_text.insert("1.0", self.app.ai_profile)

    def save_and_close(self) -> None:
        if hasattr(self, "profile_text"):
            self.app.ai_profile = self.profile_text.get("1.0", "end-1c").strip()
            self.app.config.ai_profile = self.app.ai_profile
        self.app.save_settings()
        self.destroy()

    def on_close(self) -> None:
        self.destroy()


class LiveCaptionApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(PRODUCT_NAME)
        self.root.geometry("1380x920")
        self.root.resizable(True, True)
        self.root.minsize(1080, 700)
        self.root.configure(bg=COLORS["bg"])

        self.config = load_config()
        self.event_queue: Queue = Queue()
        self.session: LiveTranslationSession | None = None
        self.monitor_stop_event: threading.Event | None = None
        self.monitor_thread: AudioLevelMonitor | None = None
        self.azure_test_running = False
        self.gemini_test_running = False

        self.history: list[dict[str, str]] = []
        self.panels: dict[str, TranscriptPanel] = {}
        self.detached: dict[str, DetachedPanel] = {}
        self.available_sources: list[AudioSource] = []
        self.source_by_label: dict[str, AudioSource] = {}

        self.topmost_var = tk.BooleanVar(value=self.config.topmost)
        self.font_size_var = tk.IntVar(value=self.config.font_size)
        self.auto_answer_var = tk.BooleanVar(value=self.config.auto_answer)
        self.last_auto_answered_source = ""
        self.question_count = 0

        # Anlık Çeviri için 2 satırlı kayan tampon (Cümle bittiğinde kaybolmaz, 2. cümle bitince 1. yerini 2'ye devreder)
        self.prev_final_translation = ""
        self.current_partial_translation = ""

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
        self._build_menu_bar()
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
        style.configure(".", background=COLORS["bg"], foreground=COLORS["ink"], font=(FONT_FAMILY, 9))
        style.configure("Card.TFrame", background=COLORS["surface"])
        style.configure("Toolbar.TFrame", background=COLORS["surface"])
        style.configure(
            "Title.TLabel",
            background=COLORS["surface"],
            foreground=COLORS["ink"],
            font=(FONT_FAMILY, 16, "bold"),
        )
        style.configure(
            "Muted.TLabel",
            background=COLORS["surface"],
            foreground=COLORS["muted"],
            font=(FONT_FAMILY, 9),
        )
        style.configure(
            "Hint.TLabel",
            background=COLORS["surface"],
            foreground=COLORS["muted"],
            font=(FONT_FAMILY, 9),
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
            font=(FONT_FAMILY, 10, "bold"),
        )
        style.configure(
            "Accent.TButton",
            background=COLORS["accent"],
            foreground="#ffffff",
            borderwidth=0,
            focusthickness=0,
            font=(FONT_FAMILY, 9, "bold"),
            padding=(10, 4),
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
            font=(FONT_FAMILY, 9),
            padding=(8, 3),
        )
        style.configure("TCheckbutton", background=COLORS["surface"], foreground=COLORS["ink"], font=(FONT_FAMILY, 9))
        style.configure("TCombobox", padding=3, font=(FONT_FAMILY, 9))
        style.configure("TEntry", padding=3, font=(FONT_FAMILY, 9))
        style.configure("TSpinbox", padding=3, font=(FONT_FAMILY, 9))
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

    def _build_menu_bar(self) -> None:
        self.menubar = tk.Menu(self.root)
        self.root.configure(menu=self.menubar)

        # Dosya Menüsü
        self.file_menu = tk.Menu(self.menubar, tearoff=0)
        self.menubar.add_cascade(label=self._t("menu_file"), menu=self.file_menu)
        self.file_menu.add_command(label=self._t("menu_import"), command=self.import_history)
        self.file_menu.add_command(label=self._t("menu_export"), command=self.export_history)
        self.file_menu.add_separator()
        self.file_menu.add_command(label=self._t("menu_clear"), command=self.clear_all)
        self.file_menu.add_separator()
        self.file_menu.add_command(label=self._t("menu_exit"), command=self.on_close)

        # Ayarlar Menüsü
        self.settings_menu = tk.Menu(self.menubar, tearoff=0)
        self.menubar.add_cascade(label=self._t("menu_settings"), menu=self.settings_menu)
        self.settings_menu.add_command(label=self._t("menu_open_settings"), command=self.open_settings_dialog)

        # Yardım Menüsü
        self.help_menu = tk.Menu(self.menubar, tearoff=0)
        self.menubar.add_cascade(label=self._t("menu_help"), menu=self.help_menu)
        self.help_menu.add_command(label=self._t("menu_about"), command=self.show_about_dialog)

    def _apply_language(self, preferred_mode: str | None = None) -> None:
        self.root.title(self._t("window_title"))
        self.header_title_label.configure(text=self._t("app_title"))
        self.header_subtitle_label.configure(text=self._t("app_subtitle"))

        # Menü Çubuğunu Güncelle
        self._build_menu_bar()

        # Ana Ekran Kontrolleri
        self.source_language_label.configure(text=self._t("source_language"))
        self.target_language_label.configure(text=self._t("target_language"))
        self.start_button.configure(text=self._t("start"))
        self.stop_button.configure(text=self._t("stop"))
        self.auto_answer_checkbox.configure(text=self._t("auto_answer"))
        self.settings_button.configure(text=self._t("settings_gear_btn"))
        self.clear_button.configure(text=self._t("clear"))
        self.topmost_checkbutton.configure(text=self._t("topmost"))
        self.zoom_label.configure(text=self._t("zoom"))

        panel_titles = {
            "auto_answer": self._t("auto_answer_panel"),
            "partial_target": self._t("partial_target"),
            "final_source": self._t("final_source"),
            "final_target": self._t("final_target"),
            "answer_source": self._t("answer_source"),
            "answer_target": self._t("answer_target"),
        }
        for key, panel in self.panels.items():
            panel.title = panel_titles.get(key, key)
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
        current_mode = preferred_mode or self.capture_mode_code or MODE_SYSTEM
        self.capture_mode_var.set(self.mode_labels_by_code.get(current_mode, self._mode_label(MODE_SYSTEM)))
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
        shell = ttk.Frame(self.root, style="Card.TFrame", padding=(10, 8, 10, 10))
        shell.pack(fill="both", expand=True, padx=8, pady=8)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(2, weight=1)

        # 1. Kompakt Başlık Satırı
        header = ttk.Frame(shell, style="Card.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        header.columnconfigure(0, weight=1)
        self.header_title_label = ttk.Label(header, text="", style="Title.TLabel")
        self.header_title_label.grid(row=0, column=0, sticky="w")
        self.header_subtitle_label = ttk.Label(
            header,
            text="",
            style="Muted.TLabel",
        )
        self.header_subtitle_label.grid(row=1, column=0, sticky="w", pady=(1, 0))

        # 2. İnce ve Sade Ana Kumanda Barı
        self._build_action_row(shell)

        # 3. 6'lı Paneller
        self._build_board(shell)

        # 4. Alt Durum Çubuğu
        status_bar = ttk.Frame(shell, style="Card.TFrame", padding=(2, 4, 2, 0))
        status_bar.grid(row=3, column=0, sticky="ew")
        ttk.Label(status_bar, textvariable=self.status_var, style="Muted.TLabel").pack(anchor="w")

    def _build_action_row(self, parent: ttk.Frame) -> None:
        container = ttk.Frame(parent, style="Card.TFrame")
        container.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        container.columnconfigure(0, weight=1)

        # Ana Kontrol Satırı
        row = ttk.Frame(container, style="Toolbar.TFrame")
        row.grid(row=0, column=0, sticky="ew")

        # Kaynak Dil
        self.source_language_label = ttk.Label(row, text="", font=(FONT_FAMILY, 9, "bold"))
        self.source_language_label.pack(side="left", padx=(0, 4))
        self.source_lang_combo = ttk.Combobox(
            row,
            textvariable=self.source_lang_var,
            state="readonly",
            values=[item.label for item in LANGUAGES],
            width=17,
        )
        self.source_lang_combo.pack(side="left", padx=(0, 6))

        # Ok simgesi ➔
        ttk.Label(row, text="➔", font=(FONT_FAMILY, 10, "bold")).pack(side="left", padx=(0, 6))

        # Hedef Dil
        self.target_language_label = ttk.Label(row, text="", font=(FONT_FAMILY, 9, "bold"))
        self.target_language_label.pack(side="left", padx=(0, 4))
        self.target_lang_combo = ttk.Combobox(
            row,
            textvariable=self.target_lang_var,
            state="readonly",
            values=[item.label for item in LANGUAGES],
            width=17,
        )
        self.target_lang_combo.pack(side="left", padx=(0, 10))

        # Başlat / Durdur
        self.start_button = ttk.Button(row, text="", style="Accent.TButton", command=self.start_session)
        self.start_button.pack(side="left", padx=(0, 4))

        self.stop_button = ttk.Button(
            row,
            text="",
            style="Soft.TButton",
            command=self.stop_session,
            state="disabled",
        )
        self.stop_button.pack(side="left", padx=(0, 12))

        # ⚡ Otomatik Cevap Toggle
        self.auto_answer_checkbox = ttk.Checkbutton(
            row,
            text="",
            variable=self.auto_answer_var,
            command=self._on_auto_answer_toggled,
        )
        self.auto_answer_checkbox.pack(side="left", padx=(0, 12))

        # ⚙️ Ayarlar Dişli Butonu
        self.settings_button = ttk.Button(
            row,
            text="",
            style="Soft.TButton",
            command=self.open_settings_dialog,
        )
        self.settings_button.pack(side="left", padx=(0, 8))

        # Temizle
        self.clear_button = ttk.Button(row, text="", style="Soft.TButton", command=self.clear_all)
        self.clear_button.pack(side="left", padx=(0, 8))

        # Sağ Taraf: Zoom & Üstte Kal
        ttk.Spinbox(
            row,
            from_=10,
            to=36,
            textvariable=self.font_size_var,
            width=3,
            command=self._apply_font_size,
        ).pack(side="right", padx=(3, 0))
        self.zoom_label = ttk.Label(row, text="")
        self.zoom_label.pack(side="right", padx=(6, 0))

        self.topmost_checkbutton = ttk.Checkbutton(
            row,
            text="",
            variable=self.topmost_var,
            command=self._apply_topmost,
        )
        self.topmost_checkbutton.pack(side="right", padx=(0, 8))

        # Ses Seviyesi ve Aygıt Durumu Alt Çubuğu (İnce)
        sub_row = ttk.Frame(container, style="Toolbar.TFrame")
        sub_row.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        sub_row.columnconfigure(1, weight=1)

        ttk.Label(sub_row, text="Ses:", font=(FONT_FAMILY, 9)).grid(row=0, column=0, sticky="w", padx=(0, 4))
        meter_bar = ttk.Frame(sub_row, style="Toolbar.TFrame")
        meter_bar.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        meter_bar.columnconfigure(0, weight=1)

        ttk.Progressbar(
            meter_bar,
            style="Level.Horizontal.TProgressbar",
            variable=self.meter_value_var,
            maximum=100,
        ).grid(row=0, column=0, sticky="ew")

        ttk.Label(sub_row, textvariable=self.meter_text_var, style="Muted.TLabel").grid(row=0, column=2, sticky="w", padx=(0, 8))
        ttk.Label(sub_row, textvariable=self.audio_source_var, style="Muted.TLabel").grid(row=0, column=3, sticky="e")

    def _build_board(self, parent: ttk.Frame) -> None:
        board = ttk.Frame(parent, style="Card.TFrame")
        board.grid(row=2, column=0, sticky="nsew")
        board.columnconfigure(0, weight=1)
        board.columnconfigure(1, weight=1)
        board.rowconfigure(0, weight=1)
        board.rowconfigure(1, weight=1)
        board.rowconfigure(2, weight=1)

        # 1. Satır: [Otomatik Cevap] | [Anlık Çeviri]
        self._create_panel(board, 0, 0, "auto_answer", "⚡ Otomatik Cevap")
        self._create_panel(board, 0, 1, "partial_target", "Anlık Çeviri")

        # 2. Satır: [Final Orijinal] | [Final Çeviri]
        self._create_panel(board, 1, 0, "final_source", "Final Orijinal")
        self._create_panel(board, 1, 1, "final_target", "Final Çeviri")

        # 3. Satır: [Önerilen Cevap (Söylenecek)] | [Cevabın Anlamı]
        self._create_panel(board, 2, 0, "answer_source", "Önerilen Cevap (Söylenecek)")
        self._create_panel(board, 2, 1, "answer_target", "Cevabın Anlamı")

    def _create_panel(self, parent: ttk.Frame, row: int, column: int, key: str, title: str) -> None:
        frame = ttk.LabelFrame(parent, text=title, style="Section.TLabelframe", padding=(6, 4, 6, 6))
        frame.grid(row=row, column=column, sticky="nsew", padx=3, pady=3)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        actions = ttk.Frame(frame, style="Toolbar.TFrame")
        actions.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        actions.columnconfigure(0, weight=1)

        action_column = 1

        # TÜM MODÜLLER İÇİN AYIR (DETACH) BUTONU!
        detach_button = ttk.Button(
            actions, text="", style="Soft.TButton", command=lambda: self.toggle_detached(key)
        )
        detach_button.grid(row=0, column=action_column, padx=(0, 4))
        action_column += 1

        copy_button = ttk.Button(actions, text="", style="Soft.TButton", command=lambda: self.copy_panel(key))
        copy_button.grid(row=0, column=action_column, padx=(0, 4))
        action_column += 1

        clear_button = ttk.Button(
            actions, text="", style="Soft.TButton", command=lambda: self.set_panel_text(key, "")
        )
        clear_button.grid(row=0, column=action_column, padx=(0, 4))
        action_column += 1

        # Konuşma panellerinde Cevap Üret butonu
        gen_button = None
        if key in ("final_source", "final_target", "partial_target"):
            gen_button = ttk.Button(
                actions, text="", style="Soft.TButton", command=self.generate_ai_answer
            )
            gen_button.grid(row=0, column=action_column, padx=(0, 4))
            action_column += 1

        panel_font_var = tk.IntVar(value=self.font_size_var.get())
        minus_btn = ttk.Button(actions, text="-", style="Soft.TButton", width=2)
        minus_btn.grid(row=0, column=action_column, padx=(0, 2))
        plus_btn = ttk.Button(actions, text="+", style="Soft.TButton", width=2)
        plus_btn.grid(row=0, column=action_column + 1, padx=(0, 2))

        if key == "auto_answer" or key.startswith("answer"):
            panel_bg = COLORS["panel_answer"]
        elif key.startswith("partial"):
            panel_bg = COLORS["panel"]
        else:
            panel_bg = COLORS["panel_alt"]

        widget = ScrolledText(
            frame,
            wrap="word",
            height=6,
            bg=panel_bg,
            fg=COLORS["text_on_dark"],
            insertbackground=COLORS["text_on_dark"],
            relief="flat",
            borderwidth=0,
            padx=10,
            pady=8,
            font=(FONT_FAMILY, panel_font_var.get()),
        )
        widget.grid(row=1, column=0, sticky="nsew")
        widget.configure(state="disabled")
        self._bind_right_click_paste(widget)

        def _make_step(delta: int) -> object:
            def _step() -> None:
                new_size = max(10, min(48, panel_font_var.get() + delta))
                panel_font_var.set(new_size)
                widget.configure(font=(FONT_FAMILY, new_size))
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

    def _bind_right_click_paste(self, widget: tk.Widget) -> None:
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

    def open_settings_dialog(self) -> None:
        SettingsDialog(self)

    def show_about_dialog(self) -> None:
        messagebox.showinfo(self._t("about_title"), self._t("about_text"))

    def _on_auto_answer_toggled(self) -> None:
        self.config.auto_answer = self.auto_answer_var.get()
        self._persist_minor_settings()

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
                errors.append(f"Ses kaynağı testi başarısız: {message}")
            elif level <= 0.01:
                warnings.append(
                    "Audio source opened, but the current signal level is low or silent."
                    if self.ui_language_code == "en"
                    else "Audioquelle geoeffnet, aber das aktuelle Signal ist zu niedrig oder still."
                    if self.ui_language_code == "de"
                    else "Ses kaynağı açıldı ama şu anda anlamlı bir sinyal yok. Kaynak sessiz olabilir."
                )

        try:
            from azure.cognitiveservices import speech as speechsdk

            if key and region:
                speechsdk.translation.SpeechTranslationConfig(subscription=key, region=region)
        except Exception:
            errors.append(
                "Azure Speech SDK could not be loaded. Make sure you are running the correct build from the dist folder."
                if self.ui_language_code == "en"
                else "Azure Speech SDK konnte nicht geladen werden. Stellen Sie sicher, dass Sie den richtigen Build aus dem dist-Ordner starten."
                if self.ui_language_code == "de"
                else "Azure Speech SDK yüklenemedi. Doğru derlemeyi kullandığınızdan emin olun."
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
            auto_answer=self.auto_answer_var.get(),
        )
        save_config(self.config)
        self.status_var.set(self._t("settings_saved"))

    def _persist_minor_settings(self) -> None:
        self.config.ui_language = self.ui_language_code
        self.config.capture_mode = self.capture_mode_code
        self.config.input_device_name = self.audio_source_var.get()
        self.config.topmost = self.topmost_var.get()
        self.config.font_size = self.font_size_var.get()
        self.config.auto_answer = self.auto_answer_var.get()
        save_config(self.config)

    def _apply_topmost(self) -> None:
        keep_top = self.topmost_var.get()
        self.root.attributes("-topmost", keep_top)
        if keep_top:
            self.root.bind_all("<FocusOut>", self._on_focus_change)
            self.root.bind_all("<FocusIn>", self._on_focus_change)
        else:
            self.root.unbind_all("<FocusOut>")
            self.root.unbind_all("<FocusIn>")
        self._persist_minor_settings()

    def _on_focus_change(self, _event: tk.Event) -> None:
        self.root.after(60, self._sync_topmost_to_focus)

    def _sync_topmost_to_focus(self) -> None:
        if not self.topmost_var.get():
            return
        has_focus = self.root.focus_get() is not None
        self.root.attributes("-topmost", has_focus)

    def _apply_font_size(self) -> None:
        font_size = self.font_size_var.get()
        for panel in self.panels.values():
            if panel.font_size_var is not None:
                panel.font_size_var.set(font_size)
            panel.widget.configure(font=(FONT_FAMILY, font_size))
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
        self.status_var.set(
            self._t(
                "working_status",
                source=selected_source_lang.label,
                target=selected_target_lang.label,
                device=selected_source.name,
            )
        )

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
        self.last_auto_answered_source = ""
        self.question_count = 0
        self.prev_final_translation = ""
        self.current_partial_translation = ""
        for key in self.panels:
            self.set_panel_text(key, "")
        self.status_var.set(self._t("cleared"))

    def generate_ai_answer(self, *, is_auto: bool = False, question_num: int = 1) -> None:
        if self.ai_busy:
            return

        api_key = self.gemini_key_var.get().strip()
        if not api_key:
            if not is_auto:
                messagebox.showerror(self._t("ai_error_title"), self._t("ai_key_empty"))
            return

        question = self.last_question_source.strip()
        if not question:
            if not is_auto:
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
        for panel in self.panels.values():
            if panel.gen_button is not None:
                panel.gen_button.configure(state="disabled")
        for dp in self.detached.values():
            if dp.winfo_exists() and dp.gen_button is not None:
                dp.gen_button.configure(state="disabled")

        self.status_var.set(self._t("ai_auto_thinking") if is_auto else self._t("ai_thinking"))

        def worker() -> None:
            try:
                result = generate_interview_answer(
                    api_key=api_key,
                    question=question_text,
                    answer_language=answer_language,
                    understand_language=understand_language,
                    model=model,
                    profile=profile,
                    question_num=question_num,
                )
                self.event_queue.put(
                    {
                        "type": "ai_answer",
                        "answer": result["answer"],
                        "answer_translation": result["answer_translation"],
                        "prompter_card": result["prompter_card"],
                        "is_auto": is_auto,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                self.event_queue.put({"type": "ai_error", "message": str(exc), "is_auto": is_auto})

        threading.Thread(target=worker, daemon=True).start()

    def run_gemini_test(self) -> None:
        if self.gemini_test_running:
            return

        api_key = self.gemini_key_var.get().strip()
        if not api_key:
            messagebox.showerror(self._t("gemini_test_title"), self._t("ai_key_empty"))
            return

        model = self.gemini_model_var.get().strip()
        self.gemini_test_running = True
        self.status_var.set(self._t("gemini_test_running"))

        def worker() -> None:
            try:
                result = test_gemini_connection(api_key=api_key, model=model)
                self.event_queue.put({"type": "gemini_test_result", "result": result})
            except Exception as exc:  # noqa: BLE001
                self.event_queue.put({"type": "gemini_test_error", "message": str(exc)})
            finally:
                self.event_queue.put({"type": "gemini_test_done"})

        threading.Thread(target=worker, daemon=True).start()

    def get_panel_text(self, key: str) -> str:
        return self.panels[key].widget.get("1.0", "end-1c")

    def set_panel_text(self, key: str, text: str, *, append: bool = False) -> None:
        if key not in self.panels:
            return
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

    def insert_top_panel_text(self, key: str, text: str) -> None:
        """Yeni gelen kartı en başa ekler ve en üstte gösterir (Latest-on-top)."""
        if key not in self.panels:
            return
        panel = self.panels[key]
        panel.widget.configure(state="normal")
        panel.widget.insert("1.0", text)
        panel.widget.yview_moveto(0.0)
        panel.widget.configure(state="disabled")

        floating = self.detached.get(key)
        if floating and floating.winfo_exists():
            floating.insert_top(text)

    def toggle_detached(self, key: str) -> None:
        current = self.detached.get(key)
        if current and current.winfo_exists():
            current.deiconify()
            current.lift()
            current.focus_force()
            return

        on_gen = self.generate_ai_answer if key in ("final_source", "final_target", "partial_target") else None

        if key in ("final_source", "answer_source"):
            header_color = "#6b1010"  # Bordo / Kırmızı
        elif key in ("auto_answer",):
            header_color = "#0d2a26"  # Koyu Zümrüt / Teal
        else:
            header_color = "#10306b"  # Lacivert / Mavi

        panel = DetachedPanel(
            self.root,
            self.panels[key].title,
            self.font_size_var.get(),
            on_generate=on_gen,
            gen_label=self._t("generate_answer"),
            header_color=header_color,
            on_close_cb=lambda: self._close_detached(key),
        )
        panel.update_text(self.get_panel_text(key))
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
        file_path = filedialog.askopenfilename(
            title=self._t("import_dialog"), filetypes=[("JSON", "*.json")]
        )
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
        if "sdk yuklenemedi" in lowered or "sdk yüklenemedi" in lowered:
            return message
        if "loopback" in lowered:
            return message
        if "permission" in lowered:
            return (
                "Audio device permission was denied. Check Windows privacy settings."
                if self.ui_language_code == "en"
                else "Zugriff auf das Audiogeraet wurde verweigert. Pruefen Sie die Windows-Datenschutzeinstellungen."
                if self.ui_language_code == "de"
                else "Ses aygıtına erişim izni reddedildi. Windows gizlilik ayarlarını kontrol edin."
            )
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
                    # Anlık Çeviri paneli (2 satırlı kayan tampon)
                    tgt = event["target"].strip()
                    self.current_partial_translation = tgt
                    if self.prev_final_translation:
                        display = f"✓ {self.prev_final_translation}\n\n▶ {tgt}"
                    else:
                        display = f"▶ {tgt}"
                    self.set_panel_text("partial_target", display)
                elif event_type == "final":
                    src = event["source"].strip()
                    tgt = event["target"].strip()

                    self.set_panel_text("final_source", f"{src}\n\n", append=True)
                    self.set_panel_text("final_target", f"{tgt}\n\n", append=True)

                    # 1. cümle bitince Anlık Çeviri ekranından kaybolmasın; sabit kalsın, 2. cümle tamamen bitince 1. yerini 2'ye devreder
                    if tgt:
                        self.prev_final_translation = tgt
                        self.current_partial_translation = ""
                        self.set_panel_text("partial_target", f"✓ {tgt}")

                    if src:
                        self.last_question_source = src
                        self.last_question_target = tgt

                        # ⚡ Sıkılaştırılmış Otomatik Cevap Motoru
                        if self.auto_answer_var.get() and src != self.last_auto_answered_source:
                            if is_potential_question_or_prompt(src) and not self.ai_busy:
                                self.question_count += 1
                                current_q_num = self.question_count
                                self.last_auto_answered_source = src
                                self.root.after(
                                    150,
                                    lambda qn=current_q_num: self.generate_ai_answer(
                                        is_auto=True, question_num=qn
                                    ),
                                )

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
                    for panel in self.panels.values():
                        if panel.gen_button is not None:
                            panel.gen_button.configure(state="normal")
                    for dp in self.detached.values():
                        if dp.winfo_exists() and dp.gen_button is not None:
                            dp.gen_button.configure(state="normal")

                    ans = event["answer"]
                    trans = event["answer_translation"]
                    prompter_card = event.get("prompter_card") or f"{ans}\n\n{trans}"

                    if event.get("is_auto"):
                        # Otomatik Cevap Modülü: En güncel soru-cevap kartı EN BAŞA eklenir (Latest-on-top)
                        self.insert_top_panel_text("auto_answer", prompter_card)
                    else:
                        # Manuel Cevap: Önerilen Cevap & Cevabın Anlamı panellerine yazılır
                        self.set_panel_text("answer_source", ans)
                        self.set_panel_text("answer_target", trans)

                    self.status_var.set(self._t("ai_ready"))
                elif event_type == "ai_error":
                    self.ai_busy = False
                    for panel in self.panels.values():
                        if panel.gen_button is not None:
                            panel.gen_button.configure(state="normal")
                    for dp in self.detached.values():
                        if dp.winfo_exists() and dp.gen_button is not None:
                            dp.gen_button.configure(state="normal")
                    self.status_var.set(self._t("ai_error_title"))
                    if not event.get("is_auto"):
                        messagebox.showerror(self._t("ai_error_title"), event["message"])
                elif event_type == "canceled":
                    prefix = (
                        "Azure canceled: "
                        if self.ui_language_code == "en"
                        else "Azure abgebrochen: "
                        if self.ui_language_code == "de"
                        else "Azure iptal etti: "
                    )
                    self.status_var.set(f"{prefix}{event['message']}")
                    self.stop_session()
                elif event_type == "no_match":
                    self.status_var.set(event["message"])
                elif event_type == "error":
                    prefix = (
                        "Audio error: "
                        if self.ui_language_code == "en"
                        else "Audiofehler: "
                        if self.ui_language_code == "de"
                        else "Ses hatası: "
                    )
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
                    summary = self._t(
                        "azure_test_summary",
                        sample_path=sample_path,
                        message=result.get("message", ""),
                        source_label=event["source_label"],
                        target_label=event["target_label"],
                        source_text=source_text or "-",
                        target_text=target_text or "-",
                    )

                    if target_text:
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
                    messagebox.showerror(
                        self._t("azure_test_title"), self._friendly_runtime_error(event["message"])
                    )
                elif event_type == "azure_test_done":
                    self.azure_test_running = False
                    if self.session is None:
                        self._restart_monitor()
                elif event_type == "gemini_test_result":
                    res = event["result"]
                    msg = self._t(
                        "gemini_test_success",
                        model=res.get("model", ""),
                        reply=res.get("reply", "OK"),
                    )
                    self.status_var.set(self._t("gemini_test_title") + ": OK")
                    messagebox.showinfo(self._t("gemini_test_title"), msg)
                elif event_type == "gemini_test_error":
                    self.status_var.set(self._t("gemini_test_failed"))
                    messagebox.showerror(
                        self._t("gemini_test_title"), self._friendly_runtime_error(event["message"])
                    )
                elif event_type == "gemini_test_done":
                    self.gemini_test_running = False
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
