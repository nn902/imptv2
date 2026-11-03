import os
import json
import time
import ssl
import html
import re
import logging
import telebot
from telebot import types
from datetime import datetime

# ==============================================================================
# 🎨 БЕЗОПАСНАЯ ПОДДЕРЖКА ПАРАМЕТРА STYLE В INLINEKEYBOARDBUTTON
# ==============================================================================
_orig_inline_btn_init = types.InlineKeyboardButton.__init__

def _safe_inline_btn_init(self, *args, **kwargs):
    style = kwargs.pop("style", None)
    try:
        _orig_inline_btn_init(self, *args, **kwargs)
    except TypeError:
        _orig_inline_btn_init(self, *args)
    if style is not None:
        self.style = style

types.InlineKeyboardButton.__init__ = _safe_inline_btn_init

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("GMPBot")

TOKEN = os.getenv("BOT_TOKEN", "")
bot = telebot.TeleBot(TOKEN)

ADMIN_IDS = [6522383487, 7727345054]

user_states = {}
admin_broadcast_state = {}
admin_rate_state = {}

# ==============================================================================
# 🗄 ОПЕРАТИВНАЯ БАЗА ДАННЫХ (RAM + JSON)
# ==============================================================================
DATA_FILE = "bot_data.json"

DATA = {
    "users": {},
    "orders": {},
    "reviews": [],
    "next_order_id": 113,
    "next_review_id": 1,
    "rates": [
        {"min": 50,   "max": 499,   "rate": 0.37},
        {"min": 500,  "max": 999,   "rate": 0.36},
        {"min": 1000, "max": 50000, "rate": 0.35},
    ],
}


def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            DATA["users"] = loaded.get("users", {})
            DATA["orders"] = loaded.get("orders", {})
            DATA["reviews"] = loaded.get("reviews", [])
            DATA["next_order_id"] = loaded.get("next_order_id", 113)
            DATA["next_review_id"] = loaded.get("next_review_id", 1)
            if loaded.get("rates"):
                DATA["rates"] = loaded["rates"]
            logger.info(f"✅ Данные загружены: {len(DATA['users'])} польз., {len(DATA['orders'])} заказов")
        except Exception as e:
            logger.error(f"Ошибка загрузки данных: {e}")
    else:
        logger.info("📁 Файл данных не найден — стартуем с чистой базы")


def save_data():
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(DATA, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Ошибка сохранения данных: {e}")


def get_rate_for_amount(rub: float) -> float:
    for tier in DATA["rates"]:
        if tier["min"] <= rub <= tier["max"]:
            return tier["rate"]
    if rub < DATA["rates"][0]["min"]:
        return DATA["rates"][0]["rate"]
    return DATA["rates"][-1]["rate"]


# ==============================================================================
# 👤 ПОЛЬЗОВАТЕЛИ
# ==============================================================================

def upsert_user(user_id: int, first_name: str, username: str, referrer_id: int = None):
    now = time.time()
    key = str(user_id)
    user = DATA["users"].get(key)
    is_new = False
    ref_extended = False

    if user is None:
        is_new = True
        valid_referrer = None
        if referrer_id and referrer_id != user_id:
            ref_user = DATA["users"].get(str(referrer_id))
            if ref_user:
                valid_referrer = referrer_id
                cur_until = ref_user.get("ref_bonus_until") or 0
                base = cur_until if cur_until > now else now
                ref_user["ref_bonus_until"] = base + 24 * 3600
                save_data()
                ref_extended = True

        user = {
            "user_id": user_id,
            "first_name": first_name,
            "username": username,
            "total_orders": 0,
            "total_gmp_received": 0,
            "total_rub_spent": 0.0,
            "referrer_id": valid_referrer,
            "ref_bonus_until": None,
            "welcome_bonus_until": now + 24 * 3600,
            "welcome_bonus_used": False,
            "created_at": now,
        }
        DATA["users"][key] = user
        save_data()
    else:
        if (
            referrer_id
            and referrer_id != user_id
            and not user.get("referrer_id")
            and (user.get("total_orders") or 0) == 0
        ):
            ref_user = DATA["users"].get(str(referrer_id))
            if ref_user:
                user["referrer_id"] = referrer_id
                user["welcome_bonus_until"] = now + 24 * 3600
                user["welcome_bonus_used"] = False
                cur_until = ref_user.get("ref_bonus_until") or 0
                base = cur_until if cur_until > now else now
                ref_user["ref_bonus_until"] = base + 24 * 3600
                ref_extended = True

        user["first_name"] = first_name
        user["username"] = username
        save_data()

    return is_new, ref_extended


def get_user_bonuses(user_id: int) -> dict:
    user = DATA["users"].get(str(user_id))
    now = time.time()
    if not user:
        return {
            "welcome_5": False, "welcome_seconds_left": 0, "welcome_time_str": "00:00:00",
            "ref_2": False, "ref_seconds_left": 0, "ref_time_str": "00:00:00",
            "refs_count": 0, "has_referrer": False,
        }
    refs_count = sum(1 for u in DATA["users"].values() if u.get("referrer_id") == user_id)
    welcome_sec = max(0, int((user.get("welcome_bonus_until") or 0) - now))
    ref_sec = max(0, int((user.get("ref_bonus_until") or 0) - now))

    welcome_5 = False
    welcome_time_str = "00:00:00"
    if not user.get("welcome_bonus_used") and (user.get("total_orders") or 0) == 0 and welcome_sec > 0:
        welcome_5 = True
        welcome_time_str = f"{welcome_sec // 3600:02d}:{(welcome_sec % 3600) // 60:02d}:{welcome_sec % 60:02d}"

    ref_2 = False
    ref_time_str = "00:00:00"
    if ref_sec > 0:
        ref_2 = True
        ref_time_str = f"{ref_sec // 3600:02d}:{(ref_sec % 3600) // 60:02d}:{ref_sec % 60:02d}"

    return {
        "welcome_5": welcome_5,
        "welcome_seconds_left": welcome_sec,
        "welcome_time_str": welcome_time_str,
        "ref_2": ref_2,
        "ref_seconds_left": ref_sec,
        "ref_time_str": ref_time_str,
        "refs_count": refs_count,
        "has_referrer": bool(user.get("referrer_id")),
    }


def get_user_stats(user_id: int) -> dict:
    user = DATA["users"].get(str(user_id))
    if not user:
        return {"total_orders": 0, "total_gmp": 0, "total_rub": 0.0}
    return {
        "total_orders": user.get("total_orders") or 0,
        "total_gmp": user.get("total_gmp_received") or 0,
        "total_rub": user.get("total_rub_spent") or 0.0,
    }


# ==============================================================================
# 📦 ЗАКАЗЫ
# ==============================================================================

def create_pending_order(user_id: int, rub: float, gmp: int, file_id: str = None, bonus_desc: str = None) -> int:
    order_id = DATA["next_order_id"]
    DATA["next_order_id"] += 1
    DATA["orders"][str(order_id)] = {
        "order_id": order_id,
        "user_id": user_id,
        "rub_amount": rub,
        "gmp_amount": gmp,
        "status": "pending",
        "file_id": file_id,
        "bonus_desc": bonus_desc,
        "created_at": time.time(),
    }
    save_data()
    logger.info(f"Заявка #{order_id} создана (pending): user={user_id}, rub={rub}, gmp={gmp}")
    return order_id


def approve_order(order_id: int) -> dict:
    order = DATA["orders"].get(str(order_id))
    if not order:
        return {"success": False, "reason": "Заказ не найден"}
    if order["status"] != "pending":
        return {"success": False, "reason": f"Заявка уже обработана (статус: {order['status']})"}

    order["status"] = "approved"
    user = DATA["users"].get(str(order["user_id"]))
    if user:
        user["total_orders"] = (user.get("total_orders") or 0) + 1
        user["total_gmp_received"] = (user.get("total_gmp_received") or 0) + order["gmp_amount"]
        user["total_rub_spent"] = (user.get("total_rub_spent") or 0.0) + order["rub_amount"]
        user["welcome_bonus_used"] = True
    save_data()

    return {
        "success": True,
        "user_id": order["user_id"],
        "rub": order["rub_amount"],
        "gmp": order["gmp_amount"],
        "bonus_desc": order.get("bonus_desc"),
    }


def reject_order(order_id: int) -> dict:
    order = DATA["orders"].get(str(order_id))
    if not order:
        return {"success": False, "reason": "Заказ не найден"}
    if order["status"] != "pending":
        return {"success": False, "reason": f"Заявка уже обработана (статус: {order['status']})"}
    order["status"] = "rejected"
    save_data()
    return {
        "success": True,
        "user_id": order["user_id"],
        "rub": order["rub_amount"],
        "gmp": order["gmp_amount"],
    }


def get_user_orders_history(user_id: int, limit: int = 5) -> list:
    user_orders = [o for o in DATA["orders"].values() if o["user_id"] == user_id]
    user_orders.sort(key=lambda o: o["order_id"], reverse=True)
    result = []
    for o in user_orders[:limit]:
        dt = datetime.fromtimestamp(o["created_at"]).strftime("%d.%m.%Y %H:%M")
        result.append((o["order_id"], o["rub_amount"], o["gmp_amount"], o["status"], dt))
    return result


def create_review(order_id, user_id, rub_amount, gmp_amount):
    review_id = DATA["next_review_id"]
    DATA["next_review_id"] += 1
    DATA["reviews"].append({
        "review_id": review_id,
        "order_id": order_id,
        "user_id": user_id,
        "rub_amount": rub_amount,
        "gmp_amount": gmp_amount,
        "created_at": time.time(),
    })
    save_data()
    return review_id


# ==============================================================================
# 📊 СТАТИСТИКА ДЛЯ АДМИНКИ
# ==============================================================================

def get_admin_detailed_stats() -> dict:
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    total_users = len(DATA["users"])
    today_users = sum(1 for u in DATA["users"].values() if (u.get("created_at") or 0) >= today_start)
    approved = [o for o in DATA["orders"].values() if o["status"] == "approved"]
    approved_orders = len(approved)
    total_gmp = sum(o["gmp_amount"] for o in approved)
    total_rub = sum(o["rub_amount"] for o in approved)
    pending_orders = sum(1 for o in DATA["orders"].values() if o["status"] == "pending")
    total_invited = sum(1 for u in DATA["users"].values() if u.get("referrer_id"))
    return {
        "total_users": total_users,
        "today_users": today_users,
        "approved_orders": approved_orders,
        "total_gmp": total_gmp,
        "total_rub": total_rub,
        "pending_orders": pending_orders,
        "total_invited": total_invited,
    }


def get_admin_referral_stats() -> dict:
    total_invited = sum(1 for u in DATA["users"].values() if u.get("referrer_id"))
    counts = {}
    for u in DATA["users"].values():
        rid = u.get("referrer_id")
        if rid:
            counts[rid] = counts.get(rid, 0) + 1
    top = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:15]
    top_referrers = []
    for uid, count in top:
        u = DATA["users"].get(str(uid), {})
        top_referrers.append((uid, u.get("first_name") or "—", u.get("username") or "Не установлен", count))
    return {"total_invited": total_invited, "top_referrers": top_referrers}


