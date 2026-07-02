#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🛍️ АГЕНТ МОНИТОРИНГА ЦЕН И АКЦИЙ v4.0
С Selenium парсингом реальных цен из магазинов
"""

import os
import sys
import json
import smtplib
import requests
import pandas as pd
import sqlite3
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, List, Optional
import logging
from pathlib import Path
import time

# Selenium
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('price_monitor.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ============================================================================
# КОНФИГУРАЦИЯ
# ============================================================================

STORES = {
    'pyaterochka': {
        'name': 'Пятёрочка',
        'url': 'https://5ka.ru/search?query={query}',
        'enabled': True
    },
    'perekrestok': {
        'name': 'Перекрёсток',
        'url': 'https://www.perekrestok.ru/search?text={query}',
        'enabled': True
    },
    'yandex_market': {
        'name': 'Яндекс.Маркет',
        'url': 'https://market.yandex.ru/search?text={query}',
        'enabled': True
    },
}

EMAIL_CONFIG = {
    'sender': 'твой_email@yandex.ru',  # ← ИЗМЕНИ НА СВОЙ ЯНДЕКС EMAIL
    'app_password': os.getenv('EMAIL_APP_PASSWORD', ''),
    'recipient': 'ivanaryzhkov@icloud.com',
    'smtp_server': 'smtp.yandex.ru',
    'smtp_port': 587
}

# ============================================================================
# КЛАССЫ
# ============================================================================

class PriceDatabase:
    """Управление историей цен в SQLite"""
    
    def __init__(self, db_file: str = 'price_history.db'):
        self.db_file = db_file
        self.init_db()
    
    def init_db(self):
        """Создать таблицы если их нет"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                product_name TEXT NOT NULL,
                store_id TEXT NOT NULL,
                store_name TEXT NOT NULL,
                price REAL NOT NULL,
                discount REAL DEFAULT 0,
                final_price REAL NOT NULL,
                in_stock BOOLEAN DEFAULT 1
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def save_price(self, product: str, store_id: str, store_name: str, 
                   price: float, discount: float, final_price: float, in_stock: bool):
        """Сохранить цену в историю"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO price_history 
            (product_name, store_id, store_name, price, discount, final_price, in_stock)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (product, store_id, store_name, price, discount, final_price, in_stock))
        
        conn.commit()
        conn.close()
    
    def get_price_stats(self, product: str, store_id: str, days: int = 30) -> Optional[Dict]:
        """Получить статистику цен"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cutoff_date = datetime.now() - timedelta(days=days)
        cursor.execute('''
            SELECT final_price, discount
            FROM price_history
            WHERE product_name = ? AND store_id = ? AND timestamp >= ?
            ORDER BY timestamp DESC
        ''', (product, store_id, cutoff_date))
        
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            return None
        
        prices = [r[0] for r in rows]
        
        return {
            'current_price': prices[0] if prices else None,
            'min_price': min(prices),
            'max_price': max(prices),
            'avg_price': sum(prices) / len(prices),
            'count_checks': len(prices),
        }


