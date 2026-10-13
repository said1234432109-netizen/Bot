# -*- coding: utf-8 -*-
"""
BOT-YARAT: bitta Railway worker ichida asosiy bot + barcha farzand botlar.

Asosiy bot (BOT_TOKEN)  -> foydalanuvchilarga bot yaratib beradi.
Farzand botlar          -> har biri alohida asyncio task (polling) sifatida shu jarayonda ishlaydi.
Turlari                 -> kino / multfilm / anime / drama (bitta "kino dvigatel") va BuilderBot.

Kerakli ENV: BOT_TOKEN, MONGO_URI, OWNER_IDS (vergul bilan)
Ixtiyoriy : DB_NAME, SUPPORT_USERNAME, PAY_INFO, WEB_URL, REF_BONUS, TRIAL_DAYS,
            GRACE_DAYS, KINO_PRICE, BUILDER_FEE, USE_COLORS (1/0)
"""
import asyncio
import html
import logging
import os
import re
from datetime import datetime, timedelta, timezone

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramUnauthorizedError
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (BufferedInputFile, CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup)
from bson import ObjectId
from bson.json_util import dumps as bson_dumps
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bot-yarat")

# ----------------------------------------------------------------------------
# SOZLAMALAR
# ----------------------------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
MONGO_URI = os.getenv("MONGO_URI") or os.getenv("MONGO_URL") or ""
DB_NAME = os.getenv("DB_NAME", "saybuilder")
OWNERS = {int(x) for x in re.split(r"[,\s]+", os.getenv("OWNER_IDS", "")) if x.strip().isdigit()}
SUPPORT = os.getenv("SUPPORT_USERNAME", "").lstrip("@")
PAY_INFO = os.getenv("PAY_INFO", "💳 To'lov rekvizitlari kiritilmagan. Adminga murojaat qiling.")
WEB_URL = os.getenv("WEB_URL", "")
REF_BONUS = int(os.getenv("REF_BONUS", "1000"))
TRIAL_DAYS = int(os.getenv("TRIAL_DAYS", "7"))
GRACE_DAYS = int(os.getenv("GRACE_DAYS", "7"))      # sinov tugagach to'lanmasa, yana shuncha kundan keyin o'chiriladi
KINO_PRICE = int(os.getenv("KINO_PRICE", "50000"))
BUILDER_FEE = int(os.getenv("BUILDER_FEE", "150000"))
USE_COLORS = os.getenv("USE_COLORS", "1") == "1"

TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")

KINDS = {
    "kino": {"emoji": "🎥", "title": "Kino bot", "item": "kino", "items": "kinolar"},
    "multfilm": {"emoji": "🧸", "title": "Multfilm bot", "item": "multfilm", "items": "multfilmlar"},
    "anime": {"emoji": "🎌", "title": "Anime bot", "item": "anime", "items": "animelar"},
    "drama": {"emoji": "🎭", "title": "Drama bot", "item": "drama", "items": "dramalar"},
}

CATEGORIES = [
    ("media", "🎬 Media botlar"), ("smm", "🚀 SMM botlar"),
    ("fin", "💰 Moliyaviy botlar"), ("shop", "🛍 Savdo biznes botlar"),
    ("edu", "📚 Ta'lim va tarjimon botlar"), ("sub", "👑 Obuna botlar"),
    ("group", "👥 Guruh uchun botlar"), ("ai", "🤖 Sun'iy intellekt botlar"),
    ("util", "🛠 Foydali servis botlar"), ("auto", "🔄 Avtomatlashtirish botlar"),
]
# Faqat media bo'limida ishlaydigan botlar bor; qolganlari "tez orada"
CAT_BOTS = {
    "media": [("kino", "🎥 Kino bot"), ("multfilm", "🧸 Multfilm bot"), ("anime", "🎌 Anime bot"),
              ("drama", "🎭 Drama bot"), ("soon", "🎬 Kinobot Pro"), ("soon", "🎵 Audio Pechat Bot"),
              ("soon", "🎬 Pro Video Bot"), ("soon", "⚡ Video va musiqa yuklovchi (yangi)")],
}

TARIFFS = [("Mini", 5, 100000), ("Optimal", 10, 195000), ("Standard", 15, 285000), ("Pro", 30, 580000),
           ("Business", 50, 850000), ("Expert", 100, 1650000), ("Max", 250, 3950000)]

# ----------------------------------------------------------------------------
# BAZA
# ----------------------------------------------------------------------------
mongo = AsyncIOMotorClient(MONGO_URI) if MONGO_URI else None
db = mongo[DB_NAME] if mongo is not None else None
if db is not None:
    U, B, M, KU, TX = db.users, db.bots, db.movies, db.kusers, db.tx

RUNNING = {}          # bot_id -> asyncio.Task
MAIN = None           # asosiy Bot obyekti
MAIN_USERNAME = ""


# ----------------------------------------------------------------------------
# YORDAMCHILAR
# ----------------------------------------------------------------------------
def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def esc(s):
    return html.escape(str(s if s is not None else ""))


def money(n):
    return f"{int(n):,}"


COLORS = {"g": "success", "b": "primary", "r": "danger"}


def ib(text, cb=None, url=None, c=None):
    kw = {"text": text}
    if cb:
        kw["callback_data"] = cb
    if url:
        kw["url"] = url
    if USE_COLORS and c:
        kw["style"] = COLORS[c]       # Bot API tugma rangi (extra maydon sifatida yuboriladi)
    return InlineKeyboardButton(**kw)


def kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


def rk(rows):
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=t) for t in r] for r in rows], resize_keyboard=True)


def cgrid(items, n=2):
    """(matn, callback) ro'yxatidan qatorma-qator yashil/ko'k tugmalar."""
    rows = []
    for i in range(0, len(items), n):
        c = "g" if (i // n) % 2 == 0 else "b"
        rows.append([ib(t, d, c=c) for t, d in items[i:i + n]])
    return rows


CANCEL_ROW = [ib("❌ Bekor qilish", "x", c="r")]


async def show(ev, text, markup=None):
    """Callback bo'lsa — xabarni joyida tahrirlaydi, bo'lmasa yangi yuboradi."""
    if isinstance(ev, CallbackQuery):
        try:
            await ev.message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest as e:
            if "not modified" not in str(e).lower():
                await ev.message.answer(text, reply_markup=markup)
        try:
            await ev.answer()
        except Exception:
            pass
    else:
        await ev.answer(text, reply_markup=markup)


async def tell(uid, text, **kw):
    try:
        await MAIN.send_message(uid, text, **kw)
    except Exception:
        pass


def is_prem(ku):
    p = ku.get("premium")
    return bool(p and p > now())


def can(doc, uid, perm=None):
    if uid == doc["owner"]:
        return True
    p = (doc.get("settings") or {}).get("admins", {}).get(str(uid))
    if p is None:
        return False
    return perm is None or "all" in p or perm in p


async def add_premium(bid, uid, days):
    ku = await KU.find_one({"bot": bid, "uid": uid}) or {}
    base = max(now(), ku.get("premium") or now())
    await KU.update_one({"bot": bid, "uid": uid}, {"$set": {"premium": base + timedelta(days=days)}})


class MenuClear(BaseMiddleware):
    """Menyu tugmasi bosilsa, kutilayotgan holatni (FSM) tozalaydi."""

    def __init__(self, texts):
        self.texts = set(texts)

    async def __call__(self, handler, event, data):
        if isinstance(event, Message) and event.text in self.texts and data.get("state"):
            await data["state"].clear()
        return await handler(event, data)


class ChildMw(BaseMiddleware):
    """Farzand bot: foydalanuvchini ro'yxatga oladi, bloklanganlarni to'xtatadi."""

    def __init__(self, bid):
        self.bid = bid

    async def __call__(self, handler, event, data):
        u = getattr(event, "from_user", None)
        if not u:
            return await handler(event, data)
        doc = await B.find_one({"_id": self.bid})
        if not doc:
            return
        ku = await KU.find_one({"bot": self.bid, "uid": u.id})
        new = ku is None
        if new:
            ku = {"bot": self.bid, "uid": u.id, "joined": now(), "refs": 0, "balance": 0,
                  "bought": [], "blocked": False, "act": 0}
            try:
                await KU.insert_one(dict(ku))
            except Exception:
                new = False
        await KU.update_one({"bot": self.bid, "uid": u.id},
                            {"$set": {"name": u.full_name, "username": u.username, "last": now()},
                             "$inc": {"act": 1}})
        if ku.get("blocked") and not can(doc, u.id):
            return
        data["doc"], data["ku"], data["new"] = doc, ku, new
        return await handler(event, data)


# ----------------------------------------------------------------------------
# FARZAND BOTLARNI BOSHQARISH
# ----------------------------------------------------------------------------
async def run_polling(bid, dp, bot):
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot, handle_signals=False, allowed_updates=dp.resolve_used_update_types())
    except TelegramUnauthorizedError:
        await B.update_one({"_id": bid}, {"$set": {"status": "token_invalid"}})
        log.warning("bot %s: token yaroqsiz", bid)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("bot %s polling xatosi", bid)