def get_all_user_ids() -> list:
    return [u["user_id"] for u in DATA["users"].values()]


def parse_broadcast_buttons(text: str):
    if not text or text.lower().strip() in ["пропустить", "нет", "-", "без кнопок"]:
        return None
    markup = types.InlineKeyboardMarkup()
    for line in text.strip().split("\n"):
        parts = [p.strip() for p in line.split("|") if p.strip()]
        if len(parts) >= 2:
            row_buttons = []
            for i in range(0, len(parts), 2):
                if i + 1 < len(parts):
                    btn_text, btn_url = parts[i], parts[i + 1]
                    if btn_url.startswith(("http://", "https://", "tg://")):
                        row_buttons.append(types.InlineKeyboardButton(text=btn_text, url=btn_url, style="primary"))
            if row_buttons:
                markup.row(*row_buttons)
    return markup if len(markup.keyboard) > 0 else None


# === ПРЕМИУМ ЭМОДЗИ ===
EMOJI_CARD     = '<tg-emoji emoji-id="5470060791883374114">🪪</tg-emoji>'
EMOJI_WRITE    = '<tg-emoji emoji-id="5375296873982604963">✍️</tg-emoji>'
EMOJI_MONEY    = '<tg-emoji emoji-id="5231449120635370684">💸</tg-emoji>'
EMOJI_CHART_UP = '<tg-emoji emoji-id="5431577498364158238">📈</tg-emoji>'
EMOJI_CHART    = '<tg-emoji emoji-id="5458799228719472718">📊</tg-emoji>'
EMOJI_STAR_BIG = '<tg-emoji emoji-id="5370869711888194012">🌟</tg-emoji>'
EMOJI_INVADER  = '<tg-emoji emoji-id="5805528485371843693">👾</tg-emoji>'
EMOJI_LIGHT    = '<tg-emoji emoji-id="5427168083074628963">⚡️</tg-emoji>'
EMOJI_DIAMOND  = '<tg-emoji emoji-id="5427168083074628963">💎</tg-emoji>'
EMOJI_ROCKET   = '<tg-emoji emoji-id="5805630830147540745">🚀</tg-emoji>'
EMOJI_SPARKLES = '<tg-emoji emoji-id="5190498849440931467">✨</tg-emoji>'
EMOJI_DEV      = '<tg-emoji emoji-id="5373012449597335010">👨‍💻</tg-emoji>'
EMOJI_USER     = '<tg-emoji emoji-id="5841276284155467413">👤</tg-emoji>'
EMOJI_NAME_TAG = '<tg-emoji emoji-id="5334882760735598374">🔤</tg-emoji>'
EMOJI_MEMO     = '<tg-emoji emoji-id="5431499171045581032">📝</tg-emoji>'
EMOJI_GIFT     = '<tg-emoji emoji-id="5203996991054432397">🎁</tg-emoji>'
EMOJI_CHANNEL  = '<tg-emoji emoji-id="5452002597592382164">📣</tg-emoji>'
EMOJI_STAR     = '<tg-emoji emoji-id="5438496463044752972">⭐️</tg-emoji>'
EMOJI_SUNFLOWER= '<tg-emoji emoji-id="5211226911367257670">🌻</tg-emoji>'
EMOJI_MUSHROOM = '<tg-emoji emoji-id="5197546963788126956">🍄</tg-emoji>'
EMOJI_KISS     = '<tg-emoji emoji-id="5253922906378881072">💋</tg-emoji>'
EMOJI_CLIP     = '<tg-emoji emoji-id="5197269100878907942">📋</tg-emoji>'
EMOJI_WARN     = '<tg-emoji emoji-id="5274099962655816924">❗️</tg-emoji>'
EMOJI_PHONE    = '<tg-emoji emoji-id="5407025283456835913">📱</tg-emoji>'
EMOJI_CAMERA   = '<tg-emoji emoji-id="5235837920081887219">📸</tg-emoji>'
EMOJI_CHECK    = '<tg-emoji emoji-id="5206607081334906820">✔️</tg-emoji>'
EMOJI_CHECC    = '<tg-emoji emoji-id="5465198403573012261">👉</tg-emoji>'
EMOJI_CSECK    = '<tg-emoji emoji-id="5454093885888281840">⚠️</tg-emoji>'

