#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import shlex
import subprocess
import threading
import queue
from pathlib import Path

import customtkinter as ctk
from tkinter import filedialog, messagebox


PRESET_COMMANDS = ["docker compose", "docker-compose"]
PRESET_SUBCOMMANDS = [
    "up -d", "up", "down", "restart", "stop", "start", "pull", "ps",
    "logs --tail=100",
]

CONFIG_PATH = Path.home() / ".config" / "check_logs" / "compose.json"

# Пороги обрезки в символах
DIR_MAX_LEN = 40
CMD_MAX_LEN = 55

#  Tooltip

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

#  Панель Compose

class ComposePanel(ctk.CTkToplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title("Docker Compose — задачи")
        self.geometry("950x720")
        self.minsize(800, 560)

        self.tasks = []            # список словарей dir / cmd / sub / enabled
        self.output_queue = queue.Queue()
        self.process = None
        self.running = False

        self._build_ui()
        self._load_config()
        self._render_tasks()

        self.after(100, self._drain_output)

        self.transient(master)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    #  UI
  
    def _build_ui(self):
        pad = {"padx": 12, "pady": 6}

        # Форма добавления
        form = ctk.CTkFrame(self, corner_radius=8)
        form.pack(fill="x", **pad)

        ctk.CTkLabel(
            form, text="Добавить задачу",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(anchor="w", padx=10, pady=(8, 4))

        # Директория
        dir_row = ctk.CTkFrame(form, fg_color="transparent")
        dir_row.pack(fill="x", padx=10, pady=(0, 4))

        ctk.CTkLabel(dir_row, text="Директория:", width=90,
                     anchor="w").pack(side="left")
        self.dir_entry = ctk.CTkEntry(
            dir_row, placeholder_text="/path/to/compose/dir",
        )
        self.dir_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        ctk.CTkButton(
            dir_row, text="📁", width=36, command=self._pick_dir,
        ).pack(side="left")

        # Команда compose
        cmd_row = ctk.CTkFrame(form, fg_color="transparent")
        cmd_row.pack(fill="x", padx=10, pady=(0, 4))

        ctk.CTkLabel(cmd_row, text="Compose:", width=90,
                     anchor="w").pack(side="left")
        self.cmd_var = ctk.StringVar(value=PRESET_COMMANDS[0])
        ctk.CTkComboBox(
            cmd_row, values=PRESET_COMMANDS, variable=self.cmd_var,
        ).pack(side="left", fill="x", expand=True)

        # Подкоманда
        sub_row = ctk.CTkFrame(form, fg_color="transparent")
        sub_row.pack(fill="x", padx=10, pady=(0, 4))

        ctk.CTkLabel(sub_row, text="Подкоманда:", width=90,
                     anchor="w").pack(side="left")
        self.sub_var = ctk.StringVar(value="up -d")
        ctk.CTkComboBox(
            sub_row, values=PRESET_SUBCOMMANDS, variable=self.sub_var,
        ).pack(side="left", fill="x", expand=True)

        ctk.CTkButton(
            form, text="+ Добавить запись",
            fg_color="#2f7d32", hover_color="#3b9640",
            command=self._add_task,
        ).pack(anchor="e", padx=10, pady=(4, 10))

        # Список задач
        ctk.CTkLabel(
            self, text="Список задач",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(anchor="w", padx=12, pady=(6, 4))

        # Горизонтальный скроллфрейм (CustomTkinter >= 5.2.0)
        self.tasks_frame = ctk.CTkScrollableFrame(
            self,
            orientation="horizontal",
            height=230,
            label_text="",
        )
        self.tasks_frame.pack(fill="x", padx=12, pady=(0, 6))

        # Кнопки действий
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill="x", **pad)

        self.run_sel_btn = ctk.CTkButton(
            actions, text="▶ Выполнить выбранные",
            fg_color="#2f7d32", hover_color="#3b9640",
            command=self._run_selected,
        )
        self.run_sel_btn.pack(side="left", padx=(0, 6))

        self.run_all_btn = ctk.CTkButton(
            actions, text="▶ Выполнить все",
            fg_color="#2a5d9f", hover_color="#3370b8",
            command=self._run_all,
        )
        self.run_all_btn.pack(side="left", padx=(0, 6))

        self.stop_btn = ctk.CTkButton(
            actions, text="■ Прервать",
            fg_color="#8b2c2c", hover_color="#a53a3a",
            state="disabled", command=self._stop,
        )
        self.stop_btn.pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            actions, text="Очистить вывод",
            fg_color="transparent", border_width=1,
            command=self._clear_output,
        ).pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            actions, text="✕ Закрыть",
            fg_color="#4a4a4a", hover_color="#5a5a5a",
            command=self._on_close,
        ).pack(side="right")

        # Вывод
        ctk.CTkLabel(
            self, text="Вывод",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(anchor="w", **pad)

        self.output = ctk.CTkTextbox(
            self, wrap="word",
            font=ctk.CTkFont(family="monospace", size=11),
        )
        self.output.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    #  Форма добавления
   
    def _pick_dir(self):
        path = filedialog.askdirectory(title="Выберите директорию")
        if path:
            self.dir_entry.delete(0, "end")
            self.dir_entry.insert(0, path)

    def _add_task(self):
        path = self.dir_entry.get().strip()
        if not path:
            self._log("[ERROR] Укажите директорию\n")
            return
        if not os.path.isdir(path):
            self._log(f"[ERROR] Не директория: {path}\n")
            return

        self.tasks.append({
            "dir": path,
            "cmd": self.cmd_var.get().strip() or PRESET_COMMANDS[0],
            "sub": self.sub_var.get().strip() or "up -d",
            "enabled": True,
        })
        self.dir_entry.delete(0, "end")
        self._render_tasks()

    #  Отрисовка списка задач
  
    def _render_tasks(self):
        for w in self.tasks_frame.winfo_children():
            w.destroy()

        if not self.tasks:
            ctk.CTkLabel(
                self.tasks_frame, text="Задач пока нет.",
                text_color="#9a9a9a",
            ).pack(pady=10)
            return

        for i, task in enumerate(self.tasks):
            row = ctk.CTkFrame(self.tasks_frame, fg_color="transparent")
            row.pack(fill="x", pady=2)

            # Чекбокс
            var = ctk.BooleanVar(value=task["enabled"])
            cb = ctk.CTkCheckBox(
                row, text="", width=24, variable=var,
                command=lambda idx=i, v=var: self._toggle_task(idx, v.get()),
            )
            cb.pack(side="left", padx=(0, 4))

            # Директория (обрезанная, с tooltip)
            dir_full = task["dir"]
            dir_text = self._shorten_path(dir_full, DIR_MAX_LEN)
            dir_lbl = ctk.CTkLabel(
                row, text=dir_text, anchor="w", justify="left",
                width=280, wraplength=280,
                font=ctk.CTkFont(family="monospace", size=11),
            )
            dir_lbl.pack(side="left", padx=(0, 8))
            ToolTip(dir_lbl, dir_full)

            # Команда + подкоманда (обрезанная, с tooltip) 
            cmd_full = f"{task['cmd']} {task['sub']}"
            cmd_text = self._shorten_path(cmd_full, CMD_MAX_LEN)
            cmd_lbl = ctk.CTkLabel(
                row, text=cmd_text, anchor="w", justify="left",
                width=420, wraplength=420,
                font=ctk.CTkFont(family="monospace", size=11),
                text_color="#a0a0a0",
            )
            cmd_lbl.pack(side="left", padx=(0, 8))
            ToolTip(cmd_lbl, cmd_full)

            # Кнопка удаления (прижата вправо) 
            ctk.CTkButton(
                row, text="✕", width=32, height=24,
                fg_color="#8b2c2c", hover_color="#a53a3a",
                command=lambda idx=i: self._remove_task(idx),
            ).pack(side="right", padx=(8, 0))

    def _shorten_path(self, text, max_len):
        """Обрезает строку, оставляя начало и хвост — удобно для путей."""
        if len(text) <= max_len:
            return text
        # Оставляем начало (для команды) и хвост (для директории)
        # Простой вариант: оставить только начало + '…'
        return text[: max_len - 1] + "…"

    def _toggle_task(self, idx, enabled):
        if 0 <= idx < len(self.tasks):
            self.tasks[idx]["enabled"] = enabled

    def _remove_task(self, idx):
        if 0 <= idx < len(self.tasks):
            del self.tasks[idx]
            self._render_tasks()
   
    #  Выполнение

    def _collect_tasks(self, only_selected):
        result = []
        for t in self.tasks:
            if only_selected and not t["enabled"]:
                continue
            try:
                cmd = shlex.split(t["cmd"]) + shlex.split(t["sub"])
            except ValueError as e:
                self._log(f"[ERROR] '{t['dir']}': {e}\n")
                continue
            result.append((t["dir"], cmd))
        return result

    def _run_selected(self):
        self._run(self._collect_tasks(only_selected=True))

    def _run_all(self):
        self._run(self._collect_tasks(only_selected=False))

    def _run(self, jobs):
        if self.running:
            self._log("[WARN] Уже выполняется другая команда\n")
            return
        if not jobs:
            self._log("[ERROR] Нет задач для выполнения\n")
            return

        self.running = True
        self.run_sel_btn.configure(state="disabled")
        self.run_all_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")

        threading.Thread(
            target=self._run_worker, args=(jobs,), daemon=True,
        ).start()

    def _run_worker(self, jobs):
        try:
            for d, cmd_parts in jobs:
                self.output_queue.put(
                    ("out", f"\n===== {d} — {' '.join(cmd_parts)} =====\n")
                )

                if not os.path.isdir(d):
                    self.output_queue.put(
                        ("err", f"[ERROR] Директория не существует: {d}\n")
                    )
                    continue

                try:
                    self.process = subprocess.Popen(
                        cmd_parts, cwd=d,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, bufsize=1,
                    )
                except FileNotFoundError:
                    self.output_queue.put(
                        ("err", f"[ERROR] Команда не найдена: {cmd_parts[0]}\n")
                    )
                    continue

                for line in self.process.stdout:
                    self.output_queue.put(("out", line))

                self.process.wait()
                self.output_queue.put(
                    ("out", f"[exit code: {self.process.returncode}]\n")
                )
                self.process = None

        finally:
            self.output_queue.put(("done", None))

    def _stop(self):
        if self.process is not None:
            try:
                self.process.terminate()
                self.output_queue.put(("out", "\n[INFO] Процесс прерван\n"))
            except Exception as e:
                self.output_queue.put(("err", f"[ERROR] {e}\n"))

    #  Очередь вывода
    
    def _drain_output(self):
        try:
            while True:
                kind, payload = self.output_queue.get_nowait()
                if kind == "done":
                    self.running = False
                    self.run_sel_btn.configure(state="normal")
                    self.run_all_btn.configure(state="normal")
                    self.stop_btn.configure(state="disabled")
                    continue
                if payload:
                    self._log(payload)
        except queue.Empty:
            pass
        self.after(100, self._drain_output)

    def _log(self, text):
        self.output.insert("end", text)
        self.output.see("end")

    def _clear_output(self):
        self.output.delete("1.0", "end")

    #  Персистентность

    def _load_config(self):
        if not CONFIG_PATH.is_file():
            return
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            for t in data.get("tasks", []):
                if isinstance(t, dict) and "dir" in t and "sub" in t:
                    self.tasks.append({
                        "dir": t["dir"],
                        "cmd": t.get("cmd", PRESET_COMMANDS[0]),
                        "sub": t["sub"],
                        "enabled": t.get("enabled", True),
                    })
        except Exception as e:
            self._log(f"[WARN] Не удалось загрузить конфиг: {e}\n")

    def _save_config(self):
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = {"tasks": [
                {
                    "dir": t["dir"],
                    "cmd": t["cmd"],
                    "sub": t["sub"],
                    "enabled": t["enabled"],
                }
                for t in self.tasks
            ]}
            CONFIG_PATH.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            self._log(f"[WARN] Не удалось сохранить конфиг: {e}\n")

    def _on_close(self):
        if self.running:
            if not messagebox.askyesno(
                "Прервать?",
                "Идёт команда. Закрыть окно и прервать?",
                parent=self,
            ):
                return
            self._stop()
        self._save_config()
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()
