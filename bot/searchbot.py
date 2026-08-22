"""روش «دیتابیس»: جست‌وجوی آهنگ از طریق ربات جستجوی خودمان (inline).

چرا یوزربات و نه ربات پلیر؟
    تلگرام به ربات‌ها اجازه نمی‌دهد آپدیت پیام ربات دیگر را ببینند، inline
    query بزنند، یا روی دکمه‌ی ربات دیگر کلیک کنند — حتی با دسترسی ادمین.
    یوزربات کمکی (`assistant`) یک اکانت واقعی است، پس همه‌ی این‌ها را می‌تواند.
    نتیجه: **ربات پلیر لازم نیست در گروه جستجو باشد؛ فقط یوزربات.**

جریان کار:
    ۱) یوزربات به ربات جستجو inline query می‌زند: `@zandXmusicBot <اسم آهنگ>`
    ۲) نتیجه‌ها را می‌گیرد (لیست ساختارمند، بدون پارس متن)
    ۳) نتیجه‌ی اول را در گروه جستجو می‌فرستد
    ۴) فایل صوتی را دانلود می‌کند و مسیر محلی را برمی‌گرداند
    ۵) پیام فرستاده‌شده در گروه جستجو پاک می‌شود (گروه تمیز بماند)

اگر هر مرحله شکست بخورد یا از SEARCH_TIMEOUT بگذرد، None برمی‌گردد و
مسیر پخش به یوتیوب fallback می‌کند.

تنظیمات محیطی:
    SEARCH_BOT     یوزرنیم ربات جستجو (پیش‌فرض zandXmusicBot)
    SEARCH_GROUP   شناسه‌ی گروه جستجو (عدد منفی)
    SEARCH_TIMEOUT ثانیه (پیش‌فرض ۲۵)
"""
from __future__ import annotations

import asyncio
import logging
import os
import re

import config
from bot import database as db

LOGGER = logging.getLogger("musicbot.searchbot")

DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "/tmp/musicbot").strip() or "/tmp/musicbot"


def bot_username() -> str:
    return (os.environ.get("SEARCH_BOT", "").strip().lstrip("@")
            or "zandXmusicBot")


def group_id() -> int:
    return getattr(config, "SEARCH_GROUP", 0) or 0


def timeout() -> float:
    try:
        return float(os.environ.get("SEARCH_TIMEOUT", "25"))
    except ValueError:
        return 25.0


def enabled() -> bool:
    """روش دیتابیس فقط وقتی کار می‌کند که گروه جستجو تنظیم شده باشد."""
    return bool(group_id())


# ---------------------------------------------------------------- کمکی
_DUR_RE = re.compile(r"(\d{1,2}):(\d{2})")


def _parse_duration(text: str) -> int:
    """مدت را از متن نتیجه بیرون می‌کشد (مثل «2:13»). ۰ اگر پیدا نشد."""
    m = _DUR_RE.search(text or "")
    if not m:
        return 0
    return int(m.group(1)) * 60 + int(m.group(2))


def _clean_title(raw: str) -> str:
    """عنوان نتیجه را از ایموجی و فاصله‌های اضافه پاک می‌کند."""
    t = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF]", "", raw or "")
    return " ".join(t.split()).strip()


def _split_artist(title: str) -> tuple:
    """«خواننده - آهنگ» یا «آهنگ — خواننده» را به (عنوان، خواننده) می‌شکند."""
    for sep in (" — ", " – ", " - "):
        if sep in title:
            left, right = title.split(sep, 1)
            return right.strip(), left.strip()
    return title, ""


