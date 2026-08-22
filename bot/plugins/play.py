"""دستورات پخش با معادل‌های فارسی چندکلمه‌ای.

نگاشت دستورات (با/بدون پیشوند / . ! ،):
  پخش اهنگ / پخش آهنگ        → پخش آهنگ
  پخش فیلم / پخش ویدیو        → پخش ویدیو
  مکث / توقف                  → مکث
  ادامه / شروع                → ادامه
  رد / بعدی / اهنگ بعدی       → آهنگ بعدی
  خروج / اتمام                → توقف کامل
  صف / صف پخش / لیست / لیست پخش → نمایش صف
"""
import logging
import os

from pyrogram import Client
from pyrogram.types import Message

import config
from bot import auth
from bot import database as db
from bot import group_config as gc
from bot import logs
from bot import messages as msg
from bot import platform_pref
from bot import player
from bot import queue as q
from bot import soundcloud
from bot import youtube
from bot.facmd import fa_command
from bot.queue import Track

LOGGER = logging.getLogger("musicbot.play")


def _is_no_voice_chat(err: str) -> bool:
    """خطای «ویس‌چت روشن نیست» را از بقیه‌ی خطاها تشخیص می‌دهد."""
    up = err.upper()
    return "GROUPCALL" in up or "no active" in err.lower()


async def _show(status, payload) -> None:
    """یک پیام آماده‌ی messages.py را در پیام وضعیت نشان می‌دهد."""
    text, ents, kb = payload
    try:
        await status.edit_text(text, entities=ents,
                              reply_markup=kb if kb.inline_keyboard else None)
    except Exception as e:  # noqa: BLE001
        LOGGER.debug("show status: %s", e)


async def _send(message: Message, payload) -> None:
    text, ents, kb = payload
    await message.reply_text(text, entities=ents,
                             reply_markup=kb if kb.inline_keyboard else None)


async def _deny_group(client: Client, message: Message) -> None:
    """علت دقیق بسته بودن گروه را می‌گوید — نه یک پیام کلی.

    نسخه‌ی قبلی برای همه‌ی حالت‌ها یک متن یکسان می‌فرستاد؛ کاربر نمی‌فهمید
    اشتراک ندارد، اشتراکش تمام شده، مکث شده، یا مالک خاموش کرده است.
    """
    from bot import subscription as sub
    from bot.plugins.start import pv_url

    chat_id = message.chat.id
    support = await auth.resolve_support_url(client)

    if sub.is_paused(chat_id):
        await _send(message, msg.subscription_paused(support))
        return
    if sub.is_expired(chat_id):
        await _send(message, msg.subscription_expired(
            await pv_url(client, "renew"), support))
        return
    if not sub.has_subscription(chat_id) and not sub.has_free_access(chat_id):
        await _send(message, msg.no_subscription(
            await pv_url(client, "buy"), support))
        return
    # اشتراک هست ولی مالک پلیر را خاموش کرده
    await _send(message, msg.player_off(support))


async def _gate(client: Client, message: Message) -> bool:
    """گارد مشترک: گروه + دسترسی گروه (روشن بودن) + دسترسی کاربر."""
    if message.chat.type.name == "PRIVATE":
        # در خصوصی فقط مالک/ویژه
        if not await auth.guard_message(client, message):
            return False
        from bot.plugins.start import add_group_url
        await _send(message, msg.group_only(await add_group_url(client)))
        return False
    # ۱) اول: آیا گروه فعال است؟ — علت را دقیق بگو، نه یک پیام کلی
    if not gc.is_enabled(message.chat.id):
        await _deny_group(client, message)
        return False
    # ۲) سپس: آیا این کاربر دسترسی دارد؟ (نه → «شما دسترسی ندارید»)
    if not await auth.guard_message(client, message):
        return False
    return True


def _requester_name(message: Message) -> str:
    u = message.from_user
    if not u:
        return "ناشناس"
    return u.first_name or (u.username and "@" + u.username) or str(u.id)


def _requester_id(message: Message) -> int:
    """شناسه‌ی عددی درخواست‌کننده — برای منشن قابل کلیک در پنل.

    منشن با شناسه‌ی عددی (text_mention) به یوزرنیم عمومی نیاز ندارد، پس برای
    کاربرانی که یوزرنیم ندارند هم کار می‌کند.
    """
    u = message.from_user
    return int(u.id) if u else 0


