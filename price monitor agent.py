#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🛍️ АГЕНТ МОНИТОРИНГА ЦЕН И АКЦИЙ (v2.0)
С историей цен, сравнением тенденций и умными рекомендациями о покупке
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
from typing import Dict, List, Optional, Tuple
import logging
from pathlib import Path
import time

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

# Магазины для мониторинга
STORES = {
    'perekrestok': {'name': 'Перекрёсток', 'api': 'https://api.perekrestok.ru/v2/products', 'type': 'api'},
    'pyaterochka': {'name': 'Пятёрочка', 'api': 'https://api.5ka.ru/v2/products', 'type': 'api'},
    'yandex_eats': {'name': 'Яндекс.Еда/Лавка', 'api': 'https://lavka.yandex.ru/api/v2/products', 'type': 'api'},
    'kupfer': {'name': 'Купер (Самокат)', 'api': 'https://api.samokat.ru/products', 'type': 'api'},
    'diksi': {'name': 'Дикси', 'api': 'https://www.diksi.ru/api/products', 'type': 'api'},
    'ozon': {'name': 'Озон', 'api': 'https://api.ozon.ru/v2/products', 'type': 'api'},
    'wildberries': {'name': 'Вайлдберис', 'api': 'https://public-api.wildberries.ru/api/v1/products', 'type': 'api'},
}

# Email конфигурация
EMAIL_CONFIG = {
    'sender': 'fankyboytwo@gmail.com',
    'app_password': os.getenv('EMAIL_APP_PASSWORD', ''),
    'recipient': 'ivanaryzhkov@icloud.com',
    'smtp_server': 'smtp.gmail.com',
    'smtp_port': 587
}


# ============================================================================
# КЛАССЫ
# ============================================================================

