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

data_lock = threading.Lock()

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
    'CNY': '🇨🇳', 'CNH': '🇨🇳', 'SAR': '🇸🇦', 'EGP': '🇪🇬'
}

COUNTRY_NAME = {
    'USD': 'الدولار الامريكي', 'EUR': 'اليورو',
    'GBP': 'الجنيه الاسترليني', 'JPY': 'الين الياباني',
    'CAD': 'الدولار الكندي', 'AUD': 'الدولار الاسترالي',
    'NZD': 'الدولار النيوزيلندي', 'CHF': 'الفرنك السويسري',
    'CNY': 'اليوان الصيني', 'SAR': 'الريال السعودي', 'EGP': 'الجنيه المصري'
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
    'https://feeds.bbci.co.uk/arabic/rss.xml',
    'https://www.skynewsarabia.com/rss.xml',
]

BBC_BREAKING_FEED = 'https://feeds.bbci.co.uk/arabic/rss.xml'
sent_bbc_titles = set()


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
            response.raise_for_status()
            root = ET.fromstring(response.content)
            items = root.findall('.//item')
            for item in items[:15]:
                title = clean_text(item.findtext('title', ''))
                desc = clean_text(item.findtext('description', ''))[:200]
                if title and is_important(title, desc) and title not in sent_news_titles:
                    all_news.append({'title': title, 'desc': desc})
        except Exception as ex:
            logger.error("خطا RSS (" + url + "): " + str(ex))
    return all_news


def fetch_bbc_breaking():
    """يجلب اخر اخبار BBC عربي بدون فلترة بالكلمات المفتاحية (اخبار عاجلة عامة)"""
    breaking_news = []
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        response = requests.get(BBC_BREAKING_FEED, headers=headers, timeout=10)
        response.raise_for_status()
        root = ET.fromstring(response.content)
        items = root.findall('.//item')
        for item in items[:10]:
            title = clean_text(item.findtext('title', ''))
            desc = clean_text(item.findtext('description', ''))[:200]
            if title and title not in sent_bbc_titles:
                breaking_news.append({'title': title, 'desc': desc})
    except Exception as ex:
        logger.error("خطا BBC العاجلة: " + str(ex))
    return breaking_news


def fetch_gold_price(retries=2):
    """يرجع السعر او None لو فشلت كل المحاولات"""
    url = "https://www.goldapi.io/api/XAU/USD"
    headers = {'x-access-token': GOLD_API}
    for attempt in range(retries + 1):
        try:
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            price = response.json().get('price', None)
            if price:
                return price
        except Exception as ex:
            logger.error("خطا في جلب سعر الذهب (محاولة " + str(attempt + 1) + "): " + str(ex))
            if attempt < retries:
                time.sleep(2)
    return None


def fetch_currency_rates():
    try:
        url = "https://api.exchangerate-api.com/v4/latest/USD"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.json().get('rates', {})
    except Exception as ex:
        logger.error("خطا في جلب اسعار العملات: " + str(ex))
        return {}


def fetch_calendar():
    try:
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code != 200:
            return []
        data = response.json()
    except Exception as ex:
        logger.error("خطا في جلب التقويم: " + str(ex))
        return []

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
        except Exception:
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
            return float(str(v).replace('%', '').replace('K', '').replace('M', '').strip())
        a = parse(actual)
        f = parse(forecast)
        if a > f:
            return 'ايجابي', '📈'
        elif a < f:
            return 'سلبي', '📉'
        else:
            return 'محايد', '➡️'
    except Exception:
        return '', ''


def get_trade_recommendation(actual, forecast, country):
    try:
        sentiment, _icon = get_sentiment(actual, forecast)
        if not sentiment:
            return ''
        currency = COUNTRY_NAME.get(country, country)
        flag = COUNTRY_FLAG.get(country, '')
        msg = "\n💡 *توصية التداول:*\n"
        if sentiment == 'ايجابي':
            msg += "📈 الخبر ايجابي لـ " + flag + " " + currency + "\n"
            msg += "✅ فكر في: شراء " + currency
        elif sentiment == 'سلبي':
            msg += "📉 الخبر سلبي لـ " + flag + " " + currency + "\n"
            msg += "✅ فكر في: بيع " + currency
        else:
            return ''
        msg += "\n\n⚠️ *تنبيه: هذه اقتراحات وليست نصائح مالية*"
        return msg
    except Exception:
        return ''


