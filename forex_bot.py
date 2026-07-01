import telebot
from telebot import types
import requests
import time
import threading
import logging
from datetime import datetime, timezone, timedelta
from flask import Flask
import xml.etree.ElementTree as ET
import re
import json

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = "8687483288:AAHZnOZs396LLTgPaNHnH_KQGhDCCBoxOzM"
CHAT_ID = "1718292210"
GOLD_API = "goldapi-c4085f23c4a16779d6d8d8bb3eaf9550-io"

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

sent_actual_ids = set()
sent_news_titles = set()
announced_pre = set()
last_gold_price = None
weekly_events = []
user_currencies = {'USD', 'EUR', 'GBP', 'JPY', 'CAD', 'AUD', 'NZD', 'CHF'}

# تنبيهات الاسعار المخصصة {chat_id: {'gold': price, 'USD': price}}
price_alerts = {}
# حالة انتظار المدخلات
user_states = {}

COUNTRY_FLAG = {
    'USD': '🇺🇸', 'EUR': '🇪🇺', 'GBP': '🇬🇧', 'JPY': '🇯🇵',
    'CAD': '🇨🇦', 'AUD': '🇦🇺', 'NZD': '🇳🇿', 'CHF': '🇨🇭',
    'CNY': '🇨🇳', 'CNH': '🇨🇳'
}

COUNTRY_NAME = {
    'USD': 'الدولار الامريكي', 'EUR': 'اليورو',
    'GBP': 'الجنيه الاسترليني', 'JPY': 'الين الياباني',
    'CAD': 'الدولار الكندي', 'AUD': 'الدولار الاسترالي',
    'NZD': 'الدولار النيوزيلندي', 'CHF': 'الفرنك السويسري',
    'CNY': 'اليوان الصيني'
}

ALL_CURRENCIES = ['USD', 'EUR', 'GBP', 'JPY', 'CAD', 'AUD', 'NZD', 'CHF', 'CNY']

KEYWORDS = [
    'حرب', 'عقوبات', 'صراع', 'توتر', 'ازمة', 'هجوم', 'غارة', 'قصف',
    'فيدرالي', 'فائدة', 'تضخم', 'بطالة', 'ناتج', 'اقتصاد', 'نمو', 'ركود',
    'بيتكوين', 'عملات رقمية', 'كريبتو', 'ايثيريوم',
    'نفط', 'ذهب', 'برميل', 'اوبك',
    'دولار', 'يورو', 'عملة', 'صرف',
    'بورصة', 'اسهم', 'سوق', 'استثمار', 'مؤشر',
    'بنك', 'مركزي', 'احتياطي',
    'نووي', 'صاروخ', 'تفجير', 'اغتيال'
]

RSS_FEEDS = [
    'https://www.aljazeera.net/rss/all.xml',
    'https://www.alarabiya.net/rss/arab-and-world',
    'https://feeds.bbci.co.uk/arabic/rss.xml',
    'https://www.skynewsarabia.com/rss/all',
]

def now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)

@app.route('/')
def home():
    return "البوت يعمل!"

def run_flask():
    app.run(host='0.0.0.0', port=9090)

def clean_text(text):
    text = re.sub('<[^<]+?>', '', text)
    text = re.sub(r'http\S+', '', text)
    return text.strip()

def is_important(title, desc=''):
    text = (title + ' ' + desc).lower()
    return any(kw in text for kw in KEYWORDS)

def fetch_rss_news():
    all_news = []
    headers = {'User-Agent': 'Mozilla/5.0'}
    for url in RSS_FEEDS:
        try:
            response = requests.get(url, headers=headers, timeout=10)
            root = ET.fromstring(response.content)
            items = root.findall('.//item')
            for item in items[:15]:
                title = clean_text(item.findtext('title', ''))
                desc = clean_text(item.findtext('description', ''))[:200]
                if title and is_important(title, desc) and title not in sent_news_titles:
                    all_news.append({'title': title, 'desc': desc})
        except Exception as ex:
            logger.error("خطا RSS: " + str(ex))
    return all_news