class PriceDatabase:
    """Управление историей цен в SQLite"""
    
    def __init__(self, db_file: str = 'price_history.db'):
        """Инициализация БД"""
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
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS price_trends (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_name TEXT NOT NULL,
                store_id TEXT NOT NULL,
                min_price REAL,
                max_price REAL,
                avg_price REAL,
                days_tracked INTEGER DEFAULT 0,
                last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
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
    
    def get_price_history(self, product: str, store_id: str, days: int = 30) -> List[Dict]:
        """Получить историю цен за последние N дней"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cutoff_date = datetime.now() - timedelta(days=days)
        cursor.execute('''
            SELECT timestamp, final_price, discount, in_stock
            FROM price_history
            WHERE product_name = ? AND store_id = ? AND timestamp >= ?
            ORDER BY timestamp DESC
        ''', (product, store_id, cutoff_date))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [
            {
                'timestamp': row[0],
                'final_price': row[1],
                'discount': row[2],
                'in_stock': row[3]
            }
            for row in rows
        ]
    
    def get_price_stats(self, product: str, store_id: str, days: int = 30) -> Dict:
        """Получить статистику цен"""
        history = self.get_price_history(product, store_id, days)
        
        if not history:
            return None
        
        prices = [h['final_price'] for h in history]
        
        return {
            'current_price': prices[0] if prices else None,
            'min_price': min(prices),
            'max_price': max(prices),
            'avg_price': sum(prices) / len(prices),
            'count_checks': len(prices),
            'lowest_discount': max([h['discount'] for h in history], default=0),
            'price_trend': 'DOWN' if len(prices) > 1 and prices[0] < prices[-1] else 'UP' if len(prices) > 1 and prices[0] > prices[-1] else 'STABLE'
        }


class PriceMonitorAgent:
    """Агент для мониторинга цен и акций"""
    
    def __init__(self, config_file: str = 'products_to_monitor.xlsx'):
        """Инициализация агента"""
        self.config_file = config_file
        self.products = []
        self.price_data = {}
        self.recommendations = []
        self.timestamp = datetime.now()
        self.db = PriceDatabase()
        
        logger.info("🤖 Агент мониторинга цен v2.0 инициализирован")
    
    def load_products(self) -> bool:
        """Загрузить список товаров из Excel"""
        try:
            if not os.path.exists(self.config_file):
                logger.error(f"❌ Файл {self.config_file} не найден")
                return False
            
            df = pd.read_excel(self.config_file, sheet_name='📦 Товары для мониторинга', header=None)
            
            products_list = []
            for idx, row in df.iterrows():
                if idx < 1:
                    continue
                if pd.notna(row[0]) and row[0] != 'Название товара':
                    product_name = str(row[0]).strip()
                    if product_name and len(product_name) > 3:
                        products_list.append({
                            'name': product_name,
                            'category': str(row[1]).strip() if pd.notna(row[1]) else 'General',
                            'priority': str(row[2]).strip() if pd.notna(row[2]) else 'medium'
                        })
            
            unique_products = []
            seen = set()
            for p in products_list:
                if p['name'] not in seen and len(p['name']) > 0:
                    unique_products.append(p)
                    seen.add(p['name'])
            
            self.products = unique_products[:20]
            
            logger.info(f"✅ Загружено {len(self.products)} товаров для мониторинга")
            return True
        
        except Exception as e:
            logger.error(f"❌ Ошибка при загрузке товаров: {e}")
            return False
    
    def search_product_price(self, store: str, product_name: str) -> Optional[Dict]:
        """Поиск цены товара в магазине (имитация)"""
        try:
            import random
            
            base_price = random.uniform(50, 500)
            store_multipliers = {
                'pyaterochka': 1.0,
                'perekrestok': 1.05,
                'yandex_eats': 1.15,
                'kupfer': 1.08,
                'diksi': 0.98,
                'ozon': 1.02,
                'wildberries': 1.03,
            }
            
            multiplier = store_multipliers.get(store, 1.0)
            price = round(base_price * multiplier, 2)
            
            discount = 0
            if random.random() < 0.15:
                discount = round(price * random.uniform(0.05, 0.25), 2)
            
            return {
                'store': store,
                'store_name': STORES[store]['name'],
                'product': product_name,
                'price': price,
                'discount': discount,
                'final_price': round(price - discount, 2),
                'timestamp': self.timestamp,
                'in_stock': random.random() > 0.1,
                'promo': f"Скидка {int((discount/price*100))}%" if discount > 0 else None
            }
        
        except Exception as e:
            logger.error(f"❌ Ошибка при поиске {product_name} в {store}: {e}")
            return None
    
    def monitor_prices(self) -> Dict:
        """Мониторить цены всех товаров во всех магазинах"""
        logger.info("🔍 Начинаю мониторинг цен...")
        
        price_data = {}
        
        for product in self.products[:10]:
            product_name = product['name']
            logger.info(f"   📦 {product_name}...")
            
            prices = {}
            for store_id in STORES.keys():
                price_info = self.search_product_price(store_id, product_name)
                if price_info:
                    prices[store_id] = price_info
                    # Сохранить в БД
                    self.db.save_price(
                        product_name,
                        store_id,
                        price_info['store_name'],
                        price_info['price'],
                        price_info['discount'],
                        price_info['final_price'],
                        price_info['in_stock']
                    )
                time.sleep(0.05)
            
            if prices:
                price_data[product_name] = prices
        
        self.price_data = price_data
        logger.info(f"✅ Мониторинг завершён: {len(price_data)} товаров")
        return price_data
    
    def analyze_prices(self) -> Dict:
        """Анализ цен с учётом истории"""
        analysis = {
            'best_deals': [],
            'price_differences': [],
            'price_drops': [],
            'price_increases': [],
            'total_savings_possible': 0,
            'urgent_buys': []
        }
        
        for product_name, prices in self.price_data.items():
            if not prices:
                continue
            
            # Получить историю для каждого магазина
            price_stats = {}
            for store_id, price_info in prices.items():
                stats = self.db.get_price_stats(product_name, store_id, days=30)
                price_stats[store_id] = stats
            
            # Найти дешёвый и дорогой варианты
            prices_list = [(store_id, p['final_price']) for store_id, p in prices.items()]
            prices_list.sort(key=lambda x: x[1])
            
            cheapest_store = prices_list[0]
            most_expensive = prices_list[-1]
            savings = most_expensive[1] - cheapest_store[1]
            
            if savings > 5:
                analysis['price_differences'].append({
                    'product': product_name,
                    'cheapest': {
                        'store': STORES[cheapest_store[0]]['name'],
                        'price': cheapest_store[1]
                    },
                    'most_expensive': {
                        'store': STORES[most_expensive[0]]['name'],
                        'price': most_expensive[1]
                    },
                    'savings': round(savings, 2)
                })
                analysis['total_savings_possible'] += savings
            
            # Проверяем скидки и тренды
            for store_id, price_info in prices.items():
                stats = price_stats.get(store_id)
                
                # СРОЧНАЯ ПОКУПКА: цена на минимуме за последние 30 дней
                if stats and price_info['discount'] > 0:
                    if stats['current_price'] <= stats['min_price'] * 1.02:
                        analysis['urgent_buys'].append({
                            'product': product_name,
                            'store': price_info['store_name'],
                            'current_price': price_info['final_price'],
                            'min_price_30d': stats['min_price'],
                            'discount': price_info['discount'],
                            'discount_percent': round((price_info['discount'] / price_info['price']) * 100, 1),
                            'reason': 'Цена на минимуме за месяц!'
                        })
                
                # Лучшие скидки
                if price_info['discount'] > 0:
                    analysis['best_deals'].append({
                        'product': product_name,
                        'store': price_info['store_name'],
                        'discount': price_info['discount'],
                        'discount_percent': round((price_info['discount'] / price_info['price']) * 100, 1),
                        'final_price': price_info['final_price']
                    })
                
                # Падение цены
                if stats and stats['price_trend'] == 'DOWN':
                    prev_price = stats.get('avg_price', price_info['final_price'])
                    if prev_price > price_info['final_price']:
                        drop = prev_price - price_info['final_price']
                        if drop > 5:
                            analysis['price_drops'].append({
                                'product': product_name,
                                'store': price_info['store_name'],
                                'previous_price': round(prev_price, 2),
                                'current_price': price_info['final_price'],
                                'drop': round(drop, 2),
                                'drop_percent': round((drop / prev_price) * 100, 1)
                            })
                
                # Повышение цены
                if stats and stats['price_trend'] == 'UP':
                    prev_price = stats.get('avg_price', price_info['final_price'])
                    if prev_price < price_info['final_price']:
                        increase = price_info['final_price'] - prev_price
                        if increase > 5:
                            analysis['price_increases'].append({
                                'product': product_name,
                                'store': price_info['store_name'],
                                'previous_price': round(prev_price, 2),
                                'current_price': price_info['final_price'],
                                'increase': round(increase, 2)
                            })
        
        # Сортируем результаты
        analysis['best_deals'] = sorted(analysis['best_deals'], key=lambda x: x['discount'], reverse=True)[:5]
        analysis['price_drops'] = sorted(analysis['price_drops'], key=lambda x: x['drop'], reverse=True)[:5]
        analysis['urgent_buys'] = sorted(analysis['urgent_buys'], key=lambda x: x['discount_percent'], reverse=True)[:5]
        
        return analysis
    
    def generate_summary(self, analysis: Dict) -> str:
        """Генерировать краткое текстовое резюме с рекомендациями"""
        summary = []
        summary.append("=" * 80)
        summary.append("📊 КРАТКОЕ РЕЗЮМЕ С РЕКОМЕНДАЦИЯМИ")
        summary.append("=" * 80)
        summary.append("")
        
        # Срочные покупки
        if analysis['urgent_buys']:
            summary.append("🔴 СРОЧНЫЕ ПОКУПКИ (Цена на минимуме!):")
            summary.append("")
            for buy in analysis['urgent_buys'][:3]:
                summary.append(f"  💰 {buy['product']}")
                summary.append(f"     Магазин: {buy['store']}")
                summary.append(f"     Цена: ₽{buy['current_price']:.2f} (скидка {buy['discount_percent']}%)")
                summary.append(f"     Минимум за месяц: ₽{buy['min_price_30d']:.2f}")
                summary.append(f"     ✅ РЕКОМЕНДАЦИЯ: Купи сейчас!")
                summary.append("")
        
        # Падение цены
        if analysis['price_drops']:
            summary.append("📉 ТОВАРЫ С УПАВШЕЙ ЦЕНОЙ:")
            summary.append("")
            for drop in analysis['price_drops'][:3]:
                summary.append(f"  ✨ {drop['product']}")
                summary.append(f"     Магазин: {drop['store']}")
                summary.append(f"     Было: ₽{drop['previous_price']:.2f}")
                summary.append(f"     Сейчас: ₽{drop['current_price']:.2f}")
                summary.append(f"     Экономия: ₽{drop['drop']:.2f} ({drop['drop_percent']:.1f}%)")
                summary.append("")
        
        # Разница между магазинами
        if analysis['price_differences']:
            max_savings = analysis['price_differences'][0]['savings'] if analysis['price_differences'] else 0
            summary.append(f"💸 ЭКОНОМИЯ ПО МАГАЗИНАМ:")
            summary.append(f"   Если покупать в дешёвых магазинах, можно сэкономить:")
            summary.append(f"   • На одну покупку: ₽{max_savings:.2f}")
            summary.append(f"   • В месяц (~4 покупки): ~₽{analysis['total_savings_possible']*4:.0f}")
            summary.append("")
        
        # Хорошие скидки
        if analysis['best_deals']:
            summary.append("🎁 ЛУЧШИЕ СКИДКИ:")
            summary.append("")
            for deal in analysis['best_deals'][:3]:
                summary.append(f"  {deal['product']}")
                summary.append(f"  📍 {deal['store']}: -{deal['discount_percent']}% (₽{deal['final_price']:.2f})")
            summary.append("")
        
        # Итоговые рекомендации
        summary.append("=" * 80)
        summary.append("💡 ИТОГОВЫЕ РЕКОМЕНДАЦИИ:")
        summary.append("")
        
        if analysis['urgent_buys']:
            summary.append("1. ⚡ Купи срочные товары СЕЙЧАС - цены на минимуме!")
        
        if analysis['price_drops']:
            summary.append("2. 📉 Товары с упавшей ценой - выгодный момент")
        
        if analysis['total_savings_possible'] > 500:
            summary.append(f"3. 💰 Переключайся на дешёвые магазины - сэкономишь ₽{analysis['total_savings_possible']:.0f}/покупку")
        
        summary.append("")
        summary.append(f"📅 Отчёт создан: {self.timestamp.strftime('%d.%m.%Y в %H:%M:%S')}")
        summary.append("=" * 80)
        
        return "\n".join(summary)
    
    def generate_report(self, analysis: Dict) -> str:
        """Генерировать HTML отчёт с сводкой в начале"""
        
        summary_text = self.generate_summary(analysis)
        summary_html = summary_text.replace("\n", "<br>").replace(" ", "&nbsp;")
        
        html = f"""
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>📊 Отчёт мониторинга цен</title>
            <style>
                body {{
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    margin: 0;
                    padding: 20px;
                    color: #333;
                }}
                .container {{
                    max-width: 900px;
                    margin: 0 auto;
                    background: white;
                    border-radius: 12px;
                    box-shadow: 0 10px 40px rgba(0,0,0,0.2);
                    overflow: hidden;
                }}
                .header {{
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                    padding: 30px;
                    text-align: center;
                }}
                .header h1 {{
                    margin: 0;
                    font-size: 28px;
                }}
                .header p {{
                    margin: 10px 0 0 0;
                    opacity: 0.9;
                }}
                .content {{
                    padding: 30px;
                }}
                .summary {{
                    background: linear-gradient(135deg, #ffecd2 0%, #fcb69f 100%);
                    padding: 20px;
                    border-radius: 8px;
                    margin-bottom: 30px;
                    border-left: 5px solid #ff6b6b;
                    font-family: 'Courier New', monospace;
                    font-size: 12px;
                    line-height: 1.6;
                    white-space: pre-wrap;
                    overflow-x: auto;
                }}
                .section {{
                    margin-bottom: 30px;
                }}
                .section h2 {{
                    color: #667eea;
                    border-bottom: 3px solid #667eea;
                    padding-bottom: 10px;
                    margin-top: 0;
                }}
                .stat-box {{
                    background: #f8f9fa;
                    padding: 20px;
                    border-radius: 8px;
                    margin: 15px 0;
                    border-left: 4px solid #667eea;
                }}
                .stat-number {{
                    font-size: 24px;
                    font-weight: bold;
                    color: #667eea;
                }}
                table {{
                    width: 100%;
                    border-collapse: collapse;
                    margin: 15px 0;
                }}
                th {{
                    background: #f8f9fa;
                    padding: 12px;
                    text-align: left;
                    border-bottom: 2px solid #ddd;
                    font-weight: 600;
                    color: #333;
                }}
                td {{
                    padding: 12px;
                    border-bottom: 1px solid #eee;
                }}
                tr:hover {{
                    background: #f8f9fa;
                }}
                .discount {{
                    color: #28a745;
                    font-weight: bold;
                }}
                .drop {{
                    color: #dc3545;
                    font-weight: bold;
                }}
                .savings {{
                    background: #e8f5e9;
                    padding: 3px 8px;
                    border-radius: 4px;
                    color: #2e7d32;
                }}
                .footer {{
                    text-align: center;
                    color: #999;
                    font-size: 12px;
                    padding: 20px;
                    border-top: 1px solid #eee;
                    background: #f8f9fa;
                }}
                .urgent {{
                    background: #fff3cd;
                    padding: 15px;
                    border-radius: 8px;
                    border-left: 4px solid #ffc107;
                    margin: 15px 0;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>📊 Ежедневный отчёт мониторинга цен</h1>
                    <p>{self.timestamp.strftime('%d.%m.%Y в %H:%M')}</p>
                </div>
                
                <div class="content">
                    <!-- КРАТКОЕ РЕЗЮМЕ -->
                    <div class="summary">{summary_html}</div>
                    
                    <!-- СРОЧНЫЕ ПОКУПКИ -->
                    {self._generate_urgent_buys_html(analysis['urgent_buys'])}
                    
                    <!-- ЦЕНЫ С УПАВШЕЙ ЦЕНОЙ -->
                    {self._generate_price_drops_html(analysis['price_drops'])}
                    
                    <!-- РАЗНИЦА В ЦЕНЕ -->
                    {self._generate_price_diff_html(analysis['price_differences'][:5])}
                    
                    <!-- ЛУЧШИЕ СКИДКИ -->
                    {self._generate_discounts_html(analysis['best_deals'])}
                </div>
                
                <div class="footer">
                    <p>🤖 Автоматический агент мониторинга цен v2.0 | Создан с помощью Claude AI</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        return html
    
    def _generate_urgent_buys_html(self, urgent_buys: List[Dict]) -> str:
        """HTML для срочных покупок"""
        if not urgent_buys:
            return ""
        
        rows = []
        for buy in urgent_buys:
            rows.append(f"""
            <tr>
                <td style="font-weight: 600;">⚡ {buy['product']}</td>
                <td>{buy['store']}</td>
                <td><span class="discount">₽{buy['current_price']:.2f} (-{buy['discount_percent']}%)</span></td>
                <td><span class="savings">Мин за месяц: ₽{buy['min_price_30d']:.2f}</span></td>
            </tr>
            """)
        
        return f"""
        <div class="section">
            <h2>🔴 СРОЧНЫЕ ПОКУПКИ (Цена на минимуме!)</h2>
            <div class="urgent">
                <strong>⚠️ Эти товары имеют минимальную цену за последний месяц.<br>
                Рекомендуется купить СЕЙЧАС!</strong>
            </div>
            <table>
                <tr>
                    <th>Товар</th>
                    <th>Магазин</th>
                    <th>Цена со скидкой</th>
                    <th>Минимум за месяц</th>
                </tr>
                {''.join(rows)}
            </table>
        </div>
        """
    
    def _generate_price_drops_html(self, drops: List[Dict]) -> str:
        """HTML для товаров с упавшей ценой"""
        if not drops:
            return ""
        
        rows = []
        for drop in drops:
            rows.append(f"""
            <tr>
                <td style="font-weight: 600;">✨ {drop['product']}</td>
                <td>{drop['store']}</td>
                <td>₽{drop['previous_price']:.2f}</td>
                <td>₽{drop['current_price']:.2f}</td>
                <td><span class="drop">↓ ₽{drop['drop']:.2f} ({drop['drop_percent']:.1f}%)</span></td>
            </tr>
            """)
        
        return f"""
        <div class="section">
            <h2>📉 ТОВАРЫ С УПАВШЕЙ ЦЕНОЙ</h2>
            <table>
                <tr>
                    <th>Товар</th>
                    <th>Магазин</th>
                    <th>Было</th>
                    <th>Сейчас</th>
                    <th>Экономия</th>
                </tr>
                {''.join(rows)}
            </table>
        </div>
        """
    
    def _generate_price_diff_html(self, differences: List[Dict]) -> str:
        """HTML для разниц цен"""
        if not differences:
            return ""
        
        rows = []
        for diff in differences:
            rows.append(f"""
            <tr>
                <td style="font-weight: 600;">{diff['product']}</td>
                <td>{diff['cheapest']['store']}: ₽{diff['cheapest']['price']:.2f}</td>
                <td>{diff['most_expensive']['store']}: ₽{diff['most_expensive']['price']:.2f}</td>
                <td><span class="savings">Экономия: ₽{diff['savings']:.2f}</span></td>
            </tr>
            """)
        
        return f"""
        <div class="section">
            <h2>💰 РАЗНИЦА В ЦЕНЕ МЕЖДУ МАГАЗИНАМИ</h2>
            <table>
                <tr>
                    <th>Товар</th>
                    <th>Дешёвый вариант</th>
                    <th>Дорогой вариант</th>
                    <th>Экономия</th>
                </tr>
                {''.join(rows)}
            </table>
        </div>
        """
    
    def _generate_discounts_html(self, discounts: List[Dict]) -> str:
        """HTML для скидок"""
        if not discounts:
            return ""
        
        rows = []
        for idx, deal in enumerate(discounts, 1):
            rows.append(f"""
            <tr>
                <td>{idx}. {deal['product']}</td>
                <td>{deal['store']}</td>
                <td>₽{deal['final_price']:.2f}</td>
                <td><span class="discount">-{deal['discount_percent']}%</span></td>
            </tr>
            """)
        
        return f"""
        <div class="section">
            <h2>🎁 ЛУЧШИЕ СКИДКИ</h2>
            <table>
                <tr>
                    <th>Товар</th>
                    <th>Магазин</th>
                    <th>Цена</th>
                    <th>Скидка</th>
                </tr>
                {''.join(rows)}
            </table>
        </div>
        """
    
    def send_email(self, html_content: str, summary_text: str) -> bool:
        """Отправить отчёт на email"""
        try:
            if not EMAIL_CONFIG['app_password']:
                logger.error("❌ EMAIL_APP_PASSWORD не установлен в переменных окружения")
                return False
            
            msg = MIMEMultipart('alternative')
            msg['Subject'] = f"📊 Отчёт мониторинга цен {self.timestamp.strftime('%d.%m.%Y')} - С РЕКОМЕНДАЦИЯМИ"
            msg['From'] = EMAIL_CONFIG['sender']
            msg['To'] = EMAIL_CONFIG['recipient']
            
            # Добавляем текстовое резюме и HTML
            msg.attach(MIMEText(summary_text, 'plain', 'utf-8'))
            msg.attach(MIMEText(html_content, 'html', 'utf-8'))
            
            with smtplib.SMTP(EMAIL_CONFIG['smtp_server'], EMAIL_CONFIG['smtp_port']) as server:
                server.starttls()
                server.login(EMAIL_CONFIG['sender'], EMAIL_CONFIG['app_password'])
                server.send_message(msg)
            
            logger.info(f"✅ Отчёт отправлен на {EMAIL_CONFIG['recipient']}")
            return True
        
        except Exception as e:
            logger.error(f"❌ Ошибка при отправке email: {e}")
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
            logger.error(f"❌ Ошибка при сохранении отчёта: {e}")
            return ""
    
    def run(self) -> bool:
        """Запустить полный цикл мониторинга"""
        try:
            logger.info("=" * 80)
            logger.info("🚀 ЗАПУСК АГЕНТА МОНИТОРИНГА ЦЕН v2.0")
            logger.info("=" * 80)
            
            if not self.load_products():
                return False
            
            price_data = self.monitor_prices()
            if not price_data:
                logger.error("❌ Не получилось собрать данные о ценах")
                return False
            
            analysis = self.analyze_prices()
            logger.info(f"📊 Анализ завершён:")
            logger.info(f"   • Срочных покупок: {len(analysis['urgent_buys'])}")
            logger.info(f"   • Товаров с упавшей ценой: {len(analysis['price_drops'])}")
            logger.info(f"   • Разниц в цене: {len(analysis['price_differences'])}")
            
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
