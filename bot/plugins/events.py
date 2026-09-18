from __future__ import annotations

import logging
import re

from pyrogram import Client, filters
from pyrogram.enums import ChatType
from pyrogram.types import ChatMemberUpdated, Message

from bot import config
from bot import database as db
from bot import group_config as gc


log = logging.getLogger("musicbot.events")


# ============================================================
# HELPERS
# ============================================================

def status_name(status) -> str:
    """Return Pyrogram status name safely."""
    try:
        return status.name
    except Exception:
        return str(status)


async def is_group_owner(
    client: Client,
    chat_id: int,
    user_id: int,
) -> bool:
    """Check whether user is the real Telegram group owner."""
    try:
        member = await client.get_chat_member(
            chat_id,
            user_id,
        )

        return status_name(member.status) == "OWNER"

    except Exception:
        return False


async def is_bot_admin(
    client: Client,
    chat_id: int,
) -> bool:
    """Check whether the bot is an administrator."""
    try:
        me = await client.get_me()

        member = await client.get_chat_member(
            chat_id,
            me.id,
        )

        return status_name(member.status) in {
            "ADMINISTRATOR",
            "OWNER",
        }

    except Exception:
        return False


def get_reply_user(message: Message):
    """Get the user from the replied message."""
    reply = message.reply_to_message

    if reply is None:
        return None

    return reply.from_user


def get_user_name(user) -> str:
    if user is None:
        return "کاربر"

    first_name = getattr(user, "first_name", None)

    if first_name:
        return first_name

    username = getattr(user, "username", None)

    if username:
        return f"@{username}"

    return str(user.id)


async def enable_chat(chat_id: int) -> bool:
    """Enable a chat in the existing database/config system."""
    try:
        db.add_chat(chat_id)
        gc.set_enabled(chat_id, True)
        return True

    except Exception:
        log.exception(
            "Failed to enable chat %s",
            chat_id,
        )
        return False


async def disable_chat(chat_id: int) -> bool:
    """Disable a chat in the existing database/config system."""
    try:
        gc.set_enabled(chat_id, False)
        return True

    except Exception:
        log.exception(
            "Failed to disable chat %s",
            chat_id,
        )
        return False


# ============================================================
# GROUP INSTALL
# ============================================================

@Client.on_message(
    filters.group
    & filters.text
    & filters.regex(
        r"^\s*(?:نصب ربات سایلنت|نصب ربات)\s*$",
        flags=re.IGNORECASE,
    )
)
async def install_bot(
    client: Client,
    message: Message,
):
    """
    Group installation.

    Only the real Telegram group owner can install the bot.
    """

    if message.from_user is None:
        return

    chat_id = message.chat.id

    if not await is_group_owner(
        client,
        chat_id,
        message.from_user.id,
    ):
        await message.reply_text(
            "❌ فقط مالک گروه می‌تواند ربات را نصب کند."
        )
        return

    if not await is_bot_admin(
        client,
        chat_id,
    ):
        await message.reply_text(
            "❌ ابتدا ربات را در گروه ادمین کنید."
        )
        return

    if not await enable_chat(chat_id):
        await message.reply_text(
            "❌ فعال‌سازی گروه انجام نشد."
        )
        return

    await message.reply_text(
        "✅ ربات سایلنت با موفقیت در گروه فعال شد.\n\n"
        "🎵 حالا گروه آماده استفاده از موزیک است."
    )


# ============================================================
# GROUP UNINSTALL
# ============================================================

@Client.on_message(
    filters.group
    & filters.text
    & filters.regex(
        r"^\s*(?:حذف نصب|حذف نصب ربات|غیرفعال سازی ربات)\s*$",
        flags=re.IGNORECASE,
    )
)
async def uninstall_bot(
    client: Client,
    message: Message,
):
    if message.from_user is None:
        return

    chat_id = message.chat.id

    if not await is_group_owner(
        client,
        chat_id,
        message.from_user.id,
    ):
        await message.reply_text(
            "❌ فقط مالک گروه می‌تواند ربات را حذف نصب کند."
        )
        return

    await disable_chat(chat_id)

    await message.reply_text(
        "✅ ربات از این گروه غیرفعال شد."
    )


