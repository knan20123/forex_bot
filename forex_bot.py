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
last_news_hour = ""
last_gold_price = None
weekly_events = []
user_currencies = {'USD', 'EUR', 'GBP', 'JPY', 'CAD', 'AUD', 'NZD', 'CHF'}

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
    'فيدرالي', 'فائدة', 'تضخم', 'بطالة', 'ناتج', 'اقتصاد', 'نمو',
    'بيتكوين', 'عملات رقمية', 'كريبتو', 'بلوكشين', 'ايثيريوم',
    'نفط', 'ذهب', 'برميل', 'اوبك', 'خام',
    'حرب', 'عقوبات', 'صراع', 'توتر', 'ازمة',
    'دولار', 'يورو', 'عملة', 'صرف',
    'بورصة', 'اسهم', 'سوق', 'استثمار', 'مؤشر',
    'بنك', 'مركزي', 'سياسة نقدية', 'احتياطي',
    'ديون', 'ميزانية', 'عجز', 'تجارة',
    'صندوق النقد', 'البنك الدولي', 'ناسداك', 'وول ستريت'
]

RSS_FEEDS = [
    ('الجزيرة اقتصاد', 'https://www.aljazeera.net/rss/economy.xml'),
    ('العربية اقتصاد', 'https://www.alarabiya.net/rss/asequence/economy'),
    ('رويترز عربي', 'https://feeds.reuters.com/reuters/MENBusinessNews'),
    ('CNBC عربية', 'https://www.cnbcarabia.com/rss'),
]

@app.route('/')
def home():
    return "البوت يعمل!"

def run_flask():
    app.run(host='0.0.0.0', port=9090)

def format_time(raw):
    try:
        dt = datetime.fromisoformat(raw)
        return dt.strftime('%Y-%m-%d %H:%M')
    except:
        return raw

def parse_event_time(raw):
    try:
        return datetime.fromisoformat(raw)
    except:
        return None

def get_sentiment(actual, forecast):
    try:
        def parse(v):
            return float(str(v).replace('%','').replace('K','').replace('M','').strip())
        a = parse(actual)
        f = parse(forecast)
        if a > f:
            return 'ايجابي - صعودي للعملة'
        elif a < f:
            return 'سلبي - هبوطي للعملة'
        else:
            return 'محايد'
    except:
        return ''

def is_important(title, desc=''):
    text = (title + ' ' + desc).lower()
    return any(kw in text for kw in KEYWORDS)

def fetch_rss_news():
    all_news = []
    headers = {'User-Agent': 'Mozilla/5.0'}
    for source_name, url in RSS_FEEDS:
        try:
            response = requests.get(url, headers=headers, timeout=10)
            root = ET.fromstring(response.content)
            items = root.findall('.//item')
            for item in items[:10]:
                title = item.findtext('title', '').strip()
                desc = item.findtext('description', '').strip()
                desc = re.sub('<[^<]+?>', '', desc)[:150]
                if title and is_important(title, desc):
                    all_news.append({
                        'title': title,
                        'desc': desc,
                        'source': source_name
                    })
        except Exception as ex:
            logger.error("خطا RSS " + source_name + ": " + str(ex))
    return all_news

def fetch_gold_price():
    url = "https://www.goldapi.io/api/XAU/USD"
    headers = {'x-access-token': GOLD_API}
    response = requests.get(url, headers=headers, timeout=15)
    return response.json().get('price', None)

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
        event_dt = parse_event_time(raw_date)
        if not event_dt:
            continue
        eid = raw_date + "_" + country + "_" + item.get('title', '')
        events.append({
            'id': eid,
            'dt': event_dt,
            'time': format_time(raw_date),
            'country': country,
            'event': item.get('title', ''),
            'forecast': item.get('forecast') or '--',
            'previous': item.get('previous') or '--',
            'actual': item.get('actual') or '',
        })
    return events

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
                        msg += "📈 التغيير: +$" + f"{diff:,.2f} (+{pct:.2f}%)" + "\n"
                        msg += "🟢 الاتجاه: صعودي"
                    elif diff < 0:
                        msg += "📉 التغيير: $" + f"{diff:,.2f} ({pct:.2f}%)" + "\n"
                        msg += "🔴 الاتجاه: هبوطي"
                    else:
                        msg += "لا يوجد تغيير"
                last_gold_price = price
                bot.send_message(CHAT_ID, msg, parse_mode="Markdown")
        except Exception as ex:
            logger.error("خطا الذهب: " + str(ex))
        time.sleep(7200)