def fetch_gold_price():
    url = "https://www.goldapi.io/api/XAU/USD"
    headers = {'x-access-token': GOLD_API}
    response = requests.get(url, headers=headers, timeout=15)
    return response.json().get('price', None)

def fetch_currency_rates():
    try:
        url = "https://api.exchangerate-api.com/v4/latest/USD"
        response = requests.get(url, timeout=10)
        data = response.json()
        return data.get('rates', {})
    except:
        return {}

def fetch_calendar():
    url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    headers = {'User-Agent': 'Mozilla/5.0'}
    response = requests.get(url, headers=headers, timeout=15)
    if response.status_code != 200:
        return []
    data = response.json()
    events = []
    for item in data:
        if item.get('impact') != 'High':
            continue
        country = item.get('country', '')
        if country not in user_currencies:
            continue
        raw_date = item.get('date', '')
        try:
            event_dt = datetime.fromisoformat(raw_date)
        except:
            continue
        eid = raw_date + "_" + country + "_" + item.get('title', '')
        events.append({
            'id': eid,
            'dt': event_dt,
            'time': event_dt.strftime('%Y-%m-%d %H:%M'),
            'country': country,
            'event': item.get('title', ''),
            'forecast': item.get('forecast') or '--',
            'previous': item.get('previous') or '--',
            'actual': item.get('actual') or '',
        })
    return events

def get_sentiment(actual, forecast):
    try:
        def parse(v):
            return float(str(v).replace('%','').replace('K','').replace('M','').strip())
        a = parse(actual)
        f = parse(forecast)
        if a > f:
            return 'ايجابي', '📈'
        elif a < f:
            return 'سلبي', '📉'
        else:
            return 'محايد', '➡️'
    except:
        return '', ''

def get_trade_recommendation(event, actual, forecast, previous, country):
    try:
        sentiment, icon = get_sentiment(actual, forecast)
        if not sentiment:
            return ''
        
        currency = COUNTRY_NAME.get(country, country)
        flag = COUNTRY_FLAG.get(country, '')
        
        msg = "\n💡 *توصية التداول:*\n"
        
        if sentiment == 'ايجابي':
            msg += f"📈 الخبر ايجابي لـ {flag} {currency}\n"
            if country == 'USD':
                msg += "✅ فكر في: شراء الدولار / بيع الذهب\n"
                msg += "⚠️ أزواج محتملة: USD/JPY ↑ | EUR/USD ↓ | XAU/USD ↓"
            elif country == 'EUR':
                msg += "✅ فكر في: شراء اليورو\n"
                msg += "⚠️ أزواج محتملة: EUR/USD ↑ | EUR/JPY ↑"
            elif country == 'GBP':
                msg += "✅ فكر في: شراء الجنيه\n"
                msg += "⚠️ أزواج محتملة: GBP/USD ↑ | GBP/JPY ↑"
            else:
                msg += "✅ فكر في: شراء " + currency
        else:
            msg += f"📉 الخبر سلبي لـ {flag} {currency}\n"
            if country == 'USD':
                msg += "✅ فكر في: بيع الدولار / شراء الذهب\n"
                msg += "⚠️ أزواج محتملة: USD/JPY ↓ | EUR/USD ↑ | XAU/USD ↑"
            elif country == 'EUR':
                msg += "✅ فكر في: بيع اليورو\n"
                msg += "⚠️ أزواج محتملة: EUR/USD ↓ | EUR/JPY ↓"
            elif country == 'GBP':
                msg += "✅ فكر في: بيع الجنيه\n"
                msg += "⚠️ أزواج محتملة: GBP/USD ↓ | GBP/JPY ↓"
            else:
                msg += "✅ فكر في: بيع " + currency
        
        msg += "\n\n⚠️ *تنبيه: هذه اقتراحات وليست نصائح مالية*"
        return msg
    except:
        return ''