# ---------------------------------------------------------------- تطبیق
# مسئله‌ی واقعی: عنوان‌های ربات جستجو **فینگلیش**‌اند («Divaneh») ولی کاربر
# فارسی می‌نویسد («دیوانه»). حرف‌به‌حرف ترنسلیت جواب نمی‌دهد چون واکه‌های کوتاه
# در فارسی نوشته نمی‌شوند. راه‌حل: مقایسه‌ی **اسکلت هم‌خوان‌ها**
#   «دیوانه»  → d v n h
#   «Divaneh» → d v n h   ✓
_TRANSLIT = {
    "ا": "a", "آ": "a", "ب": "b", "پ": "p", "ت": "t", "ث": "s", "ج": "j",
    "چ": "c", "ح": "h", "خ": "x", "د": "d", "ذ": "z", "ر": "r", "ز": "z",
    "ژ": "j", "س": "s", "ش": "c", "ص": "s", "ض": "z", "ط": "t", "ظ": "z",
    "ع": "a", "غ": "g", "ف": "f", "ق": "g", "ک": "k", "گ": "g", "ل": "l",
    # «و» هم‌خوان گرفته می‌شود (Divaneh/دیوانه) — در فینگلیش تقریباً همیشه v است
    "م": "m", "ن": "n", "و": "v", "ه": "h", "ی": "i", "ئ": "i", "ء": "",
}

# واکه‌ها و نویسه‌هایی که در اسکلت حذف می‌شوند
_VOWELS = set("aeiouy")


def _skeleton(word: str) -> str:
    """اسکلت هم‌خوان‌های یک کلمه (برای تطبیق فارسی↔فینگلیش).

    «دیوانه» و «Divaneh» هر دو به `dvnh` می‌رسند؛ «شادمهر» و «Shadmehr» به `cdmhr`.
    دوحرفی‌های فینگلیش (sh/ch/kh/gh/zh) به یک حرف نگاشت می‌شوند تا با نگاشت
    فارسی هم‌خوان بمانند.
    """
    w = word.lower()
    for a, b in (("sh", "c"), ("ch", "c"), ("kh", "x"), ("gh", "g"),
                 ("zh", "j"), ("ph", "f"), ("th", "t"), ("ck", "k")):
        w = w.replace(a, b)
    w = "".join(_TRANSLIT.get(ch, ch) for ch in w)
    out = []
    for ch in w:
        if ch in _VOWELS or not ch.isalnum():
            continue
        if out and out[-1] == ch:          # حرف مکرر را یکی کن
            continue
        out.append(ch)
    return "".join(out)

_STOPWORDS = {"اهنگ", "آهنگ", "موزیک", "پخش", "از", "با", "the", "a", "of",
              "feat", "ft", "official", "music", "video", "remix", "audio"}


def _norm_match(text: str) -> str:
    """نرمال‌سازی سبک: یکسان‌سازی فارسی + حذف نویسه‌های نگارشی."""
    from bot.facmd import normalize
    t = normalize(text or "").lower()
    t = re.sub(r"[^\w\s\u0600-\u06FF]+", " ", t)
    return " ".join(t.split())


def _tokens(text: str) -> list:
    return [w for w in _norm_match(text).split()
            if len(w) > 1 and w not in _STOPWORDS]


def _variants(sk: str) -> set:
    """گونه‌های یک اسکلت، برای پوشش ناهماهنگی‌های فارسی↔فینگلیش.

    دو مورد در داده‌ی واقعی دیده شد:
      · «ه» پایانی در فینگلیش می‌افتد → «گریه» = grh ولی «Gerye» = gr
      · «و» گاهی واکه است نه هم‌خوان → «ممنون» = mnvn ولی «Mamnoon» = mn
    """
    out = {sk}
    if sk.endswith("h") and len(sk) > 2:
        out.add(sk[:-1])
    if "v" in sk:
        stripped = sk.replace("v", "")
        if len(stripped) >= 2:
            out.add(stripped)
            if stripped.endswith("h") and len(stripped) > 2:
                out.add(stripped[:-1])
    return {v for v in out if len(v) >= 2}


def _skels(text: str) -> set:
    """مجموعه‌ی اسکلت کلمات (با گونه‌ها، خالی‌ها حذف می‌شوند)."""
    out = set()
    for w in _tokens(text):
        out |= _variants(_skeleton(w))
    return out


