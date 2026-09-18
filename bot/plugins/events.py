from __future__ import annotations

import asyncio
import logging
import time

from pyrogram import Client, filters
from pyrogram.enums import ChatMemberStatus, ChatType
from pyrogram.types import CallbackQuery, ChatMemberUpdated, Message

from bot import config
from bot import database as db
from bot import group_config as gc
from bot import subscription as sub
from bot import texts
from bot import ui
from bot import auth


log = logging.getLogger("musicbot.events")


# ============================================================
# Helpers
# ============================================================

def _status_name(status) -> str:
    try:
        return status.name
    except Exception:
        return str(status)


async def _is_chat_owner(client: Client, chat_id: int, user_id: int) -> bool:
    """
    فقط OWNER واقعی گروه/سوپرگروه.
    OWNER_ID به‌تنهایی مالک گروه محسوب نمی‌شود.
    """
    try:
        member = await client.get_chat_member(chat_id, user_id)
        return _status_name(member.status) == ChatMemberStatus.OWNER.name
    except Exception:
        return False


async def _is_group_admin(client: Client, chat_id: int, user_id: int) -> bool:
    try:
        member = await client.get_chat_member(chat_id, user_id)
        return _status_name(member.status) in {
            ChatMemberStatus.OWNER.name,
            ChatMemberStatus.ADMINISTRATOR.name,
        }
    except Exception:
        return False


async def _is_bot_admin(client: Client, chat_id: int) -> bool:
    try:
        me = await client.get_me()
        member = await client.get_chat_member(chat_id, me.id)
        return _status_name(member.status) in {
            ChatMemberStatus.OWNER.name,
            ChatMemberStatus.ADMINISTRATOR.name,
        }
    except Exception:
        return False


def _target_user(message: Message):
    """
    کاربر هدف برای ترفیع/عزل:
    فقط از ریپلای گرفته می‌شود.
    """
    reply = message.reply_to_message

    if not reply:
        return None

    return reply.from_user


def _target_name(message: Message) -> str:
    user = _target_user(message)

    if not user:
        return "کاربر"

    if user.first_name:
        return user.first_name

    if user.username:
        return f"@{user.username}"

    return str(user.id)


async def _activate_chat(chat_id: int):
    db.add_chat(chat_id)
    gc.set_enabled(chat_id, True)


async def _deactivate_chat(chat_id: int):
    gc.set_enabled(chat_id, False)


# ============================================================
# GROUP INSTALL
# ============================================================

@Client.on_message(
    filters.group
    & filters.regex(
        r"^(?:نصب ربات سایلنت|نصب ربات)$",
        flags=__import__("re").IGNORECASE,
    )
)
async def install_bot(client: Client, message: Message):
    """
    نصب فقط برای گروه.
    کانال نیازی به نصب ندارد.
    """

    if not await _is_chat_owner(
        client,
        message.chat.id,
        message.from_user.id if message.from_user else 0,
    ):
        await message.reply_text(
            "❌ فقط مالک گروه می‌تواند ربات را نصب کند."
        )
        return

    if not await _is_bot_admin(client, message.chat.id):
        await message.reply_text(
            "❌ ابتدا ربات را در گروه ادمین کنید."
        )
        return

    await _activate_chat(message.chat.id)

    await message.reply_text(
        "✅ ربات سایلنت با موفقیت در گروه فعال شد.\n\n"
        "🎵 حالا اعضای مجاز گروه می‌توانند از موزیک استفاده کنند."
    )


# ============================================================
# GROUP UNINSTALL
# ============================================================

@Client.on_message(
    filters.group
    & filters.regex(
        r"^(?:حذف نصب|حذف نصب ربات|غیرفعال سازی ربات)$",
        flags=__import__("re").IGNORECASE,
    )
)
async def uninstall_bot(client: Client, message: Message):

    if not await _is_chat_owner(
        client,
        message.chat.id,
        message.from_user.id if message.from_user else 0,
    ):
        await message.reply_text(
            "❌ فقط مالک گروه می‌تواند ربات را حذف نصب کند."
        )
        return

    await _deactivate_chat(message.chat.id)

    await message.reply_text(
        "✅ ربات از این گروه غیرفعال شد."
    )


# ============================================================
# MUSIC ADMIN PROMOTION
# ============================================================

@Client.on_message(
    filters.group
    & filters.regex(
        r"^(?:ترفیع موزیک|ارتقای موزیک)$",
        flags=__import__("re").IGNORECASE,
    )
)
async def promote_music_admin(client: Client, message: Message):

    # فقط مالک واقعی گروه
    if not await _is_chat_owner(
        client,
        message.chat.id,
        message.from_user.id if message.from_user else 0,
    ):
        await message.reply_text(
            "❌ فقط مالک گروه می‌تواند موزیک ادمین تعیین کند."
        )
        return

    # گروه باید قبلاً نصب شده باشد
    if not gc.is_enabled(message.chat.id):
        await message.reply_text(
            "❌ ابتدا ربات را در گروه نصب کنید."
        )
        return

    target = _target_user(message)

    if not target:
        await message.reply_text(
            "⚠️ این دستور را روی پیام کاربر موردنظر ریپلای کنید."
        )
        return

    if target.is_bot:
        await message.reply_text(
            "❌ نمی‌توان ربات را موزیک ادمین کرد."
        )
        return

    db.add_music_admin(
        message.chat.id,
        target.id,
        target.first_name or target.username or "",
    )

    await message.reply_text(
        f"✅ {target.first_name or 'کاربر'} به عنوان موزیک ادمین تعیین شد.\n\n"
        "🎵 اکنون می‌تواند دستورات موزیک را در این گروه استفاده کند."
    )