def monitor_gold():
    global last_gold_price
    while True:
        try:
            price = fetch_gold_price()
            if price:
                msg = "🥇 *تحديث سعر الذهب*\n"
                msg += "━━━━━━━━━━━━━━━━━\n\n"
                msg += "💰 السعر الحالي: *$" + f"{price:,.2f}" + "*\n"
                if last_gold_price:
                    diff = price - last_gold_price
                    pct = (diff / last_gold_price) * 100
                    if diff > 0:
                        msg += "📈 التغيير: +$" + f"{diff:,.2f} (+{pct:.2f}%)\n"
                        msg += "🟢 الاتجاه: صعودي"
                    elif diff < 0:
                        msg += "📉 التغيير: $" + f"{diff:,.2f} ({pct:.2f}%)\n"
                        msg += "🔴 الاتجاه: هبوطي"
                    else:
                        msg += "➡️ لا يوجد تغيير"
                last_gold_price = price
                bot.send_message(CHAT_ID, msg, parse_mode="Markdown")

                # فحص تنبيهات الذهب
                for cid, alerts in price_alerts.items():
                    if 'gold' in alerts:
                        target = alerts['gold']
                        if (last_gold_price and last_gold_price < target <= price) or \
                           (last_gold_price and last_gold_price > target >= price):
                            bot.send_message(cid,
                                "🔔 *تنبيه الذهب!*\n"
                                "━━━━━━━━━━━━━━━━━\n\n"
                                "🥇 وصل الذهب للسعر المحدد!\n"
                                "💰 السعر الحالي: *$" + f"{price:,.2f}" + "*\n"
                                "🎯 السعر المستهدف: *$" + f"{target:,.2f}" + "*",
                                parse_mode="Markdown")
                            del price_alerts[cid]['gold']
        except Exception as ex:
            logger.error("خطا الذهب: " + str(ex))
        time.sleep(7200)

def monitor_news():
    while True:
        try:
            articles = fetch_rss_news()
            for a in articles[:5]:
                msg = "📰 *خبر عاجل*\n"
                msg += "━━━━━━━━━━━━━━━━━\n\n"
                msg += "📌 *" + a['title'] + "*\n"
                if a['desc'] and a['desc'] != a['title']:
                    msg += "📝 " + a['desc'] + "\n"
                bot.send_message(CHAT_ID, msg, parse_mode="Markdown")
                sent_news_titles.add(a['title'])
                time.sleep(2)
        except Exception as ex:
            logger.error("خطا الاخبار: " + str(ex))
        time.sleep(900)

def send_weekly_stats():
    while True:
        try:
            now = now_utc()
            if now.weekday() == 4 and now.hour == 20:
                if weekly_events:
                    positive = sum(1 for e in weekly_events if 'ايجابي' in e.get('sentiment',''))
                    negative = sum(1 for e in weekly_events if 'سلبي' in e.get('sentiment',''))
                    neutral  = sum(1 for e in weekly_events if 'محايد' in e.get('sentiment',''))
                    msg = "📊 *تقرير احصائيات الاسبوع*\n"
                    msg += "━━━━━━━━━━━━━━━━━\n\n"
                    msg += "📈 نتائج ايجابية: " + str(positive) + "\n"
                    msg += "📉 نتائج سلبية: " + str(negative) + "\n"
                    msg += "➡️ نتائج محايدة: " + str(neutral) + "\n\n"
                    for e in weekly_events[-10:]:
                        flag = COUNTRY_FLAG.get(e['country'], '')
                        name = COUNTRY_NAME.get(e['country'], e['country'])
                        msg += flag + " " + name + " - " + e['event'] + "\n"
                        msg += "✅ " + e['actual'] + " | 🎯 " + e['forecast'] + "\n\n"
                    bot.send_message(CHAT_ID, msg, parse_mode="Markdown")
                    weekly_events.clear()
        except Exception as ex:
            logger.error("خطا الاحصائيات: " + str(ex))
        time.sleep(3600)