def _cover(q: set, target: set) -> float:
    """چه نسبتی از اسکلت‌های جست‌وجو در مجموعه‌ی هدف پیدا می‌شود (۰ تا ۱)."""
    if not q or not target:
        return 0.0
    hit = 0.0
    for w in q:
        if w in target:
            hit += 1.0
            continue
        # تطبیق جزئی: یکی زیررشته‌ی دیگری (صرف یا املای متفاوت)
        for c in target:
            if len(w) >= 3 and len(c) >= 3 and (w in c or c in w):
                hit += 0.6
                break
    return hit / len(q)


def _matched(q: set, target: set) -> set:
    """کدام اسکلت‌های جست‌وجو در مجموعه‌ی هدف پیدا می‌شوند."""
    out = set()
    for w in q:
        if w in target:
            out.add(w)
            continue
        for c in target:
            if len(w) >= 3 and len(c) >= 3 and (w in c or c in w):
                out.add(w)
                break
    return out


def parts(query: str, title: str, performer: str = "") -> tuple:
    """(پوشش نام آهنگ، پوشش نام خواننده) — هر دو بین ۰ و ۱.

    نکته‌ی مهم (با داده‌ی واقعی کشف شد): کلمه‌های مربوط به **خواننده** از
    سنجش نام آهنگ کنار گذاشته می‌شوند. وگرنه در «شادمهر عقیلی تماشا» عنوان
    «Tamasha» فقط ۱ از ۳ کلمه را پوشش می‌داد (۰.۳۳) و رد می‌شد، در حالی که
    همان آهنگ درست است — دو کلمه‌ی دیگر اسم خواننده بودند.
    """
    q = _skels(query)
    if not q:
        return 0.0, 0.0
    p_sk = _skels(performer)
    perf_hits = _matched(q, p_sk)
    perf_cov = len(perf_hits) / len(q) if q else 0.0
    # نام آهنگ فقط با کلمه‌های باقی‌مانده سنجیده می‌شود
    residual = q - perf_hits or q
    return _cover(residual, _skels(title)), perf_cov


def score(query: str, title: str, performer: str = "") -> float:
    """امتیاز کلی تطبیق (نام آهنگ ×۱ + خواننده ×۰.۳۵).

    تطبیق روی **اسکلت هم‌خوان‌ها** است چون عنوان‌های ربات جستجو فینگلیش‌اند
    و کاربر فارسی می‌نویسد («دیوانه» ≡ «Divaneh» ≡ `dvnh`).
    """
    t, p = parts(query, title, performer)
    return t + 0.35 * p


# آستانه‌ها (با داده‌ی واقعی ربات کالیبره شدند)
_T_IN_ARTIST = 0.34     # تطبیق نام آهنگ، وقتی خواننده هم درست است
_T_GLOBAL = 0.60        # تطبیق نام آهنگ، وقتی خواننده جور نیست (سخت‌گیرتر)
_T_ARTIST = 0.30        # از این مقدار بالاتر ⇒ خواننده در جست‌وجو آمده است