async def _play_track(client: Client, message: Message, info: dict, is_video: bool, query: str, status):
    """یک نتیجه‌ی آماده را پخش/به صف اضافه می‌کند و پیام مناسب می‌دهد."""
    if not info.get("stream_url"):
        await _show(status, msg.not_found(query))
        return

    if info.get("duration") and info["duration"] > config.DURATION_LIMIT:
        limit_min = config.DURATION_LIMIT // 60
        await _show(status, msg.too_long(info["duration_text"],
                                        f"{limit_min} دقیقه"))
        return

    source = info.get("source", "youtube")
    track = Track(
        title=info["title"],
        stream_url=info["stream_url"],
        webpage_url=info["webpage_url"],
        duration=info.get("duration") or 0,
        duration_text=info["duration_text"],
        thumbnail=info.get("thumbnail"),
        requester=_requester_name(message),
        requester_id=_requester_id(message),
        is_video=is_video,
        query=query,
        video_id=info.get("id") or "",
        source=source,
        performer=info.get("performer", "") or "",
        local_path=info.get("local_path", "") or "",
    )
    # اگر منبع آرشیو است، رکورد آماده را به track بده تا دوباره جست‌وجو نشود
    if source == "archive" and info.get("archive_rec"):
        track._archive_rec = info["archive_rec"]

    try:
        with logs.stage("CALL_PLAY", message.chat.id, title=track.title, video=is_video):
            pos = await player.play_or_queue(message.chat.id, track)
    except Exception as e:  # noqa: BLE001
        LOGGER.error("playback error: %s", e)
        if _is_no_voice_chat(str(e)):
            await _show(status, msg.no_voice_chat())
        else:
            url = await auth.resolve_support_url(client)
            await _show(status, msg.playback_error(e, url))
        return

    if pos == 0:
        await status.delete()  # پنل پخش خودش ارسال می‌شود
    else:
        await _show(status, msg.queued(pos, track.title, track.duration_text))

    # لاگ پخش در کانال (بدون بلاک کردن پاسخ)
    try:
        import asyncio
        from bot import channel
        from bot import channel_ui as cui
        chat_title = getattr(message.chat, "title", "") or str(message.chat.id)
        asyncio.create_task(channel.log(*cui.now_playing(
            track.title, track.source, chat_title,
            _requester_name(message), _requester_id(message))))
    except Exception:  # noqa: BLE001
        pass


def _archive_info(rec: dict, fallback_title: str, vid: str = "") -> dict:
    """رکورد کانال دیتابیس را به شکل info قابل پخش برمی‌گرداند (بدون دانلود)."""
    return {
        "id": vid or ("q:" + fallback_title),
        "title": rec.get("title") or fallback_title,
        "duration": rec.get("duration") or 0,
        "duration_text": _fmt_dur(int(rec.get("duration") or 0)),
        "stream_url": "archive", "webpage_url": rec.get("url") or "",
        "thumbnail": None, "source": "archive",
        "archive_rec": rec,
    }


async def _from_database_bot(client, query: str, status):
    """روش «دیتابیس»: از ربات جستجوی خودمان بگیر، دانلود کن، آماده‌ی پخش.

    خروجی info با `stream_url` = مسیر فایل محلی، یا None اگر نشد.
    """
    from bot import channel
    from bot import searchbot

    if not searchbot.enabled():
        LOGGER.info("SEARCHBOT خاموش است (SEARCH_GROUP تنظیم نشده)")
        return None

    await _show(status, msg.searching(query, 2))
    got = await searchbot.fetch(query)
    if not got:
        return None

    disp = channel.full_title(got["title"], got.get("performer", ""))
    dur = int(got.get("duration") or 0)
    return {
        "id": "",
        "title": disp,
        "duration": dur,
        "duration_text": _fmt_dur(dur),
        "stream_url": got["path"],          # فایل محلی، آماده‌ی استریم
        "webpage_url": "",
        "thumbnail": None,
        "source": "searchbot",
        "local_path": got["path"],
        "performer": got.get("performer", ""),
        "file_size": got.get("file_size", 0),
    }


