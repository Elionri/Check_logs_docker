#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import threading
import queue
import datetime

import customtkinter as ctk
import docker
import requests
import urllib3


# Глобальные настройки темы 
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

LOG_FONT = ("monospace", 12)

COLOR_STOPPED        = "#8b2c2c"
COLOR_STOPPED_HOVER  = "#a53a3a"
COLOR_RUNNING        = "#2f7d32"
COLOR_RUNNING_HOVER  = "#3b9640"
COLOR_TAB_IDLE       = "#3a3a3a"
COLOR_TAB_IDLE_HOVER = "#4a4a4a"
COLOR_TAB_ACTIVE     = "#2a5d9f"
COLOR_TAB_ACTIVE_HOV = "#3370b8"

class ToolTip:
    """Всплывающая подсказка для CustomTkinter-виджетов."""
    _active = None

    def __init__(self, widget, text, delay=400):
        self.widget = widget
        self.text = text
        self.delay = delay
        self._after_id = None
        self._tip = None

        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._cancel()
        self._after_id = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self):
        if self._tip is not None or not self.text:
            return
        if ToolTip._active is not None:
            ToolTip._active._destroy()

        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4

        tip = ctk.CTkToplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        tip.attributes("-topmost", True)

        frame = ctk.CTkFrame(
            tip, corner_radius=6,
            fg_color="#1f1f1f",
            border_width=1, border_color="#4a4a4a",
        )
        frame.pack()

        ctk.CTkLabel(
            frame, text=self.text,
            font=ctk.CTkFont(family="monospace", size=11),
            justify="left",
        ).pack(padx=10, pady=6)

        self._tip = tip
        ToolTip._active = self

    def _hide(self, _e=None):
        self._cancel()
        self._destroy()

    def _destroy(self):
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None
        if ToolTip._active is self:
            ToolTip._active = None

#  Основное окно

