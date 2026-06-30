#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
⏰ ПЛАНИРОВЩИК МОНИТОРИНГА ЦЕН
Запускает агент мониторинга ежедневно в 7:00 утра
"""

import schedule
import time
import sys
import os
from datetime import datetime
import logging
import subprocess

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('scheduler.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def run_price_monitor():
    """Запустить агент мониторинга цен"""
    logger.info("🚀 Запуск агента мониторинга цен...")
    
    try:
        result = subprocess.run(
            [sys.executable, 'price_monitor_agent.py'],
            capture_output=True,
            text=True,
            timeout=300  # 5 минут
        )
        
        if result.returncode == 0:
            logger.info("✅ Агент успешно завершил работу")
        else:
            logger.error(f"❌ Ошибка выполнения: {result.stderr}")
    
    except subprocess.TimeoutExpired:
        logger.error("❌ Агент выполнялся слишком долго (timeout)")
    except Exception as e:
        logger.error(f"❌ Ошибка при запуске: {e}")


def start_scheduler():
    """Запустить планировщик"""
    logger.info("=" * 80)
    logger.info("📅 ПЛАНИРОВЩИК МОНИТОРИНГА ЦЕН ЗАПУЩЕН")
    logger.info("=" * 80)
    logger.info("⏰ Агент будет запускаться каждый день в 07:00 (по локальному времени)")
    logger.info("   Для остановки нажмите Ctrl+C")
    logger.info("")
    
    # Запланировать ежедневный запуск в 7:00
    schedule.every().day.at("07:00").do(run_price_monitor)
    
    # Запустить один раз сразу (опционально, раскомментируй если нужно)
    # run_price_monitor()
    
    # Основной цикл
    while True:
        try:
            schedule.run_pending()
            time.sleep(60)  # Проверяем каждую минуту
        except KeyboardInterrupt:
            logger.info("\n⛔ Планировщик остановлен пользователем")
            break
        except Exception as e:
            logger.error(f"❌ Ошибка в планировщике: {e}")
            time.sleep(60)


if __name__ == '__main__':
    # Проверка зависимостей
    try:
        import schedule
    except ImportError:
        logger.error("❌ Необходим пакет 'schedule'. Установи: pip install schedule")
        sys.exit(1)
    
    start_scheduler()
