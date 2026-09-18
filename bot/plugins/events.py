"""لاگ رویدادها + مدیریت کانال دیتابیس (آرشیو آهنگ).

دو کار اصلی:
  ۱) رویدادهای عضویت ربات را در کانال لاگ ثبت می‌کند.
  ۲) کانال دیتابیس را مدیریت می‌کند:
     · آهنگی که مالک فوروارد می‌کند، توسط ربات دانلود و دوباره ارسال
       می‌شود با کپشن کامل و دکمه‌ی حذف؛ سپس پیام فوروارد اصلی پاک می‌شود
       تا کانال تکراری نماند.
     · دکمه‌ی حذف با تأیید دومرحله‌ای کار می‌کند.

الگوی callback: `arch|del|<key>` · `arch|yes|<key>` · `arch|no|<key>`
"""
from __future__ import annotations

import asyncio
import logging
import os

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, ChatMemberUpdated, Message

import config
from bot import channel
from bot import channel_ui as cui
from bot import database as db
from bot.auth import OWNER_ID

LOGGER = logging.getLogger("musicbot.events")

# کلیدهایی که در حال پردازش‌اند
_processing: set = set()


# ================================================================ کانال دیتابیس

@Client.on_message(filters.channel & (filters.audio | filters.video))
async def _on_archive_media(client: Client, message: Message):
    """رسانه‌ی جدید در کانال دیتابیس."""
    try:
        if not config.ARCHIVE_CHANNEL:
            return

        if not (message.chat and message.chat.id == config.ARCHIVE_CHANNEL):
            return

        # پیامی که خود ربات فرستاده
        if message.id in _processing:
            return

        media = message.audio or message.video
        if not media:
            return

        # اگر قبلاً ثبت شده
        if db.archive_by_message(message.id):
            return

        is_forward = bool(
            message.forward_date
            or message.forward_from
            or message.forward_from_chat
        )

        if is_forward:
            await _reprocess_forward(client, message)
        else:
            await channel.store_message(message, source="upload")

    except Exception as e:  # noqa: BLE001
        LOGGER.warning("archive media handler: %s", e)


async def _reprocess_forward(client: Client, message: Message) -> None:
    """آهنگ فورواردشده را دانلود، دوباره ارسال و اصل را حذف می‌کند."""
    media = message.audio or message.video
    is_video = message.video is not None
    title, performer = channel.media_title(media)

    status = None

    try:
        text, ents = cui.forward_processing(title)
        status = await client.send_message(
            config.ARCHIVE_CHANNEL,
            text,
            entities=ents,
        )
    except Exception:
        pass

    path = ""

    try:
        os.makedirs(channel.DOWNLOAD_DIR, exist_ok=True)

        path = await message.download(
            file_name=os.path.join(
                channel.DOWNLOAD_DIR,
                f"fw_{message.id}",
            )
        )

        if not path or not os.path.isfile(path):
            raise RuntimeError("download produced no file")

        sent = await channel.publish_song(
            client,
            path,
            title=title,
            performer=performer,
            duration=int(getattr(media, "duration", 0) or 0),
            file_size=int(getattr(media, "file_size", 0) or 0),
            source="forward",
            added_by=OWNER_ID,
            is_video=is_video,
        )

        if sent:
            _processing.add(sent.id)

            try:
                await message.delete()
            except Exception as e:  # noqa: BLE001
                LOGGER.debug("delete original forward: %s", e)

    except Exception as e:  # noqa: BLE001
        LOGGER.warning("reprocess forward failed: %s", e)

        # اگر بازارسال نشد، فوروارد را از دست نده
        await channel.store_message(
            message,
            source="forward",
        )

    finally:
        if status:
            try:
                await status.delete()
            except Exception:
                pass

        if path and os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass


