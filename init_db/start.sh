#!/usr/bin/env bash
# Запуск на Linux. Без аргументов — ежедневное меню.
# Аргументы: install (первичная установка), server (сразу поднять сайт).

set -euo pipefail
cd "$(dirname "$0")"

PYTHON="$(command -v python3 || command -v python)"

case "${1:-}" in
  install)
    echo "Первичная установка..."
    "$PYTHON" install.py
    ;;
  server)
    echo "Запуск сайта..."
    "$PYTHON" server.py --no-reload
    ;;
  "")
    echo "Рабочее меню..."
    "$PYTHON" launcher.py
    ;;
  *)
    echo "Неизвестный аргумент: $1" >&2
    echo "Использование: $0 [install|server]" >&2
    exit 2
    ;;
esac
