"""پیام‌های وضعیت و خطا — تنها منبع ساخت پیام‌های کاربر.

قاعده‌ی تأییدشده: هر خطا سه چیز می‌گوید — **چه شد، چرا، چه کار کنم** — و
دکمه‌ی راه‌حل دارد.

قاعده‌ی دوم: **خطای خام پایتون هرگز به کاربر نشان داده نمی‌شود.** کاربر یک
کد خطای کوتاه می‌بیند که می‌تواند به پشتیبانی بدهد؛ متن کامل استثنا فقط در
لاگ ثبت می‌شود. نسخه‌ی قبلی چند جا `f"❌ خطا در پخش:\\n{e}"` می‌فرستاد و
کاربر فارسی‌زبان یک traceback انگلیسی می‌دید.
"""
from __future__ import annotations

import hashlib
from typing import Optional

from pyrogram.types import InlineKeyboardMarkup

from bot import ui


def error_code(exc: BaseException | str) -> str:
    """کد خطای کوتاه و پایدار از متن استثنا (برای پیگیری با پشتیبانی)."""
    raw = str(exc) or "unknown"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:4].upper()
    return f"E-{digest}"


def _msg(title: str, icon: str = ui.EMO_STOP):
    return ui.Text().title(icon, ui.BASE_ARROW, title)


def _pack(t: ui.Text, rows) -> tuple:
    return t.text, t.entities, ui.kb(rows)


# ================================================================ وضعیت
SEARCH_STAGES = ("در حال جست‌وجو", "پیدا شد — در حال آماده‌سازی",
                 "در حال اتصال به ویس‌چت")


def searching(query: str, stage: int = 1):
    """پیام مرحله‌ای جست‌وجو. نسخه‌ی قبلی یک متن ثابت بود و کاربر نمی‌دانست
    کار پیش می‌رود یا ربات گیر کرده."""
    stage = max(1, min(len(SEARCH_STAGES), stage))
    t = _msg(SEARCH_STAGES[stage - 1], ui.EMO_SEARCH)
    if query:
        t.emoji(ui.EMO_ARROW_BLUE).add(" جست‌وجو : ")
        t.code(ui.trunc(query, 40))
        t.add("\n")
    t.emoji(ui.EMO_ARROW_RED).add(
        f" مرحله : {ui.fa(stage)} از {ui.fa(len(SEARCH_STAGES))}\n")
    return _pack(t, [])


def downloading(title: str):
    t = _msg("در حال آماده‌سازی فایل", ui.EMO_DOWNLOAD)
    t.line(0, ui.trunc(title, 46))
    return _pack(t, [])


def queued(position: int, title: str, duration_text: str = ""):
    t = _msg("به صف اضافه شد", ui.EMO_LIST)
    t.emoji(ui.EMO_ARROW_BLUE).add(" ").bold(ui.trunc(title, 40)).add("\n")
    t.emoji(ui.EMO_ARROW_RED).add(f" موقعیت در صف : {ui.fa(position)}\n")
    if duration_text:
        t.emoji(ui.EMO_ARROW_BLUE).add(f" مدت : {ui.fa(duration_text)}\n")
    return _pack(t, [[ui.btn("لیست پخش", "p|playlist", ui.PLAIN, ui.EMO_LIST)]])


def stream_big_file(size_mb: int):
    t = _msg("استریم مستقیم فایل حجیم", ui.EMO_DOWNLOAD)
    t.field(0, "حجم", f"{ui.fa(size_mb)} مگابایت")
    t.add("\n")
    t.italic("بدون دانلود کامل پخش می‌شود؛ چند لحظه صبر کن.")
    return _pack(t, [])


# ================================================================ خطاهای پخش
def no_voice_chat():
    t = _msg("ویس‌چت روشن نیست")
    t.why("برای پخش، ویس‌چت گروه باید فعال باشد.")
    t.how("از منوی گروه «ویدیو چت» را شروع کن، بعد دوباره امتحان کن.")
    return _pack(t, [[ui.btn("راهنما", "h|main", ui.PLAIN, ui.EMO_LIST)]])