async def _search(chat_id: int, query: str, is_video: bool, status,
                  client=None):
    """جست‌وجو طبق روش انتخابی گروه. info یا None.

    **هر سه روش اول کانال دیتابیس خودمان را چک می‌کنند** — اگر آهنگ آنجا بود،
    بدون دانلود از تلگرام بازیابی می‌شود.

    روش‌ها (تصمیم کاربر):
      · دیتابیس   → ربات جستجوی خودمان (inline با یوزربات)، fallback: یوتیوب
      · یوتیوب    → مستقیم یوتیوب، بدون امتحان ساوندکلاد
      · ساوندکلاد → اول خودِ دستور؛ اگر نبود، اسم دقیق از یوتیوب و دوباره ساوندکلاد
    """
    from bot import channel
    mode = platform_pref.effective(chat_id)

    # ---------- ویدیو: فقط یوتیوب ----------
    if is_video:
        try:
            return await youtube.get_media(query, video=True)
        except Exception as e:  # noqa: BLE001
            LOGGER.warning("youtube video error: %s", e)
            friendly = logs.classify_youtube_error(str(e))
            url = await auth.resolve_support_url(client) if client else ""
            await _show(status, msg.playback_error(e, url, friendly))
            return None

    # ---------- گام صفر (مشترک): کانال دیتابیس با خودِ دستور ----------
    try:
        rec = channel.archive_lookup(query=query)
        if rec:
            LOGGER.info("ARCHIVE HIT (direct) | %s", rec.get("title"))
            return _archive_info(rec, query)
    except Exception as e:  # noqa: BLE001
        LOGGER.debug("archive lookup direct: %s", e)

    # ---------- روش دیتابیس (ربات جستجو) ----------
    # تصمیم کاربر: این روش به یوتیوب fallback **نمی‌کند**. اگر ربات جستجو
    # آهنگ را نداشت یا تطبیق قاطع نبود، پیام «پیدا نشد» داده می‌شود تا کاربر
    # اسم را دقیق‌تر بنویسد یا خودش روش را عوض کند.
    if mode == platform_pref.DATABASE:
        info = await _from_database_bot(client, query, status)
        if info:
            return info
        LOGGER.info("SEARCHBOT نتیجه نداد | q=%s", query)
        await _show(status, msg.not_found_database(query))
        return None

    # ---------- ساوندکلاد ----------
    if mode == platform_pref.SOUNDCLOUD:
        # ۱) خودِ دستور را مستقیم در ساوندکلاد بگرد
        sc = await soundcloud.search(query)
        if sc and sc.get("stream_url"):
            return sc
        # ۲) اسم دقیق را از یوتیوب بگیر و با آن اسم دوباره ساوندکلاد
        exact = ""
        try:
            meta = await youtube.search_title(query)
            exact = (meta.get("title") or "").strip()
        except Exception as e:  # noqa: BLE001
            LOGGER.debug("yt title for sc: %s", e)
        if exact and exact != query:
            # قبل از جست‌وجو، دیتابیس را با اسم دقیق هم چک کن
            try:
                rec = channel.archive_lookup(query=exact)
                if rec:
                    return _archive_info(rec, exact)
            except Exception:  # noqa: BLE001
                pass
            sc = await soundcloud.search(exact)
            if sc and sc.get("stream_url"):
                return sc
        await _show(status, msg.not_found(query))
        return None

    # ---------- یوتیوب (و fallback روش دیتابیس) ----------
    # اسم دقیق را بگیر تا هم دیتابیس را دقیق‌تر چک کنیم هم video_id داشته باشیم
    exact_title = ""
    yt_vid = ""
    try:
        meta = await youtube.search_title(query)
        exact_title = (meta.get("title") or "").strip()
        yt_vid = meta.get("id") or ""
    except Exception as e:  # noqa: BLE001
        LOGGER.debug("yt title: %s", e)

    if exact_title or yt_vid:
        try:
            rec = channel.archive_lookup(video_id=yt_vid,
                                        query=(exact_title or query))
            if rec:
                LOGGER.info("ARCHIVE HIT (exact) | %s", rec.get("title"))
                return _archive_info(rec, exact_title or query, yt_vid)
        except Exception as e:  # noqa: BLE001
            LOGGER.debug("archive lookup: %s", e)

    try:
        await _show(status, msg.searching(query, 2))
        return await youtube.get_media(query, video=False)
    except Exception as e:  # noqa: BLE001
        LOGGER.warning("youtube error: %s", e)
        friendly = logs.classify_youtube_error(str(e))
        url = await auth.resolve_support_url(client) if client else ""
        await _show(status, msg.playback_error(e, url, friendly))
        return None


