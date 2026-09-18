"""
لاگ رویدادها + مدیریت کانال دیتابیس + نصب و ترفیع ربات سایلنت

قابلیت‌ها:
1) ثبت رویدادهای عضویت ربات در کانال لاگ
2) مدیریت آرشیو آهنگ
3) نصب ربات با:
   نصب ربات سایلنت
4) ترفیع دائمی ربات با ریپلای:
   ترفیع ربات سایلنت

بعد از نصب یا ترفیع:
- چت در دیتابیس ثبت می‌شود
- پلیر فعال می‌شود
- دسترسی پخش دائمی می‌شود
- خرید اشتراک لازم نیست
- دکمه‌های خرید تغییر نمی‌کنند
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
# فعال‌سازی دائمی چت
# ================================================================

async def _activate_chat(client: Client, chat_id: int) -> bool:
    """ثبت، روشن‌کردن و دائمی‌کردن یک گروه/کانال."""

    try:
        db.add_chat(chat_id)

        group_config.set_enabled(
            chat_id,
            True,
        )

        subscription.make_permanent(
            chat_id,
        )

        LOGGER.info(
            "Silent bot permanently activated: %s",
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
    نصب ربات در گروه یا کانال.

    ربات باید قبلاً در آن چت ادمین شده باشد.
    """

    try:
        chat = message.chat

        if not chat:
            return

        chat_type = str(
            getattr(chat, "type", "")
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

        me = client.me

        if me is None:
            me = await client.get_me()

        # --------------------------------------------------------
        # بررسی ادمین بودن خود ربات
        # --------------------------------------------------------

        try:
            bot_member = await client.get_chat_member(
                chat_id,
                me.id,
            )

            bot_status = getattr(
                bot_member.status,
                "name",
                str(bot_member.status),
            ).upper()

        except Exception as e:
            LOGGER.warning(
                "install bot status check failed: %s",
                e,
            )
            return

        if bot_status not in (
            "ADMINISTRATOR",
            "OWNER",
        ):
            return

        # --------------------------------------------------------
        # در گروه فقط مدیر گروه بتواند نصب کند
        # --------------------------------------------------------

        is_channel = "CHANNEL" in chat_type

        if not is_channel:

            user = message.from_user

            if not user:
                return

            try:
                user_member = await client.get_chat_member(
                    chat_id,
                    user.id,
                )

                user_status = getattr(
                    user_member.status,
                    "name",
                    str(user_member.status),
                ).upper()

            except Exception as e:
                LOGGER.warning(
                    "installer status check failed: %s",
                    e,
                )
                return

            if user_status not in (
                "ADMINISTRATOR",
                "OWNER",
            ):
                return

        # --------------------------------------------------------
        # فعال‌سازی دائمی
        # --------------------------------------------------------

        ok = await _activate_chat(
            client,
            chat_id,
        )

        if not ok:
            return

        try:
            await message.reply_text(
                "✅ ربات سایلنت نصب و فعال شد.\n"
                "🎵 پلیر آماده پخش است."
            )
        except Exception:
            pass

    except Exception as e:
        LOGGER.exception(
            "silent install failed: %s",
            e,
        )


# ================================================================
# ترفیع ربات سایلنت
# ================================================================

@Client.on_message(
    filters.text
    & filters.reply
    & filters.regex(
        r"^\s*ترفیع\s+ربات\s+سایلنت\s*$"
    )
)
async def _on_promote_silent(
    client: Client,
    message: Message,
):
    """
    وقتی مدیر گروه روی پیام یک کاربر ریپلای کند و بنویسد:

        ترفیع ربات سایلنت

    همان گروه بدون خرید اشتراک دائمی فعال می‌شود.
    """

    try:
        chat = message.chat

        if not chat:
            return

        chat_type = str(
            getattr(chat, "type", "")
        ).upper()

        # این دستور مخصوص گروه/سوپرگروه است
        if not any(
            x in chat_type
            for x in (
                "GROUP",
                "SUPERGROUP",
            )
        ):
            return

        user = message.from_user

        # باید فرستنده‌ی دستور مدیر باشد
        if not user:
            return

        try:
            member = await client.get_chat_member(
                chat.id,
                user.id,
            )

            status = getattr(
                member.status,
                "name",
                str(member.status),
            ).upper()

        except Exception as e:
            LOGGER.warning(
                "promote installer check failed: %s",
                e,
            )
            return

        if status not in (
            "ADMINISTRATOR",
            "OWNER",
        ):
            return

        # ربات باید خودش ادمین باشد
        me = client.me

        if me is None:
            me = await client.get_me()

        try:
            bot_member = await client.get_chat_member(
                chat.id,
                me.id,
            )

            bot_status = getattr(
                bot_member.status,
                "name",
                str(bot_member.status),
            ).upper()

        except Exception as e:
            LOGGER.warning(
                "promote bot status check failed: %s",
                e,
            )
            return

        if bot_status not in (
            "ADMINISTRATOR",
            "OWNER",
        ):
            return

        # --------------------------------------------------------
        # ریپلای باید وجود داشته باشد
        # --------------------------------------------------------

        target = message.reply_to_message

        if not target:
            return

        # --------------------------------------------------------
        # فعال‌سازی دائمی همان گروه
        # --------------------------------------------------------

        ok = await _activate_chat(
            client,
            chat.id,
        )

        if not ok:
            return

        target_user = target.from_user

        if target_user:
            target_name = (
                target_user.first_name
                or (
                    target_user.username
                    and "@"
                    + target_user.username
                )
                or str(target_user.id)
            )
        else:
            target_name = "کاربر"

        try:
            await message.reply_text(
                "✅ ترفیع انجام شد.\n"
                f"👤 {target_name}\n"
                "🎵 ربات سایلنت برای این گروه فعال شد.\n"
                "♾️ دسترسی دائمی است."
            )
        except Exception:
            pass

        LOGGER.info(
            "Silent promotion: chat=%s target=%s by=%s",
            chat.id,
            getattr(
                target_user,
                "id",
                0,
            ),
            user.id,
        )

    except Exception as e:
        LOGGER.exception(
            "silent promotion failed: %s",
            e,
        )


# ================================================================
# کانال دیتابیس
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

        media = message.audio or message.video

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

    media = message.audio or message.video

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
            _processing.add(sent.id)

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
# حذف قدیمی با ریپلای
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