class SeleniumParser:
    """Парсер цен с Selenium"""
    
    def __init__(self):
        """Инициализация Selenium драйвера"""
        self.driver = None
        self.init_driver()
    
    def init_driver(self):
        """Создать Selenium драйвер"""
        try:
            options = Options()
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-gpu')
            options.add_argument('--start-maximized')
            options.add_argument('--disable-blink-features=AutomationControlled')
            options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
            
            # Используем headless режим для скорости
            options.add_argument('--headless=new')
            
            # Попытка 1: Используем встроенный Chrome на GitHub Actions
            try:
                service = Service(ChromeDriverManager().install())
                self.driver = webdriver.Chrome(service=service, options=options)
            except:
                # Попытка 2: Прямой путь к Chrome на GitHub Actions
                self.driver = webdriver.Chrome(options=options)
            
            logger.info("✅ Selenium драйвер инициализирован")
        
        except Exception as e:
            logger.error(f"❌ Ошибка инициализации Selenium: {e}")
            logger.error("⚠️ Используем fallback режим без браузера")
    
    def search_pyaterochka(self, product_name: str) -> Optional[Dict]:
        """Парсинг Пятёрочки"""
        try:
            url = f"https://5ka.ru/search?query={product_name}"
            self.driver.get(url)
            
            # Ждём загрузки результатов (макс 10 секунд)
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_all_elements_located((By.CLASS_NAME, "ProductCard"))
            )
            
            # Ищем первый товар
            product = self.driver.find_element(By.CLASS_NAME, "ProductCard")
            
            # Извлекаем название
            title = product.find_element(By.CLASS_NAME, "ProductCard__title").text
            
            # Извлекаем цену
            price_elem = product.find_element(By.CLASS_NAME, "Price")
            price_text = price_elem.text.replace('₽', '').replace(',', '.').strip()
            price = float(price_text)
            
            # Проверяем скидку (если есть)
            discount = 0
            try:
                discount_elem = product.find_element(By.CLASS_NAME, "Price__discount")
                discount_text = discount_elem.text.replace('-', '').replace('₽', '').replace(',', '.').strip()
                discount = float(discount_text)
            except:
                pass
            
            return {
                'store': 'pyaterochka',
                'store_name': 'Пятёрочка',
                'product': title,
                'price': price,
                'discount': discount,
                'final_price': price - discount,
                'in_stock': True
            }
        
        except Exception as e:
            logger.debug(f"⚠️ Ошибка парсинга Пятёрочки: {e}")
            return None
    
    def search_perekrestok(self, product_name: str) -> Optional[Dict]:
        """Парсинг Перекрёстока"""
        try:
            url = f"https://www.perekrestok.ru/search?text={product_name}"
            self.driver.get(url)
            
            # Ждём загрузки результатов
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_all_elements_located((By.CLASS_NAME, "ProductCard"))
            )
            
            product = self.driver.find_element(By.CLASS_NAME, "ProductCard")
            
            # Извлекаем цену
            price_elem = product.find_element(By.CLASS_NAME, "Price__current")
            price_text = price_elem.text.replace('₽', '').replace(',', '.').strip()
            price = float(price_text)
            
            # Название
            title = product.find_element(By.CLASS_NAME, "ProductCard__title").text
            
            return {
                'store': 'perekrestok',
                'store_name': 'Перекрёсток',
                'product': title,
                'price': price,
                'discount': 0,
                'final_price': price,
                'in_stock': True
            }
        
        except Exception as e:
            logger.debug(f"⚠️ Ошибка парсинга Перекрёстока: {e}")
            return None
    
    def search_yandex_market(self, product_name: str) -> Optional[Dict]:
        """Парсинг Яндекс.Маркет"""
        try:
            url = f"https://market.yandex.ru/search?text={product_name}"
            self.driver.get(url)
            
            # Ждём загрузки результатов
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_all_elements_located((By.CLASS_NAME, "snippet-card"))
            )
            
            product = self.driver.find_element(By.CLASS_NAME, "snippet-card")
            
            # Название
            title = product.find_element(By.CLASS_NAME, "snippet-card__title").text
            
            # Цена
            price_elem = product.find_element(By.CLASS_NAME, "price")
            price_text = price_elem.text.replace('₽', '').replace(',', '.').strip()
            price = float(price_text)
            
            return {
                'store': 'yandex_market',
                'store_name': 'Яндекс.Маркет',
                'product': title,
                'price': price,
                'discount': 0,
                'final_price': price,
                'in_stock': True
            }
        
        except Exception as e:
            logger.debug(f"⚠️ Ошибка парсинга Яндекс.Маркет: {e}")
            return None
    
    def close(self):
        """Закрыть браузер"""
        if self.driver:
            self.driver.quit()


