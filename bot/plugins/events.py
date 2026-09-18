"""
لاگ رویدادها + مدیریت کانال دیتابیس + نصب/حذف نصب + مدیران موزیک

دستورات:

نصب ربات سایلنت
حذف نصب

ترفیع موزیک
عزل موزیک

قوانین:
- نصب و حذف نصب فقط توسط OWNER_ID
- ترفیع و عزل موزیک توسط OWNER_ID یا مالک گروه
- ترفیع موزیک دائمی است و در SQLite ذخیره می‌شود
- حذف نصب، پلیر گروه را خاموش می‌کند
- دکمه‌های خرید و سیستم آرشیو دست‌نخورده می‌مانند
"""

from __future__ import annotations

import logging
import os

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, ChatMemberUpdated, Message

import config

from bot import channel
from bot import channel_ui as cui
from bot import database as db
from bot import group_config
from bot import subscription
from bot.auth import OWNER_ID


LOGGER = logging.getLogger("musicbot.events")

_processing: set = set()


# ================================================================
# ابزارهای کمکی
# ================================================================

def _status_name(member) -> str:
    """نام وضعیت عضو را به صورت امن برمی‌گرداند."""
    if not member:
        return ""

    status = getattr(member, "status", "")

    return getattr(
        status,
        "name",
        str(status),
    ).upper()


async def _is_chat_owner(
    client: Client,
    chat_id: int,
    user_id: int,
) -> bool:
    """بررسی مالک بودن کاربر در گروه."""

    if not user_id:
        return False

    if user_id == OWNER_ID:
        return True

    try:
        member = await client.get_chat_member(
            chat_id,
            user_id,
        )

        return _status_name(member) == "OWNER"

    except Exception as e:
        LOGGER.debug(
            "owner check failed chat=%s user=%s: %s",
            chat_id,
            user_id,
            e,
        )
        return False


async def _is_group_admin(
    client: Client,
    chat_id: int,
    user_id: int,
) -> bool:
    """بررسی مدیر بودن کاربر."""

    if not user_id:
        return False

    if user_id == OWNER_ID:
        return True

    try:
        member = await client.get_chat_member(
            chat_id,
            user_id,
        )

        return _status_name(member) in (
            "ADMINISTRATOR",
            "OWNER",
        )

    except Exception as e:
        LOGGER.debug(
            "admin check failed chat=%s user=%s: %s",
            chat_id,
            user_id,
            e,
        )
        return False


async def _is_bot_admin(
    client: Client,
    chat_id: int,
) -> bool:
    """بررسی ادمین بودن ربات."""

    try:
        me = client.me

        if me is None:
            me = await client.get_me()

        member = await client.get_chat_member(
            chat_id,
            me.id,
        )

        return _status_name(member) in (
            "ADMINISTRATOR",
            "OWNER",
        )

    except Exception as e:
        LOGGER.debug(
            "bot admin check failed chat=%s: %s",
            chat_id,
            e,
        )
        return False


def _target_name(message: Message) -> str:
    """نام کاربر موردنظر از روی ریپلای."""

    user = getattr(
        message,
        "from_user",
        None,
    )

    if not user:
        return "کاربر"

    if getattr(user, "first_name", None):
        return user.first_name

    if getattr(user, "username", None):
        return "@" + user.username

    return str(
        getattr(user, "id", "کاربر")
    )


# ================================================================
# نصب دائمی ربات
# ================================================================

async def _activate_chat(
    client: Client,
    chat_id: int,
) -> bool:
    """
    فعال‌سازی دائمی گروه/کانال.

    بعد از فعال‌سازی، تا وقتی «حذف نصب» زده نشود
    ربات فعال باقی می‌ماند.
    """

    try:
        db.add_chat(chat_id)

        group_config.set_enabled(
            chat_id,
            True,
        )

        # 0 یعنی دائمی
        subscription.make_permanent(
            chat_id,
        )

        LOGGER.info(
            "Silent bot permanently installed: %s",
            chat_id,
        )

        return True

    except Exception as e:
        LOGGER.exception(
            "activate chat failed: %s",
            e,
        )
        return False


# ================================================================
# حذف نصب ربات
# ================================================================

async def _deactivate_chat(
    client: Client,
    chat_id: int,
) -> bool:
    """
    حذف نصب ربات.

    گروه در دیتابیس باقی می‌ماند اما پلیر خاموش می‌شود.
    """

    try:
        db.add_chat(chat_id)

        group_config.set_enabled(
            chat_id,
            False,
        )

        # اشتراک دائمی نصب را هم حذف می‌کنیم.
        subscription.sub_delete(
            chat_id,
        )

        LOGGER.info(
            "Silent bot uninstalled: %s",
            chat_id,
        )

        return True

    except Exception as e:
        LOGGER.exception(
            "deactivate chat failed: %s",
            e,
        )
        return False