def bot_not_admin():
    t = _msg("من در این گروه ادمین نیستم", ui.EMO_GEAR)
    t.why("برای اتصال به ویس‌چت به دسترسی ادمین نیاز دارم.")
    t.how("در تنظیمات گروه من را ادمین کن (دسترسی مدیریت ویدیو چت لازم است).")
    return _pack(t, [[ui.btn("راهنما", "h|main", ui.PLAIN, ui.EMO_LIST)]])


def not_found(query: str = ""):
    t = _msg("پیدا نشد", ui.EMO_SEARCH)
    if query:
        t.emoji(ui.EMO_ARROW_RED).add(" جست‌وجو : ")
        t.code(ui.trunc(query, 40))
        t.add("\n")
    t.how("اسم را دقیق‌تر بنویس (نام خواننده + نام آهنگ).")
    t.how("یا لینک مستقیم یوتیوب/ساوندکلاد بفرست.")
    return _pack(t, [[ui.btn("راهنما", "h|main", ui.PLAIN, ui.EMO_LIST)]])


def not_found_database(query: str = ""):
    """روش «دیتابیس» نتیجه نداد.

    این روش عمداً به یوتیوب fallback نمی‌کند (تصمیم کاربر)، پس پیام باید
    راه‌حل بدهد: اسم دقیق‌تر، یا عوض کردن روش از خود پنل.
    """
    t = _msg("در دیتابیس پیدا نشد", ui.EMO_DATABASE)
    if query:
        t.emoji(ui.EMO_ARROW_RED).add(" جست‌وجو : ")
        t.code(ui.trunc(query, 40))
        t.add("\n")
    t.how("اسم خواننده و آهنگ را دقیق‌تر بنویس.")
    t.how("یا از پنل، پلتفرم را روی یوتیوب / ساوندکلاد بگذار.")
    return _pack(t, [[ui.btn("راهنما", "h|main", ui.PLAIN, ui.EMO_LIST)]])


def too_long(duration_text: str, limit_text: str):
    t = _msg("طول محتوا بیش از حد مجاز است", ui.EMO_MOVIE)
    t.why(f"مدت این محتوا {ui.fa(duration_text)} است و سقف مجاز "
          f"{ui.fa(limit_text)}.")
    t.how("نسخه‌ی کوتاه‌تر را جست‌وجو کن، یا فایل را در گروه بفرست و رویش "
          "ریپلای کن.")
    return _pack(t, [[ui.btn("راهنما", "h|main", ui.PLAIN, ui.EMO_LIST)]])


def download_failed(support_url: str = ""):
    t = _msg("دانلود ناموفق بود", ui.EMO_DOWNLOAD)
    t.why("منبع پاسخ نداد یا فایل قابل دریافت نبود.")
    t.how("چند لحظه بعد دوباره امتحان کن. اگر تکرار شد، پلتفرم دیگر را انتخاب کن.")
    rows = []
    if support_url:
        rows.append([ui.btn("پشتیبانی", None, ui.PLAIN, None, url=support_url)])
    return _pack(t, rows)


def playback_error(exc, support_url: str = "", friendly: str = ""):
    """خطای عمومی پخش — با کد خطا به‌جای متن خام استثنا."""
    t = _msg("خطای غیرمنتظره")
    t.why(friendly or "مشکلی در پخش پیش آمد و در لاگ ثبت شد.")
    t.how("دوباره امتحان کن. اگر ادامه داشت با پشتیبانی تماس بگیر.")
    t.add("\n")
    t.emoji(ui.EMO_ARROW_RED).add(" کد خطا : ")
    t.code(error_code(exc))
    t.add("\n")
    rows = []
    if support_url:
        rows.append([ui.btn("پشتیبانی", None, ui.PLAIN, None, url=support_url)])
    return _pack(t, rows)


def empty_archive():
    t = _msg("آرشیو خالی است", ui.EMO_LIST)
    t.why("هنوز آهنگی در کانال آرشیو ثبت نشده.")
    t.how("چند آهنگ پخش کن؛ خودکار آرشیو می‌شوند و بعد پخش رندوم کار می‌کند.")
    return _pack(t, [[ui.btn("راهنما", "h|main", ui.PLAIN, ui.EMO_LIST)]])


