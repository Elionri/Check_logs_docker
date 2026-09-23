#!/usr/bin/env bash
# run.sh — запуск Docker Log Reader
set -e

PROJECT_DIR="/home/user/Check_Logs_Python"
VENV_PY="$PROJECT_DIR/venv/bin/python"
APP="$PROJECT_DIR/Check_logs_new.py"

# Проверка, что venv существует
if [ ! -x "$VENV_PY" ]; then
    echo "venv не найден. Создайте его:"
    echo "  cd $PROJECT_DIR && python3 -m venv venv && source venv/bin/activate && pip install customtkinter docker"
    exit 1
fi

# Проверка, что docker-демон доступен
if ! docker info >/dev/null 2>&1; then
    echo "Docker недоступен. Проверьте, что демон запущен и вы в группе docker."
    exit 1
fi

exec "$VENV_PY" "$APP"