# ================================================================
# نصب ربات سایلنت
# ================================================================

@Client.on_message(
    filters.text
    & filters.regex(
        r"^\s*نصب\s+ربات\s+سایلنت\s*$"
    )
)
async def _on_install_silent(
    client: Client,
    message: Message,
):
    """
    فقط مالک اصلی ربات می‌تواند نصب کند.
    """

    try:
        if not message.chat:
            return

        user = message.from_user

        # فقط OWNER_ID
        if not user or user.id != OWNER_ID:
            return

        chat = message.chat

        chat_type = str(
            getattr(
                chat,
                "type",
                "",
            )
        ).upper()

        if not any(
            x in chat_type
            for x in (
                "GROUP",
                "SUPERGROUP",
                "CHANNEL",
            )
        ):
            return

        chat_id = int(chat.id)

        # ربات باید ادمین باشد
        if not await _is_bot_admin(
            client,
            chat_id,
        ):
            await message.reply_text(
                "❌ اول ربات سایلنت را ادمین کن."
            )
            return

        ok = await _activate_chat(
            client,
            chat_id,
        )

        if not ok:
            await message.reply_text(
                "❌ نصب ربات انجام نشد."
            )
            return

        await message.reply_text(
            "✅ ربات سایلنت نصب شد.\n"
            "🎵 پلیر فعال است.\n"
            "♾️ این نصب دائمی است.\n"
            "برای خاموش کردن: حذف نصب"
        )

    except Exception as e:
        LOGGER.exception(
            "silent install failed: %s",
            e,
        )


# ================================================================
# حذف نصب
# ================================================================

@Client.on_message(
    filters.text
    & filters.regex(
        r"^\s*حذف\s+نصب\s*$"
    )
)
async def _on_uninstall_silent(
    client: Client,
    message: Message,
):
    """
    فقط مالک اصلی ربات می‌تواند حذف نصب کند.
    """

    try:
        if not message.chat:
            return

        user = message.from_user

        # فقط مالک اصلی ربات
        if not user or user.id != OWNER_ID:
            return

        chat = message.chat

        chat_type = str(
            getattr(
                chat,
                "type",
                "",
            )
        ).upper()

        if not any(
            x in chat_type
            for x in (
                "GROUP",
                "SUPERGROUP",
                "CHANNEL",
            )
        ):
            return

        chat_id = int(chat.id)

        ok = await _deactivate_chat(
            client,
            chat_id,
        )

        if not ok:
            await message.reply_text(
                "❌ حذف نصب انجام نشد."
            )
            return

        await message.reply_text(
            "🛑 ربات سایلنت حذف نصب شد.\n"
            "🎵 پلیر این چت غیرفعال شد.\n"
            "برای فعال‌سازی دوباره:\n"
            "نصب ربات سایلنت"
        )

    except Exception as e:
        LOGGER.exception(
            "silent uninstall failed: %s",
            e,
        )


# ================================================================
# ترفیع موزیک
# ================================================================

@Client.on_message(
    filters.text
    & filters.reply
    & filters.regex(
        r"^\s*ترفیع\s+موزیک\s*$"
    )
)
async def _on_promote_music(
    client: Client,
    message: Message,
):
    """
    مالک گروه یا مالک اصلی ربات می‌تواند
    روی پیام کاربر ریپلای کند و بنویسد:

        ترفیع موزیک
    """

    try:
        chat = message.chat

        if not chat:
            return

        chat_type = str(
            getattr(
                chat,
                "type",
                "",
            )
        ).upper()

        # فقط گروه و سوپرگروه
        if not any(
            x in chat_type
            for x in (
                "GROUP",
                "SUPERGROUP",
            )
        ):
            return

        command_user = message.from_user

        if not command_user:
            return

        # فقط مالک اصلی یا مالک گروه
        allowed = await _is_chat_owner(
            client,
            chat.id,
            command_user.id,
        )

        if not allowed:
            return

        # ربات باید ادمین باشد
        if not await _is_bot_admin(
            client,
            chat.id,
        ):
            await message.reply_text(
                "❌ ربات باید ادمین گروه باشد."
            )
            return

        target = message.reply_to_message

        if not target:
            return

        target_user = target.from_user

        if not target_user:
            await message.reply_text(
                "❌ روی پیام یک کاربر ریپلای کن."
            )
            return

        # خود ربات را مدیر موزیک نکن
        me = client.me

        if me is None:
            me = await client.get_me()

        if target_user.id == me.id:
            return

        # ثبت دائمی در SQLite
        db.add_music_admin(
            chat.id,
            target_user.id,
            _target_name(target),
        )

        await message.reply_text(
            "✅ ترفیع موزیک انجام شد.\n"
            f"👤 {_target_name(target)}\n"
            "🎵 دسترسی مدیریت موزیک فعال شد.\n"
            "♾️ دائمی است تا زمانی که «عزل موزیک» زده شود."
        )

        LOGGER.info(
            "Music admin promoted: chat=%s user=%s by=%s",
            chat.id,
            target_user.id,
            command_user.id,
        )

    except Exception as e:
        LOGGER.exception(
            "music promotion failed: %s",
            e,
        )