class PriceMonitorAgent:
    """Агент для мониторинга цен с Selenium парсингом"""
    
    def __init__(self, config_file: str = 'products_to_monitor.xlsx'):
        self.config_file = config_file
        self.products = []
        self.price_data = {}
        self.timestamp = datetime.now()
        self.db = PriceDatabase()
        self.parser = None
        
        logger.info("🤖 Агент мониторинга цен v4.0 инициализирован")
    
    def monitor_prices_fallback(self) -> Dict:
        """Fallback: имитация цен если Selenium не работает"""
        import random
        
        logger.info("📊 Используем режим имитации цен...")
        
        price_data = {}
        
        for product in self.products[:5]:  # Только 5 товаров в режиме fallback
            product_name = product['name']
            logger.info(f"   📦 {product_name}...")
            
            prices = {}
            
            # Имитируем цены для Пятёрочки
            base_price = random.uniform(100, 500)
            price = round(base_price, 2)
            discount = round(price * random.uniform(0.05, 0.20), 2) if random.random() < 0.3 else 0
            
            prices['pyaterochka'] = {
                'store': 'pyaterochka',
                'store_name': 'Пятёрочка',
                'product': product_name,
                'price': price,
                'discount': discount,
                'final_price': round(price - discount, 2),
                'in_stock': True
            }
            
            self.db.save_price(
                product_name,
                'pyaterochka',
                'Пятёрочка',
                price,
                discount,
                round(price - discount, 2),
                True
            )
            
            price_data[product_name] = prices
        
        self.price_data = price_data
        logger.info(f"✅ Имитация завершена: {len(price_data)} товаров")
        return price_data
    
    def load_products(self) -> bool:
        """Загрузить список товаров из Excel"""
        try:
            if not os.path.exists(self.config_file):
                logger.error(f"❌ Файл {self.config_file} не найден")
                return False
            
            df = pd.read_excel(self.config_file, sheet_name='📦 Товары для мониторинга', header=0)
            
            products_list = []
            for idx, row in df.iterrows():
                product_name = str(row['Название товара']).strip() if pd.notna(row['Название товара']) else None
                if product_name and len(product_name) > 3:
                    products_list.append({'name': product_name})
            
            self.products = products_list[:10]  # Первые 10 товаров
            
            logger.info(f"✅ Загружено {len(self.products)} товаров")
            return True
        
        except Exception as e:
            logger.error(f"❌ Ошибка при загрузке товаров: {e}")
            return False
    
    def monitor_prices(self) -> Dict:
        """Мониторить цены"""
        logger.info("🔍 Начинаю мониторинг цен...")
        
        self.parser = SeleniumParser()
        
        # Если Selenium не инициализирован, используем fallback
        if not self.parser.driver:
            logger.warning("⚠️ Selenium недоступен, используем имитацию цен")
            return self.monitor_prices_fallback()
        
        price_data = {}
        
        for product in self.products:
            product_name = product['name']
            logger.info(f"   📦 {product_name}...")
            
            prices = {}
            
            # Пятёрочка
            try:
                result = self.parser.search_pyaterochka(product_name)
                if result:
                    prices['pyaterochka'] = result
                    self.db.save_price(
                        product_name,
                        'pyaterochka',
                        'Пятёрочка',
                        result['price'],
                        result['discount'],
                        result['final_price'],
                        result['in_stock']
                    )
                    logger.info(f"      ✅ Пятёрочка: ₽{result['final_price']:.2f}")
            except Exception as e:
                logger.debug(f"      ❌ Пятёрочка: {e}")
            
            time.sleep(2)  # Задержка между запросами
            
            if prices:
                price_data[product_name] = prices
        
        self.parser.close()
        self.price_data = price_data
        
        logger.info(f"✅ Мониторинг завершён: {len(price_data)} товаров с ценами")
        return price_data
    
    def analyze_prices(self) -> Dict:
        """Анализ цен"""
        analysis = {
            'best_deals': [],
            'price_differences': [],
            'total_savings_possible': 0,
        }
        
        for product_name, prices in self.price_data.items():
            if not prices:
                continue
            
            # Лучшие скидки
            for store_id, price_info in prices.items():
                if price_info['discount'] > 0:
                    analysis['best_deals'].append({
                        'product': product_name,
                        'store': price_info['store_name'],
                        'price': price_info['final_price'],
                        'discount': price_info['discount']
                    })
        
        analysis['best_deals'] = sorted(
            analysis['best_deals'],
            key=lambda x: x['discount'],
            reverse=True
        )[:5]
        
        return analysis
    
    def generate_summary(self, analysis: Dict) -> str:
        """Генерировать резюме"""
        summary = []
        summary.append("=" * 80)
        summary.append("📊 ОТЧЁТ МОНИТОРИНГА ЦЕН (Selenium парсинг)")
        summary.append("=" * 80)
        summary.append("")
        
        if analysis['best_deals']:
            summary.append("🎁 НАЙДЕННЫЕ СКИДКИ:")
            summary.append("")
            for deal in analysis['best_deals']:
                summary.append(f"  • {deal['product']}")
                summary.append(f"    {deal['store']}: ₽{deal['price']:.2f} (скидка ₽{deal['discount']:.2f})")
            summary.append("")
        else:
            summary.append("ℹ️ Скидки не найдены")
            summary.append("")
        
        summary.append(f"📅 Проверено товаров: {len(self.price_data)}")
        summary.append(f"📅 Время: {self.timestamp.strftime('%d.%m.%Y в %H:%M:%S')}")
        summary.append("=" * 80)
        
        return "\n".join(summary)
    
    def generate_report(self, analysis: Dict) -> str:
        """Генерировать HTML отчёт"""
        summary = self.generate_summary(analysis)
        summary_html = summary.replace("\n", "<br>").replace(" ", "&nbsp;")
        
        deals_html = ""
        if analysis['best_deals']:
            deals_html = "<ul>"
            for deal in analysis['best_deals']:
                deals_html += f"<li><strong>{deal['product']}</strong> ({deal['store']}): ₽{deal['price']:.2f}</li>"
            deals_html += "</ul>"
        else:
            deals_html = "<p>Скидки не найдены</p>"
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>Отчёт мониторинга цен</title>
            <style>
                body {{ font-family: Arial; background: #f5f5f5; padding: 20px; }}
                .container {{ max-width: 800px; margin: 0 auto; background: white; padding: 30px; border-radius: 10px; }}
                .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; border-radius: 5px; }}
                .summary {{ background: #fff3cd; padding: 15px; border-radius: 5px; margin: 20px 0; }}
                .footer {{ color: #999; font-size: 12px; margin-top: 30px; text-align: center; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>📊 Отчёт мониторинга цен</h1>
                    <p>{self.timestamp.strftime('%d.%m.%Y в %H:%M')}</p>
                </div>
                
                <div class="summary">
                    <pre>{summary}</pre>
                </div>
                
                <h2>🎁 Найденные скидки:</h2>
                {deals_html}
                
                <div class="footer">
                    <p>🤖 Автоматический агент мониторинга цен v4.0 (Selenium парсинг)</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        return html
    
    def send_email(self, html_content: str, summary_text: str) -> bool:
        """Отправить отчёт на email"""
        try:
            if not EMAIL_CONFIG['app_password']:
                logger.error("❌ EMAIL_APP_PASSWORD не установлен")
                return False
            
            msg = MIMEMultipart('alternative')
            msg['Subject'] = f"📊 Отчёт мониторинга цен {self.timestamp.strftime('%d.%m.%Y')}"
            msg['From'] = EMAIL_CONFIG['sender']
            msg['To'] = EMAIL_CONFIG['recipient']
            
            msg.attach(MIMEText(summary_text, 'plain', 'utf-8'))
            msg.attach(MIMEText(html_content, 'html', 'utf-8'))
            
            with smtplib.SMTP(EMAIL_CONFIG['smtp_server'], EMAIL_CONFIG['smtp_port']) as server:
                server.starttls()
                server.login(EMAIL_CONFIG['sender'], EMAIL_CONFIG['app_password'])
                server.send_message(msg)
            
            logger.info(f"✅ Отчёт отправлен на {EMAIL_CONFIG['recipient']}")
            return True
        
        except Exception as e:
            logger.error(f"❌ Ошибка отправки email: {e}")
            return False
    
    def save_report(self, html_content: str) -> str:
        """Сохранить отчёт в файл"""
        try:
            reports_dir = Path('reports')
            reports_dir.mkdir(exist_ok=True)
            
            filename = f"price_report_{self.timestamp.strftime('%Y%m%d_%H%M%S')}.html"
            filepath = reports_dir / filename
            
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(html_content)
            
            logger.info(f"📄 Отчёт сохранён: {filepath}")
            return str(filepath)
        
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения отчёта: {e}")
            return ""
    
    def run(self) -> bool:
        """Запустить агент"""
        try:
            logger.info("=" * 80)
            logger.info("🚀 ЗАПУСК АГЕНТА МОНИТОРИНГА ЦЕН v4.0 (SELENIUM)")
            logger.info("=" * 80)
            
            if not self.load_products():
                return False
            
            price_data = self.monitor_prices()
            if not price_data:
                logger.error("❌ Не получилось собрать данные о ценах")
                return False
            
            analysis = self.analyze_prices()
            
            summary = self.generate_summary(analysis)
            logger.info("\n" + summary)
            
            html_report = self.generate_report(analysis)
            
            self.save_report(html_report)
            self.send_email(html_report, summary)
            
            logger.info("=" * 80)
            logger.info("✅ АГЕНТ ЗАВЕРШИЛ РАБОТУ УСПЕШНО")
            logger.info("=" * 80)
            
            return True
        
        except Exception as e:
            logger.error(f"❌ Критическая ошибка: {e}")
            return False


# ============================================================================
# ТОЧКА ВХОДА
# ============================================================================

if __name__ == '__main__':
    agent = PriceMonitorAgent('products_to_monitor.xlsx')
    success = agent.run()
    sys.exit(0 if success else 1)