def decide(query: str, results: list) -> tuple:
    """(شماره‌ی نتیجه، مطمئن‌بودن) — منطق تأییدشده با پاسخ‌های واقعی ربات.

    مراحل:
      ۱) اگر خواننده‌ای در جست‌وجو آمده، **اول بین آهنگ‌های همان خواننده**
         بگرد. ربات ۲۰ آهنگ از همان خواننده می‌دهد؛ بدون این قید یک ریمیکس
         بی‌ربط از خواننده‌ی دیگر ممکن است برنده شود.
      ۲) اگر آن خواننده این آهنگ را نداشت، در کل نتایج بگرد ولی با آستانه‌ی
         سخت‌گیرتر (تطبیق باید واضح باشد).
      ۳) **خواننده مشخص بود ولی آهنگش پیدا نشد** ⇒ (۰, False):
         یعنی «مطمئن نیستم». مسیر پخش با این علامت به یوتیوب fallback می‌کند،
         چون آهنگ هم‌نام از خواننده‌ی دیگر پاسخ درست نیست و نتیجه‌ی اولِ ربات
         هم آهنگ دیگری از همان خواننده است.
      ۴) خواننده‌ای مشخص نشده و تطبیق واضحی نبود ⇒ (۰, True): به ترتیب خودِ
         ربات اعتماد می‌شود (جست‌وجوی مبهم، احتمالاً ربات بهتر می‌داند).
    """
    if not results:
        return 0, True
    scored = [(i, *parts(query, r.get("title", ""), r.get("performer", "")))
              for i, r in enumerate(results)]

    # ۱) خواننده‌ی خواسته‌شده در نتایج هست؟
    in_artist = [(i, t) for i, t, p in scored if p >= _T_ARTIST]
    if in_artist:
        best_i, best_t = max(in_artist, key=lambda x: x[1])
        if best_t >= _T_IN_ARTIST:
            return best_i, True
        # خواننده هست ولی این آهنگ را ندارد ⇒ **حدس نزن**.
        # آهنگ هم‌نام از خواننده‌ی دیگر پاسخ درست نیست. (تصمیم کاربر: به
        # یوتیوب هم نرود؛ پیام «پیدا نشد» داده می‌شود.)
        return 0, False

    # ۲) خواننده‌ای در جست‌وجو نبود ⇒ کل نتایج با آستانه‌ی سخت‌گیرتر
    best_i, best_t = max(((i, t) for i, t, _p in scored), key=lambda x: x[1])
    if best_t >= _T_GLOBAL:
        return best_i, True

    # ۳) جست‌وجوی مبهم ⇒ ترتیب خودِ ربات
    return 0, True


def best_index(query: str, results: list) -> int:
    """فقط شماره‌ی نتیجه (برای سازگاری و استفاده‌های ساده)."""
    return decide(query, results)[0]


# ---------------------------------------------------------------- جست‌وجو
async def search(query: str, want_video: bool = False) -> list:
    """inline query به ربات جستجو می‌زند و نتیجه‌ها را برمی‌گرداند.

    هر آیتم: {"index", "title", "performer", "duration", "raw"}
    لیست خالی یعنی نتیجه‌ای نبود یا ربات جواب نداد.
    """
    if not enabled():
        return []
    from bot import assistant

    try:
        res = await asyncio.wait_for(
            assistant.get_inline_bot_results(bot_username(), query),
            timeout=timeout(),
        )
    except asyncio.TimeoutError:
        LOGGER.warning("SEARCHBOT timeout | q=%s", query)
        return []
    except Exception as e:  # noqa: BLE001
        LOGGER.warning("SEARCHBOT inline query failed: %s", e)
        return []

    out = []
    for i, r in enumerate(getattr(res, "results", []) or []):
        raw_title = getattr(r, "title", "") or ""
        desc = getattr(r, "description", "") or ""
        name = _clean_title(raw_title)
        if not name:
            continue
        # ساختار واقعی نتایج (با تست زنده تأیید شد):
        #   title       = نام آهنگ            («Gerye Kon Baram»)
        #   description = نام خواننده         («Ali Navab»)
        # مدت در نتیجه‌ی inline نیست؛ از خودِ فایل پس از ارسال خوانده می‌شود.
        performer = _clean_title(desc)
        if not performer:
            name, performer = _split_artist(name)
        out.append({
            "index": i,
            "title": name,
            "name": name,
            "performer": performer,
            "duration": _parse_duration(desc) or _parse_duration(raw_title),
            "raw": raw_title,
            "query_id": getattr(res, "query_id", None),
            "result_id": getattr(r, "id", None),
        })
    LOGGER.info("SEARCHBOT | q=%s نتایج=%d", query, len(out))
    return out