# ================================================================
# عزل موزیک
# ================================================================

@Client.on_message(
    filters.text
    & filters.reply
    & filters.regex(
        r"^\s*عزل\s+موزیک\s*$"
    )
)
async def _on_demote_music(
    client: Client,
    message: Message,
):
    """
    مالک گروه یا مالک اصلی ربات می‌تواند
    روی پیام مدیر موزیک ریپلای کند و بنویسد:

        عزل موزیک
    """

    try:
        chat = message.chat

        if not chat:
            return

        chat_type = str(
            getattr(
                chat,
                "type",
                "",
            )
        ).upper()

        if not any(
            x in chat_type
            for x in (
                "GROUP",
                "SUPERGROUP",
            )
        ):
            return

        command_user = message.from_user

        if not command_user:
            return

        # فقط مالک اصلی یا مالک گروه
        allowed = await _is_chat_owner(
            client,
            chat.id,
            command_user.id,
        )

        if not allowed:
            return

        if not await _is_bot_admin(
            client,
            chat.id,
        ):
            await message.reply_text(
                "❌ ربات باید ادمین گروه باشد."
            )
            return

        target = message.reply_to_message

        if not target:
            return

        target_user = target.from_user

        if not target_user:
            await message.reply_text(
                "❌ روی پیام کاربر ریپلای کن."
            )
            return

        removed = db.remove_music_admin(
            chat.id,
            target_user.id,
        )

        if not removed:
            await message.reply_text(
                "ℹ️ این کاربر مدیر موزیک نیست."
            )
            return

        await message.reply_text(
            "🛑 عزل موزیک انجام شد.\n"
            f"👤 {_target_name(target)}\n"
            "دسترسی مدیریت موزیک این کاربر قطع شد."
        )

        LOGGER.info(
            "Music admin removed: chat=%s user=%s by=%s",
            chat.id,
            target_user.id,
            command_user.id,
        )

    except Exception as e:
        LOGGER.exception(
            "music demotion failed: %s",
            e,
        )


# ================================================================
# آرشیو آهنگ در کانال
# ================================================================

@Client.on_message(
    filters.channel
    & (filters.audio | filters.video)
)
async def _on_archive_media(
    client: Client,
    message: Message,
):
    try:
        if not config.ARCHIVE_CHANNEL:
            return

        if not (
            message.chat
            and message.chat.id
            == config.ARCHIVE_CHANNEL
        ):
            return

        if message.id in _processing:
            return

        media = (
            message.audio
            or message.video
        )

        if not media:
            return

        if db.archive_by_message(
            message.id
        ):
            return

        is_forward = bool(
            message.forward_date
            or message.forward_from
            or message.forward_from_chat
        )

        if is_forward:
            await _reprocess_forward(
                client,
                message,
            )
        else:
            await channel.store_message(
                message,
                source="upload",
            )

    except Exception as e:
        LOGGER.warning(
            "archive media handler: %s",
            e,
        )


async def _reprocess_forward(
    client: Client,
    message: Message,
) -> None:

    media = (
        message.audio
        or message.video
    )

    is_video = (
        message.video is not None
    )

    title, performer = (
        channel.media_title(media)
    )

    status = None

    try:
        text, ents = (
            cui.forward_processing(title)
        )

        status = await client.send_message(
            config.ARCHIVE_CHANNEL,
            text,
            entities=ents,
        )

    except Exception:
        pass

    path = ""

    try:
        os.makedirs(
            channel.DOWNLOAD_DIR,
            exist_ok=True,
        )

        path = await message.download(
            file_name=os.path.join(
                channel.DOWNLOAD_DIR,
                f"fw_{message.id}",
            )
        )

        if not path or not os.path.isfile(path):
            raise RuntimeError(
                "download produced no file"
            )

        sent = await channel.publish_song(
            client,
            path,
            title=title,
            performer=performer,
            duration=int(
                getattr(
                    media,
                    "duration",
                    0,
                )
                or 0
            ),
            file_size=int(
                getattr(
                    media,
                    "file_size",
                    0,
                )
                or 0
            ),
            source="forward",
            added_by=OWNER_ID,
            is_video=is_video,
        )

        if sent:
            _processing.add(
                sent.id
            )

            try:
                await message.delete()
            except Exception as e:
                LOGGER.debug(
                    "delete original forward: %s",
                    e,
                )

    except Exception as e:
        LOGGER.warning(
            "reprocess forward failed: %s",
            e,
        )

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