async def _handle_play(client: Client, message: Message, is_video: bool):
    if not await _gate(client, message):
        return

    # اگر روی یک فایل صوتی/ویدیویی تلگرام ریپلای شده، مستقیم آن را پخش کن
    if await _play_telegram_file(client, message):
        return

    query = " ".join(message.command[1:]).strip()
    if not query and message.reply_to_message and message.reply_to_message.text:
        query = message.reply_to_message.text.strip()
    if not query:
        example = "پخش فیلم هزارپا" if is_video else "پخش اهنگ شادمهر"
        await message.reply_text(f"نام {'فیلم' if is_video else 'آهنگ'} یا لینک را بده.\nمثال: `{example}`")
        return

    db.add_chat(message.chat.id)
    _t, _e, _k = msg.searching(query, 1)
    status = await message.reply_text(_t, entities=_e)
    info = await _search(message.chat.id, query, is_video, status, client)
    if info is None:
        return
    await _play_track(client, message, info, is_video, query, status)


# --- ریپلای روی فایل صوتی/ویدیویی: افزودن به صف یا پخش ---
# آستانه: فایل‌های حجیم (بالای این حد) به‌جای دانلود، مستقیم از تلگرام استریم می‌شوند.
_TG_STREAM_THRESHOLD = int(os.environ.get("TG_STREAM_THRESHOLD_MB", "50")) * 1024 * 1024


async def _play_telegram_file(client: Client, message: Message) -> bool:
    """اگر روی یک فایل صوتی/ویدیویی ریپلای شده، آن را پخش/به صف می‌کند. True اگر انجام شد.

    - فایل کوچک (زیر آستانه): دانلود و پخش از فایل محلی (پایدار).
    - فایل حجیم (بالای آستانه): استریم مستقیم توسط یوزربات کمکی (بدون دانلود کامل).
    """
    reply = message.reply_to_message
    media = None
    if reply:
        media = reply.audio or reply.voice or reply.video or reply.document
    if not media:
        return False

    db.add_chat(message.chat.id)
    size = getattr(media, "file_size", 0) or 0
    title = getattr(media, "title", None) or getattr(media, "file_name", None) or "فایل تلگرام"
    dur = getattr(media, "duration", 0) or 0
    is_video = bool(reply.video or (reply.document and "video" in (getattr(media, "mime_type", "") or "")))

    # --- مسیر استریم مستقیم برای فایل حجیم (بدون دانلود) ---
    if size >= _TG_STREAM_THRESHOLD:
        status = await message.reply_text(
            ""
        )
        track = Track(
            title=title, stream_url="", webpage_url="",
            duration=dur, duration_text=_fmt_dur(dur), thumbnail=None,
            requester=_requester_name(message),
            requester_id=_requester_id(message), is_video=True,
            query="", video_id="", source="telegram_stream",
            tg_chat_id=message.chat.id, tg_msg_id=reply.id,
        )
        try:
            with logs.stage("CALL_STREAM", message.chat.id, title=title):
                pos = await player.play_or_queue(message.chat.id, track)
        except Exception as e:  # noqa: BLE001
            LOGGER.error("tg stream playback error: %s", e)
            err = str(e)
            if "GROUPCALL" in err.upper() or "no active" in err.lower():
                await _show(status, msg.no_voice_chat())
            else:
                url = await auth.resolve_support_url(client)
                await _show(status, msg.playback_error(e, url))
            return True
        if pos == 0:
            await status.delete()
        else:
            await status.edit_text(f"✅ به صف اضافه شد (موقعیت {pos}):\n**{title}**")
        return True

    # --- مسیر دانلود برای فایل کوچک ---
    _t, _e, _k = msg.downloading(title)
    status = await message.reply_text(_t, entities=_e)
    try:
        os.makedirs(player.DOWNLOAD_DIR, exist_ok=True)
        path = await client.download_media(reply, file_name=os.path.join(player.DOWNLOAD_DIR, ""))
    except Exception as e:  # noqa: BLE001
        LOGGER.warning("tg media download failed: %s", e)
        url = await auth.resolve_support_url(client)
        await _show(status, msg.download_failed(url))
        return True

    track = Track(
        title=title, stream_url=path, webpage_url="",
        duration=dur, duration_text=_fmt_dur(dur), thumbnail=None,
        requester=_requester_name(message),
        requester_id=_requester_id(message), is_video=is_video,
        query="", video_id="", source="telegram",
    )
    track.local_path = path
    try:
        with logs.stage("CALL_PLAY", message.chat.id, title=title, video=is_video):
            pos = await player.play_or_queue(message.chat.id, track)
    except Exception as e:  # noqa: BLE001
        LOGGER.error("playback error: %s", e)
        err = str(e)
        if "GROUPCALL" in err.upper() or "no active" in err.lower():
            await _show(status, msg.no_voice_chat())
        else:
            url = await auth.resolve_support_url(client)
            await _show(status, msg.playback_error(e, url))
        return True
    if pos == 0:
        await status.delete()
    else:
        await status.edit_text(f"✅ به صف اضافه شد (موقعیت {pos}):\n**{title}**")
    return True