def send_daily_news():
    global last_news_hour
    while True:
        try:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            hour_key = now.strftime('%Y-%m-%d') + str(now.hour)
            if now.hour in [8, 16] and hour_key != last_news_hour:
                articles = fetch_rss_news()
                new_articles = [a for a in articles if a['title'] not in sent_news_titles]
                if new_articles:
                    msg = "📰 *اخبار اقتصادية مهمة*\n"
                    msg += "━━━━━━━━━━━━━━━━━\n\n"
                    for a in new_articles[:5]:
                        msg += "📌 *" + a['title'] + "*\n"
                        if a['desc']:
                            msg += "📝 " + a['desc'] + "\n"
                        msg += "📡 " + a['source'] + "\n\n"
                        sent_news_titles.add(a['title'])
                    bot.send_message(CHAT_ID, msg, parse_mode="Markdown")
                    last_news_hour = hour_key
                    logger.info("تم ارسال الاخبار")
        except Exception as ex:
            logger.error("خطا الاخبار: " + str(ex))
        time.sleep(1800)

def send_weekly_stats():
    while True:
        try:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
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
                    msg += "*ابرز احداث الاسبوع:*\n"
                    msg += "─────────────────\n"
                    for e in weekly_events[-10:]:
                        flag = COUNTRY_FLAG.get(e['country'], '')
                        name = COUNTRY_NAME.get(e['country'], e['country'])
                        msg += flag + " " + name + " - " + e['event'] + "\n"
                        msg += "✅ الفعلي: " + e['actual'] + " | 🎯 التوقع: " + e['forecast'] + "\n"
                        msg += e.get('sentiment','') + "\n\n"
                    bot.send_message(CHAT_ID, msg, parse_mode="Markdown")
                    weekly_events.clear()
        except Exception as ex:
            logger.error("خطا الاحصائيات: " + str(ex))
        time.sleep(3600)

def check_calendar():
    upcoming_sent = {}
    while True:
        try:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            events = fetch_calendar()
            for e in events:
                event_dt = e['dt']
                diff_minutes = (event_dt.replace(tzinfo=None) - now.replace(tzinfo=None)).total_seconds() / 60

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
                    sentiment = get_sentiment(e['actual'], e['forecast'])
                    icon = "📈" if "ايجابي" in sentiment else "📉" if "سلبي" in sentiment else "➡️"
                    msg = "🚨 *صدر الخبر الان!*\n━━━━━━━━━━━━━━━━━\n\n"
                    msg += "🕐 `" + e['time'] + "`\n"
                    msg += flag + " *" + name + "*\n"
                    msg += "📌 " + e['event'] + "\n"
                    msg += "⚡ التاثير: 🔴 عالي\n"
                    msg += "🎯 التوقع: `" + e['forecast'] + "`\n"
                    msg += "📉 السابق: `" + e['previous'] + "`\n"
                    msg += "✅ الفعلي: `" + e['actual'] + "`\n"
                    if sentiment:
                        msg += icon + " التحليل: " + sentiment
                    bot.send_message(CHAT_ID, msg, parse_mode="Markdown")
                    sent_actual_ids.add(actual_id)
                    e['sentiment'] = sentiment
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
        types.InlineKeyboardButton("📅 احداث اليوم", callback_data="today"),
        types.InlineKeyboardButton("📰 اخر الاخبار", callback_data="news"),
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
        "• سعر الذهب كل ساعتين\n"
        "• اخبار اقتصادية مهمة مرتين يوميا\n\n"
        "اضغط على 📋 *القائمة* للبدء\n\n"
        "👨 *تم انشاء هذا البوت بواسطة*\n"
        "د/عاصم النجار"
    )
    bot.send_message(m.chat.id, welcome, parse_mode="Markdown", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "📋 القائمة")