async def fetch(query: str, pick: int | None = None) -> dict | None:
    """بهترین نتیجه را می‌گیرد، دانلود می‌کند و اطلاعاتش را برمی‌گرداند.

    `pick=None` (پیش‌فرض) ⇒ **تطبیق هوشمند**: بین ۲۰ نتیجه‌ی ربات، نزدیک‌ترین
    به عبارت جست‌وجو انتخاب می‌شود. عدد بدهی، همان شماره برداشته می‌شود.

    خروجی: {"path", "title", "performer", "duration", "file_size"} یا None.
    پیام فرستاده‌شده در گروه جستجو پاک می‌شود تا گروه شلوغ نشود.
    """
    if not enabled():
        return None
    from bot import assistant

    results = await search(query)
    if not results:
        return None
    if pick is None:
        pick, confident = decide(query, results)
        if not confident:
            # خواننده مشخص بود ولی آهنگش بین نتایج نبود ⇒ حدس نزن.
            # (تصمیم کاربر: به یوتیوب fallback نشود.)
            LOGGER.info("SEARCHBOT: خواننده پیدا شد ولی آهنگ نه | q=%s", query)
            return None
        if pick:
            LOGGER.info("SEARCHBOT تطبیق هوشمند: نتیجه %d انتخاب شد (%s)",
                        pick + 1, results[pick].get("title", ""))
    pick = max(0, min(pick, len(results) - 1))
    chosen = results[pick]

    sent = None
    path = ""
    try:
        sent = await asyncio.wait_for(
            assistant.send_inline_bot_result(
                group_id(), chosen["query_id"], chosen["result_id"]),
            timeout=timeout(),
        )
        # پیام واقعی (با فایل) را از گروه بخوان
        msg = await _resolve_message(assistant, sent)
        if msg is None:
            LOGGER.warning("SEARCHBOT: پیام نتیجه پیدا نشد")
            return None
        media = msg.audio or msg.voice or msg.document
        if media is None:
            LOGGER.warning("SEARCHBOT: نتیجه فایل صوتی نداشت")
            return None

        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        path = await asyncio.wait_for(
            msg.download(file_name=os.path.join(DOWNLOAD_DIR,
                                               f"sb_{msg.id}")),
            timeout=max(60.0, timeout() * 3),
        )
        if not path or not os.path.isfile(path):
            return None

        title = (getattr(media, "title", "") or chosen["name"]
                 or chosen["title"])
        performer = getattr(media, "performer", "") or chosen["performer"]
        duration = int(getattr(media, "duration", 0) or chosen["duration"] or 0)
        size = int(getattr(media, "file_size", 0) or 0)
        if not size:
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0

        return {
            "path": path,
            "title": title,
            "performer": performer,
            "duration": duration,
            "file_size": size,
        }
    except asyncio.TimeoutError:
        LOGGER.warning("SEARCHBOT fetch timeout | q=%s", query)
        return None
    except Exception as e:  # noqa: BLE001
        LOGGER.warning("SEARCHBOT fetch failed: %s", e)
        return None
    finally:
        # گروه جستجو را تمیز نگه دار
        if sent is not None:
            try:
                await _delete(assistant, sent)
            except Exception:  # noqa: BLE001
                pass


async def _resolve_message(client, sent):
    """پیام حاوی فایل را برمی‌گرداند.

    در تست زنده `send_inline_bot_result` یک `Message` برگرداند، ولی رسانه‌اش
    ممکن است لحظه‌ای بعد برسد؛ پس اگر فایل نبود با message_id چند بار retry
    می‌کنیم. نسخه‌های دیگر ممکن است آبجکت آپدیت برگردانند.
    """
    if sent is None:
        return None
    # اگر همین حالا فایل دارد، تمام
    if getattr(sent, "audio", None) or getattr(sent, "voice", None) \
            or getattr(sent, "document", None):
        return sent
    mid = getattr(sent, "id", None) or getattr(sent, "message_id", None)
    updates = getattr(sent, "updates", None)
    if mid is None and updates:
        for u in updates:
            m = getattr(u, "message", None)
            if m is not None and getattr(m, "id", None):
                mid = m.id
                break
    if mid is None:
        return None
    for _ in range(6):                       # چند تلاش کوتاه تا فایل برسد
        try:
            m = await client.get_messages(group_id(), mid)
            if m and (m.audio or m.voice or m.document):
                return m
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(0.7)
    return None


async def _delete(client, sent) -> None:
    mid = getattr(sent, "id", None) or getattr(sent, "message_id", None)
    if mid:
        await client.delete_messages(group_id(), mid)
