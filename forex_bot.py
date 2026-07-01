import telebot
from telebot import types
import requests
import time
import threading
import logging
from datetime import datetime, timezone
from flask import Flask
import xml.etree.ElementTree as ET
import re

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = "8636811936:AAGjxcifErfiuq_JmTRpK_A94SMq8XUFEKo"
CHAT_ID = "1718292210"
GOLD_API = "goldapi-c4085f23c4a16779d6d8d8bb3eaf9550-io"

bot = telebot.TeleBot(TOKEN, threaded=True)
app = Flask(__name__)

sent_actual_ids = set()
sent_news_titles = set()
announced_pre = set()
last_gold_price = None
weekly_events = []
user_currencies = {'USD', 'EUR', 'GBP', 'JPY', 'CAD', 'AUD', 'NZD', 'CHF'}
price_alerts = {}
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
        return response.json().get('rates', {})
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

def get_trade_recommendation(actual, forecast, country):
    try:
        sentiment, icon = get_sentiment(actual, forecast)
        if not sentiment:
            return ''
        currency = COUNTRY_NAME.get(country, country)
        flag = COUNTRY_FLAG.get(country, '')
        msg = "\n💡 *توصية التداول:*\n"
        if sentiment == 'ايجابي':
            msg += "📈 الخبر ايجابي لـ " + flag + " " + currency + "\n"
            msg += "✅ فكر في: شراء " + currency
        else:
            msg += "📉 الخبر سلبي لـ " + flag + " " + currency + "\n"
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
                msg = "🥇 *تحديث سعر الذهب*\n━━━━━━━━━━━━━━━━━\n\n"
                msg += "💰 السعر الحالي: *$" + f"{price:,.2f}" + "*\n"
                if last_gold_price:
                    diff = price - last_gold_price
                    pct = (diff / last_gold_price) * 100
                    if diff > 0:
                        msg += "📈 +$" + f"{diff:,.2f} (+{pct:.2f}%)\n🟢 صعودي"
                    elif diff < 0:
                        msg += "📉 $" + f"{diff:,.2f} ({pct:.2f}%)\n🔴 هبوطي"
                    else:
                        msg += "➡️ لا يوجد تغيير"
                last_gold_price = price
                bot.send_message(CHAT_ID, msg, parse_mode="Markdown")

                for cid in list(price_alerts.keys()):
                    if 'gold' in price_alerts.get(cid, {}):
                        target = price_alerts[cid]['gold']
                        if last_gold_price and ((last_gold_price < target <= price) or (last_gold_price > target >= price)):
                            bot.send_message(cid,
                                "🔔 *تنبيه الذهب!*\nوصل السعر الى: $" + f"{price:,.2f}",
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
                msg = "📰 *خبر عاجل*\n━━━━━━━━━━━━━━━━━\n\n"
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
                    msg = "📊 *تقرير الاسبوع*\n━━━━━━━━━━━━━━━━━\n\n"
                    msg += "📈 ايجابي: " + str(positive) + "\n📉 سلبي: " + str(negative)
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
                    msg = "⏰ *خبر بعد 15 دقيقة*\n━━━━━━━━━━━━━━━━━\n\n"
                    msg += flag + " *" + name + "*\n📌 " + e['event']
                    bot.send_message(CHAT_ID, msg, parse_mode="Markdown")
                    announced_pre.add(pre_id)

                actual_id = "actual_" + e['id']
                if e['actual'] and actual_id not in sent_actual_ids:
                    flag = COUNTRY_FLAG.get(e['country'], '')
                    name = COUNTRY_NAME.get(e['country'], e['country'])
                    sentiment_text, icon = get_sentiment(e['actual'], e['forecast'])
                    msg = "🚨 *صدر الخبر الان!*\n━━━━━━━━━━━━━━━━━\n\n"
                    msg += "🕐 `" + e['time'] + "`\n" + flag + " *" + name + "*\n"
                    msg += "📌 " + e['event'] + "\n🎯 " + e['forecast'] + " | 📉 " + e['previous'] + " | ✅ " + e['actual']
                    if sentiment_text:
                        msg += "\n" + icon + " " + sentiment_text
                    rec = get_trade_recommendation(e['actual'], e['forecast'], e['country'])
                    if rec:
                        msg += rec
                    bot.send_message(CHAT_ID, msg, parse_mode="Markdown")
                    sent_actual_ids.add(actual_id)
                    e['sentiment'] = sentiment_text
                    weekly_events.append(e)

                upcoming_id = "upcoming_" + e['id']
                if not e['actual'] and upcoming_id not in upcoming_sent and -60 <= diff_minutes <= 2880:
                    flag = COUNTRY_FLAG.get(e['country'], '')
                    name = COUNTRY_NAME.get(e['country'], e['country'])
                    msg = "📊 *حدث قادم* 🔴\n━━━━━━━━━━━━━━━━━\n\n"
                    msg += "🕐 `" + e['time'] + "`\n" + flag + " *" + name + "*\n📌 " + e['event']
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
        types.InlineKeyboardButton("📡 حالة البوت", callback_data="status")
    )
    bot.send_message(chat_id, "🤖 القائمة الرئيسية - اختر ما تريد:", reply_markup=markup)