# ============================================================
# MUSIC ADMIN DEMOTION
# ============================================================

@Client.on_message(
    filters.group
    & filters.regex(
        r"^(?:عزل موزیک|حذف موزیک|حذف موزیک ادمین)$",
        flags=__import__("re").IGNORECASE,
    )
)
async def demote_music_admin(client: Client, message: Message):

    # فقط مالک واقعی گروه
    if not await _is_chat_owner(
        client,
        message.chat.id,
        message.from_user.id if message.from_user else 0,
    ):
        await message.reply_text(
            "❌ فقط مالک گروه می‌تواند موزیک ادمین را عزل کند."
        )
        return

    target = _target_user(message)

    if not target:
        await message.reply_text(
            "⚠️ این دستور را روی پیام موزیک ادمین ریپلای کنید."
        )
        return

    removed = db.remove_music_admin(
        message.chat.id,
        target.id,
    )

    if removed:
        await message.reply_text(
            f"✅ {target.first_name or 'کاربر'} از موزیک ادمینی عزل شد."
        )
    else:
        await message.reply_text(
            "ℹ️ این کاربر موزیک ادمین این گروه نبود."
        )


# ============================================================
# MUSIC OWNER
# ============================================================

@Client.on_message(
    filters.group
    & filters.regex(
        r"^مالک موزیک$",
        flags=__import__("re").IGNORECASE,
    )
)
async def music_owner(client: Client, message: Message):

    if not await _is_chat_owner(
        client,
        message.chat.id,
        message.from_user.id if message.from_user else 0,
    ):
        await message.reply_text(
            "❌ فقط مالک گروه می‌تواند مالک موزیک را مدیریت کند."
        )
        return

    target = _target_user(message)

    if not target:
        await message.reply_text(
            "⚠️ این دستور را روی پیام کاربر ریپلای کنید."
        )
        return

    db.add_music_admin(
        message.chat.id,
        target.id,
        target.first_name or target.username or "",
    )

    await message.reply_text(
        f"👑 {target.first_name or 'کاربر'} به عنوان مالک موزیک تعیین شد."
    )


# ============================================================
# CHAT MEMBER EVENTS
# ============================================================

@Client.on_chat_member_updated()
async def _on_member_update(
    client: Client,
    update: ChatMemberUpdated,
):

    try:
        chat = update.chat

        old_status = _status_name(
            update.old_chat_member.status
            if update.old_chat_member
            else ""
        )

        new_status = _status_name(
            update.new_chat_member.status
            if update.new_chat_member
            else ""
        )

        # ----------------------------------------------------
        # CHANNEL
        # ----------------------------------------------------
        # کانال کاملاً مستقل است:
        # - نصب ندارد
        # - اشتراک ندارد
        # - ترفیع موزیک ندارد
        # ----------------------------------------------------
        if chat.type == ChatType.CHANNEL:

            # وقتی ربات ادمین کانال شد، کانال را فعال کن.
            if new_status in {
                ChatMemberStatus.ADMINISTRATOR.name,
                ChatMemberStatus.OWNER.name,
            }:
                await _activate_chat(chat.id)

                log.info(
                    "Channel enabled automatically: %s",
                    chat.id,
                )

            return

        # ----------------------------------------------------
        # GROUP / SUPERGROUP
        # ----------------------------------------------------
        if chat.type not in {
            ChatType.GROUP,
            ChatType.SUPERGROUP,
        }:
            return

        me = await client.get_me()

        # اگر خود ربات اضافه شد
        if (
            update.new_chat_member
            and update.new_chat_member.user
            and update.new_chat_member.user.id == me.id
        ):

            # ربات فقط با نصب مالک فعال می‌شود.
            if new_status in {
                ChatMemberStatus.MEMBER.name,
                ChatMemberStatus.ADMINISTRATOR.name,
            }:
                log.info(
                    "Bot joined group: %s",
                    chat.id,
                )

            # اگر از گروه حذف شد
            if new_status in {
                ChatMemberStatus.LEFT.name,
                ChatMemberStatus.BANNED.name,
            }:
                await _deactivate_chat(chat.id)

                log.info(
                    "Bot removed from group: %s",
                    chat.id,
                )

            return

    except Exception:
        log.exception("Failed to process chat member update")


# ============================================================
# START / GENERAL EVENTS
# ============================================================

@Client.on_message(
    filters.private
    & filters.command(
        "start",
        prefixes="/",
    )
)
async def private_start(client: Client, message: Message):

    try:
        await message.reply_text(
            "سلام 👋\n\n"
            "🎵 من ربات موزیک پلیر هستم.\n\n"
            "برای استفاده از موزیک، ربات را در گروه خود نصب کنید."
        )
    except Exception:
        log.exception("private_start failed")


# ============================================================
# ID COMMAND
# ============================================================

@Client.on_message(
    filters.group
    & filters.regex(
        r"^آیدی$",
        flags=__import__("re").IGNORECASE,
    )
)
async def show_id(client: Client, message: Message):

    user = message.from_user

    if not user:
        return

    await message.reply_text(
        f"🆔 آیدی شما:\n`{user.id}`"
    )


# ============================================================
# ALWAYS ONLINE / PING
# ============================================================

@Client.on_message(
    filters.group
    & filters.regex(
        r"^(?:آنلاین|همیشه آنلاینم|پینگ)$",
        flags=__import__("re").IGNORECASE,
    )
)
async def online_status(client: Client, message: Message):

    await message.reply_text(
        "🟢 همیشه آنلاینم."
    )


# ============================================================
# ERROR HANDLER
# ============================================================

@Client.on_callback_query()
async def callback_guard(
    client: Client,
    query: CallbackQuery,
):

    try:
        if query.message:
            await query.answer()
    except Exception:
        pass