def check_calendar():
    upcoming_sent = {}
    while True:
        try:
            now = now_utc()
            events = fetch_calendar()
            for e in events:
                event_dt = e['dt'].replace(tzinfo=None)
                diff_minutes = (event_dt - now).total_seconds() / 60

                pre_id = "pre_" + e['id']
                if 14 <= diff_minutes <= 16 and pre_id not in announced_pre:
                    flag = COUNTRY_FLAG.get(e['country'], '')
                    name = COUNTRY_NAME.get(e['country'], e['country'])
                    msg = "⏰ *تنبيه! خبر بعد 15 دقيقة*\n━━━━━━━━━━━━━━━━━\n\n"
                    msg += flag + " *" + name + "*\n"
                    msg += "📌 " + e['event'] + "\n"
                    msg += "🎯 التوقع: `" + e['forecast'] + "`\n"
                    msg += "📉 السابق: `" + e['previous'] + "`"
                    bot.send_message(CHAT_ID, msg, parse_mode="Markdown")
                    announced_pre.add(pre_id)

                actual_id = "actual_" + e['id']
                if e['actual'] and actual_id not in sent_actual_ids:
                    flag = COUNTRY_FLAG.get(e['country'], '')
                    name = COUNTRY_NAME.get(e['country'], e['country'])
                    sentiment_text, icon = get_sentiment(e['actual'], e['forecast'])
                    msg = "🚨 *صدر الخبر الان!*\n━━━━━━━━━━━━━━━━━\n\n"
                    msg += "🕐 `" + e['time'] + "`\n"
                    msg += flag + " *" + name + "*\n"
                    msg += "📌 " + e['event'] + "\n"
                    msg += "⚡ التاثير: 🔴 عالي\n"
                    msg += "🎯 التوقع: `" + e['forecast'] + "`\n"
                    msg += "📉 السابق: `" + e['previous'] + "`\n"
                    msg += "✅ الفعلي: `" + e['actual'] + "`\n"
                    if sentiment_text:
                        msg += icon + " التحليل: " + sentiment_text + "\n"
                    recommendation = get_trade_recommendation(
                        e['event'], e['actual'], e['forecast'],
                        e['previous'], e['country'])
                    if recommendation:
                        msg += recommendation
                    bot.send_message(CHAT_ID, msg, parse_mode="Markdown")
                    sent_actual_ids.add(actual_id)
                    e['sentiment'] = sentiment_text
                    weekly_events.append(e)

                upcoming_id = "upcoming_" + e['id']
                if (not e['actual'] and
                    upcoming_id not in upcoming_sent and
                    -60 <= diff_minutes <= 2880):
                    flag = COUNTRY_FLAG.get(e['country'], '')
                    name = COUNTRY_NAME.get(e['country'], e['country'])
                    msg = "📊 *حدث اقتصادي قادم* 🔴\n━━━━━━━━━━━━━━━━━\n\n"
                    msg += "🕐 `" + e['time'] + "`\n"
                    msg += flag + " *" + name + "*\n"
                    msg += "📌 " + e['event'] + "\n"
                    msg += "⚡ التاثير: 🔴 عالي\n"
                    msg += "🎯 التوقع: `" + e['forecast'] + "`\n"
                    msg += "📉 السابق: `" + e['previous'] + "`"
                    bot.send_message(CHAT_ID, msg, parse_mode="Markdown")
                    upcoming_sent[upcoming_id] = now
        except Exception as ex:
            logger.error("خطا التقويم: " + str(ex))
        time.sleep(120)