START_TEXT = (
    f"{{bonus_block}}"
    f"{EMOJI_STAR} Здравствуй, {{name}}! {EMOJI_STAR}\n\n"
    f"{EMOJI_INVADER} Ты попал в бота-продавца GMP.\n"
    f"{EMOJI_LIGHT} Здесь ты можешь быстро приобрести GMP по выгодному курсу!\n\n"
    f"{EMOJI_DIAMOND} Наши преимущества:\n"
    f"• {EMOJI_ROCKET} Быстрая выдача\n"
    f"• {EMOJI_CARD} Удобные способы оплаты\n\n"
    f"{EMOJI_SPARKLES} Выбери что тебя интересует:"
)


# === КЛАВИАТУРЫ ===

def main_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(types.InlineKeyboardButton("💸 Купить GMP", callback_data="buy_gmp", style="success"))
    markup.add(types.InlineKeyboardButton("👥 Реферальная система (+2%)", callback_data="open_referral", style="primary"))
    markup.add(
        types.InlineKeyboardButton("📢 Канал", callback_data="open_channel", style="primary"),
        types.InlineKeyboardButton("🆘 Поддержка", callback_data="open_support", style="primary"),
    )
    markup.add(
        types.InlineKeyboardButton("⭐ Отзывы", callback_data="open_reviews", style="primary"),
        types.InlineKeyboardButton("👤 Мой профиль", callback_data="open_profile", style="primary"),
    )
    markup.add(types.InlineKeyboardButton("🌻 Информация о выводе", callback_data="info_withdraw", style="danger"))
    markup.add(types.InlineKeyboardButton("🔐 Политика конфиденциальности", url="https://telegra.ph/Politika-konfidencialnosti-06-21-31"))
    markup.add(types.InlineKeyboardButton("📜 Пользовательское соглашение", url="https://telegra.ph/Polzovatelskoe-soglashenie-04-01-19"))
    return markup


def channel_keyboard():
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("📢 Перейти в канал", url="https://t.me/Kitaez_com", style="primary"))
    markup.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back_to_main"))
    return markup


def reviews_keyboard():
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("⭐ Посмотреть отзывы", url="https://t.me/OTZIVI_KRABIKA_TB", style="primary"))
    markup.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back_to_main"))
    return markup


def order_success_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton("⭐ Посмотреть отзывы", url="https://t.me/OTZIVI_KRABIKA_TB", style="primary"))
    markup.add(types.InlineKeyboardButton("🔙 Вернуться в меню", callback_data="back_to_main"))
    return markup


def support_keyboard():
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("👤 Написать @KRABIK_TB", url="https://t.me/KRABIK_TB", style="primary"))
    markup.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back_to_main"))
    return markup


def profile_keyboard():
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("📋 Все мои заказы", callback_data="my_orders_stub", style="success"))
    markup.add(types.InlineKeyboardButton("👥 Рефералы", callback_data="open_referral", style="primary"))
    markup.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back_to_main"))
    return markup


def referral_keyboard(bot_username: str, user_id: int):
    markup = types.InlineKeyboardMarkup()
    ref_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
    share_url = f"https://t.me/share/url?url={ref_link}&text=Купи%20GMP%20по%20самому%20выгодному%20курсу!"
    markup.add(types.InlineKeyboardButton("🔗 Поделиться ссылкой", url=share_url, style="success"))
    markup.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back_to_main"))
    return markup


def back_keyboard():
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back_to_main"))
    return markup


def admin_main_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📊 Статистика", callback_data="admin_stats", style="primary"),
        types.InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast_start", style="primary"),
    )
    markup.add(
        types.InlineKeyboardButton("👥 Рефералы", callback_data="admin_referrals", style="success"),
        types.InlineKeyboardButton("💱 Курсы", callback_data="admin_rates", style="success"),
    )
    markup.add(types.InlineKeyboardButton("❌ Закрыть", callback_data="admin_close", style="danger"))
    return markup


def admin_rates_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=1)
    for i, tier in enumerate(DATA["rates"]):
        markup.add(types.InlineKeyboardButton(
            f"{tier['min']}–{tier['max']}₽ → {tier['rate']}₽ / 1 GMP",
            callback_data=f"admin_rate_edit_{i}",
            style="primary",
        ))
    markup.add(types.InlineKeyboardButton("🔙 Назад", callback_data="admin_menu", style="danger"))
    return markup


def broadcast_confirm_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🚀 Запустить рассылку", callback_data="admin_bc_confirm", style="success"),
        types.InlineKeyboardButton("❌ Отмена", callback_data="admin_bc_cancel", style="danger"),
    )
    return markup


def skip_buttons_keyboard():
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("⏩ Пропустить (без кнопок)", callback_data="admin_bc_skip_buttons", style="primary"))
    markup.add(types.InlineKeyboardButton("❌ Отмена", callback_data="admin_bc_cancel", style="danger"))
    return markup


def is_probable_amount(text):
    if not text or text.startswith('/'):
        return False
    txt = text.lower().replace(" ", "")
    cleaned = re.sub(r'[₽ррууббlluubbr]', '', txt).replace(',', '.')
    try:
        val = float(cleaned)
        return 50 <= val <= 50000
    except ValueError:
        return False


def build_bonus_countdown_block(bonuses: dict) -> str:
    parts = []
    if bonuses["welcome_5"]:
        ref_note = "по реферальной ссылке" if bonuses["has_referrer"] else "на 1-й заказ"
        parts.append(
            f"🔥 <b>ВНИМАНИЕ! Бонус +5% GMP ({ref_note}) сгорит через: <code>{bonuses['welcome_time_str']}</code></b>\n"
            f"⚡️ <i>Успейте оформить заказ прямо сейчас! Нажмите «💸 Купить GMP» ниже.</i>"
        )
    if bonuses["ref_2"]:
        parts.append(
            f"⏰ <b>Реферальный бонус +2% GMP сгорит через: <code>{bonuses['ref_time_str']}</code></b>\n"
            f"🚀 <i>Не упустите повышенный процент!</i>"
        )
    if parts:
        return "\n\n".join(parts) + "\n\n━━━━━━━━━━━━━━━━━━━━\n\n"
    return ""


# ==============================================================================
# 👑 КОМАНДА /admin
# ==============================================================================