def show_menu(m):
    send_main_menu(m.chat.id)

@bot.callback_query_handler(func=lambda c: True)
def handle_callback(c):
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
                    msg += "📈 التغيير: +$" + f"{diff:,.2f} (+{pct:.2f}%)" + "\n🟢 صعودي"
                elif diff < 0:
                    msg += "📉 التغيير: $" + f"{diff:,.2f} ({pct:.2f}%)" + "\n🔴 هبوطي"
                else:
                    msg += "لا يوجد تغيير"
            bot.send_message(chat_id, msg, parse_mode="Markdown")
        except:
            bot.send_message(chat_id, "خطا في جلب سعر الذهب.")

    elif data == "today":
        try:
            events = fetch_calendar()
            today = datetime.now(timezone.utc).replace(tzinfo=None).strftime('%Y-%m-%d')
            today_events = [e for e in events if e['time'].startswith(today)]
            if today_events:
                msg = "📅 *احداث اليوم الاقتصادية* 🔴\n━━━━━━━━━━━━━━━━━\n\n"
                for e in today_events:
                    flag = COUNTRY_FLAG.get(e['country'], '')
                    name = COUNTRY_NAME.get(e['country'], e['country'])
                    msg += "🕐 `" + e['time'] + "`\n"
                    msg += flag + " *" + name + "*\n"
                    msg += "📌 " + e['event'] + "\n"
                    msg += "🎯 التوقع: `" + e['forecast'] + "`\n\n"
                bot.send_message(chat_id, msg, parse_mode="Markdown")
            else:
                bot.send_message(chat_id, "لا توجد احداث عالية التاثير اليوم.")
        except Exception as ex:
            bot.send_message(chat_id, "خطا: " + str(ex))

    elif data == "news":
        try:
            articles = fetch_rss_news()
            if articles:
                msg = "📰 *اخر الاخبار الاقتصادية*\n━━━━━━━━━━━━━━━━━\n\n"
                for a in articles[:5]:
                    msg += "📌 *" + a['title'] + "*\n"
                    if a['desc']:
                        msg += "📝 " + a['desc'] + "\n"
                    msg += "📡 " + a['source'] + "\n\n"
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
        markup.add(types.InlineKeyboardButton("💾 حفظ الاعدادات", callback_data="save_filter"))
        bot.send_message(chat_id, "⚙️ *فلتر العملات*\nاضغط لتفعيل او ايقاف العملة:",
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
        markup.add(types.InlineKeyboardButton("💾 حفظ الاعدادات", callback_data="save_filter"))
        bot.edit_message_reply_markup(chat_id, c.message.message_id, reply_markup=markup)

    elif data == "save_filter":
        names = [COUNTRY_NAME.get(cu, cu) for cu in sorted(user_currencies)]
        msg = "✅ *تم الحفظ!*\nالعملات المفعلة:\n" + "\n".join("• " + n for n in names)
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
        msg = (
            "✅ *البوت يعمل بشكل طبيعي*\n"
            "━━━━━━━━━━━━━━━━━\n"
            "📌 احداث متابعة: " + str(len(sent_actual_ids)) + "\n"
            "💱 عملات مفعلة: " + str(len(user_currencies)) +
            gold_txt
        )
        bot.send_message(chat_id, msg, parse_mode="Markdown")

    bot.answer_callback_query(c.id)

if __name__ == "__main__":
    logger.info("البوت بدا...")
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=check_calendar, daemon=True).start()
    threading.Thread(target=monitor_gold, daemon=True).start()
    threading.Thread(target=send_daily_news, daemon=True).start()
    threading.Thread(target=send_weekly_stats, daemon=True).start()
    bot.infinity_polling()
# update Mon Jun 29 08:16:53 +03 2026