def send_main_menu(chat_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🥇 سعر الذهب", callback_data="gold"),
        types.InlineKeyboardButton("💱 اسعار العملات", callback_data="currencies"),
        types.InlineKeyboardButton("📅 احداث اليوم", callback_data="today"),
        types.InlineKeyboardButton("📰 اخر الاخبار", callback_data="news"),
        types.InlineKeyboardButton("🔔 تنبيه سعر", callback_data="set_alert"),
        types.InlineKeyboardButton("⚙️ فلتر العملات", callback_data="filter"),
        types.InlineKeyboardButton("📊 احصائيات الاسبوع", callback_data="stats"),
        types.InlineKeyboardButton("📡 حالة البوت", callback_data="status")
    )
    bot.send_message(chat_id, "🤖 *القائمة الرئيسية*\nاختر ما تريد:",
                     parse_mode="Markdown", reply_markup=markup)

@bot.message_handler(commands=['start'])
def start(m):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("📋 القائمة"))
    welcome = (
        "👋 *اهلا بك في بوت الاسواق المالية!*\n\n"
        "🔔 سيتم اشعارك تلقائيا بـ:\n"
        "• الاحداث الاقتصادية عالية التاثير\n"
        "• توصيات تداول بعد كل خبر\n"
        "• سعر الذهب كل ساعتين\n"
        "• اخبار عاجلة كل 15 دقيقة\n"
        "• تنبيهات اسعار مخصصة\n\n"
        "اضغط على 📋 *القائمة* للبدء\n\n"
        "👨 *تم انشاء هذا البوت بواسطة*\n"
        "د/عاصم النجار"
    )
    bot.send_message(m.chat.id, welcome, parse_mode="Markdown", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "📋 القائمة")
def show_menu(m):
    send_main_menu(m.chat.id)

@bot.message_handler(func=lambda m: m.chat.id in user_states, content_types=['text'])
def handle_text(m):
    chat_id = m.chat.id
    text = m.text.strip()

    if chat_id in user_states:
        state = user_states[chat_id]

        if state == 'waiting_gold_alert':
            try:
                price = float(text.replace(',', ''))
                if chat_id not in price_alerts:
                    price_alerts[chat_id] = {}
                price_alerts[chat_id]['gold'] = price
                del user_states[chat_id]
                bot.send_message(chat_id,
                    "✅ *تم تفعيل التنبيه!*\n"
                    "🥇 سيتم اشعارك عندما يصل الذهب الى: *$" + f"{price:,.2f}" + "*",
                    parse_mode="Markdown")
            except:
                bot.send_message(chat_id, "❌ ارسل رقماً صحيحاً مثل: 4100")

        elif state == 'waiting_currency_alert':
            try:
                parts = text.split()
                if len(parts) == 2:
                    currency = parts[0].upper()
                    price = float(parts[1])
                    if chat_id not in price_alerts:
                        price_alerts[chat_id] = {}
                    price_alerts[chat_id][currency] = price
                    del user_states[chat_id]
                    bot.send_message(chat_id,
                        "✅ *تم تفعيل التنبيه!*\n"
                        "💱 سيتم اشعارك عندما يصل " + currency + " الى: *" + str(price) + "*",
                        parse_mode="Markdown")
                else:
                    bot.send_message(chat_id, "❌ ارسل العملة والسعر مثل: EUR 1.10")
            except:
                bot.send_message(chat_id, "❌ ارسل العملة والسعر مثل: EUR 1.10")