class ModernLogReader(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Docker Log Reader — Modern UI")
        self.geometry("1200x720")
        self.minsize(900, 500)

        # Структуры данных
        self.logs = {}            # name - list[str]
        self.streams = {}         # name - generator
        self.threads = {}         # name - Thread
        self.active = set()       # имена контейнеров со стримом

        # Левая панель
        self.left_toggles = {}    # name - CTkButton (старт/стоп)
        self.left_names = {}      # name - CTkButton (только фокус)
        self.left_tooltips = {}   # name - ToolTip

        # Правый таб-бар
        self.tab_buttons = {}     # name - CTkButton
        self.tab_frames = {}      # name - CTkFrame
        self.tab_texts = {}       # name - CTkTextbox
        self.current_tab = None

        self.closing = set()
        self.line_queue = queue.Queue()

        # Docker-клиент
        try:
            self.client = docker.from_env()
        except Exception as e:
            ctk.CTkLabel(
                self,
                text=f"Не удалось подключиться к Docker:\n{e}\n\n"
                     f"Проверьте, что демон запущен и пользователь в группе docker.",
                text_color="#ff6666",
            ).pack(padx=20, pady=20)
            return

        self._build_ui()
        self._refresh_containers()
        self.after(100, self._drain_queue)
        
    #  UI
    
    def _build_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Левая панель 
        left = ctk.CTkFrame(self, width=280, corner_radius=0)
        left.grid(row=0, column=0, sticky="nsew")
        left.grid_propagate(False)

        ctk.CTkLabel(
            left, text="Контейнеры",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(16, 4))

        ctk.CTkLabel(
            left,
            text="▶ — открыть вкладку и запустить стрим.\n"
                 "■ — остановить стрим и закрыть вкладку.\n"
                 "Клик по имени — переключиться на открытую вкладку.",
            font=ctk.CTkFont(size=11),
            text_color="#9a9a9a",
            wraplength=240,
            justify="left",
        ).pack(anchor="w", padx=16, pady=(0, 8))

        self.list_frame = ctk.CTkScrollableFrame(left, label_text="")
        self.list_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        # Кнопки Старт всех / Стоп всех
        row2 = ctk.CTkFrame(left, fg_color="transparent")
        row2.pack(fill="x", padx=8, pady=(0, 4))
        ctk.CTkButton(
            row2, text="▶ Старт всех", command=self.start_all,
            corner_radius=8, fg_color=COLOR_RUNNING,
            hover_color=COLOR_RUNNING_HOVER,
        ).pack(side="left", expand=True, fill="x", padx=(0, 4))
        ctk.CTkButton(
            row2, text="■ Стоп всех", command=self.stop_all,
            corner_radius=8, fg_color=COLOR_STOPPED,
            hover_color=COLOR_STOPPED_HOVER,
        ).pack(side="left", expand=True, fill="x", padx=(4, 0))

        ctk.CTkButton(
            left, text="🐳 Docker Compose",
            command=self._open_compose_panel,
            corner_radius=8, fg_color="#2a5d9f", hover_color="#3370b8",
        ).pack(fill="x", padx=8, pady=(0, 4))

        ctk.CTkButton(
            left, text="⟳ Обновить список",
            command=self._refresh_containers,
            corner_radius=8, fg_color="transparent", border_width=1,
        ).pack(fill="x", padx=8, pady=(0, 8))

        self.filter_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            left, text="Только CRITICAL/ERROR",
            variable=self.filter_var,
        ).pack(anchor="w", padx=16, pady=(4, 0))

        self.theme_switch = ctk.CTkSwitch(
            left, text="Тёмная тема",
            command=self._toggle_theme,
        )
        self.theme_switch.select()
        self.theme_switch.pack(anchor="w", padx=16, pady=(12, 4))

        ctk.CTkButton(
            left, text="Очистить всё", command=self.clear_all,
            corner_radius=8, fg_color="#4a4a4a", hover_color="#5a5a5a",
        ).pack(fill="x", padx=8, pady=(8, 16), side="bottom")

        # Правая часть 
        right = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=12, pady=12)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)

        # Горизонтально-скроллируемый таб-бар (CTk >= 5.2.0)
        self.tab_bar = ctk.CTkScrollableFrame(
            right,
            orientation="horizontal",
            corner_radius=0,
            fg_color="transparent",
            height=52,
        )
        self.tab_bar.grid(row=0, column=0, sticky="ew")

        self.content = ctk.CTkFrame(right, corner_radius=8)
        self.content.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)

        self.placeholder = ctk.CTkLabel(
            self.content,
            text="Нажмите ▶ слева, чтобы открыть вкладку и запустить стрим.",
            justify="center",
            text_color="#9a9a9a",
        )
        self.placeholder.grid(row=0, column=0)

        # Статус-бар
        self.status_label = ctk.CTkLabel(
            self, text="Готово.", anchor="w",
            font=ctk.CTkFont(size=12),
            text_color="#9a9a9a",
        )
        self.status_label.grid(row=1, column=0, columnspan=2,
                               sticky="ew", padx=16, pady=(0, 8))

    #  Список контейнеров (слева)
   
    def _refresh_containers(self):
        active_before = set(self.active)

        for w in self.list_frame.winfo_children():
            w.destroy()
        self.left_toggles.clear()
        self.left_names.clear()
        self.left_tooltips.clear()

        try:
            containers = sorted(self.client.containers.list(), key=lambda c: c.name)
        except Exception as e:
            self.status_label.configure(text=f"Ошибка получения списка: {e}")
            return

        for c in containers:
            row = ctk.CTkFrame(self.list_frame, fg_color="transparent")
            row.pack(fill="x", padx=2, pady=2)

            running = c.name in active_before
            fg = COLOR_RUNNING if running else COLOR_STOPPED
            hv = COLOR_RUNNING_HOVER if running else COLOR_STOPPED_HOVER
            sym = "■" if running else "▶"

            toggle = ctk.CTkButton(
                row,
                text=sym,
                width=32, height=28,
                corner_radius=6,
                fg_color=fg,
                hover_color=hv,
                font=ctk.CTkFont(size=14, weight="bold"),
                command=lambda n=c.name: self._toggle_stream(n),
            )
            toggle.pack(side="left", padx=(0, 4))
            self.left_toggles[c.name] = toggle

            name_btn = ctk.CTkButton(
                row,
                text=self._shorten(c.name, 22),
                anchor="w",
                height=28,
                corner_radius=6,
                fg_color="transparent",
                hover_color="#3a3a3a",
                font=ctk.CTkFont(size=12),
                command=lambda n=c.name: self._focus_tab(n),
            )
            name_btn.pack(side="left", fill="x", expand=True)
            self.left_names[c.name] = name_btn

            # Tooltip с полным именем контейнера
            self.left_tooltips[c.name] = ToolTip(name_btn, c.name)

        self._update_status()

    #  Переключение на вкладку (без создания)

    def _focus_tab(self, name):
        if name in self.tab_frames:
            self._show_tab(name)
        else:
            self.status_label.configure(
                text=f"Вкладка '{self._shorten(name, 22)}' закрыта. "
                     f"Нажмите ▶ слева, чтобы открыть её и запустить стрим."
            )

    #  Табы справа

    def _ensure_tab(self, name):
        if name in self.tab_frames:
            return self.tab_frames[name]

        self.placeholder.grid_remove()

        btn = ctk.CTkButton(
            self.tab_bar,
            text=self._shorten(name, 18),
            width=180, height=32,
            corner_radius=8,
            fg_color=COLOR_TAB_IDLE,
            hover_color=COLOR_TAB_IDLE_HOVER,
            font=ctk.CTkFont(size=12),
            command=lambda n=name: self._show_tab(n),
        )
        btn.pack(side="left", padx=(0, 4), pady=4)
        self.tab_buttons[name] = btn

        # Tooltip с полным именем на табе
        ToolTip(btn, name)

        page = ctk.CTkFrame(self.content, corner_radius=8)
        page.grid(row=0, column=0, sticky="nsew")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(0, weight=1)

        txt = ctk.CTkTextbox(
            page,
            wrap="word",
            corner_radius=6,
            font=ctk.CTkFont(family=LOG_FONT[0], size=LOG_FONT[1]),
        )
        txt.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)

        self.tab_frames[name] = page
        self.tab_texts[name] = txt
        self.logs.setdefault(name, [])

        if self.current_tab is None:
            self._show_tab(name)

        # Автопрокрутка таб-бара вправо, чтобы новая вкладка была видна
        try:
            self.tab_bar._parent_canvas.xview_moveto(1.0)
        except AttributeError:
            pass

        return page

    def _show_tab(self, name):
        if name not in self.tab_frames:
            return

        for n, frame in self.tab_frames.items():
            if n == name:
                frame.grid()
            else:
                frame.grid_remove()

        for n, btn in self.tab_buttons.items():
            if n == name:
                btn.configure(fg_color=COLOR_TAB_ACTIVE,
                              hover_color=COLOR_TAB_ACTIVE_HOV)
            else:
                btn.configure(fg_color=COLOR_TAB_IDLE,
                              hover_color=COLOR_TAB_IDLE_HOVER)

        self.current_tab = name

    def _close_tab(self, name):
        if name in self.closing:
            return
        self.closing.add(name)
        try:
            btn = self.tab_buttons.pop(name, None)
            frame = self.tab_frames.pop(name, None)
            self.tab_texts.pop(name, None)

            if btn is not None:
                btn.destroy()
            if frame is not None:
                frame.destroy()

            if self.current_tab == name:
                self.current_tab = None
                remaining = list(self.tab_frames.keys())
                if remaining:
                    self._show_tab(remaining[0])
                else:
                    self.placeholder.grid()
        finally:
            self.after(0, lambda n=name: self.closing.discard(n))

    #  Управление стримингом
   
    def _toggle_stream(self, name):
        if name in self.active:
            self._stop_one(name)
        else:
            self._start_one(name)

    def start_all(self):
        for name in list(self.left_toggles.keys()):
            if name not in self.active:
                self._start_one(name)

    def stop_all(self):
        for name in list(self.active):
            self._stop_one(name)

    def _start_one(self, name):
        if name in self.active:
            return

        try:
            container = self.client.containers.get(name)
        except docker.errors.NotFound:
            self.status_label.configure(text=f"'{name}' не найден — пропуск.")
            return
        except Exception as e:
            self.status_label.configure(text=f"Ошибка '{name}': {e}")
            return

        # 1 Открываем вкладку
        self._ensure_tab(name)

        # 2 Помечаем активным, красим индикатор слева
        self.active.add(name)
        self._set_left_toggle_state(name, running=True)

        # 3 Служебная строка «Старт»
        self._append_service_line(name, "▶ Старт стриминга")

        # 4 Запускаем воркер
        t = threading.Thread(
            target=self._stream_worker, args=(container,), daemon=True,
        )
        self.threads[name] = t
        t.start()

        # 5 Фокус на новую вкладку
        self._show_tab(name)

        self._update_status()

    def _stop_one(self, name):
        if name not in self.active:
            return

        self.active.discard(name)

        stream = self.streams.get(name)
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass

        self._set_left_toggle_state(name, running=False)

        # Служебная строка — только в массив 
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.logs.setdefault(name, []).append(
            f"--- {ts} ■ Стриминг остановлен ---"
        )

        self._close_tab(name)
        self._update_status()

    def _set_left_toggle_state(self, name, running):
        toggle = self.left_toggles.get(name)
        if toggle is None or not toggle.winfo_exists():
            return
        if running:
            toggle.configure(
                text="■",
                fg_color=COLOR_RUNNING,
                hover_color=COLOR_RUNNING_HOVER,
            )
        else:
            toggle.configure(
                text="▶",
                fg_color=COLOR_STOPPED,
                hover_color=COLOR_STOPPED_HOVER,
            )

    def _stream_worker(self, container):
        name = container.name
        try:
            stream = container.logs(stream=True, follow=True, tail=0)
            self.streams[name] = stream

            for chunk in stream:
                if name not in self.active:
                    break
                line = chunk.decode("utf-8", errors="replace").rstrip()
                if not line:
                    continue
                if self.filter_var.get():
                    if "CRITICAL" not in line and "ERROR" not in line:
                        continue
                self.line_queue.put((name, line))

        except (requests.exceptions.ChunkedEncodingError,
                urllib3.exceptions.ProtocolError,
                OSError):
            pass
        except Exception as e:
            self.line_queue.put((name, f"[STREAM ERROR] {e}"))
        finally:
            self.streams.pop(name, None)
            self.line_queue.put((name, None))

    #  Разбор очереди
    
    def _drain_queue(self):
        processed = 0
        try:
            while True:
                name, payload = self.line_queue.get_nowait()
                processed += 1

                if payload is None:
                    if name in self.active:
                        self.active.discard(name)
                        self._set_left_toggle_state(name, running=False)
                        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        self.logs.setdefault(name, []).append(
                            f"--- {ts} ■ Стриминг завершён ---"
                        )
                        self._close_tab(name)
                    continue

                self.logs.setdefault(name, []).append(payload)
                txt = self.tab_texts.get(name)
                if txt is not None and txt.winfo_exists():
                    txt.insert("end", self._wrap_paths(payload) + "\n")
                    txt.see("end")

        except queue.Empty:
            pass

        if processed:
            self._update_status()
        self.after(100, self._drain_queue)

    def _update_status(self):
        total = sum(len(v) for v in self.logs.values())
        active = len(self.active)
        if active:
            self.status_label.configure(
                text=f"Активных стримов: {active} | Всего строк: {total}"
            )
        else:
            self.status_label.configure(text=f"Стримов нет. Всего строк: {total}")

    #  Утилиты

    def _shorten(self, text, max_len=18):
        return text if len(text) <= max_len else text[: max_len - 1] + "…"

    def _wrap_paths(self, line):
        return line.replace("/", "/\u200b")

    def _append_service_line(self, name, text):
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"--- {ts} {text} ---"
        self.logs.setdefault(name, []).append(line)

        txt = self.tab_texts.get(name)
        if txt is not None and txt.winfo_exists():
            txt.insert("end", line + "\n")
            txt.see("end")

    def _toggle_theme(self):
        mode = "dark" if self.theme_switch.get() else "light"
        ctk.set_appearance_mode(mode)

    def clear_all(self):
        for name in list(self.active):
            self._stop_one(name)
        for name in list(self.tab_frames.keys()):
            self._close_tab(name)
        self.logs.clear()
        self.status_label.configure(text="Все превью и массивы очищены.")

    #  Compose-панель
  
    def _open_compose_panel(self):
        try:
            from compose_panel import ComposePanel
        except ImportError:
            self.status_label.configure(
                text="Модуль compose_panel.py не найден рядом со скриптом."
            )
            return
        if getattr(self, "_compose_panel", None) is not None \
                and self._compose_panel.winfo_exists():
            self._compose_panel.lift()
            self._compose_panel.focus_force()
            return
        self._compose_panel = ComposePanel(self)

if __name__ == "__main__":
    app = ModernLogReader()
    app.mainloop()