def nothing_playing():
    t = _msg("چیزی در حال پخش نیست", ui.EMO_HEADPHONE)
    t.how("یک آهنگ بفرست یا بنویس «پخش اهنگ <اسم>».")
    return _pack(t, [])


def empty_queue():
    t = _msg("صف خالی است", ui.EMO_LIST)
    t.how("یک آهنگ دیگر بفرست تا به صف اضافه شود.")
    return _pack(t, [])


# ================================================================ دسترسی
def no_subscription(buy_url: str = "", support_url: str = ""):
    t = _msg("این گروه اشتراک فعال ندارد", ui.EMO_BELL)
    t.why("برای پخش در این گروه، اشتراک لازم است.")
    t.how("در پیوی ربات «خرید اشتراک» را بزن و همین گروه را انتخاب کن.")
    rows = []
    if buy_url:
        rows.append([ui.btn("خرید اشتراک", None, ui.GREEN, ui.EMO_DOWNLOAD,
                            url=buy_url)])
    if support_url:
        rows.append([ui.btn("پشتیبانی", None, ui.PLAIN, None, url=support_url)])
    return _pack(t, rows)


def subscription_expired(renew_url: str = "", support_url: str = ""):
    t = _msg("اشتراک این گروه تمام شد", ui.EMO_BELL)
    t.why("ربات در این گروه از کار افتاده است.")
    t.how("برای فعال‌سازی مجدد، در پیوی ربات اشتراک را تمدید کن.")
    rows = []
    if renew_url:
        rows.append([ui.btn("تمدید اشتراک", None, ui.GREEN, ui.EMO_DOWNLOAD,
                            url=renew_url)])
    if support_url:
        rows.append([ui.btn("پشتیبانی", None, ui.PLAIN, None, url=support_url)])
    return _pack(t, rows)


def subscription_paused(support_url: str = ""):
    t = _msg("اشتراک این گروه مکث شده", ui.EMO_PAUSE)
    t.why("مدیریت اشتراک این گروه را موقتاً مکث کرده است.")
    t.how("برای بررسی با پشتیبانی تماس بگیر.")
    rows = []
    if support_url:
        rows.append([ui.btn("پشتیبانی", None, ui.BLUE, None, url=support_url)])
    return _pack(t, rows)


def not_admin(support_url: str = ""):
    t = _msg("دسترسی نداری")
    t.why("فقط ادمین‌های گروه می‌توانند از ربات استفاده کنند.")
    t.how("از ادمین گروه بخواه تو را ادمین کند، یا از او بخواه دستور را بزند.")
    rows = []
    if support_url:
        rows.append([ui.btn("پشتیبانی", None, ui.PLAIN, None, url=support_url)])
    return _pack(t, rows)


def group_only(add_url: str = ""):
    t = _msg("این دستور فقط در گروه کار می‌کند", ui.EMO_HEADPHONE)
    t.why("پخش موزیک به ویس‌چت گروه نیاز دارد.")
    t.how("من را به گروهت اضافه کن و همان‌جا دستور را بزن.")
    rows = []
    if add_url:
        rows.append([ui.btn("افزودن به گروه", None, ui.GREEN, None, url=add_url)])
    rows.append([ui.btn("راهنما", "h|main", ui.PLAIN, ui.EMO_LIST)])
    return _pack(t, rows)


def pv_denied(support_url: str = ""):
    t = _msg("دسترسی خصوصی نداری", ui.EMO_HEADPHONE)
    t.why("پیوی ربات فقط برای مالک و کاربران ویژه است.")
    t.how("ربات را به گروهت اضافه کن و همان‌جا استفاده کن.")
    rows = []
    if support_url:
        rows.append([ui.btn("پشتیبانی", None, ui.PLAIN, None, url=support_url)])
    return _pack(t, rows)


def player_off(support_url: str = ""):
    t = _msg("ربات در این گروه خاموش است", ui.EMO_STOP)
    t.why("مدیریت پلیر را برای این گروه خاموش کرده است.")
    t.how("برای فعال‌سازی با پشتیبانی تماس بگیر.")
    rows = []
    if support_url:
        rows.append([ui.btn("پشتیبانی", None, ui.BLUE, None, url=support_url)])
    return _pack(t, rows)