@Client.on_callback_query(filters.regex(r"^arch\|"))
async def _on_archive_cb(client: Client, cq: CallbackQuery):
    """دکمه‌ی حذف زیر آهنگ‌های کانال دیتابیس."""
    if not cq.from_user or cq.from_user.id != OWNER_ID:
        await cq.answer(
            "فقط مالک می‌تواند دیتابیس را تغییر دهد.",
            show_alert=True,
        )
        return

    parts = str(cq.data or "").split("|")
    action = parts[1] if len(parts) > 1 else ""
    short = parts[2] if len(parts) > 2 else ""

    rec = db.archive_by_message(
        cq.message.id
    ) if cq.message else None

    if not rec:
        rec = db.archive_by_short(short)

    if not rec:
        await cq.answer(
            "این آهنگ در دیتابیس نیست.",
            show_alert=True,
        )
        return

    if action == "del":
        try:
            await cq.message.edit_reply_markup(
                cui.confirm_keyboard(rec["key"])
            )
        except Exception:
            pass

        await cq.answer("برای حذف تأیید کن")
        return

    if action == "no":
        try:
            await cq.message.edit_reply_markup(
                cui.song_keyboard(rec["key"])
            )
        except Exception:
            pass

        await cq.answer("انصراف داده شد")
        return

    if action == "yes":
        db.archive_delete(key=rec["key"])
        n = db.archive_count()

        try:
            await cq.message.delete()
        except Exception as e:  # noqa: BLE001
            LOGGER.debug("delete archive msg: %s", e)

        await channel.log(
            *cui.song_deleted(
                rec.get("title", ""),
                n,
            )
        )

        await cq.answer("از دیتابیس حذف شد")
        return

    await cq.answer()


# --- سازگاری با روش قدیمی: ریپلای «حذف» روی آهنگ ---

@Client.on_message(
    filters.channel
    & filters.reply
    & filters.regex(r"^\s*حذف\s*$")
)
async def _on_archive_delete_reply(
    client: Client,
    message: Message,
):
    try:
        if not config.ARCHIVE_CHANNEL:
            return

        if not (
            message.chat
            and message.chat.id == config.ARCHIVE_CHANNEL
        ):
            return

        target = message.reply_to_message

        if not target:
            return

        rec = await channel.delete_from_archive(target)

        try:
            await message.delete()

            if rec:
                await target.delete()

        except Exception:
            pass

    except Exception as e:  # noqa: BLE001
        LOGGER.debug(
            "archive delete reply: %s",
            e,
        )


# ================================================================ کانال لاگ

@Client.on_chat_member_updated()
async def _on_member_update(
    client: Client,
    ev: ChatMemberUpdated,
):
    """ثبت گروه و کانال‌هایی که ربات به آن‌ها اضافه/ادمین می‌شود."""
    try:
        me = client.me

        if me is None:
            me = await client.get_me()

        who = ev.new_chat_member or ev.old_chat_member

        if not who or not who.user or who.user.id != me.id:
            return

        chat = ev.chat
        title = getattr(chat, "title", "") or str(chat.id)

        adder = ev.from_user
        adder_name = ""
        adder_id = 0

        if adder:
            adder_name = (
                adder.first_name
                or (
                    adder.username
                    and "@" + adder.username
                )
                or str(adder.id)
            )
            adder_id = adder.id

        old_status = (
            ev.old_chat_member.status.name
            if ev.old_chat_member
            else None
        )

        new_status = (
            ev.new_chat_member.status.name
            if ev.new_chat_member
            else None
        )

        added = (
            old_status in (None, "LEFT", "BANNED")
            and new_status in (
                "MEMBER",
                "ADMINISTRATOR",
            )
        )

        removed = new_status in (
            "LEFT",
            "BANNED",
        )

        # ============================================================
        # اصلاح مخصوص کانال
        #
        # اگر ربات در کانال ادمین باشد، کانال حتماً داخل chats ثبت شود.
        # این قسمت باعث می‌شود کانال در لیست خرید اشتراک ظاهر شود.
        #
        # هیچ دکمه‌ای اینجا تغییر نمی‌کند.
        # ============================================================

        chat_type = getattr(chat, "type", None)

        if (
            str(chat_type).upper().endswith("CHANNEL")
            and new_status == "ADMINISTRATOR"
        ):
            db.add_chat(chat.id)

            LOGGER.info(
                "Channel registered in database: %s (%s)",
                title,
                chat.id,
            )

        # رفتار قبلی گروه‌ها و سوپرگروه‌ها بدون تغییر

        if added:
            db.add_chat(chat.id)

            await channel.log(
                *cui.group_added(
                    title,
                    chat.id,
                    adder_name,
                    adder_id,
                )
            )

        elif removed:
            await channel.log(
                *cui.group_removed(
                    title,
                    chat.id,
                )
            )

    except Exception as e:  # noqa: BLE001
        LOGGER.debug(
            "member update log: %s",
            e,
        )
