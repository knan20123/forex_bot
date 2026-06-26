import telebot
from telebot import types
import requests
import time
import threading
import logging
from datetime import datetime
from flask import Flask

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = "8687483288:AAHZnOZs396LLTgPaNHnH_KQGhDCCBoxOzM"
CHAT_ID = "1718292210"
NEWS_API = "2d204ac646d8474aa51ba3804ff4cc62"
GOLD_API = "goldapi-c4085f23c4a16779d6d8d8bb3eaf9550-io"

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

sent_event_ids = set()
sent_news_titles = set()
last_news_date = ""
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
        eid = f"{item.get('date')}_{country}_{item.get('title')}"
        events.append({
            'id': eid,
            'time': format_time(item.get('date', '')),
            'country': country,
            'event': item.get('title', ''),
            'forecast': item.get('forecast') or '--',
            'previous': item.get('previous') or '--',
            'actual': item.get('actual') or '',
        })
    return events

def fetch_arabic_news():
    url = (
        "https://newsapi.org/v2/everything?"
        "q=gold+OR+forex+OR+markets&"
        "language=ar&sortBy=publishedAt&"
        "pageSize=5&apiKey=" + NEWS_API
    )
    response = requests.get(url, timeout=15)
    articles = response.json().get('articles', [])
    if not articles:
        url2 = (
            "https://newsapi.org/v2/top-headlines?"
            "category=business&language=ar&"
            "pageSize=5&apiKey=" + NEWS_API
        )
        response2 = requests.get(url2, timeout=15)
        articles = response2.json().get('articles', [])
    return articles

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
            logger.error(f"خطا الذهب: {ex}")
        time.sleep(7200)

def send_daily_news():
    global last_news_date
    while True:
        try:
            now = datetime.utcnow()
            today = now.strftime('%Y-%m-%d')
            if now.hour == 8 and today != last_news_date:
                articles = fetch_arabic_news()
                if articles:
                    msg = "📰 *اخبار السوق والذهب اليومية*\n"
                    msg += "━━━━━━━━━━━━━━━━━\n\n"
                    for a in articles:
                        title = a.get('title', '')
                        source = a.get('source', {}).get('name', '')
                        desc = a.get('description', '')
                        if title and title not in sent_news_titles:
                            msg += "📌 *" + title + "*\n"
                            if desc:
                                msg += "📝 " + desc[:100] + "...\n"
                            msg += "📡 المصدر: " + source + "\n\n"
                            sent_news_titles.add(title)
                    bot.send_message(CHAT_ID, msg, parse_mode="Markdown")
                    last_news_date = today
        except Exception as ex:
            logger.error(f"خطا الاخبار: {ex}")
        time.sleep(1800)

def send_weekly_stats():
    while True:
        try:
            now = datetime.utcnow()
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
            logger.error(f"خطا الاحصائيات: {ex}")
        time.sleep(3600)

def check_calendar():
    announced_pre = set()
    while True:
        try:
            events = fetch_calendar()
            for e in events:
                try:
                    dt = datetime.fromisoformat(e['time'].replace(' ', 'T') + ':00')
                    diff = (dt - datetime.utcnow()).total_seconds() / 60
                    pre_id = "pre_" + e['id']
                    if 14 <= diff <= 16 and pre_id not in announced_pre:
                        flag = COUNTRY_FLAG.get(e['country'], '')
                        name = COUNTRY_NAME.get(e['country'], e['country'])
                        msg = "⏰ *تنبيه! خبر بعد 15 دقيقة*\n━━━━━━━━━━━━━━━━━\n\n"
                        msg += flag + " *" + name + "* - " + e['event'] + "\n"
                        msg += "🎯 التوقع: `" + e['forecast'] + "`\n"
                        msg += "📉 السابق: `" + e['previous'] + "`"
                        bot.send_message(CHAT_ID, msg, parse_mode="Markdown")
                        announced_pre.add(pre_id)
                except:
                    pass

                actual_id = "actual_" + e['id']
                if e['actual'] and actual_id not in sent_event_ids:
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
                    sent_event_ids.add(actual_id)
                    e['sentiment'] = sentiment
                    weekly_events.append(e)

                if e['id'] not in sent_event_ids and not e['actual']:
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
                    sent_event_ids.add(e['id'])
        except Exception as ex:
            logger.error(f"خطا التقويم: {ex}")
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
        "• اخبار السوق اليومية\n\n"
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
            today = datetime.utcnow().strftime('%Y-%m-%d')
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
            articles = fetch_arabic_news()
            if articles:
                msg = "📰 *اخر اخبار السوق*\n━━━━━━━━━━━━━━━━━\n\n"
                for a in articles[:5]:
                    title = a.get('title', '')
                    source = a.get('source', {}).get('name', '')
                    desc = a.get('description', '')
                    msg += "📌 *" + title + "*\n"
                    if desc:
                        msg += "📝 " + desc[:100] + "...\n"
                    msg += "📡 " + source + "\n\n"
                bot.send_message(chat_id, msg, parse_mode="Markdown")
            else:
                bot.send_message(chat_id, "لا توجد اخبار متاحة الان.")
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
            "📌 احداث متابعة: " + str(len(sent_event_ids)) + "\n"
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