def _fmt_dur(seconds: int) -> str:
    if not seconds:
        return "نامشخص"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# --- «پخش» تنها (ریپلای روی فایل تلگرام، یا «پخش <اسم>») ---
# نکته: این هندلر «پخش» را می‌گیرد و چون قبل از play_cmd ثبت می‌شود،
# «پخش اهنگ/فیلم ...» هم به اینجا می‌رسد؛ پس خودمان کلیدواژه را تشخیص می‌دهیم.
_VIDEO_KW = {"فیلم", "ویدیو", "ویدئو", "کلیپ"}
_AUDIO_KW = {"اهنگ", "آهنگ", "موزیک", "موسیقی", "صدا"}


@Client.on_message(fa_command(["پخش", "بذار", "بنداز"]))
async def bare_play_cmd(client: Client, message: Message):
    if not await _gate(client, message):
        return

    # ۰) «پخش رندوم» → حالت رندوم و پخش تصادفی (قبل از هر چیز)
    args0 = message.command[1:]
    if args0 and _norm(args0[0]) == "رندوم":
        await _start_random(client, message)
        return

    # ۱) ریپلای روی فایل صوتی/ویدیویی تلگرام → مستقیم پخش
    if await _play_telegram_file(client, message):
        return

    # ۲) تشخیص «پخش اهنگ/فیلم <اسم>» و جداکردن نوع + پاک‌سازی کوئری
    args = message.command[1:]
    is_video = False
    if args and args[0] in _VIDEO_KW:
        is_video = True
        args = args[1:]
    elif args and args[0] in _AUDIO_KW:
        args = args[1:]

    query = " ".join(args).strip()
    if not query and message.reply_to_message and message.reply_to_message.text:
        query = message.reply_to_message.text.strip()
    if not query:
        await _send(message, msg.not_found())
        return

    db.add_chat(message.chat.id)
    _t, _e, _k = msg.searching(query, 1)
    status = await message.reply_text(_t, entities=_e)
    info = await _search(message.chat.id, query, is_video, status, client)
    if info is None:
        return
    await _play_track(client, message, info, is_video, query, status)


# --- پخش آهنگ: «پخش اهنگ» / «پخش آهنگ» ---
@Client.on_message(fa_command(["پخش اهنگ", "پخش آهنگ"]))
async def play_cmd(client: Client, message: Message):
    await _handle_play(client, message, is_video=False)


# --- پخش ویدیو: «پخش فیلم» / «پخش ویدیو» ---
@Client.on_message(fa_command(["پخش فیلم", "پخش ویدیو"]))
async def vplay_cmd(client: Client, message: Message):
    await _handle_play(client, message, is_video=True)


# --- شروع پخش رندوم: حالت را رندوم کن و فوراً یک آهنگ تصادفی پخش کن ---
def _norm(w: str) -> str:
    from bot.facmd import normalize
    return normalize(w)


