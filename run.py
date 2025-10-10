#!/usr/bin/env python3
"""Скрипт для запуска бота без Docker"""

import asyncio
import sys
import os

# Добавляем корневую папку проекта в Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.bot.main import main

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Бот остановлен пользователем")
    except Exception as e:
        print(f"❌ Ошибка запуска: {e}")
        sys.exit(1)