@bot.callback_query_handler(func=lambda c: True)
def handle_callback(c):
    logger.info('CALLBACK RECEIVED: ' + str(c.data))
    chat_id = c.message.chat.id
    data = c.data

    if data == "gold":
        try:
            price = fetch_gold_price()
            msg = "🥇 *سعر الذهب الحالي*\n━━━━━━━━━━━━━━━━━\n\n"
            msg += "💰 السعر: *$" + f"{price:,.2f}" + "* للاونصة\n"
            if last_gold_price:
                diff = price - last_gold_price
                pct = (diff / last_gold_price) * 100
                if diff > 0:
                    msg += "📈 +$" + f"{diff:,.2f} (+{pct:.2f}%)" + "\n🟢 صعودي"
                elif diff < 0:
                    msg += "📉 $" + f"{diff:,.2f} ({pct:.2f}%)" + "\n🔴 هبوطي"
            bot.send_message(chat_id, msg, parse_mode="Markdown")
        except:
            bot.send_message(chat_id, "خطا في جلب سعر الذهب.")

    elif data == "currencies":
        try:
            rates = fetch_currency_rates()
            if rates:
                msg = "💱 *اسعار العملات مقابل الدولار*\n━━━━━━━━━━━━━━━━━\n\n"
                pairs = [
                    ('EUR', '🇪🇺', 'اليورو'),
                    ('GBP', '🇬🇧', 'الجنيه'),
                    ('JPY', '🇯🇵', 'الين'),
                    ('CAD', '🇨🇦', 'الدولار الكندي'),
                    ('AUD', '🇦🇺', 'الدولار الاسترالي'),
                    ('CHF', '🇨🇭', 'الفرنك السويسري'),
                    ('NZD', '🇳🇿', 'الدولار النيوزيلندي'),
                ]
                for code, flag, name in pairs:
                    if code in rates:
                        rate = rates[code]
                        usd_per = 1 / rate if rate != 0 else 0
                        msg += flag + " " + code + " = *" + f"{usd_per:.4f}" + "* USD\n"
                msg += "\n🕐 " + now_utc().strftime('%Y-%m-%d %H:%M') + " UTC"
                bot.send_message(chat_id, msg, parse_mode="Markdown")
            else:
                bot.send_message(chat_id, "خطا في جلب اسعار العملات.")
        except Exception as ex:
            bot.send_message(chat_id, "خطا: " + str(ex))

    elif data == "set_alert":
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("🥇 تنبيه الذهب", callback_data="alert_gold"),
            types.InlineKeyboardButton("💱 تنبيه عملة", callback_data="alert_currency")
        )
        bot.send_message(chat_id, "🔔 *اختر نوع التنبيه:*",
                        parse_mode="Markdown", reply_markup=markup)

    elif data == "alert_gold":
        user_states[chat_id] = 'waiting_gold_alert'
        bot.send_message(chat_id,
            "🥇 *تنبيه سعر الذهب*\n\n"
            "ارسل السعر المطلوب بالدولار:\n"
            "مثال: *4100*",
            parse_mode="Markdown")

    elif data == "alert_currency":
        user_states[chat_id] = 'waiting_currency_alert'
        bot.send_message(chat_id,
            "💱 *تنبيه سعر العملة*\n\n"
            "ارسل رمز العملة والسعر المطلوب:\n"
            "مثال: *EUR 1.10*",
            parse_mode="Markdown")

    elif data == "today":
        try:
            events = fetch_calendar()
            today = now_utc().strftime('%Y-%m-%d')
            today_events = [e for e in events if e['time'].startswith(today)]
            if today_events:
                msg = "📅 *احداث اليوم الاقتصادية* 🔴\n━━━━━━━━━━━━━━━━━\n\n"
                for e in today_events:
                    flag = COUNTRY_FLAG.get(e['country'], '')
                    name = COUNTRY_NAME.get(e['country'], e['country'])
                    msg += "🕐 `" + e['time'] + "`\n"
                    msg += flag + " *" + name + "*\n"
                    msg += "📌 " + e['event'] + "\n"
                    msg += "🎯 " + e['forecast'] + " | 📉 " + e['previous'] + "\n\n"
                bot.send_message(chat_id, msg, parse_mode="Markdown")
            else:
                bot.send_message(chat_id, "لا توجد احداث عالية التاثير اليوم.")
        except Exception as ex:
            bot.send_message(chat_id, "خطا: " + str(ex))

    elif data == "news":
        try:
            articles = fetch_rss_news()
            if articles:
                msg = "📰 *اخر الاخبار العاجلة*\n━━━━━━━━━━━━━━━━━\n\n"
                for a in articles[:5]:
                    msg += "📌 *" + a['title'] + "*\n"
                    if a['desc'] and a['desc'] != a['title']:
                        msg += "📝 " + a['desc'] + "\n"
                    msg += "\n"
                bot.send_message(chat_id, msg, parse_mode="Markdown")
            else:
                bot.send_message(chat_id, "لا توجد اخبار مهمة الان.")
        except Exception as ex:
            bot.send_message(chat_id, "خطا: " + str(ex))

    elif data == "filter":
        markup = types.InlineKeyboardMarkup(row_width=3)
        buttons = []
        for cur in ALL_CURRENCIES:
            flag = COUNTRY_FLAG.get(cur, '')
            status = "✅" if cur in user_currencies else "❌"
            buttons.append(types.InlineKeyboardButton(
                status + " " + flag + " " + cur, callback_data="toggle_" + cur))
        markup.add(*buttons)
        markup.add(types.InlineKeyboardButton("💾 حفظ", callback_data="save_filter"))
        bot.send_message(chat_id, "⚙️ *فلتر العملات*",
                        parse_mode="Markdown", reply_markup=markup)

    elif data.startswith("toggle_"):
        cur = data.replace("toggle_", "")
        if cur in user_currencies:
            user_currencies.discard(cur)
        else:
            user_currencies.add(cur)
        markup = types.InlineKeyboardMarkup(row_width=3)
        buttons = []
        for cu in ALL_CURRENCIES:
            flag = COUNTRY_FLAG.get(cu, '')
            status = "✅" if cu in user_currencies else "❌"
            buttons.append(types.InlineKeyboardButton(
                status + " " + flag + " " + cu, callback_data="toggle_" + cu))
        markup.add(*buttons)
        markup.add(types.InlineKeyboardButton("💾 حفظ", callback_data="save_filter"))
        bot.edit_message_reply_markup(chat_id, c.message.message_id, reply_markup=markup)

    elif data == "save_filter":
        names = [COUNTRY_NAME.get(cu, cu) for cu in sorted(user_currencies)]
        msg = "✅ *تم الحفظ!*\n" + "\n".join("• " + n for n in names)
        bot.send_message(chat_id, msg, parse_mode="Markdown")

    elif data == "stats":
        if weekly_events:
            positive = sum(1 for e in weekly_events if 'ايجابي' in e.get('sentiment',''))
            negative = sum(1 for e in weekly_events if 'سلبي' in e.get('sentiment',''))
            neutral  = sum(1 for e in weekly_events if 'محايد' in e.get('sentiment',''))
            msg = "📊 *احصائيات هذا الاسبوع*\n━━━━━━━━━━━━━━━━━\n\n"
            msg += "📈 ايجابي: " + str(positive) + "\n"
            msg += "📉 سلبي: " + str(negative) + "\n"
            msg += "➡️ محايد: " + str(neutral)
            bot.send_message(chat_id, msg, parse_mode="Markdown")
        else:
            bot.send_message(chat_id, "لا توجد بيانات بعد هذا الاسبوع.")

    elif data == "status":
        gold_txt = "\n🥇 اخر سعر ذهب: $" + f"{last_gold_price:,.2f}" if last_gold_price else ""
        alerts_count = sum(len(v) for v in price_alerts.values())
        msg = (
            "✅ *البوت يعمل*\n"
            "━━━━━━━━━━━━━━━━━\n"
            "📌 احداث: " + str(len(sent_actual_ids)) + "\n"
            "📰 اخبار: " + str(len(sent_news_titles)) + "\n"
            "🔔 تنبيهات: " + str(alerts_count) + "\n"
            "💱 عملات: " + str(len(user_currencies)) +
            gold_txt
        )
        bot.send_message(chat_id, msg, parse_mode="Markdown")

    bot.answer_callback_query(c.id)

if __name__ == "__main__":
    logger.info("البوت بدا...")
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=check_calendar, daemon=True).start()
    threading.Thread(target=monitor_gold, daemon=True).start()
    threading.Thread(target=monitor_news, daemon=True).start()
    threading.Thread(target=send_weekly_stats, daemon=True).start()
    bot.infinity_polling()