@bot.message_handler(commands=['admin', 'adm'])
def handle_admin_command(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    text = (
        "👑 <b>ПАНЕЛЬ АДМИНИСТРАТОРА GMP</b>\n"
        "• • • • • • • • • • • • •\n"
        "Выберите нужное действие ниже:"
    )
    bot.send_message(message.chat.id, text, reply_markup=admin_main_keyboard(), parse_mode="HTML")


# ==============================================================================
# 🔘 CALLBACK-ОБРАБОТЧИК
# ==============================================================================

@bot.callback_query_handler(func=lambda call: True)
def callback_inline(call):
    user_id = call.from_user.id
    name = call.from_user.first_name or "Пользователь"
    username = f"@{call.from_user.username}" if call.from_user.username else "Не установлен"

    upsert_user(user_id, name, username)

    # --- АДМИН-ПАНЕЛЬ ---
    if call.data.startswith("admin_"):
        if user_id not in ADMIN_IDS:
            bot.answer_callback_query(call.id, text="❌ Доступ запрещен!", show_alert=True)
            return

        if call.data == "admin_stats":
            bot.answer_callback_query(call.id)
            s = get_admin_detailed_stats()
            stats_text = (
                "📊 <b>СТАТИСТИКА БОТА И ПРОДАЖ</b>\n"
                "• • • • • • • • • • • •\n\n"
                f"👥 <b>Пользователи:</b>\n"
                f"• Всего: <b>{s['total_users']:,}</b> чел.\n"
                f"• По рефкам: <b>{s['total_invited']:,}</b> чел.\n"
                f"• За сегодня: <b>{s['today_users']:,}</b> чел.\n\n"
                f"💰 <b>Заказы и GMP:</b>\n"
                f"• Успешных: <b>{s['approved_orders']:,}</b>\n"
                f"• Выдано: <b>{s['total_gmp']:,} GMP</b>\n"
                f"• Заработано: <b>{s['total_rub']:,.2f} ₽</b>\n"
                f"• В очереди: <b>{s['pending_orders']}</b> шт.\n\n"
                f"🕒 <i>Обновлено: {datetime.now().strftime('%H:%M:%S')}</i>"
            )
            markup = types.InlineKeyboardMarkup()
            markup.add(
                types.InlineKeyboardButton("🔄 Обновить", callback_data="admin_stats", style="primary"),
                types.InlineKeyboardButton("🔙 Назад", callback_data="admin_menu", style="danger"),
            )
            try:
                bot.edit_message_text(
                    text=stats_text,
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=markup,
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.error(f"Ошибка edit admin_stats: {e}")
                bot.send_message(call.message.chat.id, stats_text, reply_markup=markup, parse_mode="HTML")

        elif call.data == "admin_referrals":
            bot.answer_callback_query(call.id)
            ref_data = get_admin_referral_stats()
            ref_text = (
                f"👥 <b>СТАТИСТИКА РЕФЕРАЛОВ</b>\n"
                f"• • • • • • • • • • • • •\n\n"
                f"🔗 Всего приглашено: <b>{ref_data['total_invited']}</b> чел.\n\n"
                f"🏆 <b>ТОП РЕФЕРЕРОВ:</b>\n"
            )
            if not ref_data["top_referrers"]:
                ref_text += "<i>Пока никого не пригласили</i>\n"
            else:
                for idx, (uid, fname, uname, count) in enumerate(ref_data["top_referrers"], 1):
                    disp = f"@{uname.replace('@', '')}" if uname and uname != "Не установлен" else (fname or f"ID: {uid}")
                    ref_text += f"<b>{idx}.</b> {disp} (<code>{uid}</code>) — <b>{count}</b> чел.\n"
            ref_text += f"\n🕒 <i>Обновлено: {datetime.now().strftime('%H:%M:%S')}</i>"
            markup = types.InlineKeyboardMarkup()
            markup.add(
                types.InlineKeyboardButton("🔄 Обновить", callback_data="admin_referrals", style="primary"),
                types.InlineKeyboardButton("🔙 Назад", callback_data="admin_menu", style="danger"),
            )
            try:
                bot.edit_message_text(
                    text=ref_text,
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=markup,
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.error(f"Ошибка edit admin_referrals: {e}")
                bot.send_message(call.message.chat.id, ref_text, reply_markup=markup, parse_mode="HTML")

        elif call.data == "admin_rates":
            bot.answer_callback_query(call.id)
            rates_text = (
                "💱 <b>УПРАВЛЕНИЕ КУРСАМИ</b>\n"
                "• • • • • • • • • • • •\n\n"
                "Нажмите на нужный диапазон, чтобы изменить курс.\n\n"
                "<b>Актуальные курсы:</b>\n"
            )
            for t in DATA["rates"]:
                rates_text += f"• {t['min']}–{t['max']}₽ → <b>{t['rate']}₽</b> за 1 GMP\n"
            try:
                bot.edit_message_text(
                    text=rates_text,
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=admin_rates_keyboard(),
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.error(f"Ошибка edit admin_rates: {e}")
                bot.send_message(call.message.chat.id, rates_text, reply_markup=admin_rates_keyboard(), parse_mode="HTML")

        elif call.data.startswith("admin_rate_edit_"):
            bot.answer_callback_query(call.id)
            idx = int(call.data.split("_")[-1])
            if idx >= len(DATA["rates"]):
                return
            tier = DATA["rates"][idx]
            admin_rate_state[user_id] = {"index": idx}
            cancel_kb = types.InlineKeyboardMarkup()
            cancel_kb.add(types.InlineKeyboardButton("❌ Отмена", callback_data="admin_rates", style="danger"))
            bot.send_message(
                call.message.chat.id,
                f"✏️ <b>Изменение курса</b>\n\n"
                f"Диапазон: <b>{tier['min']}–{tier['max']}₽</b>\n"
                f"Текущий курс: <b>{tier['rate']}₽</b> за 1 GMP\n\n"
                f"Отправьте новый курс числом (например: <code>0.35</code>):",
                reply_markup=cancel_kb,
                parse_mode="HTML",
            )

        elif call.data == "admin_menu":
            bot.answer_callback_query(call.id)
            text = "👑 <b>ПАНЕЛЬ АДМИНИСТРАТОРА GMP</b>\n━━━━━━━━━━━━━━━━━━━━\nВыберите нужное действие ниже:"
            try:
                bot.edit_message_text(
                    text=text,
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=admin_main_keyboard(),
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.error(f"Ошибка edit admin_menu: {e}")
                bot.send_message(call.message.chat.id, text, reply_markup=admin_main_keyboard(), parse_mode="HTML")

        elif call.data == "admin_close":
            bot.answer_callback_query(call.id)
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except Exception:
                pass

        elif call.data == "admin_broadcast_start":
            bot.answer_callback_query(call.id)
            admin_broadcast_state[user_id] = {"step": "waiting_content"}
            cancel_kb = types.InlineKeyboardMarkup()
            cancel_kb.add(types.InlineKeyboardButton("❌ Отмена", callback_data="admin_bc_cancel", style="danger"))
            bot.send_message(
                call.message.chat.id,
                "📢 <b>СОЗДАНИЕ РАССЫЛКИ</b>\n\n"
                "Отправьте сообщение (текст, фото или видео).\n"
                "<i>Все стили и премиум-эмодзи сохранятся полностью!</i>",
                reply_markup=cancel_kb,
                parse_mode="HTML",
            )

        elif call.data == "admin_bc_skip_buttons":
            bot.answer_callback_query(call.id)
            state = admin_broadcast_state.get(user_id)
            if not state or state.get("step") != "waiting_buttons":
                return
            state["reply_markup"] = None
            state["step"] = "confirm"
            bot.send_message(call.message.chat.id, "👀 <b>Предпросмотр:</b>", parse_mode="HTML")
            bot.copy_message(chat_id=call.message.chat.id, from_chat_id=state["chat_id"], message_id=state["message_id"])
            total_users = len(get_all_user_ids())
            bot.send_message(call.message.chat.id, f"👥 Получателей: <b>{total_users}</b>\n\nНачать рассылку?", reply_markup=broadcast_confirm_keyboard(), parse_mode="HTML")

        elif call.data == "admin_bc_cancel":
            bot.answer_callback_query(call.id, text="Рассылка отменена.")
            admin_broadcast_state.pop(user_id, None)
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except Exception:
                pass

        elif call.data == "admin_bc_confirm":
            bot.answer_callback_query(call.id)
            state = admin_broadcast_state.pop(user_id, None)
            if not state:
                bot.send_message(call.message.chat.id, "❌ Данные рассылки устарели.")
                return
            src_chat_id = state["chat_id"]
            src_msg_id = state["message_id"]
            kb = state.get("reply_markup")
            user_ids = get_all_user_ids()
            total = len(user_ids)
            bot.send_message(call.message.chat.id, f"🚀 <b>Рассылка запущена...</b>\nПолучателей: <b>{total}</b>", parse_mode="HTML")
            success_count = 0
            fail_count = 0
            for uid in user_ids:
                try:
                    bot.copy_message(chat_id=uid, from_chat_id=src_chat_id, message_id=src_msg_id, reply_markup=kb)
                    success_count += 1
                except Exception:
                    fail_count += 1
                time.sleep(0.04)
            bot.send_message(
                call.message.chat.id,
                f"✅ <b>Рассылка завершена!</b>\n\n"
                f"• Доставлено: <b>{success_count}</b>\n"
                f"• Ошибок: <b>{fail_count}</b>\n"
                f"• Всего: <b>{total}</b>",
                reply_markup=admin_main_keyboard(),
                parse_mode="HTML",
            )
        return

    # --- ОДОБРЕНИЕ/ОТКЛОНЕНИЕ ЗАЯВОК ---
    if call.data.startswith("adm_approve_") or call.data.startswith("adm_reject_"):
        if user_id not in ADMIN_IDS:
            bot.answer_callback_query(call.id, text="❌ Недостаточно прав!", show_alert=True)
            return

        is_approve = call.data.startswith("adm_approve_")
        order_id = int(call.data.split("_")[-1])

        if is_approve:
            res = approve_order(order_id)
            if not res["success"]:
                bot.answer_callback_query(call.id, text=f"⚠️ {res['reason']}", show_alert=True)
                return
            bot.answer_callback_query(call.id, text=f"✅ Заявка #{order_id} подтверждена!", show_alert=True)
            admin_name = call.from_user.first_name or "Админ"
            new_text = f"✅ <b>Заявка #{order_id} ПОДТВЕРЖДЕНА</b>\nПринял: {admin_name}\n\n"
            if call.message.caption:
                new_text += call.message.caption
            elif call.message.text:
                new_text += call.message.text
            try:
                if call.message.photo or call.message.document:
                    bot.edit_message_caption(chat_id=call.message.chat.id, message_id=call.message.message_id, caption=new_text, parse_mode="HTML")
                else:
                    bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text=new_text, parse_mode="HTML")
            except Exception as e:
                logger.error(f"Ошибка редактирования админ-сообщения: {e}")

            target_user = res["user_id"]
            bonus_info = f"\n<i>Учтен бонус: {res.get('bonus_desc')}</i>" if res.get('bonus_desc') else ""
            user_msg = (
                f"🎉 Заказ #{order_id} успешно выполнен!\n\n"
                f"💎 Вам начислено: {res['gmp']:,} GMP{bonus_info}\n\n"
                f"Спасибо за доверие! 🚀\n\n"
                f"<blockquote>🌟 Понравился сервис? \n\n"
                f"Нажмите «Ответить» (Reply) на это сообщение со своими впечатлениями и тегом @KRABIK_TB — ваш отзыв сразу попадет на Канал отзывов! 💬</blockquote>"
            )
            try:
                bot.send_message(target_user, user_msg, reply_markup=order_success_keyboard(), parse_mode="HTML")
            except Exception as e:
                logger.error(f"Не удалось уведомить {target_user}: {e}")

        else:
            res = reject_order(order_id)
            if not res["success"]:
                bot.answer_callback_query(call.id, text=f"⚠️ {res['reason']}", show_alert=True)
                return
            bot.answer_callback_query(call.id, text=f"❌ Заявка #{order_id} отклонена!", show_alert=True)
            admin_name = call.from_user.first_name or "Админ"
            new_text = f"❌ <b>Заявка #{order_id} ОТКЛОНЕНА</b>\nОтклонил: {admin_name}\n\n"
            if call.message.caption:
                new_text += call.message.caption
            elif call.message.text:
                new_text += call.message.text
            try:
                if call.message.photo or call.message.document:
                    bot.edit_message_caption(chat_id=call.message.chat.id, message_id=call.message.message_id, caption=new_text, parse_mode="HTML")
                else:
                    bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text=new_text, parse_mode="HTML")
            except Exception as e:
                logger.error(f"Ошибка редактирования админ-сообщения: {e}")

            target_user = res["user_id"]
            user_msg = (
                f"{EMOJI_CSECK} <b>ВАША ЗАЯВКА ОТКЛОНЕНА</b>\n\n"
                f"Администратор не подтвердил ваш платеж #{order_id}.\n"
                f"Возможные причины:\n"
                f"• Чек не читаем\n"
                f"• Сумма перевода не совпадает\n"
                f"• Оплата не поступила на счет\n"
                f"• Перевод без обязательного описания\n\n"
                f"Если произошла ошибка, напишите в поддержку {EMOJI_DEV} @KRABIK_TB"
            )
            try:
                bot.send_message(target_user, user_msg, reply_markup=support_keyboard(), parse_mode="HTML")
            except Exception as e:
                logger.error(f"Не удалось уведомить {target_user}: {e}")
        return

    # --- ПОЛЬЗОВАТЕЛЬСКИЕ КНОПКИ ---
    if call.data == "back_to_main":
        bot.answer_callback_query(call.id)
        user_states[user_id] = None
        bonuses = get_user_bonuses(user_id)
        bonus_block = build_bonus_countdown_block(bonuses)
        try:
            bot.edit_message_text(
                text=START_TEXT.format(name=name, bonus_block=bonus_block),
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=main_keyboard(),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"Ошибка back_to_main: {e}")
            bot.send_message(call.message.chat.id, START_TEXT.format(name=name, bonus_block=bonus_block), reply_markup=main_keyboard(), parse_mode="HTML")

    elif call.data == "open_channel":
        bot.answer_callback_query(call.id)
        try:
            bot.edit_message_text(
                text=f"{EMOJI_CHANNEL} Наш официальный канал:",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=channel_keyboard(),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"Ошибка open_channel: {e}")

    elif call.data == "open_reviews":
        bot.answer_callback_query(call.id)
        try:
            bot.edit_message_text(
                text=f"{EMOJI_STAR} Отзывы наших клиентов:",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=reviews_keyboard(),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"Ошибка open_reviews: {e}")

    elif call.data == "open_support":
        bot.answer_callback_query(call.id)
        try:
            bot.edit_message_text(
                text=f"{EMOJI_DEV} Служба поддержки:\n{EMOJI_USER} @KRABIK_TB",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=support_keyboard(),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"Ошибка open_support: {e}")

    elif call.data == "open_referral":
        bot.answer_callback_query(call.id)
        bonuses = get_user_bonuses(user_id)
        bot_info = bot.get_me()
        bot_user = bot_info.username or "GMPBot"
        ref_link = f"https://t.me/{bot_user}?start=ref_{user_id}"
        status_text = (
            f"🟢 <b>АКТИВЕН (+2% GMP)</b> — осталось <b>{bonuses['ref_time_str']}</b>"
            if bonuses["ref_2"] else "⚪️ <i>Не активен (пригласите друга)</i>"
        )
        ref_text = (
            f"👥 <b>РЕФЕРАЛЬНАЯ СИСТЕМА</b>\n"
            f"• • • • • • • • • • • • •\n\n"
            f"Приглашай друзей и получай <b>+2% к каждому заказу GMP</b>!\n\n"
            f"⏳ <b>Как работает бонус времени:</b>\n"
            f"• 1 друг = бонус на <b>24 часа</b>\n"
            f"• Каждый следующий друг продлевает бонус ещё на <b>+24 часа</b>!\n\n"
            f"🎁 <i>Твой друг получает бонус <b>+5% GMP на первый заказ</b>!</i>\n\n"
            f"📊 <b>Твоя статистика:</b>\n"
            f"• Приглашено: <b>{bonuses['refs_count']}</b> чел.\n"
            f"• Статус бонуса: {status_text}\n\n"
            f"🔗 <b>Твоя реферальная ссылка:</b>\n"
            f"<code>{ref_link}</code>"
        )
        try:
            bot.edit_message_text(
                text=ref_text,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=referral_keyboard(bot_user, user_id),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"Ошибка open_referral: {e}")
            bot.send_message(call.message.chat.id, ref_text, reply_markup=referral_keyboard(bot_user, user_id), parse_mode="HTML")

    elif call.data in ["profile", "open_profile"]:
        bot.answer_callback_query(call.id)
        stats = get_user_stats(user_id)
        bonuses = get_user_bonuses(user_id)
        bonus_status = []
        if bonuses["welcome_5"]:
            bonus_status.append(f"🎁 +5% (осталось {bonuses['welcome_time_str']})")
        if bonuses["ref_2"]:
            bonus_status.append(f"👥 +2% (осталось {bonuses['ref_time_str']})")
        bonus_str = ", ".join(bonus_status) if bonus_status else "нет активных"
        profile_text = (
            f"{EMOJI_USER} <b>МОЙ ПРОФИЛЬ</b>\n"
            f"{EMOJI_CARD} ID: <code>{user_id}</code>\n"
            f"{EMOJI_NAME_TAG} Имя: {name}\n"
            f"{EMOJI_USER} Юзернейм: {username}\n\n"
            f"{EMOJI_CHART} <b>СТАТИСТИКА</b>\n"
            f"🛒 Заказов: <b>{stats['total_orders']}</b>\n"
            f"{EMOJI_GIFT} Получено GMP: <b>{stats['total_gmp']:,}</b>\n"
            f"{EMOJI_MONEY} Потрачено: <b>{stats['total_rub']:,.0f}₽</b>\n"
            f"👥 Приглашено: <b>{bonuses['refs_count']}</b>\n"
            f"✨ Бонусы: <b>{bonus_str}</b>"
        )
        try:
            bot.edit_message_text(
                text=profile_text,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=profile_keyboard(),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"Ошибка open_profile: {e}")

    elif call.data == "info_withdraw":
        bot.answer_callback_query(call.id)
        withdraw_text = (
            f"{EMOJI_SUNFLOWER} <b>Время вывода GMP</b>\n\n"
            "В среднем не превышает 5-10 минут, но если админ занят, время может увеличиться.\n"
            f"{EMOJI_GIFT} За долгое ожидание вы гарантированно получите бонус!\n\n"
            f"{EMOJI_MUSHROOM} <b>Время активности админа (МСК):</b>\n"
            "• Будни: 01:15 – 17:30\n"
            "• Сб: 01:15 – 18:30\n"
            "• Вс: 04:30 – 18:00\n\n"
            f"Пожалуйста, будьте понимающими {EMOJI_KISS}"
        )
        try:
            bot.edit_message_text(
                text=withdraw_text,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=back_keyboard(),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"Ошибка info_withdraw: {e}")

    elif call.data == "buy_gmp":
        bot.answer_callback_query(call.id)
        user_states[user_id] = "awaiting_rubles"
        bonuses = get_user_bonuses(user_id)

        bonus_hints = []
        if bonuses["welcome_5"]:
            ref_label = "по реф. ссылке" if bonuses["has_referrer"] else "на 1-й заказ"
            bonus_hints.append(f"🎁 <i>Бонус +5% GMP ({ref_label})! До сгорания: <code>{bonuses['welcome_time_str']}</code></i>")
        if bonuses["ref_2"]:
            bonus_hints.append(f"👥 <i>Реферальный бонус +2% GMP! До сгорания: <code>{bonuses['ref_time_str']}</code></i>")
        bonus_hint_text = ("\n" + "\n".join(bonus_hints) + "\n") if bonus_hints else ""

        rates_lines = "\n".join(
            f"{EMOJI_CHART} {t['min']}–{t['max']}₽: {t['rate']}₽ = 1 GMP"
            for t in DATA["rates"]
        )

        calc_text = (
            f"{EMOJI_CARD} <b>Для подсчёта GMP отправьте сумму в рублях</b>\n"
            f"{EMOJI_WRITE} <i>Напишите просто числом (например: 500)</i>\n\n"
            f"{EMOJI_MONEY} Минимум: {DATA['rates'][0]['min']}₽\n"
            f"{EMOJI_MONEY} Максимум: {DATA['rates'][-1]['max']}₽\n\n"
            f"{EMOJI_CHART_UP} <b>Актуальные курсы:</b>\n"
            f"{rates_lines}\n"
            f"{bonus_hint_text}"
        )
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        bot.send_message(call.message.chat.id, calc_text, reply_markup=back_keyboard(), parse_mode="HTML")

    elif call.data == "pay_confirm":
        state_data = user_states.get(user_id)
        if isinstance(state_data, dict) and state_data.get("file_id") is None:
            bot.answer_callback_query(call.id, text="Сначала отправьте скриншот оплаты фото-сообщением!", show_alert=True)
        elif isinstance(state_data, dict) and state_data.get("file_id") is not None:
            bot.answer_callback_query(call.id)
            rub = state_data["rub"]
            gmp = state_data["gmp"]
            file_id = state_data["file_id"]
            is_photo = state_data["is_photo"]
            admin_bonus_text = state_data.get("admin_bonus_text", "")
            bonus_desc = state_data.get("bonus_desc", "")

            order_id = create_pending_order(user_id, rub, gmp, file_id, bonus_desc)
            user_states[user_id] = None

            admin_msg = (
                f"🔔 <b>НОВАЯ ЗАЯВКА #{order_id}</b>\n\n"
                f"{EMOJI_USER} {name}\n"
                f"{EMOJI_CARD} ID: <code>{user_id}</code>\n"
                f"{EMOJI_NAME_TAG} {username}\n\n"
                f"{EMOJI_MONEY} Сумма: <b>{rub}₽</b>\n"
                f"{EMOJI_DIAMOND} GMP: <b>{gmp:,}</b>\n"
                f"{admin_bonus_text}\n"
                f"{EMOJI_WARN} <i>Проверьте комментарий к переводу!</i>"
            )
            admin_markup = types.InlineKeyboardMarkup(row_width=2)
            admin_markup.add(
                types.InlineKeyboardButton("✅ Подтвердить", callback_data=f"adm_approve_{order_id}"),
                types.InlineKeyboardButton("❌ Отклонить", callback_data=f"adm_reject_{order_id}"),
            )
            for admin in ADMIN_IDS:
                try:
                    if is_photo:
                        bot.send_photo(admin, file_id, caption=admin_msg, reply_markup=admin_markup, parse_mode="HTML")
                    else:
                        bot.send_document(admin, file_id, caption=admin_msg, reply_markup=admin_markup, parse_mode="HTML")
                except Exception as e:
                    logger.error(f"Ошибка отправки админу {admin}: {e}")

            bot.send_message(
                call.message.chat.id,
                f"{EMOJI_CHECK} <b>Заявка #{order_id} отправлена на проверку!</b>\n"
                "Администратор проверит платёж. Ожидайте зачисления в течение 5-10 минут.",
                reply_markup=back_keyboard(),
                parse_mode="HTML",
            )
        else:
            bot.answer_callback_query(call.id, text="Сессия истекла. Нажмите «Купить GMP» заново.", show_alert=True)

    elif call.data == "my_orders_stub":
        bot.answer_callback_query(call.id)
        orders = get_user_orders_history(user_id)
        if not orders:
            bot.send_message(call.message.chat.id, f"{EMOJI_CLIP} У вас пока нет заказов.", parse_mode="HTML")
        else:
            txt = f"{EMOJI_CLIP} <b>Ваши последние заказы:</b>\n• • • • • • • • • • • • •\n"
            for oid, orub, ogmp, ostatus, odate in orders:
                status_str = "✅ Выдан" if ostatus == "approved" else ("❌ Отклонен" if ostatus == "rejected" else "⏳ На проверке")
                txt += f"• <b>Заказ #{oid}</b> ({odate}) — {status_str}\n  {orub:,.0f}₽ → <b>{ogmp:,} GMP</b>\n\n"
            bot.send_message(call.message.chat.id, txt, reply_markup=back_keyboard(), parse_mode="HTML")


# ==============================================================================
# 📎 МЕДИА
# ==============================================================================

@bot.message_handler(content_types=['photo', 'document', 'video', 'animation'])
def handle_media(message):
    user_id = message.from_user.id

    if user_id in ADMIN_IDS and user_id in admin_broadcast_state:
        state = admin_broadcast_state[user_id]
        if state.get("step") == "waiting_content":
            state["message_id"] = message.message_id
            state["chat_id"] = message.chat.id
            state["step"] = "waiting_buttons"
            help_text = (
                "🔘 <b>Добавление инлайн-кнопок:</b>\n\n"
                "Формат: <code>Текст | https://ссылка.com</code>\n\n"
                "Если кнопки не нужны — нажмите кнопку ниже:"
            )
            bot.send_message(message.chat.id, help_text, reply_markup=skip_buttons_keyboard(), parse_mode="HTML")
            return

    state_data = user_states.get(user_id)
    if isinstance(state_data, dict) and state_data.get("state") == "awaiting_screenshot":
        if message.content_type == 'photo':
            file_id = message.photo[-1].file_id
            is_photo = True
        else:
            file_id = getattr(message, message.content_type).file_id
            is_photo = False
        state_data["file_id"] = file_id
        state_data["is_photo"] = is_photo
        user_states[user_id] = state_data
        bot.reply_to(
            message,
            f"{EMOJI_CAMERA} <b>Скриншот получен!</b>\n\n"
            f"Теперь нажмите «✅ Подтвердить оплату» под сообщением с реквизитами.",
            parse_mode="HTML",
        )


# ==============================================================================
# ✍️ ТЕКСТ
# ==============================================================================

@bot.message_handler(content_types=['text'])
def handle_all_text(message):
    user_id = message.from_user.id
    name = message.from_user.first_name or "Пользователь"
    username = f"@{message.from_user.username}" if message.from_user.username else "Не установлен"

    # --- ОТЗЫВ ЧЕРЕЗ REPLY ---
    if message.reply_to_message and message.from_user.username:
        replied_text = message.reply_to_message.text or message.reply_to_message.caption or ""
        if "успешно выполнен!" in replied_text:
            order_num_match = re.search(r"Заказ #(\d+)", replied_text)
            ord_num = order_num_match.group(1) if order_num_match else None
            if ord_num:
                order = DATA["orders"].get(str(ord_num))
                gmp_val = order["gmp_amount"] if order else 0
                rub_val = int(order["rub_amount"]) if order else 0

                review_id = create_review(int(ord_num), user_id, rub_val, gmp_val) or 171

                # ✅ НИКНЕЙМ (first_name), а не @username
                buyer_display = message.from_user.first_name or f"@{message.from_user.username}"

                admin_review_msg = (
                    f"<b>Отзыв #{review_id}</b>\n"
                    f"Продано — {gmp_val} {EMOJI_DIAMOND}\n"
                    f"За {rub_val} {EMOJI_MONEY}\n\n"
                    f"<b>Комментарий от — {buyer_display}:</b>\n\n"
                    f"{html.escape(message.text)}"
                )
                for admin in ADMIN_IDS:
                    try:
                        bot.send_message(admin, admin_review_msg, parse_mode="HTML")
                    except Exception as e:
                        logger.error(f"Не удалось переслать отзыв админу {admin}: {e}")

    # --- РЕФЕРАЛЬНЫЙ СТАРТ ---
    referrer_id = None
    if message.text and message.text.startswith('/start'):
        parts = message.text.split()
        if len(parts) > 1 and parts[1].startswith("ref_"):
            try:
                referrer_id = int(parts[1].replace("ref_", ""))
            except ValueError:
                referrer_id = None

    is_new_user, ref_extended = upsert_user(user_id, name, username, referrer_id)

    if ref_extended and referrer_id:
        try:
            bot.send_message(
                referrer_id,
                f"🎉 <b>По вашей ссылке зарегистрировался новый пользователь ({name})!</b>\n\n"
                f"✨ Ваш бонус <b>+2% GMP</b> продлен на <b>+24 часа</b>!",
                parse_mode="HTML",
            )
        except Exception:
            pass

    # --- АДМИН: РАССЫЛКА ---
    if user_id in ADMIN_IDS and user_id in admin_broadcast_state:
        state = admin_broadcast_state[user_id]
        step = state.get("step")
        if step == "waiting_content":
            state["message_id"] = message.message_id
            state["chat_id"] = message.chat.id
            state["step"] = "waiting_buttons"
            bot.send_message(
                message.chat.id,
                "🔘 <b>Добавление инлайн-кнопок:</b>\n\n"
                "Формат: <code>Текст | https://ссылка.com</code>\n\n"
                "Если кнопки не нужны — нажмите кнопку ниже:",
                reply_markup=skip_buttons_keyboard(),
                parse_mode="HTML",
            )
            return
        elif step == "waiting_buttons":
            custom_markup = parse_broadcast_buttons(message.text)
            state["reply_markup"] = custom_markup
            state["step"] = "confirm"
            bot.send_message(message.chat.id, "👀 <b>Предпросмотр с кнопками:</b>", parse_mode="HTML")
            bot.copy_message(chat_id=message.chat.id, from_chat_id=state["chat_id"], message_id=state["message_id"], reply_markup=custom_markup)
            total_users = len(get_all_user_ids())
            bot.send_message(message.chat.id, f"👥 Получателей: <b>{total_users}</b>\n\nНачать рассылку?", reply_markup=broadcast_confirm_keyboard(), parse_mode="HTML")
            return

    # --- АДМИН: ИЗМЕНЕНИЕ КУРСА ---
    if user_id in ADMIN_IDS and user_id in admin_rate_state:
        state = admin_rate_state[user_id]
        try:
            new_rate = float(message.text.replace(",", ".").strip())
        except ValueError:
            bot.send_message(message.chat.id, "❌ Неверный формат. Отправьте число, например: <code>0.35</code>", parse_mode="HTML")
            return
        if new_rate <= 0 or new_rate > 1000:
            bot.send_message(message.chat.id, "❌ Курс должен быть от 0.01 до 1000.", parse_mode="HTML")
            return
        idx = state["index"]
        old_rate = DATA["rates"][idx]["rate"]
        DATA["rates"][idx]["rate"] = round(new_rate, 4)
        save_data()
        admin_rate_state.pop(user_id, None)
        tier = DATA["rates"][idx]
        bot.send_message(
            message.chat.id,
            f"✅ <b>Курс обновлен!</b>\n\n"
            f"Диапазон: <b>{tier['min']}–{tier['max']}₽</b>\n"
            f"Было: {old_rate}₽ → Стало: <b>{tier['rate']}₽</b> за 1 GMP",
            reply_markup=admin_main_keyboard(),
            parse_mode="HTML",
        )
        return

    # --- /start ---
    if message.text and message.text.startswith('/start'):
        user_states[user_id] = None
        bonuses = get_user_bonuses(user_id)
        bonus_block = build_bonus_countdown_block(bonuses)
        full_text = START_TEXT.format(name=name, bonus_block=bonus_block)
        bot.send_message(message.chat.id, full_text, reply_markup=main_keyboard(), parse_mode="HTML")
        return

    # --- ВВОД СУММЫ ---
    state = user_states.get(user_id)
    if state == "awaiting_rubles" or is_probable_amount(message.text):
        txt = message.text.lower().replace(" ", "")
        clean_text = re.sub(r'[₽ррууббlluubbr]', '', txt).replace(',', '.')
        try:
            rub_value = float(clean_text)
        except Exception:
            bot.send_message(message.chat.id, "❌ Не удалось распознать сумму. Напишите просто число (например: 540).", parse_mode="HTML")
            return

        min_amt = DATA["rates"][0]["min"]
        max_amt = DATA["rates"][-1]["max"]
        if rub_value < min_amt or rub_value > max_amt:
            bot.send_message(
                message.chat.id,
                f"{EMOJI_WARN} Сумма должна быть от <b>{min_amt}₽</b> до <b>{max_amt}₽</b>.",
                parse_mode="HTML",
            )
            return

        rate = get_rate_for_amount(rub_value)
        base_gmp = int(rub_value / rate)

        bonuses = get_user_bonuses(user_id)
        bonus_percent = 0
        bonus_user_notes = []
        admin_lines = []

        if bonuses["welcome_5"]:
            bonus_percent += 5
            ref_tag = "переход по реф. ссылке" if bonuses["has_referrer"] else "приветственный бонус"
            bonus_user_notes.append(f"+5% ({ref_tag})")
            admin_lines.append(f"🎁 <b>Бонус:</b> {ref_tag} (+5% GMP)")
            admin_lines.append(f"⏳ <b>Успел за 24ч:</b> да (оставалось <code>{bonuses['welcome_time_str']}</code>)")

        if bonuses["ref_2"]:
            bonus_percent += 2
            bonus_user_notes.append("+2% (за приглашение)")
            admin_lines.append(f"👥 <b>Бонус реферера:</b> +2% (осталось <code>{bonuses['ref_time_str']}</code>)")

        bonus_gmp_val = int(base_gmp * (bonus_percent / 100))
        final_gmp = base_gmp + bonus_gmp_val

        if admin_lines:
            admin_lines.append(f"ℹ️ <i>Базово: {base_gmp:,} ➔ С бонусом: {final_gmp:,} (+{bonus_gmp_val})</i>")
            admin_bonus_text = "\n" + "\n".join(admin_lines) + "\n"
        else:
            admin_bonus_text = ""

        bonus_desc = ", ".join(bonus_user_notes) if bonus_user_notes else None

        user_states[user_id] = {
            "state": "awaiting_screenshot",
            "rub": int(rub_value),
            "gmp": final_gmp,
            "base_gmp": base_gmp,
            "admin_bonus_text": admin_bonus_text,
            "bonus_desc": bonus_desc,
            "file_id": None,
            "is_photo": True,
        }

        bonus_calc_line = ""
        if bonus_percent > 0:
            time_left_str = bonuses['welcome_time_str'] if bonuses['welcome_5'] else bonuses['ref_time_str']
            bonus_calc_line = (
                f"\n🎁 <i>Применен бонус +{bonus_percent}%: +{bonus_gmp_val} GMP!</i>\n"
                f"⏳ <i>До сгорания: <code>{time_left_str}</code></i>\n"
            )

        line1 = f"{EMOJI_CARD} <b>{int(rub_value)}₽ → {final_gmp:,} GMP</b> (курс {rate}₽){bonus_calc_line}"
        line2 = f"{EMOJI_CLIP} <b>РЕКВИЗИТЫ ДЛЯ ОПЛАТЫ (СБП / ПЕРЕВОД):</b>"
        line3 = f"{EMOJI_WARN} Демид Андреевич П."
        line4 = f"{EMOJI_WARN} Банк: <b>ВТБ</b>"
        line5 = f"{EMOJI_WARN} <i>При переводе на другой банк—  <b>GMP</b> не выдаю!</i>"
        line6 = f"{EMOJI_PHONE} <b>Номер (СБП):</b>\n<code>+79142515945</code>"
        line7 = (
            f"{EMOJI_CSECK} <b>ОБЯЗАТЕЛЬНОЕ ПОЛЕ «ОПИСАНИЕ/СООБЩЕНИЕ ПОЛУЧАТЕЛЮ»:</b>\n"
            f"Напишите ОДНУ из фраз:\n"
            f"{EMOJI_CHECC} <code>на подарок</code>\n"
            f"{EMOJI_CHECC} спасибо за помощь\n"
            f"{EMOJI_CHECC} <code>сбор</code>\n"
            f"{EMOJI_CHECC} <code>на общие нужды</code>\n\n"
            f"{EMOJI_WARN * 2} <b>ВНИМАНИЕ:</b>Юез описания — <b> выдаётся только 50% GMP</b>"
        )
        line8 = (
            f"{EMOJI_CAMERA} <b>ИНСТРУКЦИЯ:</b>\n"
            f"1. Переведите <b>{int(rub_value)}₽</b> на ВТБ.\n"
            f"2. Укажите в комментарии одну из фраз выше.\n"
            f"3. Перейдите во вкладку чек и сделайте скриншот.\n"
            f"4. <u><b>ОТПРАВЬТЕ СКРИНШОТ В ЭТОТ ЧАТ!</b></u>\n"
            f"5. Нажмите «✅ Подтвердить оплату» ниже."
        )
        requisites_text = f"{line1}\n\n{line2}\n{line3}\n{line4}\n{line5}\n\n{line6}\n\n{line7}\n\n{line8}"

        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("✅ Подтвердить оплату", callback_data="pay_confirm", style="success"))
        markup.add(types.InlineKeyboardButton("❌ Отмена", callback_data="back_to_main", style="danger"))
        bot.send_message(message.chat.id, requisites_text, reply_markup=markup, parse_mode="HTML")


# === ЗАПУСК ===
if __name__ == "__main__":
    load_data()
    try:
        bot.remove_webhook()
    except Exception:
        pass
    logger.info("🚀 Бот запущен! База — оперативная (RAM + bot_data.json)")
    bot.polling(none_stop=True, interval=0, timeout=20)