def monitor_gold():
    global last_gold_price
    while True:
        try:
            price = fetch_gold_price()
            if price is None:
                logger.warning("تخطي تحديث الذهب - فشل جلب السعر")
            else:
                msg = "🥇 *تحديث سعر الذهب*\n━━━━━━━━━━━━━━━━━\n\n"
                msg += "💰 السعر الحالي: *$" + f"{price:,.2f}" + "*\n"

                with data_lock:
                    previous_price = last_gold_price

                if previous_price:
                    diff = price - previous_price
                    pct = (diff / previous_price) * 100
                    if diff > 0:
                        msg += "📈 +$" + f"{diff:,.2f} (+{pct:.2f}%)\n🟢 صعودي"
                    elif diff < 0:
                        msg += "📉 $" + f"{diff:,.2f} ({pct:.2f}%)\n🔴 هبوطي"
                    else:
                        msg += "➡️ لا يوجد تغيير"

                with data_lock:
                    last_gold_price = price

                bot.send_message(CHAT_ID, msg, parse_mode="Markdown")

                with data_lock:
                    chat_ids = list(price_alerts.keys())

                for cid in chat_ids:
                    with data_lock:
                        target = price_alerts.get(cid, {}).get('gold')
                    if target is not None and previous_price is not None:
                        crossed_up = previous_price < target <= price
                        crossed_down = previous_price > target >= price
                        if crossed_up or crossed_down:
                            bot.send_message(
                                cid,
                                "🔔 *تنبيه الذهب!*\nوصل السعر الى: $" + f"{price:,.2f}",
                                parse_mode="Markdown"
                            )
                            with data_lock:
                                price_alerts[cid].pop('gold', None)
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
                with data_lock:
                    sent_news_titles.add(a['title'])
                time.sleep(2)
        except Exception as ex:
            logger.error("خطا الاخبار: " + str(ex))
        time.sleep(900)


def monitor_bbc_breaking():
    while True:
        try:
            articles = fetch_bbc_breaking()
            for a in articles[:5]:
                msg = "🌍 *خبر عاجل - BBC عربي*\n━━━━━━━━━━━━━━━━━\n\n"
                msg += "📌 *" + a['title'] + "*\n"
                if a['desc'] and a['desc'] != a['title']:
                    msg += "📝 " + a['desc'] + "\n"
                bot.send_message(CHAT_ID, msg, parse_mode="Markdown")
                with data_lock:
                    sent_bbc_titles.add(a['title'])
                time.sleep(2)
        except Exception as ex:
            logger.error("خطا مراقبة BBC العاجلة: " + str(ex))
        time.sleep(600)


def send_weekly_stats():
    while True:
        try:
            now = now_utc()
            if now.weekday() == 4 and now.hour == 20:
                with data_lock:
                    events_snapshot = list(weekly_events)
                if events_snapshot:
                    positive = sum(1 for e in events_snapshot if 'ايجابي' in e.get('sentiment', ''))
                    negative = sum(1 for e in events_snapshot if 'سلبي' in e.get('sentiment', ''))
                    msg = "📊 *تقرير الاسبوع*\n━━━━━━━━━━━━━━━━━\n\n"
                    msg += "📈 ايجابي: " + str(positive) + "\n📉 سلبي: " + str(negative)
                    bot.send_message(CHAT_ID, msg, parse_mode="Markdown")
                    with data_lock:
                        weekly_events.clear()
                time.sleep(3600)
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
                with data_lock:
                    already_pre = pre_id in announced_pre
                if 13 <= diff_minutes <= 17 and not already_pre:
                    flag = COUNTRY_FLAG.get(e['country'], '')
                    name = COUNTRY_NAME.get(e['country'], e['country'])
                    msg = "⏰ *خبر بعد 15 دقيقة*\n━━━━━━━━━━━━━━━━━\n\n"
                    msg += flag + " *" + name + "*\n📌 " + e['event']
                    bot.send_message(CHAT_ID, msg, parse_mode="Markdown")
                    with data_lock:
                        announced_pre.add(pre_id)

                actual_id = "actual_" + e['id']
                with data_lock:
                    already_actual = actual_id in sent_actual_ids
                if e['actual'] and not already_actual:
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
                    with data_lock:
                        sent_actual_ids.add(actual_id)
                        e['sentiment'] = sentiment_text
                        weekly_events.append(e)

                upcoming_id = "upcoming_" + e['id']
                already_upcoming = upcoming_id in upcoming_sent
                if not e['actual'] and not already_upcoming and -60 <= diff_minutes <= 2880:
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
        types.InlineKeyboardButton("📡 حالة البوت", callback_data="status"),
        types.InlineKeyboardButton("🌍 اخبار BBC العاجلة", callback_data="bbc_news")
    )
    bot.send_message(chat_id, "🤖 القائمة الرئيسية - اختر ما تريد:", reply_markup=markup)