async def _start_random(client: Client, message: Message):
    from bot import group_config as gc
    gc.set_mode(message.chat.id, gc.MODE_RANDOM)
    db.add_chat(message.chat.id)
    _t, _e, _k = msg.searching("پخش رندوم", 1)
    status = await message.reply_text(_t, entities=_e)
    try:
        with logs.stage("RANDOM_PLAY", message.chat.id):
            await player.play_random(message.chat.id)
    except Exception as e:  # noqa: BLE001
        LOGGER.error("random play error: %s", e)
        err = str(e)
        if "GROUPCALL" in err.upper() or "no active" in err.lower():
            await _show(status, msg.no_voice_chat())
        else:
            url = await auth.resolve_support_url(client)
            await _show(status, msg.playback_error(e, url))
        return
    if q.now_playing(message.chat.id) is None:
        await _show(status, msg.empty_archive())
    else:
        await status.delete()


@Client.on_message(fa_command(["شروع پخش رندوم", "پخش رندوم", "رندوم"]))
async def random_cmd(client: Client, message: Message):
    if not await _gate(client, message):
        return
    await _start_random(client, message)


# --- مکث: «مکث» / «توقف موقت» (فقط نگه‌داشتن موقت، در کال می‌ماند) ---
@Client.on_message(fa_command(["مکث", "توقف موقت"]))
async def pause_cmd(client: Client, message: Message):
    if not await _gate(client, message):
        return
    track = q.now_playing(message.chat.id)
    if not track:
        await _send(message, msg.nothing_playing())
        return
    from bot import call
    await call.pause(message.chat.id)
    track.mark_paused()
    await player.refresh_panel(message.chat.id)
    await message.reply_text("⏸ موقتاً متوقف شد. برای ادامه بنویس «ادامه».")


# --- ادامه: «ادامه» / «شروع» ---
@Client.on_message(fa_command(["ادامه", "شروع"]))
async def resume_cmd(client: Client, message: Message):
    if not await _gate(client, message):
        return
    track = q.now_playing(message.chat.id)
    if not track:
        await _send(message, msg.nothing_playing())
        return
    from bot import call
    await call.resume(message.chat.id)
    track.mark_resumed()
    await player.refresh_panel(message.chat.id)
    await message.reply_text("▶️ ادامه یافت.")


# --- آهنگ بعدی: «رد» / «بعدی» / «آهنگ بعدی» / «اهنگ بعدی» ---
@Client.on_message(fa_command(["اهنگ بعدی", "آهنگ بعدی", "بعدی", "رد"]))
async def skip_cmd(client: Client, message: Message):
    if not await _gate(client, message):
        return
    if q.now_playing(message.chat.id) is None:
        await _send(message, msg.nothing_playing())
        return
    nxt = await player.skip(message.chat.id)
    if nxt:
        await message.reply_text(f"⏭ رد شد. پخش بعدی:\n**{nxt.title}**")
    else:
        await message.reply_text("⏹ صف خالی شد. از کال خارج شدم.")


# --- توقف کامل: «توقف» / «خروج» / «اتمام» / «قطع» (از کال خارج می‌شود) ---
@Client.on_message(fa_command(["توقف", "خروج", "اتمام", "قطع"]))
async def stop_cmd(client: Client, message: Message):
    if not await _gate(client, message):
        return
    if q.now_playing(message.chat.id) is None:
        await _send(message, msg.nothing_playing())
        return
    await player.stop(message.chat.id)
    await message.reply_text("⏹ پخش متوقف و از کال خارج شدم.")


# --- صف: «صف» / «صف پخش» / «لیست» / «لیست پخش» ---
@Client.on_message(fa_command(["صف پخش", "لیست پخش", "صف", "لیست"]))
async def queue_cmd(client: Client, message: Message):
    if not await _gate(client, message):
        return
    cur = q.now_playing(message.chat.id)
    if not cur:
        await _send(message, msg.empty_queue())
        return
    lines = [f"🎧 **در حال پخش:** {cur.title} — `{cur.duration_text}`", ""]
    items = list(q.get_queue(message.chat.id))
    if items:
        lines.append("**در صف:**")
        for i, t in enumerate(items, 1):
            lines.append(f"{i}. {t.title} — `{t.duration_text}` ({t.requester})")
    else:
        lines.append("صف بعدی خالی است.")
    await message.reply_text("\n".join(lines))