# ================================================================
# دکمه حذف آرشیو
# ================================================================

@Client.on_callback_query(
    filters.regex(r"^arch\|")
)
async def _on_archive_cb(
    client: Client,
    cq: CallbackQuery,
):

    if (
        not cq.from_user
        or cq.from_user.id != OWNER_ID
    ):
        await cq.answer(
            "فقط مالک می‌تواند دیتابیس را تغییر دهد.",
            show_alert=True,
        )
        return

    parts = str(
        cq.data or ""
    ).split("|")

    action = (
        parts[1]
        if len(parts) > 1
        else ""
    )

    short = (
        parts[2]
        if len(parts) > 2
        else ""
    )

    rec = (
        db.archive_by_message(
            cq.message.id
        )
        if cq.message
        else None
    )

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
                cui.confirm_keyboard(
                    rec["key"]
                )
            )
        except Exception:
            pass

        await cq.answer(
            "برای حذف تأیید کن"
        )
        return

    if action == "no":

        try:
            await cq.message.edit_reply_markup(
                cui.song_keyboard(
                    rec["key"]
                )
            )
        except Exception:
            pass

        await cq.answer(
            "انصراف داده شد"
        )
        return

    if action == "yes":

        db.archive_delete(
            key=rec["key"]
        )

        n = db.archive_count()

        try:
            await cq.message.delete()
        except Exception as e:
            LOGGER.debug(
                "delete archive msg: %s",
                e,
            )

        await channel.log(
            *cui.song_deleted(
                rec.get(
                    "title",
                    "",
                ),
                n,
            )
        )

        await cq.answer(
            "از دیتابیس حذف شد"
        )
        return

    await cq.answer()


# ================================================================
# حذف قدیمی آرشیو با ریپلای
# ================================================================

@Client.on_message(
    filters.channel
    & filters.reply
    & filters.regex(
        r"^\s*حذف\s*$"
    )
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
            and message.chat.id
            == config.ARCHIVE_CHANNEL
        ):
            return

        target = message.reply_to_message

        if not target:
            return

        rec = await channel.delete_from_archive(
            target
        )

        try:
            await message.delete()

            if rec:
                await target.delete()

        except Exception:
            pass

    except Exception as e:
        LOGGER.debug(
            "archive delete reply: %s",
            e,
        )


# ================================================================
# ثبت عضویت ربات
# ================================================================

@Client.on_chat_member_updated()
async def _on_member_update(
    client: Client,
    ev: ChatMemberUpdated,
):

    try:

        me = client.me

        if me is None:
            me = await client.get_me()

        who = (
            ev.new_chat_member
            or ev.old_chat_member
        )

        if (
            not who
            or not who.user
            or who.user.id != me.id
        ):
            return

        chat = ev.chat

        title = (
            getattr(
                chat,
                "title",
                "",
            )
            or str(chat.id)
        )

        adder = ev.from_user

        adder_name = ""
        adder_id = 0

        if adder:

            adder_name = (
                adder.first_name
                or (
                    adder.username
                    and "@"
                    + adder.username
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
            old_status
            in (
                None,
                "LEFT",
                "BANNED",
            )
            and new_status
            in (
                "MEMBER",
                "ADMINISTRATOR",
            )
        )

        removed = new_status in (
            "LEFT",
            "BANNED",
        )

        # کانال هم ثبت شود
        chat_type = str(
            getattr(
                chat,
                "type",
                "",
            )
        ).upper()

        if (
            "CHANNEL" in chat_type
            and new_status
            == "ADMINISTRATOR"
        ):
            db.add_chat(
                chat.id
            )

            LOGGER.info(
                "Channel registered: %s (%s)",
                title,
                chat.id,
            )

        if added:

            db.add_chat(
                chat.id
            )

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

    except Exception as e:
        LOGGER.debug(
            "member update log: %s",
            e,
        )