@bot.message_handler(commands=['start'])
def start(m):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("📋 القائمة"))
    bot.send_message(
        m.chat.id,
        "👋 اهلا بك في بوت الاسواق المالية!\nاضغط على القائمة للبدء\n\nتم انشاء هذا البوت بواسطة د/عاصم النجار",
        reply_markup=markup
    )


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
            if price <= 0:
                raise ValueError("سعر غير صحيح")
            with data_lock:
                price_alerts.setdefault(chat_id, {})['gold'] = price
                del user_states[chat_id]
            bot.send_message(chat_id, "✅ تم التفعيل! سيتم اشعارك عند وصول الذهب الى $" + f"{price:,.2f}")
        except Exception:
            bot.send_message(chat_id, "❌ ارسل رقم صحيح مثل: 4100")


@bot.callback_query_handler(func=lambda c: True)
def handle_callback(c):
    logger.info("CALLBACK: " + str(c.data) + " from " + str(c.message.chat.id))
    chat_id = c.message.chat.id
    data = c.data

    try:
        if data == "gold":
            price = fetch_gold_price()
            if price is None:
                bot.send_message(chat_id, "❌ تعذر جلب سعر الذهب حاليًا، حاول لاحقًا")
            else:
                msg = "🥇 سعر الذهب: $" + f"{price:,.2f}"
                bot.send_message(chat_id, msg)

        elif data == "currencies":
            rates = fetch_currency_rates()
            if rates:
                msg = "💱 اسعار العملات مقابل الدولار\n\n"
                inverted_pairs = [('EUR', '🇪🇺'), ('GBP', '🇬🇧'), ('JPY', '🇯🇵'), ('CAD', '🇨🇦'), ('AUD', '🇦🇺')]
                for code, flag in inverted_pairs:
                    rate = rates.get(code)
                    if rate:
                        usd_per = 1 / rate
                        msg += flag + " " + code + " = " + f"{usd_per:.4f}" + " USD\n"

                msg += "\n"
                direct_pairs = [('SAR', '🇸🇦'), ('EGP', '🇪🇬')]
                for code, flag in direct_pairs:
                    rate = rates.get(code)
                    if rate:
                        name = COUNTRY_NAME.get(code, code)
                        msg += flag + " 1$ = " + f"{rate:.2f}" + " " + name + "\n"
                bot.send_message(chat_id, msg)
            else:
                bot.send_message(chat_id, "❌ خطا في جلب الاسعار، حاول لاحقًا")

        elif data == "bbc_news":
            articles = fetch_bbc_breaking()
            if articles:
                msg = "🌍 اخر الاخبار العاجلة - BBC عربي\n\n"
                for a in articles[:5]:
                    msg += "📌 " + a['title'] + "\n\n"
                bot.send_message(chat_id, msg)
            else:
                bot.send_message(chat_id, "لا توجد اخبار جديدة الان")

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
            with data_lock:
                user_states[chat_id] = 'waiting_gold_alert'
            bot.send_message(chat_id, "🥇 ارسل السعر المطلوب للذهب بالدولار، مثال: 4100")

        elif data == "status":
            with data_lock:
                events_count = len(sent_actual_ids)
                news_count = len(sent_news_titles)
            msg = "✅ البوت يعمل\n📌 احداث: " + str(events_count) + "\n📰 اخبار: " + str(news_count)
            bot.send_message(chat_id, msg)

        bot.answer_callback_query(c.id)
    except Exception as ex:
        logger.error("خطا في الكولباك: " + str(ex))
        try:
            bot.answer_callback_query(c.id, text="حدث خطأ")
        except Exception:
            pass


if __name__ == "__main__":
    logger.info("البوت بدا...")
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=check_calendar, daemon=True).start()
    threading.Thread(target=monitor_gold, daemon=True).start()
    threading.Thread(target=monitor_news, daemon=True).start()
    threading.Thread(target=monitor_bbc_breaking, daemon=True).start()
    threading.Thread(target=send_weekly_stats, daemon=True).start()
    bot.remove_webhook()
    time.sleep(1)
    bot.infinity_polling(skip_pending=True)