async def start_child(doc):
    bid = doc["_id"]
    t = RUNNING.get(bid)
    if t and not t.done():
        return
    bot = Bot(doc["token"], default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    if doc["kind"] == "builder":
        dp.include_router(make_builder_router(bid))
    else:
        dp.include_router(make_kino_router(bid, doc["kind"]))
    RUNNING[bid] = asyncio.create_task(run_polling(bid, dp, bot))


async def stop_child(bid):
    t = RUNNING.pop(bid, None)
    if t and not t.done():
        t.cancel()
        try:
            await t
        except BaseException:
            pass


async def purge_bot(bid):
    async for c in B.find({"builder_of": bid}, {"_id": 1}):
        await purge_bot(c["_id"])
    await stop_child(bid)
    await B.delete_one({"_id": bid})
    await M.delete_many({"bot": bid})
    await KU.delete_many({"bot": bid})
    await db.counters.delete_one({"_id": f"m{bid}"})


async def probe(token):
    b = Bot(token)
    try:
        return await b.get_me()
    finally:
        await b.session.close()


async def check_token(m, token):
    """Tokenni tekshiradi. Muvaffaqiyatli bo'lsa bot haqidagi ma'lumotni qaytaradi."""
    token = token.strip()
    if not TOKEN_RE.match(token):
        await m.answer("❌ Token noto'g'ri. Qaytadan yuboring.")
        return None
    try:
        me = await probe(token)
    except Exception:
        await m.answer("❌ Token noto'g'ri yoki bot topilmadi. Qaytadan yuboring.")
        return None
    if await B.find_one({"_id": me.id}):
        await m.answer("⚠️ Bu bot allaqachon ro'yxatdan o'tgan.")
        return None
    return me


async def create_bot_doc(owner, me, token, kind, tariff, expires, builder_of=None, limit=0):
    d = {"_id": me.id, "token": token, "username": me.username, "name": me.full_name, "owner": owner,
         "kind": kind, "tariff": tariff, "status": "active", "paid": False, "created": now(),
         "expires": expires, "builder_of": builder_of, "limit": limit,
         "settings": {"channels": [], "admins": {}}}
    await B.insert_one(d)
    await start_child(d)
    return d


def success_text(d):
    if d["kind"] == "builder":
        return (f"✅ 🛡 BuilderBot ishga tushdi: <b>{esc(d['name'])}</b>\n\n"
                f"💠 Tarif: <b>{esc(d['tariff'])}</b> — {d['limit']} ta bot limiti\n"
                f"🗓 Muddati: {d['expires']:%d.%m.%Y} gacha")
    k = KINDS[d["kind"]]
    return (f"✅ {k['emoji']} {k['title']} ishga tushdi: <b>{esc(d['name'])}</b>\n\n"
            f"💠 Tarif: ⚡ Turbo — {money(KINO_PRICE)} so'm/oy (kuniga 7,500 tagacha foydalanuvchi)\n"
            f"🎁 {TRIAL_DAYS} kunlik bepul sinov boshlandi!\n"
            f"Majburiy obuna qo'shish uchun o'sha botga /channels yozing.")


def open_kb(d):
    return kb([[ib("🤖 Botga o'tish", url=f"https://t.me/{d['username']}", c="b")]])


# ============================================================================
#  KINO / MULTFILM / ANIME / DRAMA  (farzand bot)
# ============================================================================
class KS(StatesGroup):
    add_type = State()
    add_title = State()
    add_genre = State()
    add_price = State()
    add_file = State()
    search = State()
    receipt = State()
    find = State()
    bal = State()
    block = State()
    full = State()
    partner = State()
    card = State()
    pprice = State()
    stars = State()
    chan = State()
    bcast = State()


PERMS = {"movies": "🎬 Kontent", "stats": "📊 Statistika", "users": "👤 Foydalanuvchilar",
         "channels": "📢 Obuna", "broadcast": "📣 Xabar"}
DEFAULT_PERMS = ["movies", "stats", "users"]


async def missing_subs(bot, uid, doc):
    miss = []
    for ch in (doc.get("settings") or {}).get("channels", []):
        try:
            mem = await bot.get_chat_member(ch["id"], uid)
            if mem.status in ("left", "kicked"):
                miss.append(ch)
        except Exception:
            continue
    return miss


def subs_kb(miss, code):
    rows = [[ib(f"📢 {c['title']}"[:40], url=c["url"], c="b")] for c in miss if c.get("url")]
    rows.append([ib("✅ Tekshirish", f"chk:{code}", c="g")])
    return kb(rows)


def make_kino_router(bid, kind):
    k = KINDS[kind]
    r = Router()
    UT = {"new": f"🆕 Yangi {k['items']}", "top": f"🔥 TOP {k['items']}", "cat": "🏷 Kategoriyalar",
          "prem": "💎 Premium", "me": "👤 Profilim", "ref": "🔗 Do'st taklif qilish",
          "help": "ℹ️ Yordam", "search": "🔍 Nom bo'yicha qidirish"}
    A = {"add": f"➕ {k['item'].capitalize()} qo'shish", "list": f"🗂 {k['items'].capitalize()}",
         "tar": "💵 Tariflar", "pay": "💳 To'lov tizimlar", "stars": "⭐ Stars kursi",
         "partner": "👥 Hamkor-adminlar", "trial": "🎁 Sinov/Pullik", "ultra": "🚀 Ultra statistika",
         "tref": "🏆 Top referal", "tact": "🔝 TOP faol", "chan": "📢 Majburiy obuna",
         "find": "🔍 Foydalanuvchi qidirish", "block": "🚫 Bloklash", "delbot": "🗑 Botni o'chirish",
         "bcast": "📣 Barchaga xabar", "backup": "📥 Zaxira nusxa",
         "full": "👑 To'liq admin qo'shish/olib tashlash",
         "user": "⬅️ Foydalanuvchi menyusi", "admin": "🛠 Admin panel"}
    PERM = {"add": "movies", "list": "movies", "ultra": "stats", "tref": "stats", "tact": "stats",
            "chan": "channels", "find": "users", "block": "users", "bcast": "broadcast"}
    OWNER_ONLY = {"tar", "pay", "stars", "partner", "trial", "delbot", "backup", "full"}

    r.message.outer_middleware(MenuClear(list(UT.values()) + list(A.values())))
    mw = ChildMw(bid)
    r.message.outer_middleware(mw)
    r.callback_query.outer_middleware(mw)

    def user_kb(doc, uid):
        rows = [[UT["new"], UT["top"]], [UT["cat"], UT["prem"]], [UT["me"], UT["ref"]],
                [UT["search"], UT["help"]]]
        if can(doc, uid):
            rows.append([A["admin"]])
        return rk(rows)

    def admin_kb():
        return rk([[A["add"], A["list"]], [A["tar"], A["pay"]], [A["stars"], A["partner"]], [A["trial"]],
                   [A["ultra"], A["tref"]], [A["tact"]], [A["chan"]], [A["find"], A["block"]],
                   [A["delbot"]], [A["bcast"], A["backup"]], [A["full"]], [A["user"]]])

    async def fresh():
        return await B.find_one({"_id": bid})

    def mv_icon(mv):
        return {"vip": "💎", "paid": "💰"}.get(mv["type"], k["emoji"])

    def mv_kb(items, back=None):
        rows = [[ib(f"{mv_icon(x)} {x['title']} · #{x['code']}"[:60], f"mv:{x['code']}", c="b")] for x in items]
        if back:
            rows.append([ib("⬅️ Orqaga", back, c="r")])
        return kb(rows)

    async def recent(n):
        return await M.find({"bot": bid}).sort("added", -1).limit(n).to_list(n)

    # ------------------------------------------------------------- umumiy
    @r.message(Command("cancel"))
    async def cancel_cmd(m: Message, state: FSMContext, doc):
        await state.clear()
        await m.answer("❌ Bekor qilindi.", reply_markup=user_kb(doc, m.from_user.id))

    @r.callback_query(F.data == "x")
    async def cancel_cb(cb: CallbackQuery, state: FSMContext):
        await state.clear()
        await show(cb, "❌ Bekor qilindi.")

    # ------------------------------------------------------------- start
    @r.message(CommandStart())
    async def start(m: Message, command: CommandObject, state: FSMContext, doc, ku, new):
        await state.clear()
        uid = m.from_user.id
        arg = command.args or ""
        if new and arg.startswith("ref_") and arg[4:].isdigit() and int(arg[4:]) != uid:
            ref = int(arg[4:])
            refu = await KU.find_one_and_update({"bot": bid, "uid": ref}, {"$inc": {"refs": 1}},
                                                return_document=ReturnDocument.AFTER)
            if refu:
                await KU.update_one({"bot": bid, "uid": uid}, {"$set": {"ref_by": ref}})
                if refu["refs"] % 3 == 0:
                    await add_premium(bid, ref, 1)
                    try:
                        await m.bot.send_message(ref, "🎉 3 ta do'st taklif qildingiz — sizga +1 kun Premium berildi!")
                    except Exception:
                        pass
        if not can(doc, uid):
            miss = await missing_subs(m.bot, uid, doc)
            if miss:
                return await m.answer("📢 Botdan foydalanish uchun kanallarga obuna bo'ling:",
                                      reply_markup=subs_kb(miss, 0))
        await m.answer(f"{k['emoji']} <b>{esc(doc['name'])}</b>\n\n"
                       f"{k['item'].capitalize()} kodini yuboring yoki nomi bo'yicha qidiring.",
                       reply_markup=user_kb(doc, uid))

    @r.message(Command("admin"))
    async def admin_cmd(m: Message, doc):
        if can(doc, m.from_user.id):
            await m.answer("🛠 Admin panel", reply_markup=admin_kb())

    @r.message(F.text == A["admin"])
    async def admin_open(m: Message, doc):
        if can(doc, m.from_user.id):
            await m.answer("🛠 Admin panel", reply_markup=admin_kb())

    @r.message(F.text == A["user"])
    async def admin_close(m: Message, doc):
        await m.answer("👤 Foydalanuvchi menyusi", reply_markup=user_kb(doc, m.from_user.id))

    @r.callback_query(F.data.startswith("chk:"))
    async def chk(cb: CallbackQuery, doc, ku):
        code = int(cb.data[4:])
        if await missing_subs(cb.bot, cb.from_user.id, doc):
            return await cb.answer("❌ Hali hamma kanalga obuna bo'lmadingiz.", show_alert=True)
        await cb.answer("✅ Rahmat!")
        if code:
            await deliver(cb.bot, cb.from_user.id, code, doc, ku)
        else:
            await cb.message.answer("✅ Obuna tasdiqlandi.", reply_markup=user_kb(doc, cb.from_user.id))

    # ------------------------------------------------------------- kontent yuborish
    async def deliver(bot, uid, code, doc, ku):
        mv = await M.find_one({"bot": bid, "code": code})
        if not mv:
            return await bot.send_message(uid, "❌ Bunday kod topilmadi.")
        if not can(doc, uid):
            if await missing_subs(bot, uid, doc):
                miss = await missing_subs(bot, uid, doc)
                return await bot.send_message(uid, "📢 Avval kanallarga obuna bo'ling:", reply_markup=subs_kb(miss, code))
            if mv["type"] == "vip" and not is_prem(ku):
                return await bot.send_message(uid, "💎 Bu kontent faqat Premium foydalanuvchilar uchun.",
                                              reply_markup=kb([[ib("💎 Premium olish", "prem", c="g")]]))
            if mv["type"] == "paid" and code not in ku.get("bought", []):
                return await bot.send_message(
                    uid, f"💰 Narxi: <b>{money(mv['price'])} so'm</b>\nBalansingiz: {money(ku.get('balance', 0))} so'm",
                    reply_markup=kb([[ib("🛒 Sotib olish", f"buy:{code}", c="g")]]))
        cap = (f"{k['emoji']} <b>{esc(mv['title'])}</b>\n🏷 {esc(mv['genre'])}\n🔢 Kod: <code>{code}</code>\n\n"
               f"🤖 @{doc['username']}")
        try:
            if mv["ftype"] == "video":
                await bot.send_video(uid, mv["file_id"], caption=cap)
            else:
                await bot.send_document(uid, mv["file_id"], caption=cap)
        except TelegramBadRequest:
            return await bot.send_message(uid, "⚠️ Faylni yuborib bo'lmadi.")
        await M.update_one({"_id": mv["_id"]}, {"$inc": {"views": 1}})

    @r.callback_query(F.data.startswith("mv:"))
    async def mv_cb(cb: CallbackQuery, doc, ku):
        await cb.answer()
        await deliver(cb.bot, cb.from_user.id, int(cb.data[3:]), doc, ku)

    @r.callback_query(F.data.startswith("buy:"))
    async def buy(cb: CallbackQuery, doc):
        code = int(cb.data[4:])
        mv = await M.find_one({"bot": bid, "code": code})
        if not mv:
            return await cb.answer("Topilmadi", show_alert=True)
        ok = await KU.find_one_and_update(
            {"bot": bid, "uid": cb.from_user.id, "balance": {"$gte": mv["price"]}, "bought": {"$ne": code}},
            {"$inc": {"balance": -mv["price"]}, "$addToSet": {"bought": code}})
        if not ok:
            return await cb.answer("❌ Balans yetarli emas. Balansni to'ldirish uchun admin bilan bog'laning.",
                                   show_alert=True)
        await cb.answer("✅ Sotib olindi!")
        ku = await KU.find_one({"bot": bid, "uid": cb.from_user.id})
        await deliver(cb.bot, cb.from_user.id, code, doc, ku)

    # ------------------------------------------------------------- foydalanuvchi menyusi
    @r.message(F.text == UT["new"])
    async def h_new(m: Message, ku):
        prem = is_prem(ku)
        items = await recent(30 if prem else 5)
        if not items:
            return await m.answer("Hozircha hech narsa qo'shilmagan.")
        note = "" if prem else "\n\n💎 Barcha yangiliklarni ko'rish faqat Premium foydalanuvchilar uchun."
        await m.answer(f"🆕 <b>So'nggi {k['items']}</b>{note}", reply_markup=mv_kb(items))

    @r.message(F.text == UT["top"])
    async def h_top(m: Message):
        items = await M.find({"bot": bid}).sort("views", -1).limit(5).to_list(5)
        if not items:
            return await m.answer("Hozircha hech narsa qo'shilmagan.")
        await m.answer(f"🔥 <b>TOP {k['items']}</b>", reply_markup=mv_kb(items))

    async def cats_view():
        gs = await M.aggregate([{"$match": {"bot": bid}},
                                {"$group": {"_id": "$gkey", "name": {"$first": "$genre"}, "n": {"$sum": 1}}},
                                {"$sort": {"n": -1}}, {"$limit": 20}]).to_list(20)
        if not gs:
            return "Hozircha kategoriya yo'q.", None
        rows = cgrid([(f"{g['name']} ({g['n']})", f"gn:{g['_id']}") for g in gs])
        return "🏷 <b>Kategoriyalar</b>", kb(rows)

    @r.message(F.text == UT["cat"])
    async def h_cat(m: Message):
        text, mk = await cats_view()
        await m.answer(text, reply_markup=mk)

    @r.callback_query(F.data == "cats")
    async def cats_cb(cb: CallbackQuery):
        text, mk = await cats_view()
        await show(cb, text, mk)

    @r.callback_query(F.data.startswith("gn:"))
    async def genre_cb(cb: CallbackQuery):
        items = await M.find({"bot": bid, "gkey": cb.data[3:]}).sort("added", -1).limit(20).to_list(20)
        await show(cb, "🏷 <b>So'nggi 20 ta</b>", mv_kb(items, "cats"))

    def prem_view(doc, ku):
        s = doc.get("settings") or {}
        price, card, rate = s.get("premium_price"), s.get("card"), s.get("stars_rate")
        status = f"✅ Premium: {ku['premium']:%d.%m.%Y} gacha" if is_prem(ku) else "❌ Premium yo'q"
        if not price or not card:
            return f"💎 <b>Premium</b>\n\n{status}\n\nTo'lov hali sozlanmagan. Admin bilan bog'laning.", kb([CANCEL_ROW])
        txt = (f"💎 <b>Premium — 30 kun</b>\n\n{status}\n\n💰 Narxi: <b>{money(price)} so'm</b>\n"
               f"💳 To'lov: {esc(card)}\n")
        if rate:
            txt += f"⭐ Stars kursi: 1 ⭐ = {money(rate)} so'm (≈ {max(1, int(price) // int(rate))} ⭐)\n"
        txt += "\nTo'lovdan so'ng chek rasmini yuboring 👇"
        return txt, kb([[ib("🧾 Chek yuborish", "prem:rc", c="g")], CANCEL_ROW])

    @r.message(F.text == UT["prem"])
    async def h_prem(m: Message, doc, ku):
        text, mk = prem_view(doc, ku)
        await m.answer(text, reply_markup=mk)

    @r.callback_query(F.data == "prem")
    async def prem_cb(cb: CallbackQuery, doc, ku):
        text, mk = prem_view(doc, ku)
        await show(cb, text, mk)

    @r.callback_query(F.data == "prem:rc")
    async def prem_rc(cb: CallbackQuery, state: FSMContext):
        await state.set_state(KS.receipt)
        await show(cb, "🧾 To'lov chekining rasmini yuboring:", kb([CANCEL_ROW]))

    @r.message(KS.receipt, F.photo)
    async def got_receipt(m: Message, state: FSMContext, doc):
        await state.clear()
        t = await TX.insert_one({"kind": "premium", "bot": bid, "uid": m.from_user.id,
                                 "status": "pending", "created": now()})
        oid = str(t.inserted_id)
        cap = f"💎 <b>Premium so'rovi</b>\n👤 {esc(m.from_user.full_name)} (<code>{m.from_user.id}</code>)"
        try:
            await m.bot.send_photo(doc["owner"], m.photo[-1].file_id, caption=cap,
                                   reply_markup=kb([[ib("✅ Tasdiqlash", f"pr:ok:{oid}", c="g"),
                                                     ib("❌ Rad etish", f"pr:no:{oid}", c="r")]]))
        except Exception:
            log.warning("owner'ga yuborib bo'lmadi")
        await m.answer("✅ Chek yuborildi. Admin tasdiqlashini kuting.")

    @r.callback_query(F.data.regexp(r"^pr:(ok|no):[0-9a-f]{24}$"))
    async def pr_decide(cb: CallbackQuery, doc):
        if not can(doc, cb.from_user.id, "users"):
            return await cb.answer("⛔ Ruxsat yo'q", show_alert=True)
        _, act, oid = cb.data.split(":")
        t = await TX.find_one_and_update({"_id": ObjectId(oid), "status": "pending"}, {"$set": {"status": act}})
        if not t:
            return await cb.answer("Allaqachon ko'rib chiqilgan.", show_alert=True)
        if act == "ok":
            await add_premium(bid, t["uid"], 30)
            try:
                await cb.bot.send_message(t["uid"], "✅ Premium 30 kunga faollashtirildi!")
            except Exception:
                pass
        else:
            try:
                await cb.bot.send_message(t["uid"], "❌ To'lov rad etildi. Savol bo'lsa adminga yozing.")
            except Exception:
                pass
        mark = "\n\n✅ Tasdiqlandi" if act == "ok" else "\n\n❌ Rad etildi"
        await cb.message.edit_caption(caption=cb.message.html_text + mark, reply_markup=None)
        await cb.answer()

    @r.message(F.text == UT["me"])
    async def h_me(m: Message, ku):
        prem = f"✅ {ku['premium']:%d.%m.%Y} gacha" if is_prem(ku) else "❌ yo'q"
        await m.answer(f"👤 <b>Profilim</b>\n\n🆔 ID: <code>{m.from_user.id}</code>\n💎 Premium: {prem}\n"
                       f"🛒 Sotib olinganlar: {len(ku.get('bought', []))} ta\n"
                       f"👥 Taklif qilingan do'stlar: {ku.get('refs', 0)} ta\n"
                       f"💰 Balans: {money(ku.get('balance', 0))} so'm")

    @r.message(F.text == UT["ref"])
    async def h_ref(m: Message, doc):
        await m.answer(f"🔗 <b>Sizning havolangiz:</b>\nhttps://t.me/{doc['username']}?start=ref_{m.from_user.id}\n\n"
                       f"Har 3 ta taklif qilingan do'st uchun +1 kun Premium beriladi 🎁")

    @r.message(F.text == UT["help"])
    async def h_help(m: Message, doc):
        txt = (doc.get("settings") or {}).get("help") or \
            f"Savol bo'lsa <a href=\"tg://user?id={doc['owner']}\">adminga</a> yozing."
        await m.answer(f"ℹ️ <b>Yordam</b>\n\n{txt}")

    @r.message(F.text == UT["search"])
    async def h_search(m: Message, state: FSMContext):
        await state.set_state(KS.search)
        await m.answer(f"🔍 {k['item'].capitalize()} nomini yozing:")

    async def do_search(m, text):
        items = await M.find({"bot": bid, "title": {"$regex": re.escape(text[:50]), "$options": "i"}}) \
            .limit(10).to_list(10)
        if not items:
            return await m.answer("😔 Hech narsa topilmadi.")
        await m.answer(f"🔍 Natijalar: <b>{esc(text[:50])}</b>", reply_markup=mv_kb(items))

    @r.message(KS.search, F.text)
    async def search_state(m: Message, state: FSMContext):
        await state.clear()
        await do_search(m, m.text)

    # ------------------------------------------------------------- admin tugmalari
    async def act_add(m, state, doc):
        await state.set_state(KS.add_type)
        await m.answer(f"{k['emoji']} Turini tanlang:", reply_markup=kb(
            [[ib("🆓 Bepul", "mt:free", c="g"), ib("💎 VIP (premium)", "mt:vip", c="b")],
             [ib("💰 Pullik", "mt:paid", c="g")], CANCEL_ROW]))

    @r.callback_query(KS.add_type, F.data.startswith("mt:"))
    async def add_type(cb: CallbackQuery, state: FSMContext):
        await state.update_data(type=cb.data[3:])
        await state.set_state(KS.add_title)
        await show(cb, f"✏️ {k['item'].capitalize()} <b>nomini</b> yuboring:", kb([CANCEL_ROW]))

    @r.message(KS.add_title, F.text)
    async def add_title(m: Message, state: FSMContext):
        await state.update_data(title=m.text.strip()[:100])
        await state.set_state(KS.add_genre)
        await m.answer("🏷 Janrini yuboring (masalan: Jangari):")

    @r.message(KS.add_genre, F.text)
    async def add_genre(m: Message, state: FSMContext):
        g = m.text.strip()[:30]
        await state.update_data(genre=g, gkey=g.lower())
        if (await state.get_data())["type"] == "paid":
            await state.set_state(KS.add_price)
            return await m.answer("💰 Narxini so'mda yuboring:")
        await state.set_state(KS.add_file)
        await m.answer("📎 Endi video yoki faylni yuboring:")

    @r.message(KS.add_price, F.text)
    async def add_price(m: Message, state: FSMContext):
        if not m.text.strip().isdigit():
            return await m.answer("Faqat raqam yuboring.")
        await state.update_data(price=int(m.text))
        await state.set_state(KS.add_file)
        await m.answer("📎 Endi video yoki faylni yuboring:")

    @r.message(KS.add_file, F.video | F.document)
    async def add_file(m: Message, state: FSMContext, doc):
        d = await state.get_data()
        await state.clear()
        cnt = await db.counters.find_one_and_update({"_id": f"m{bid}"}, {"$inc": {"n": 1}}, upsert=True,
                                                    return_document=ReturnDocument.AFTER)
        code = cnt["n"]
        await M.insert_one({"bot": bid, "code": code, "title": d["title"], "genre": d["genre"], "gkey": d["gkey"],
                            "type": d["type"], "price": d.get("price", 0),
                            "file_id": (m.video or m.document).file_id,
                            "ftype": "video" if m.video else "doc", "views": 0, "added": now()})
        await m.answer(f"✅ Qo'shildi!\n🔢 Kod: <code>{code}</code>\n🎬 {esc(d['title'])}",
                       reply_markup=admin_kb())

    async def act_list(m, state, doc):
        items = await recent(15)
        if not items:
            return await m.answer("Hozircha bo'sh.")
        lines = "\n".join(f"#{x['code']} {mv_icon(x)} {esc(x['title'])} — 👁 {x['views']}" for x in items)
        await m.answer(f"🗂 <b>So'nggi {k['items']}</b>\n\n{lines}\n\nO'chirish: <code>/del KOD</code>")

    @r.message(Command("del"))
    async def del_cmd(m: Message, command: CommandObject, doc):
        if not can(doc, m.from_user.id, "movies"):
            return
        if not (command.args or "").strip().isdigit():
            return await m.answer("Foydalanish: /del KOD")
        res = await M.delete_one({"bot": bid, "code": int(command.args)})
        await m.answer("🗑 O'chirildi." if res.deleted_count else "❌ Topilmadi.")

    async def act_tar(m, state, doc):
        left = max(0, int((doc["expires"] - now()).total_seconds() // 86400))
        await m.answer(f"💵 <b>Tarif</b>\n\n⚡ Turbo — {money(KINO_PRICE)} so'm/oy\n"
                       f"👥 Kuniga 7,500 tagacha foydalanuvchi\n🗓 Tugash sanasi: {doc['expires']:%d.%m.%Y} "
                       f"({left} kun qoldi)")

    async def act_pay(m, state, doc):
        await state.set_state(KS.card)
        await m.answer("💳 Premium uchun to'lov ma'lumotini yuboring (karta raqami, ism):")

    @r.message(KS.card, F.text)
    async def pay_card(m: Message, state: FSMContext):
        await B.update_one({"_id": bid}, {"$set": {"settings.card": m.text.strip()[:200]}})
        await state.set_state(KS.pprice)
        await m.answer("💰 Premium (30 kun) narxini so'mda yuboring:")

    @r.message(KS.pprice, F.text)
    async def pay_price(m: Message, state: FSMContext):
        if not m.text.strip().isdigit():
            return await m.answer("Faqat raqam yuboring.")
        await B.update_one({"_id": bid}, {"$set": {"settings.premium_price": int(m.text)}})
        await state.clear()
        await m.answer("✅ Saqlandi.", reply_markup=admin_kb())

    async def act_stars(m, state, doc):
        await state.set_state(KS.stars)
        await m.answer("⭐ 1 Stars necha so'm? (raqam yuboring):")

    @r.message(KS.stars, F.text)
    async def stars_set(m: Message, state: FSMContext):
        if not m.text.strip().isdigit():
            return await m.answer("Faqat raqam yuboring.")
        await B.update_one({"_id": bid}, {"$set": {"settings.stars_rate": int(m.text)}})
        await state.clear()
        await m.answer("✅ Saqlandi.", reply_markup=admin_kb())

    # --- hamkor-adminlar
    def partner_list(doc):
        adm = (doc.get("settings") or {}).get("admins", {})
        rows = [[ib(f"👤 {u} — {'to‘liq' if 'all' in p else str(len(p)) + ' ruxsat'}", f"pa:{u}", c="b")]
                for u, p in adm.items()]
        rows.append([ib("➕ Admin qo'shish", "pa:add", c="g")])
        rows.append(CANCEL_ROW)
        return "👥 <b>Hamkor-adminlar</b>\n\nHar bir adminga qaysi funksiyalar ruxsat etilishini siz belgilaysiz.", kb(rows)

    def partner_panel(uid, perms):
        rows = [[ib(f"{'✅' if ('all' in perms or p in perms) else '⬜️'} {label}", f"pp:{uid}:{p}",
                    c="g" if ('all' in perms or p in perms) else "b")] for p, label in PERMS.items()]
        rows.append([ib("🗑 Adminni olib tashlash", f"pd:{uid}", c="r")])
        rows.append([ib("⬅️ Orqaga", "pl", c="r")])
        return f"👤 Admin <code>{uid}</code>\nRuxsatlarni belgilang:", kb(rows)

    async def act_partner(m, state, doc):
        text, mk = partner_list(doc)
        await m.answer(text, reply_markup=mk)

    @r.callback_query(F.data == "pl")
    async def pl(cb: CallbackQuery, doc):
        if cb.from_user.id != doc["owner"]:
            return await cb.answer("⛔", show_alert=True)
        text, mk = partner_list(await fresh())
        await show(cb, text, mk)

    @r.callback_query(F.data == "pa:add")
    async def pa_add(cb: CallbackQuery, state: FSMContext, doc):
        if cb.from_user.id != doc["owner"]:
            return await cb.answer("⛔", show_alert=True)
        await state.set_state(KS.partner)
        await show(cb, "Yangi admin Telegram ID sini yuboring:", kb([CANCEL_ROW]))

    @r.message(KS.partner, F.text)
    async def partner_add(m: Message, state: FSMContext, doc):
        if m.from_user.id != doc["owner"] or not m.text.strip().isdigit():
            return await m.answer("Faqat ID (raqam) yuboring.")
        await B.update_one({"_id": bid}, {"$set": {f"settings.admins.{int(m.text)}": DEFAULT_PERMS}})
        await state.clear()
        text, mk = partner_list(await fresh())
        await m.answer(text, reply_markup=mk)

    @r.callback_query(F.data.regexp(r"^pa:\d+$"))
    async def pa_open(cb: CallbackQuery, doc):
        if cb.from_user.id != doc["owner"]:
            return await cb.answer("⛔", show_alert=True)
        uid = cb.data[3:]
        perms = doc["settings"]["admins"].get(uid, [])
        text, mk = partner_panel(uid, perms)
        await show(cb, text, mk)

    @r.callback_query(F.data.regexp(r"^pp:\d+:\w+$"))
    async def pp_toggle(cb: CallbackQuery, doc):
        if cb.from_user.id != doc["owner"]:
            return await cb.answer("⛔", show_alert=True)
        _, uid, perm = cb.data.split(":")
        perms = [x for x in doc["settings"]["admins"].get(uid, []) if x != "all"]
        if perm in perms:
            perms.remove(perm)
        else:
            perms.append(perm)
        await B.update_one({"_id": bid}, {"$set": {f"settings.admins.{uid}": perms}})
        text, mk = partner_panel(uid, perms)
        await show(cb, text, mk)

    @r.callback_query(F.data.regexp(r"^pd:\d+$"))
    async def pd(cb: CallbackQuery, doc):
        if cb.from_user.id != doc["owner"]:
            return await cb.answer("⛔", show_alert=True)
        await B.update_one({"_id": bid}, {"$unset": {f"settings.admins.{cb.data[3:]}": ""}})
        text, mk = partner_list(await fresh())
        await show(cb, text, mk)

    async def act_full(m, state, doc):
        await state.set_state(KS.full)
        await m.answer("👑 To'liq admin qilinadigan (yoki olib tashlanadigan) foydalanuvchi ID sini yuboring:")

    @r.message(KS.full, F.text)
    async def full_toggle(m: Message, state: FSMContext, doc):
        if not m.text.strip().isdigit():
            return await m.answer("Faqat ID (raqam) yuboring.")
        uid = m.text.strip()
        cur = doc["settings"]["admins"].get(uid)
        await state.clear()
        if cur and "all" in cur:
            await B.update_one({"_id": bid}, {"$unset": {f"settings.admins.{uid}": ""}})
            return await m.answer("➖ To'liq admin olib tashlandi.", reply_markup=admin_kb())
        await B.update_one({"_id": bid}, {"$set": {f"settings.admins.{uid}": ["all"]}})
        await m.answer("👑 To'liq admin qo'shildi.", reply_markup=admin_kb())

    async def act_trial(m, state, doc):
        left = max(0, int((doc["expires"] - now()).total_seconds() // 3600))
        st = "✅ Pullik" if doc.get("paid") else "🎁 Sinov"
        await m.answer(f"🎁 <b>Sinov / Pullik</b>\n\nHolat: {st}\n🗓 Tugash: {doc['expires']:%d.%m.%Y %H:%M}\n"
                       f"⏳ Qoldi: {left // 24} kun {left % 24} soat\n\n"
                       f"Muddatni uzaytirish asosiy botdan amalga oshiriladi.",
                       reply_markup=kb([[ib("💳 Uzaytirish", url=f"https://t.me/{MAIN_USERNAME}?start=ext_{bid}", c="g")]]))

    async def act_ultra(m, state, doc):
        t0 = now().replace(hour=0, minute=0, second=0, microsecond=0)
        total = await KU.count_documents({"bot": bid})
        today = await KU.count_documents({"bot": bid, "joined": {"$gte": t0}})
        week = await KU.count_documents({"bot": bid, "joined": {"$gte": t0 - timedelta(days=7)}})
        act24 = await KU.count_documents({"bot": bid, "last": {"$gte": now() - timedelta(days=1)}})
        prem = await KU.count_documents({"bot": bid, "premium": {"$gt": now()}})
        blk = await KU.count_documents({"bot": bid, "blocked": True})
        movies = await M.count_documents({"bot": bid})
        views = 0
        async for x in M.aggregate([{"$match": {"bot": bid}}, {"$group": {"_id": None, "v": {"$sum": "$views"}}}]):
            views = x["v"]
        await m.answer(f"🚀 <b>Ultra statistika</b>\n\n👥 Jami: {total}\n🆕 Bugun: {today}\n📅 7 kunda: {week}\n"
                       f"🔥 24 soatda faol: {act24}\n💎 Premium: {prem}\n🚫 Bloklangan: {blk}\n"
                       f"{k['emoji']} {k['items'].capitalize()}: {movies}\n👁 Ko'rishlar: {views}")

    async def act_tref(m, state, doc):
        top = await KU.find({"bot": bid, "refs": {"$gt": 0}}).sort("refs", -1).limit(10).to_list(10)
        lines = "\n".join(f"{i + 1}. {esc(x.get('name'))} — {x['refs']} ta" for i, x in enumerate(top))
        await m.answer("🏆 <b>Top referal</b>\n\n" + (lines or "Hozircha yo'q."))

    async def act_tact(m, state, doc):
        top = await KU.find({"bot": bid}).sort("act", -1).limit(10).to_list(10)
        lines = "\n".join(f"{i + 1}. {esc(x.get('name'))} — {x.get('act', 0)} amal" for i, x in enumerate(top))
        await m.answer("🔝 <b>TOP faol foydalanuvchilar</b>\n\n" + (lines or "Hozircha yo'q."))

    # --- majburiy obuna
    def chan_view(doc):
        ch = (doc.get("settings") or {}).get("channels", [])
        lines = "\n".join(f"{i + 1}. {esc(c['title'])}" for i, c in enumerate(ch)) or "Kanal qo'shilmagan."
        rows = [[ib(f"🗑 {c['title']}"[:30], f"cd:{i}", c="r")] for i, c in enumerate(ch)]
        rows.append([ib("➕ Kanal qo'shish", "ca", c="g")])
        rows.append(CANCEL_ROW)
        return f"📢 <b>Majburiy obuna</b>\n\n{lines}\n\nBot kanalda admin bo'lishi shart.", kb(rows)

    async def act_chan(m, state, doc):
        text, mk = chan_view(doc)
        await m.answer(text, reply_markup=mk)

    @r.message(Command("channels"))
    async def channels_cmd(m: Message, doc):
        if can(doc, m.from_user.id, "channels"):
            text, mk = chan_view(doc)
            await m.answer(text, reply_markup=mk)

    @r.callback_query(F.data == "ca")
    async def ca(cb: CallbackQuery, state: FSMContext, doc):
        if not can(doc, cb.from_user.id, "channels"):
            return await cb.answer("⛔", show_alert=True)
        await state.set_state(KS.chan)
        await show(cb, "Kanal <b>@username</b> yoki ID sini yuboring (bot kanalda admin bo'lsin):", kb([CANCEL_ROW]))

    @r.message(KS.chan, F.text)
    async def chan_add(m: Message, state: FSMContext, doc):
        raw = m.text.strip()
        try:
            chat = await m.bot.get_chat(int(raw) if raw.lstrip("-").isdigit() else raw)
            me_m = await m.bot.get_chat_member(chat.id, m.bot.id)
            if me_m.status not in ("administrator", "creator"):
                return await m.answer("❌ Bot bu kanalda admin emas. Admin qiling va qayta yuboring.")
        except Exception:
            return await m.answer("❌ Kanal topilmadi yoki bot kanalda emas.")
        url = f"https://t.me/{chat.username}" if chat.username else None
        if not url:
            try:
                url = await m.bot.export_chat_invite_link(chat.id)
            except Exception:
                url = None
        await B.update_one({"_id": bid}, {"$push": {"settings.channels": {
            "id": chat.id, "title": chat.title or str(chat.id), "url": url}}})
        await state.clear()
        text, mk = chan_view(await fresh())
        await m.answer("✅ Qo'shildi.\n\n" + text, reply_markup=mk)

    @r.callback_query(F.data.regexp(r"^cd:\d+$"))
    async def cd(cb: CallbackQuery, doc):
        if not can(doc, cb.from_user.id, "channels"):
            return await cb.answer("⛔", show_alert=True)
        ch = doc["settings"]["channels"]
        i = int(cb.data[3:])
        if i < len(ch):
            ch.pop(i)
            await B.update_one({"_id": bid}, {"$set": {"settings.channels": ch}})
        text, mk = chan_view(await fresh())
        await show(cb, text, mk)

    # --- foydalanuvchi qidirish / bloklash
    async def act_find(m, state, doc):
        await state.set_state(KS.find)
        await m.answer("🔍 Foydalanuvchi ID sini yuboring:")

    @r.message(KS.find, F.text)
    async def find_user(m: Message, state: FSMContext):
        if not m.text.strip().isdigit():
            return await m.answer("Faqat ID (raqam) yuboring.")
        u = await KU.find_one({"bot": bid, "uid": int(m.text)})
        await state.clear()
        if not u:
            return await m.answer("❌ Topilmadi.")
        prem = f"{u['premium']:%d.%m.%Y}" if is_prem(u) else "yo'q"
        uid = u["uid"]
        await m.answer(f"👤 <b>{esc(u.get('name'))}</b>\n🆔 <code>{uid}</code>\n💎 Premium: {prem}\n"
                       f"💰 Balans: {money(u.get('balance', 0))}\n👥 Takliflar: {u.get('refs', 0)}\n"
                       f"🚫 Bloklangan: {'ha' if u.get('blocked') else 'yo‘q'}",
                       reply_markup=kb([[ib("➕ Balans", f"ui:bal:{uid}", c="g"), ib("💎 +30 kun", f"ui:prem:{uid}", c="b")],
                                        [ib("🚫 Blok/ochish", f"ui:blk:{uid}", c="r")]]))

    @r.callback_query(F.data.regexp(r"^ui:(bal|prem|blk):\d+$"))
    async def ui(cb: CallbackQuery, state: FSMContext, doc):
        if not can(doc, cb.from_user.id, "users"):
            return await cb.answer("⛔", show_alert=True)
        _, act, uid = cb.data.split(":")
        uid = int(uid)
        if act == "bal":
            await state.set_state(KS.bal)
            await state.update_data(uid=uid)
            await cb.message.answer("💰 Summani yuboring (kamaytirish uchun minus bilan):")
        elif act == "prem":
            await add_premium(bid, uid, 30)
            await cb.message.answer("✅ +30 kun Premium berildi.")
        else:
            u = await KU.find_one({"bot": bid, "uid": uid})
            await KU.update_one({"bot": bid, "uid": uid}, {"$set": {"blocked": not u.get("blocked")}})
            await cb.message.answer("🚫 Bloklandi." if not u.get("blocked") else "✅ Blokdan chiqarildi.")
        await cb.answer()

    @r.message(KS.bal, F.text)
    async def bal_amount(m: Message, state: FSMContext):
        try:
            amt = int(m.text.strip())
        except ValueError:
            return await m.answer("Faqat raqam yuboring.")
        uid = (await state.get_data())["uid"]
        await state.clear()
        await KU.update_one({"bot": bid, "uid": uid}, {"$inc": {"balance": amt}})
        await m.answer(f"✅ Balans o'zgartirildi: {amt:+,} so'm")

    async def act_block(m, state, doc):
        await state.set_state(KS.block)
        await m.answer("🚫 Bloklash/blokdan chiqarish uchun foydalanuvchi ID sini yuboring:")

    @r.message(KS.block, F.text)
    async def block_toggle(m: Message, state: FSMContext, doc):
        if not m.text.strip().isdigit():
            return await m.answer("Faqat ID (raqam) yuboring.")
        uid = int(m.text)
        u = await KU.find_one({"bot": bid, "uid": uid})
        await state.clear()
        if not u:
            return await m.answer("❌ Topilmadi.")
        if uid == doc["owner"]:
            return await m.answer("⛔ Egasini bloklab bo'lmaydi.")
        await KU.update_one({"bot": bid, "uid": uid}, {"$set": {"blocked": not u.get("blocked")}})
        await m.answer("🚫 Bloklandi." if not u.get("blocked") else "✅ Blokdan chiqarildi.", reply_markup=admin_kb())

    # --- xabar, zaxira, o'chirish
    async def act_bcast(m, state, doc):
        await state.set_state(KS.bcast)
        await m.answer("📣 Yuboriladigan xabarni yuboring (matn, rasm, video...).\n/cancel — bekor qilish")

    @r.message(KS.bcast)
    async def do_bcast(m: Message, state: FSMContext):
        await state.clear()
        ids = [x["uid"] async for x in KU.find({"bot": bid, "blocked": {"$ne": True}}, {"uid": 1})]
        status = await m.answer(f"⏳ Yuborilmoqda... ({len(ids)})")
        ok = bad = 0
        for uid in ids:
            try:
                await m.copy_to(uid)
                ok += 1
            except Exception:
                bad += 1
            await asyncio.sleep(0.05)
        await status.edit_text(f"✅ Yuborildi: {ok}\n❌ Yetmadi: {bad}")

    async def act_backup(m, state, doc):
        data = {"bot": {x: v for x, v in doc.items() if x != "token"},
                "movies": await M.find({"bot": bid}).to_list(None),
                "users": await KU.find({"bot": bid}).to_list(None)}
        raw = bson_dumps(data, ensure_ascii=False, indent=1).encode()
        await m.answer_document(BufferedInputFile(raw, f"backup_{doc['username']}_{now():%Y%m%d}.json"),
                                caption="📥 Zaxira nusxa")

    async def act_delbot(m, state, doc):
        await m.answer("⚠️ Botni butunlay o'chirasizmi? Barcha kontent va foydalanuvchilar o'chadi!",
                       reply_markup=kb([[ib("🗑 Ha, o'chirish", "db:yes", c="r"), ib("Yo'q", "x", c="g")]]))

    @r.callback_query(F.data == "db:yes")
    async def db_yes(cb: CallbackQuery, doc):
        if cb.from_user.id != doc["owner"]:
            return await cb.answer("⛔", show_alert=True)
        await show(cb, "🗑 Bot o'chirilmoqda...")

        async def later():
            await asyncio.sleep(1)
            await purge_bot(bid)
        asyncio.create_task(later())

    ACTIONS = {"add": act_add, "list": act_list, "tar": act_tar, "pay": act_pay, "stars": act_stars,
               "partner": act_partner, "trial": act_trial, "ultra": act_ultra, "tref": act_tref,
               "tact": act_tact, "chan": act_chan, "find": act_find, "block": act_block,
               "delbot": act_delbot, "bcast": act_bcast, "backup": act_backup, "full": act_full}
    TEXT2KEY = {v: key for key, v in A.items() if key in ACTIONS}

    @r.message(F.text.in_(set(TEXT2KEY)))
    async def admin_btn(m: Message, state: FSMContext, doc):
        key = TEXT2KEY[m.text]
        uid = m.from_user.id
        ok = (uid == doc["owner"]) if key in OWNER_ONLY else can(doc, uid, PERM[key])
        if not ok:
            return await m.answer("⛔ Sizda bu funksiyaga ruxsat yo'q.")
        await ACTIONS[key](m, state, doc)

    # --- kod yoki nom bo'yicha qidirish (eng oxirida)
    @r.message(StateFilter(None), F.text.regexp(r"^\d{1,9}$"))
    async def by_code(m: Message, doc, ku):
        await deliver(m.bot, m.from_user.id, int(m.text), doc, ku)

    @r.message(StateFilter(None), F.text, ~F.text.startswith("/"))
    async def by_name(m: Message):
        await do_search(m, m.text)

    return r


# ============================================================================
#  BUILDERBOT (farzand bot: o'z foydalanuvchilariga bot yaratib beradi)
# ============================================================================
class BS(StatesGroup):
    token = State()


def make_builder_router(bid):
    r = Router()
    BT = {"create": "➕ Bot yaratish", "mine": "🤖 Botlarim", "guide": "📖 Qo'llanma", "stat": "📊 Statistika"}
    r.message.outer_middleware(MenuClear(BT.values()))
    mw = ChildMw(bid)
    r.message.outer_middleware(mw)
    r.callback_query.outer_middleware(mw)

    def menu(doc, uid):
        rows = [[BT["create"], BT["mine"]], [BT["guide"]]]
        if uid == doc["owner"]:
            rows.append([BT["stat"]])
        return rk(rows)

    @r.callback_query(F.data == "x")
    async def cancel_cb(cb: CallbackQuery, state: FSMContext):
        await state.clear()
        await show(cb, "❌ Bekor qilindi.")

    @r.message(CommandStart())
    async def start(m: Message, state: FSMContext, doc):
        await state.clear()
        await m.answer(f"👋 <b>{esc(doc['name'])}</b> ga xush kelibsiz!\n\nBu yerda o'zingizga tayyor bot yaratishingiz mumkin.",
                       reply_markup=menu(doc, m.from_user.id))

    @r.message(F.text == BT["create"])
    async def create(m: Message):
        items = [(f"{v['emoji']} {v['title']}", f"bk:{key}") for key, v in KINDS.items()]
        await m.answer("📋 Qaysi turdagi bot yaratmoqchisiz?", reply_markup=kb(cgrid(items) + [CANCEL_ROW]))

    @r.callback_query(F.data.startswith("bk:"))
    async def pick(cb: CallbackQuery, state: FSMContext, doc):
        kind = cb.data[3:]
        if kind not in KINDS:
            return await cb.answer()
        used = await B.count_documents({"builder_of": bid})
        if used >= doc["limit"]:
            return await cb.answer("❌ Bot limiti tugagan.", show_alert=True)
        await state.set_state(BS.token)
        await state.update_data(kind=kind)
        await show(cb, f"{KINDS[kind]['emoji']} <b>{KINDS[kind]['title']}</b>\n\n@BotFather orqali /newbot yozib "
                       f"bot oching va tokenini shu yerga yuboring:", kb([CANCEL_ROW]))

    @r.message(BS.token, F.text)
    async def got_token(m: Message, state: FSMContext, doc):
        kind = (await state.get_data())["kind"]
        try:
            await m.delete()
        except Exception:
            pass
        me = await check_token(m, m.text)
        if not me:
            return
        if await B.count_documents({"builder_of": bid}) >= doc["limit"]:
            await state.clear()
            return await m.answer("❌ Bot limiti tugagan.")
        await state.clear()
        d = await create_bot_doc(m.from_user.id, me, m.text.strip(), kind, "Turbo", doc["expires"], builder_of=bid)
        await m.answer(success_text(d), reply_markup=open_kb(d))

    @r.message(F.text == BT["mine"])
    async def mine(m: Message):
        bots = await B.find({"builder_of": bid, "owner": m.from_user.id}).to_list(50)
        if not bots:
            return await m.answer("Sizda hali bot yo'q.")
        rows = [[ib(f"{KINDS[b['kind']]['emoji']} @{b['username']}", url=f"https://t.me/{b['username']}", c="b")]
                for b in bots]
        await m.answer("🤖 <b>Botlarim</b>", reply_markup=kb(rows))

    @r.message(F.text == BT["guide"])
    async def guide(m: Message):
        await m.answer("📖 <b>Qo'llanma</b>\n\n1. «Bot yaratish» ni bosing.\n2. Bot turini tanlang.\n"
                       "3. @BotFather dan olingan tokenni yuboring.\n4. Bot ishga tushadi — admin panel o'sha botda.")

    @r.message(F.text == BT["stat"])
    async def stat(m: Message, doc):
        if m.from_user.id != doc["owner"]:
            return
        n = await B.count_documents({"builder_of": bid})
        u = await KU.count_documents({"bot": bid})
        await m.answer(f"📊 <b>Statistika</b>\n\n👥 Foydalanuvchilar: {u}\n🤖 Yaratilgan botlar: {n} / {doc['limit']}\n"
                       f"🗓 Muddati: {doc['expires']:%d.%m.%Y} gacha")

    return r


# ============================================================================
#  ASOSIY BOT
# ============================================================================
class Create(StatesGroup):
    token = State()


class Pay(StatesGroup):
    amount = State()
    receipt = State()


class Adm(StatesGroup):
    add = State()
    sub = State()
    bcast = State()
    ban = State()


M_CREATE, M_MINE = "➕ Bot yaratish", "🤖 Botlarim"
M_PAYIN, M_ACC = "💬 Pul kiritish", "👛 Hisobim"
M_REF, M_GUIDE, M_SUP, M_WEB = "🗣 Referal", "📖 Qo'llanma", "☎️ Qo'llab-quvvatlash", "🌐 Saytga kirish"
M_STAT, M_BC = "📊 Statistika", "📢 Barchaga xabar"
M_ADDB, M_SUBB = "➕ Hisob qo'shish", "➖ Hisob ayirish"
M_EXP, M_BAN = "📅 Tugayotgan botlar", "🚫 Bloklash"
MAIN_TEXTS = [M_CREATE, M_MINE, M_PAYIN, M_ACC, M_REF, M_GUIDE, M_SUP, M_WEB, M_STAT, M_BC, M_ADDB, M_SUBB, M_EXP, M_BAN]

mr = Router()


class MainMw(BaseMiddleware):
    async def __call__(self, handler, event, data):
        u = getattr(event, "from_user", None)
        if u and not (u.id in OWNERS):
            if await U.find_one({"_id": u.id, "banned": True}):
                return
        return await handler(event, data)


mr.message.outer_middleware(MenuClear(MAIN_TEXTS))
mr.message.outer_middleware(MainMw())
mr.callback_query.outer_middleware(MainMw())


def main_kb(uid):
    row3 = [M_REF, M_GUIDE]
    rows = [[M_CREATE, M_MINE], [M_PAYIN, M_ACC], row3, [M_SUP] + ([M_WEB] if WEB_URL else [])]
    if uid in OWNERS:
        rows += [[M_STAT, M_BC], [M_ADDB, M_SUBB], [M_EXP, M_BAN]]
    return rk(rows)


WELCOME = ("⚡ <b>Bot yaratish — kod yozmasdan!</b>\n\n"
           "• Tez va oson — o'z botingizni bir necha daqiqada yarating.\n"
           "• Qulay interfeys — hammasi tugmalar orqali.\n"
           "• Yaxshi qo'llab-quvvatlash — doimiy va tezkor xizmat!")


@mr.message(CommandStart())
async def m_start(m: Message, command: CommandObject, state: FSMContext):
    await state.clear()
    u = m.from_user
    arg = command.args or ""
    if not await U.find_one({"_id": u.id}):
        d = {"_id": u.id, "name": u.full_name, "username": u.username, "balance": 0, "refs": 0,
             "joined": now(), "banned": False}
        if arg.startswith("ref_") and arg[4:].isdigit() and int(arg[4:]) != u.id:
            ref = int(arg[4:])
            if await U.find_one({"_id": ref}):
                d["ref_by"] = ref
                await U.update_one({"_id": ref}, {"$inc": {"refs": 1, "balance": REF_BONUS}})
                await tell(ref, f"🎉 Yangi do'st qo'shildi! Balansingizga +{money(REF_BONUS)} so'm berildi.")
        await U.insert_one(d)
    await m.answer(WELCOME, reply_markup=main_kb(u.id))
    if arg.startswith("ext_") and arg[4:].isdigit():
        b = await B.find_one({"_id": int(arg[4:]), "owner": u.id})
        if b:
            text, mk = bot_card(b)
            await m.answer(text, reply_markup=mk)


@mr.callback_query(F.data == "x")
async def m_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(cb, "❌ Bekor qilindi.")


@mr.callback_query(F.data == "soon")
async def soon(cb: CallbackQuery):
    await cb.answer("🛠 Bu bot tez orada qo'shiladi.", show_alert=True)


# ------------------------------------------------------------- bot yaratish
def type_kb():
    return kb([[ib("🤖 Telegram Bot", "new:tg", c="b"), ib("🛡 BuilderBot", "new:bb", c="g")], CANCEL_ROW])


TYPE_TEXT = ("🤖 <b>Nima yaratmoqchisiz?</b>\n\n"
             "🤖 <b>Telegram Bot</b> — kino, multfilm, anime, drama va boshqa tayyor botlar.\n"
             "🛡 <b>BuilderBot</b> — o'z foydalanuvchilaringizga bot yaratib beruvchi shaxsiy bot.\n\n"
             "Birini tanlang: 👇")


@mr.message(F.text == M_CREATE)
async def create_menu(m: Message):
    await m.answer(TYPE_TEXT, reply_markup=type_kb())


@mr.callback_query(F.data == "new")
async def new_cb(cb: CallbackQuery):
    await show(cb, TYPE_TEXT, type_kb())


@mr.callback_query(F.data == "new:tg")
async def new_tg(cb: CallbackQuery):
    rows = cgrid([(t, f"cat:{key}") for key, t in CATEGORIES])
    rows.append([ib("⬅️ Orqaga", "new", c="r")])
    await show(cb, "🗂 Quyidagi bo'limlardan birini tanlang:", kb(rows))


@mr.callback_query(F.data.startswith("cat:"))
async def cat_cb(cb: CallbackQuery):
    key = cb.data[4:]
    items = CAT_BOTS.get(key)
    back = [ib("⬅️ Orqaga", "new:tg", c="r")]
    if not items:
        return await show(cb, "🛠 Bu bo'limga botlar tez orada qo'shiladi.", kb([back]))
    rows = cgrid([(t, f"mk:{kd}" if kd in KINDS else "soon") for kd, t in items])
    rows.append(back)
    await show(cb, "📋 Quyidagi botlardan birini tanlang:", kb(rows))


@mr.callback_query(F.data.startswith("mk:"))
async def mk_cb(cb: CallbackQuery, state: FSMContext):
    kind = cb.data[3:]
    if kind not in KINDS:
        return await cb.answer()
    kd = KINDS[kind]
    await state.set_state(Create.token)
    await state.update_data(kind=kind)
    await show(cb, f"{kd['emoji']} <b>{kd['title']}</b>\n\n💠 Tarif: ⚡ Turbo — {money(KINO_PRICE)} so'm/oy\n"
                   f"🎁 {TRIAL_DAYS} kunlik bepul sinov\n\n@BotFather ga kiring → /newbot → olingan <b>tokenni</b> "
                   f"shu yerga yuboring:", kb([[ib("⬅️ Orqaga", "cat:media", c="r")]]))


def tariff_text():
    parts = [f"🛡 <b>BuilderBot</b>\n\n🔄 Bot yaratish narxi: <b>{money(BUILDER_FEE)} so'm</b> (bir marta)\n\n"
             f"📋 <b>Tarif rejalar ro'yxati:</b>\n"]
    for n, lim, pr in TARIFFS:
        parts.append(f"\n📄 Tarif: <b>{n}</b>\n⚡ Tezlik: 10x\n🗓 Muddati: 30 kun\n🤖 Bot limiti: {lim} ta bot\n"
                     f"👛 Narxi: {money(pr)} so'm ({money(pr // 30)} so'm/kun)\n")
    parts.append("\nTarifni tanlang 👇")
    return "".join(parts)


@mr.callback_query(F.data == "new:bb")
async def new_bb(cb: CallbackQuery):
    rows = cgrid([(n, f"bbt:{n}") for n, _, _ in TARIFFS])
    rows.append([ib("⬅️ Orqaga", "new", c="r")])
    await show(cb, tariff_text(), kb(rows))


@mr.callback_query(F.data.startswith("bbt:"))
async def bbt(cb: CallbackQuery, state: FSMContext):
    t = next((x for x in TARIFFS if x[0] == cb.data[4:]), None)
    if not t:
        return await cb.answer()
    n, lim, pr = t
    total = BUILDER_FEE + pr
    u = await U.find_one({"_id": cb.from_user.id})
    if u["balance"] < total:
        return await cb.answer(f"❌ Balans yetarli emas. Kerak: {money(total)} so'm, sizda: {money(u['balance'])} so'm. "
                               f"«{M_PAYIN}» orqali to'ldiring.", show_alert=True)
    await state.set_state(Create.token)
    await state.update_data(kind="builder", tname=n, limit=lim, total=total)
    await show(cb, f"🛡 <b>BuilderBot — {n}</b>\n🤖 Limit: {lim} ta bot\n💰 To'lov: {money(total)} so'm "
                   f"(yaratish {money(BUILDER_FEE)} + tarif {money(pr)})\n\n@BotFather dan yangi bot oching va "
                   f"<b>tokenni</b> yuboring:", kb([[ib("⬅️ Orqaga", "new:bb", c="r")]]))


@mr.message(Create.token, F.text)
async def got_token(m: Message, state: FSMContext):
    d = await state.get_data()
    kind = d["kind"]
    try:
        await m.delete()
    except Exception:
        pass
    me = await check_token(m, m.text)
    if not me:
        return
    token = m.text.strip()
    uid = m.from_user.id
    if kind == "builder":
        ok = await U.find_one_and_update({"_id": uid, "balance": {"$gte": d["total"]}}, {"$inc": {"balance": -d["total"]}})
        if not ok:
            await state.clear()
            return await m.answer("❌ Balans yetarli emas.")
        await state.clear()
        try:
            doc = await create_bot_doc(uid, me, token, "builder", d["tname"], now() + timedelta(days=30),
                                       limit=d["limit"])
            await B.update_one({"_id": me.id}, {"$set": {"paid": True}})
        except Exception:
            await U.update_one({"_id": uid}, {"$inc": {"balance": d["total"]}})
            log.exception("builder yaratilmadi")
            return await m.answer("❌ Xatolik yuz berdi, mablag' qaytarildi.")
    else:
        await state.clear()
        doc = await create_bot_doc(uid, me, token, kind, "Turbo", now() + timedelta(days=TRIAL_DAYS))
    await m.answer(success_text(doc), reply_markup=open_kb(doc))


# ------------------------------------------------------------- botlarim
def status_icon(b):
    return {"active": "🟢", "expired": "⏸", "token_invalid": "⚠️"}.get(b["status"], "❔")


def bot_card(b):
    left = max(0, int((b["expires"] - now()).total_seconds() // 86400))
    kd = KINDS.get(b["kind"], {"emoji": "🛡", "title": "BuilderBot"})
    extra = ""
    if b["kind"] == "builder":
        extra = f"\n🤖 Limit: {b['limit']} ta"
    price = KINO_PRICE if b["kind"] in KINDS else next((p for n, _, p in TARIFFS if n == b["tariff"]), 0)
    text = (f"{kd['emoji']} <b>{kd['title']}</b> — @{b['username']}\n{status_icon(b)} Holat: {b['status']}\n"
            f"💠 Tarif: {esc(b['tariff'])}{extra}\n🗓 Tugash: {b['expires']:%d.%m.%Y} ({left} kun)\n"
            f"{'✅ Pullik' if b.get('paid') else '🎁 Sinov'}")
    rows = [[ib(f"🔄 30 kunga uzaytirish ({money(price)})", f"bx:{b['_id']}", c="g")],
            [ib("🤖 Botga o'tish", url=f"https://t.me/{b['username']}", c="b")],
            [ib("🗑 O'chirish", f"bd:{b['_id']}", c="r"), ib("⬅️ Orqaga", "mybots", c="r")]]
    return text, kb(rows)


async def my_bots_view(uid):
    bots = await B.find({"owner": uid, "builder_of": None}).to_list(100)
    if not bots:
        return "🤖 Sizda hali bot yo'q. «➕ Bot yaratish» ni bosing.", None
    rows = [[ib(f"{status_icon(b)} {KINDS.get(b['kind'], {'emoji': '🛡'})['emoji']} @{b['username']}", f"bot:{b['_id']}", c="b")]
            for b in bots]
    return "🤖 <b>Botlarim</b>", kb(rows)


@mr.message(F.text == M_MINE)
async def my_bots(m: Message):
    text, mk = await my_bots_view(m.from_user.id)
    await m.answer(text, reply_markup=mk)


@mr.callback_query(F.data == "mybots")
async def mybots_cb(cb: CallbackQuery):
    text, mk = await my_bots_view(cb.from_user.id)
    await show(cb, text, mk)


@mr.callback_query(F.data.regexp(r"^bot:\d+$"))
async def bot_open(cb: CallbackQuery):
    b = await B.find_one({"_id": int(cb.data[4:]), "owner": cb.from_user.id})
    if not b:
        return await cb.answer("Topilmadi", show_alert=True)
    text, mk = bot_card(b)
    await show(cb, text, mk)


async def extend_bot(b, uid):
    price = KINO_PRICE if b["kind"] in KINDS else next((p for n, _, p in TARIFFS if n == b["tariff"]), KINO_PRICE)
    ok = await U.find_one_and_update({"_id": uid, "balance": {"$gte": price}}, {"$inc": {"balance": -price}})
    if not ok:
        return False, price
    new_exp = max(now(), b["expires"]) + timedelta(days=30)
    upd = {"$set": {"expires": new_exp, "status": "active", "paid": True}, "$unset": {"expired_at": "", "warned": ""}}
    await B.update_one({"_id": b["_id"]}, upd)
    if b["kind"] == "builder":
        await B.update_many({"builder_of": b["_id"]},
                            {"$set": {"expires": new_exp, "status": "active"}, "$unset": {"expired_at": "", "warned": ""}})
    ids = [b["_id"]] + [c["_id"] async for c in B.find({"builder_of": b["_id"]}, {"_id": 1})]
    for i in ids:
        d = await B.find_one({"_id": i})
        if d:
            await start_child(d)
    return True, price


@mr.callback_query(F.data.regexp(r"^bx:\d+$"))
async def bot_extend(cb: CallbackQuery):
    b = await B.find_one({"_id": int(cb.data[3:]), "owner": cb.from_user.id})
    if not b:
        return await cb.answer("Topilmadi", show_alert=True)
    ok, price = await extend_bot(b, cb.from_user.id)
    if not ok:
        return await cb.answer(f"❌ Balans yetarli emas. Kerak: {money(price)} so'm. «{M_PAYIN}» orqali to'ldiring.",
                               show_alert=True)
    b = await B.find_one({"_id": b["_id"]})
    text, mk = bot_card(b)
    await show(cb, "✅ Muddat 30 kunga uzaytirildi.\n\n" + text, mk)


@mr.callback_query(F.data.regexp(r"^bd:\d+$"))
async def bot_del(cb: CallbackQuery):
    bid = cb.data[3:]
    await show(cb, "⚠️ Botni butunlay o'chirasizmi? Bu qaytarilmaydi!",
               kb([[ib("🗑 Ha, o'chirish", f"bdy:{bid}", c="r"), ib("Yo'q", f"bot:{bid}", c="g")]]))


@mr.callback_query(F.data.regexp(r"^bdy:\d+$"))
async def bot_del_yes(cb: CallbackQuery):
    b = await B.find_one({"_id": int(cb.data[4:]), "owner": cb.from_user.id})
    if not b:
        return await cb.answer("Topilmadi", show_alert=True)
    await purge_bot(b["_id"])
    text, mk = await my_bots_view(cb.from_user.id)
    await show(cb, "🗑 Bot o'chirildi.\n\n" + text, mk)


# ------------------------------------------------------------- hisob, pul, referal
@mr.message(F.text == M_ACC)
async def account(m: Message):
    u = await U.find_one({"_id": m.from_user.id})
    n = await B.count_documents({"owner": m.from_user.id})
    await m.answer(f"👛 <b>Hisobim</b>\n\n🆔 ID: <code>{m.from_user.id}</code>\n💰 Balans: <b>{money(u['balance'])} so'm</b>\n"
                   f"🤖 Botlar: {n} ta\n👥 Takliflar: {u.get('refs', 0)} ta")


@mr.message(F.text == M_PAYIN)
async def pay_in(m: Message, state: FSMContext):
    await state.set_state(Pay.amount)
    await m.answer(f"{PAY_INFO}\n\n💰 To'lamoqchi bo'lgan summani yuboring (so'm, kamida 1000):",
                   reply_markup=kb([CANCEL_ROW]))


@mr.message(Pay.amount, F.text)
async def pay_amount(m: Message, state: FSMContext):
    t = m.text.replace(" ", "").replace(",", "")
    if not t.isdigit() or int(t) < 1000:
        return await m.answer("❌ Kamida 1000 so'm, faqat raqam yuboring.")
    await state.update_data(amount=int(t))
    await state.set_state(Pay.receipt)
    await m.answer(f"🧾 {money(int(t))} so'm to'lovning <b>chek rasmini</b> yuboring:", reply_markup=kb([CANCEL_ROW]))


@mr.message(Pay.receipt, F.photo)
async def pay_receipt(m: Message, state: FSMContext):
    amount = (await state.get_data())["amount"]
    await state.clear()
    t = await TX.insert_one({"kind": "topup", "uid": m.from_user.id, "amount": amount, "status": "pending",
                             "created": now()})
    oid = str(t.inserted_id)
    cap = (f"💳 <b>To'lov so'rovi</b>\n👤 {esc(m.from_user.full_name)} (<code>{m.from_user.id}</code>)\n"
           f"💰 {money(amount)} so'm")
    for o in OWNERS:
        try:
            await m.bot.send_photo(o, m.photo[-1].file_id, caption=cap,
                                   reply_markup=kb([[ib("✅ Tasdiqlash", f"pay:ok:{oid}", c="g"),
                                                     ib("❌ Rad etish", f"pay:no:{oid}", c="r")]]))
        except Exception:
            pass
    await m.answer("✅ Chek yuborildi. Admin tasdiqlagach hisobingizga tushadi.")


@mr.callback_query(F.data.regexp(r"^pay:(ok|no):[0-9a-f]{24}$"))
async def pay_decide(cb: CallbackQuery):
    if cb.from_user.id not in OWNERS:
        return await cb.answer("⛔", show_alert=True)
    _, act, oid = cb.data.split(":")
    t = await TX.find_one_and_update({"_id": ObjectId(oid), "status": "pending"}, {"$set": {"status": act}})
    if not t:
        return await cb.answer("Allaqachon ko'rib chiqilgan.", show_alert=True)
    if act == "ok":
        await U.update_one({"_id": t["uid"]}, {"$inc": {"balance": t["amount"]}})
        await tell(t["uid"], f"✅ Hisobingiz {money(t['amount'])} so'mga to'ldirildi!")
    else:
        await tell(t["uid"], "❌ To'lov rad etildi. Savol bo'lsa qo'llab-quvvatlashga yozing.")
    mark = "\n\n✅ Tasdiqlandi" if act == "ok" else "\n\n❌ Rad etildi"
    await cb.message.edit_caption(caption=cb.message.html_text + mark, reply_markup=None)
    await cb.answer()


@mr.message(F.text == M_REF)
async def referral(m: Message):
    u = await U.find_one({"_id": m.from_user.id})
    await m.answer(f"🗣 <b>Referal dasturi</b>\n\nHar bir taklif qilingan do'st uchun <b>{money(REF_BONUS)} so'm</b> olasiz.\n\n"
                   f"🔗 Havolangiz:\nhttps://t.me/{MAIN_USERNAME}?start=ref_{m.from_user.id}\n\n"
                   f"👥 Takliflar: {u.get('refs', 0)} ta")


@mr.message(F.text == M_GUIDE)
async def guide(m: Message):
    await m.answer("📖 <b>Qo'llanma</b>\n\n1. «💬 Pul kiritish» orqali hisobni to'ldiring (kino bot uchun shart emas — "
                   f"{TRIAL_DAYS} kun bepul sinov).\n2. «➕ Bot yaratish» → Telegram Bot → Media botlar → bot turini tanlang.\n"
                   "3. @BotFather dan olingan tokenni yuboring.\n4. Bot ishga tushadi, admin panel o'sha botda: /admin\n"
                   "5. Muddatni «🤖 Botlarim» orqali uzaytiring.\n\n"
                   f"⚠️ Sinov tugagach to'lanmasa bot to'xtaydi, yana {GRACE_DAYS} kundan keyin butunlay o'chiriladi.")


@mr.message(F.text == M_SUP)
async def support(m: Message):
    if SUPPORT:
        await m.answer("☎️ Qo'llab-quvvatlash:", reply_markup=kb([[ib("✍️ Yozish", url=f"https://t.me/{SUPPORT}", c="b")]]))
    else:
        await m.answer("☎️ Qo'llab-quvvatlash hozircha sozlanmagan.")


@mr.message(F.text == M_WEB)
async def web(m: Message):
    if WEB_URL:
        await m.answer("🌐 Sayt:", reply_markup=kb([[ib("🌐 Ochish", url=WEB_URL, c="b")]]))


# ------------------------------------------------------------- platforma admini
own = F.from_user.id.in_(OWNERS)


@mr.message(F.text == M_STAT, own)
async def a_stat(m: Message):
    users = await U.count_documents({})
    act = await B.count_documents({"status": "active"})
    exp = await B.count_documents({"status": "expired"})
    parts = [f"{kd['emoji']} {kd['title']}: {await B.count_documents({'kind': key})}" for key, kd in KINDS.items()]
    parts.append(f"🛡 BuilderBot: {await B.count_documents({'kind': 'builder'})}")
    nl = "\n".join(parts)
    await m.answer(f"📊 <b>Statistika</b>\n\n👥 Foydalanuvchilar: {users}\n🟢 Faol botlar: {act}\n⏸ Muddati o'tgan: {exp}\n"
                   f"🧾 Ishlayotgan polling: {sum(1 for t in RUNNING.values() if not t.done())}\n\n{nl}")


@mr.message(F.text == M_ADDB, own)
async def a_add(m: Message, state: FSMContext):
    await state.set_state(Adm.add)
    await m.answer("➕ Format: <code>ID SUMMA</code>  (masalan: 123456 50000)")


@mr.message(F.text == M_SUBB, own)
async def a_sub(m: Message, state: FSMContext):
    await state.set_state(Adm.sub)
    await m.answer("➖ Format: <code>ID SUMMA</code>")


async def _balance_change(m, state, sign):
    p = m.text.split()
    await state.clear()
    if len(p) != 2 or not p[0].isdigit() or not p[1].isdigit():
        return await m.answer("❌ Format noto'g'ri.")
    uid, amt = int(p[0]), int(p[1]) * sign
    q = {"_id": uid} if sign > 0 else {"_id": uid, "balance": {"$gte": -amt}}
    res = await U.update_one(q, {"$inc": {"balance": amt}})
    if not res.matched_count:
        return await m.answer("❌ Foydalanuvchi topilmadi yoki balans yetarli emas.")
    u = await U.find_one({"_id": uid})
    await m.answer(f"✅ Bajarildi. Yangi balans: {money(u['balance'])} so'm")
    await tell(uid, f"{'➕' if sign > 0 else '➖'} Balansingiz o'zgardi: {amt:+,} so'm")


@mr.message(Adm.add, F.text, own)
async def a_add_do(m: Message, state: FSMContext):
    await _balance_change(m, state, 1)


@mr.message(Adm.sub, F.text, own)
async def a_sub_do(m: Message, state: FSMContext):
    await _balance_change(m, state, -1)


@mr.message(F.text == M_BC, own)
async def a_bc(m: Message, state: FSMContext):
    await state.set_state(Adm.bcast)
    await m.answer("📢 Hammaga yuboriladigan xabarni yuboring:")


@mr.message(Adm.bcast, own)
async def a_bc_do(m: Message, state: FSMContext):
    await state.clear()
    ids = [x["_id"] async for x in U.find({"banned": {"$ne": True}}, {"_id": 1})]
    status = await m.answer(f"⏳ Yuborilmoqda... ({len(ids)})")
    ok = bad = 0
    for uid in ids:
        try:
            await m.copy_to(uid)
            ok += 1
        except Exception:
            bad += 1
        await asyncio.sleep(0.05)
    await status.edit_text(f"✅ Yuborildi: {ok}\n❌ Yetmadi: {bad}")


@mr.message(F.text == M_EXP, own)
async def a_exp(m: Message):
    soon_ = now() + timedelta(days=3)
    bots = await B.find({"$or": [{"status": "expired"}, {"status": "active", "expires": {"$lt": soon_}}]}) \
        .sort("expires", 1).limit(40).to_list(40)
    if not bots:
        return await m.answer("📅 Tugayotgan botlar yo'q.")
    lines = "\n".join(f"{status_icon(b)} @{b['username']} — {b['expires']:%d.%m %H:%M} (egasi <code>{b['owner']}</code>)"
                      for b in bots)
    await m.answer(f"📅 <b>Tugayotgan / tugagan botlar</b>\n\n{lines}")


@mr.message(F.text == M_BAN, own)
async def a_ban(m: Message, state: FSMContext):
    await state.set_state(Adm.ban)
    await m.answer("🚫 Bloklash/blokdan chiqarish uchun foydalanuvchi ID sini yuboring:")


@mr.message(Adm.ban, F.text, own)
async def a_ban_do(m: Message, state: FSMContext):
    await state.clear()
    if not m.text.strip().isdigit():
        return await m.answer("Faqat ID yuboring.")
    uid = int(m.text)
    u = await U.find_one({"_id": uid})
    if not u or uid in OWNERS:
        return await m.answer("❌ Topilmadi.")
    await U.update_one({"_id": uid}, {"$set": {"banned": not u.get("banned")}})
    await m.answer("🚫 Bloklandi." if not u.get("banned") else "✅ Blokdan chiqarildi.")


# ============================================================================
#  MUDDAT NAZORATI (sinov / to'lov)
# ============================================================================
async def watcher():
    while True:
        try:
            t = now()
            # 1) muddati tugaganlarni to'xtatish
            async for b in B.find({"status": "active", "expires": {"$lt": t}}):
                await B.update_one({"_id": b["_id"]}, {"$set": {"status": "expired", "expired_at": t}})
                await stop_child(b["_id"])
                await tell(b["owner"], f"⏸ @{b['username']} boti muddati tugagani uchun to'xtatildi.\n"
                                       f"«🤖 Botlarim» orqali uzaytiring, aks holda {GRACE_DAYS} kundan keyin "
                                       f"butunlay o'chiriladi.")
            # 2) 1 kun qolganda ogohlantirish
            async for b in B.find({"status": "active", "warned": {"$ne": True},
                                   "expires": {"$lt": t + timedelta(days=1)}}):
                await B.update_one({"_id": b["_id"]}, {"$set": {"warned": True}})
                await tell(b["owner"], f"⚠️ @{b['username']} boti muddati 24 soatdan kam qoldi. "
                                       f"«🤖 Botlarim» orqali uzaytiring.")
            # 3) to'lanmagan va grace tugaganlarni o'chirish
            async for b in B.find({"status": "expired", "expired_at": {"$lt": t - timedelta(days=GRACE_DAYS)}}):
                await tell(b["owner"], f"🗑 @{b['username']} boti to'lanmagani sababli dasturdan o'chirildi.")
                await purge_bot(b["_id"])
        except Exception:
            log.exception("watcher xatosi")
        await asyncio.sleep(900)


# ============================================================================
#  ISHGA TUSHIRISH
# ============================================================================
async def main():
    global MAIN, MAIN_USERNAME
    if not BOT_TOKEN or not MONGO_URI:
        raise SystemExit("BOT_TOKEN va MONGO_URI o'zgaruvchilari kerak!")
    await KU.create_index([("bot", 1), ("uid", 1)], unique=True)
    await M.create_index([("bot", 1), ("code", 1)], unique=True)
    await M.create_index([("bot", 1), ("added", -1)])
    await B.create_index([("owner", 1)])

    MAIN = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    MAIN_USERNAME = (await MAIN.get_me()).username
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(mr)

    n = 0
    async for d in B.find({"status": "active"}):
        await start_child(d)
        n += 1
    log.info("%s ta farzand bot ishga tushirildi", n)
    asyncio.create_task(watcher())

    await MAIN.delete_webhook()
    await dp.start_polling(MAIN, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