@bot.message_handler(commands=['start'])
def start(m):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("📋 القائمة"))
    bot.send_message(m.chat.id,
        "👋 اهلا بك في بوت الاسواق المالية!\nاضغط على القائمة للبدء\n\nتم انشاء هذا البوت بواسطة د/عاصم النجار",
        reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "📋 القائمة")
def show_menu(m):
    send_main_menu(m.chat.id)

@bot.message_handler(func=lambda m: m.chat.id in user_states)
def handle_text(m):
    chat_id = m.chat.id
    text = m.text.strip()
    state = user_states.get(chat_id)

    if state == 'waiting_gold_alert':
        try:
            price = float(text.replace(',', ''))
            price_alerts.setdefault(chat_id, {})['gold'] = price
            del user_states[chat_id]
            bot.send_message(chat_id, "✅ تم التفعيل! سيتم اشعارك عند وصول الذهب الى $" + f"{price:,.2f}")
        except:
            bot.send_message(chat_id, "❌ ارسل رقم صحيح مثل: 4100")

@bot.callback_query_handler(func=lambda c: True)
def handle_callback(c):
    logger.info("CALLBACK: " + str(c.data) + " from " + str(c.message.chat.id))
    chat_id = c.message.chat.id
    data = c.data

    try:
        if data == "gold":
            price = fetch_gold_price()
            msg = "🥇 سعر الذهب: $" + f"{price:,.2f}"
            bot.send_message(chat_id, msg)

        elif data == "currencies":
            rates = fetch_currency_rates()
            if rates:
                msg = "💱 اسعار العملات مقابل الدولار\n\n"
                pairs = [('EUR','🇪🇺'),('GBP','🇬🇧'),('JPY','🇯🇵'),('CAD','🇨🇦'),('AUD','🇦🇺')]
                for code, flag in pairs:
                    if code in rates:
                        usd_per = 1 / rates[code] if rates[code] else 0
                        msg += flag + " " + code + " = " + f"{usd_per:.4f}" + " USD\n"
                bot.send_message(chat_id, msg)
            else:
                bot.send_message(chat_id, "خطا في جلب الاسعار")

        elif data == "today":
            events = fetch_calendar()
            today = now_utc().strftime('%Y-%m-%d')
            today_events = [e for e in events if e['time'].startswith(today)]
            if today_events:
                msg = "📅 احداث اليوم\n\n"
                for e in today_events:
                    flag = COUNTRY_FLAG.get(e['country'], '')
                    msg += flag + " " + e['event'] + " - " + e['time'] + "\n"
                bot.send_message(chat_id, msg)
            else:
                bot.send_message(chat_id, "لا توجد احداث اليوم")

        elif data == "news":
            articles = fetch_rss_news()
            if articles:
                msg = "📰 اخر الاخبار\n\n"
                for a in articles[:5]:
                    msg += "📌 " + a['title'] + "\n\n"
                bot.send_message(chat_id, msg)
            else:
                bot.send_message(chat_id, "لا توجد اخبار مهمة الان")

        elif data == "set_alert":
            user_states[chat_id] = 'waiting_gold_alert'
            bot.send_message(chat_id, "🥇 ارسل السعر المطلوب للذهب بالدولار، مثال: 4100")

        elif data == "status":
            msg = "✅ البوت يعمل\n📌 احداث: " + str(len(sent_actual_ids)) + "\n📰 اخبار: " + str(len(sent_news_titles))
            bot.send_message(chat_id, msg)

        bot.answer_callback_query(c.id)
    except Exception as ex:
        logger.error("خطا في الكولباك: " + str(ex))
        try:
            bot.answer_callback_query(c.id, text="حدث خطأ")
        except:
            pass

if __name__ == "__main__":
    logger.info("البوت بدا...")
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=check_calendar, daemon=True).start()
    threading.Thread(target=monitor_gold, daemon=True).start()
    threading.Thread(target=monitor_news, daemon=True).start()
    threading.Thread(target=send_weekly_stats, daemon=True).start()
    bot.remove_webhook()
    time.sleep(1)
    bot.infinity_polling(skip_pending=True)