# ============================================================
# PROMOTE MUSIC ADMIN
# ============================================================

@Client.on_message(
    filters.group
    & filters.text
    & filters.reply
    & filters.regex(
        r"^\s*(?:ترفیع موزیک|ارتقای موزیک)\s*$",
        flags=re.IGNORECASE,
    )
)
async def promote_music_admin(
    client: Client,
    message: Message,
):
    """
    Promote a replied user to music admin.

    Only the real Telegram group owner can do this.
    """

    if message.from_user is None:
        return

    chat_id = message.chat.id

    if not await is_group_owner(
        client,
        chat_id,
        message.from_user.id,
    ):
        await message.reply_text(
            "❌ فقط مالک گروه می‌تواند موزیک ادمین تعیین کند."
        )
        return

    if not gc.is_enabled(chat_id):
        await message.reply_text(
            "❌ ابتدا ربات را در گروه نصب کنید."
        )
        return

    target = get_reply_user(message)

    if target is None:
        await message.reply_text(
            "⚠️ دستور را روی پیام کاربر موردنظر ریپلای کنید."
        )
        return

    if target.is_bot:
        await message.reply_text(
            "❌ نمی‌توان ربات را موزیک ادمین کرد."
        )
        return

    try:
        db.add_music_admin(
            chat_id,
            target.id,
            get_user_name(target),
        )

    except Exception:
        log.exception(
            "Failed to promote music admin %s in %s",
            target.id,
            chat_id,
        )

        await message.reply_text(
            "❌ ذخیره موزیک ادمین انجام نشد."
        )
        return

    await message.reply_text(
        f"✅ {get_user_name(target)} موزیک ادمین شد.\n\n"
        "🎵 این کاربر اکنون می‌تواند از دستورات موزیک استفاده کند."
    )


# ============================================================
# DEMOTE MUSIC ADMIN
# ============================================================

@Client.on_message(
    filters.group
    & filters.text
    & filters.reply
    & filters.regex(
        r"^\s*(?:عزل موزیک|حذف موزیک|حذف موزیک ادمین)\s*$",
        flags=re.IGNORECASE,
    )
)
async def demote_music_admin(
    client: Client,
    message: Message,
):
    """
    Remove a replied user from music admins.
    """

    if message.from_user is None:
        return

    chat_id = message.chat.id

    if not await is_group_owner(
        client,
        chat_id,
        message.from_user.id,
    ):
        await message.reply_text(
            "❌ فقط مالک گروه می‌تواند موزیک ادمین را عزل کند."
        )
        return

    target = get_reply_user(message)

    if target is None:
        await message.reply_text(
            "⚠️ دستور را روی پیام موزیک ادمین ریپلای کنید."
        )
        return

    try:
        result = db.remove_music_admin(
            chat_id,
            target.id,
        )

    except Exception:
        log.exception(
            "Failed to remove music admin %s from %s",
            target.id,
            chat_id,
        )

        await message.reply_text(
            "❌ حذف موزیک ادمین انجام نشد."
        )
        return

    if result:
        await message.reply_text(
            f"✅ {get_user_name(target)} از موزیک ادمینی عزل شد."
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
    & filters.text
    & filters.reply
    & filters.regex(
        r"^\s*مالک موزیک\s*$",
        flags=re.IGNORECASE,
    )
)
async def set_music_owner(
    client: Client,
    message: Message,
):
    """
    Set a replied user as music owner.

    The existing database uses the music-admin table,
    so this does not introduce a new database system.
    """

    if message.from_user is None:
        return

    chat_id = message.chat.id

    if not await is_group_owner(
        client,
        chat_id,
        message.from_user.id,
    ):
        await message.reply_text(
            "❌ فقط مالک گروه می‌تواند مالک موزیک را تعیین کند."
        )
        return

    if not gc.is_enabled(chat_id):
        await message.reply_text(
            "❌ ابتدا ربات را در گروه نصب کنید."
        )
        return

    target = get_reply_user(message)

    if target is None:
        await message.reply_text(
            "⚠️ دستور را روی پیام کاربر موردنظر ریپلای کنید."
        )
        return

    if target.is_bot:
        await message.reply_text(
            "❌ نمی‌توان ربات را مالک موزیک کرد."
        )
        return

    try:
        db.add_music_admin(
            chat_id,
            target.id,
            get_user_name(target),
        )

    except Exception:
        log.exception(
            "Failed to set music owner %s in %s",
            target.id,
            chat_id,
        )

        await message.reply_text(
            "❌ ذخیره مالک موزیک انجام نشد."
        )
        return

    await message.reply_text(
        f"👑 {get_user_name(target)} به عنوان مالک موزیک تعیین شد."
    )


# ============================================================
# CHAT MEMBER UPDATE
# ============================================================

@Client.on_chat_member_updated()
async def chat_member_update(
    client: Client,
    update: ChatMemberUpdated,
):
    """
    Handle bot membership changes.

    CHANNEL:
        - No installation command.
        - No subscription.
        - No music-admin promotion.
        - Automatically enabled when bot becomes admin.

    GROUP:
        - Installation remains controlled by the owner.
        - Joining the group does not automatically activate it.
    """

    try:
        chat = update.chat

        if chat is None:
            return

        new_member = update.new_chat_member

        if new_member is None:
            return

        changed_user = new_member.user

        if changed_user is None:
            return

        me = await client.get_me()

        # We only care about changes involving the bot itself.
        if changed_user.id != me.id:
            return

        new_status = status_name(
            new_member.status
        )

        # ====================================================
        # CHANNEL
        # ====================================================

        if chat.type == ChatType.CHANNEL:

            if new_status in {
                "ADMINISTRATOR",
                "OWNER",
            }:
                # Channel has no install/subscription requirement.
                await enable_chat(chat.id)

                log.info(
                    "Channel automatically enabled: %s",
                    chat.id,
                )

            elif new_status in {
                "LEFT",
                "BANNED",
            }:
                await disable_chat(chat.id)

                log.info(
                    "Channel disabled: %s",
                    chat.id,
                )

            return

        # ====================================================
        # GROUP / SUPERGROUP
        # ====================================================

        if chat.type not in {
            ChatType.GROUP,
            ChatType.SUPERGROUP,
        }:
            return

        if new_status in {
            "MEMBER",
            "ADMINISTRATOR",
            "OWNER",
        }:
            log.info(
                "Bot joined/updated in group: %s",
                chat.id,
            )

            # IMPORTANT:
            # Do NOT automatically enable a group.
            # The owner must use "نصب ربات سایلنت".

        elif new_status in {
            "LEFT",
            "BANNED",
        }:
            await disable_chat(chat.id)

            log.info(
                "Bot removed from group: %s",
                chat.id,
            )

    except Exception:
        log.exception(
            "Failed to process chat member update"
        )


# ============================================================
# PRIVATE START
# ============================================================

@Client.on_message(
    filters.private
    & filters.command(
        "start",
        prefixes="/",
    )
)
async def private_start(
    client: Client,
    message: Message,
):
    try:
        await message.reply_text(
            "سلام 👋\n\n"
            "🎵 من ربات موزیک پلیر هستم.\n\n"
            "برای استفاده در گروه، ربات را به گروه اضافه و "
            "ادمین کنید."
        )

    except Exception:
        log.exception(
            "Private start handler failed"
        )


# ============================================================
# ID
# ============================================================

@Client.on_message(
    filters.group
    & filters.text
    & filters.regex(
        r"^\s*آیدی\s*$",
        flags=re.IGNORECASE,
    )
)
async def show_id(
    client: Client,
    message: Message,
):
    if message.from_user is None:
        return

    await message.reply_text(
        f"🆔 آیدی شما:\n`{message.from_user.id}`"
    )


# ============================================================
# ONLINE
# ============================================================

@Client.on_message(
    filters.group
    & filters.text
    & filters.regex(
        r"^\s*(?:آنلاین|همیشه آنلاینم|پینگ)\s*$",
        flags=re.IGNORECASE,
    )
)
async def online_status(
    client: Client,
    message: Message,
):
    await message.reply_text(
        "🟢 همیشه آنلاینم."
    )
