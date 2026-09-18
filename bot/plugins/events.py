"""
لاگ رویدادها + مدیریت کانال دیتابیس + نصب/حذف نصب + مدیران موزیک

قوانین:

گروه:

- نصب ربات فقط توسط OWNER_ID
- حذف نصب فقط توسط OWNER_ID
- اشتراک/فعال‌سازی گروه طبق سیستم موجود
- فقط مالک همان گروه می‌تواند «ترفیع موزیک» انجام دهد
- فقط مالک همان گروه می‌تواند «عزل موزیک» انجام دهد
- مدیر موزیک در SQLite ذخیره می‌شود و دائمی است

کانال:

- نصب ربات لازم نیست
- اشتراک لازم نیست
- ترفیع موزیک لازم نیست
- با اضافه شدن ربات به عنوان ادمین، کانال برای پخش آماده می‌شود
- سیستم آرشیو و دکمه‌های خرید دست‌نخورده می‌مانند
  """

from future import annotations

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

================================================================

ابزارهای کمکی

================================================================

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
"""
بررسی مالک بودن کاربر در گروه.

نکته:
OWNER_ID مالک اصلی ربات محسوب می‌شود،
اما برای ترفیع موزیک فقط مالک همان گروه مجاز است.
"""

if not user_id:
    return False

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

target = getattr(
    message,
    "reply_to_message",
    None,
)

if not target:
    return "کاربر"

user = getattr(
    target,
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

================================================================

فعال‌سازی گروه

================================================================

async def _activate_chat(
client: Client,
chat_id: int,
) -> bool:
"""
فعال‌سازی دائمی گروه.

این قسمت مخصوص گروه است.
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
        "Silent bot permanently installed in group: %s",
        chat_id,
    )

    return True

except Exception as e:
    LOGGER.exception(
        "activate group failed: %s",
        e,
    )

    return False

================================================================

غیرفعال‌سازی گروه

================================================================

async def _deactivate_chat(
client: Client,
chat_id: int,
) -> bool:
"""
حذف نصب گروه.

اطلاعات گروه حذف نمی‌شود،
فقط دسترسی پلیر خاموش می‌شود.
"""

try:
    db.add_chat(chat_id)

    group_config.set_enabled(
        chat_id,
        False,
    )

    subscription.sub_delete(
        chat_id,
    )

    LOGGER.info(
        "Silent bot uninstalled from group: %s",
        chat_id,
    )

    return True

except Exception as e:
    LOGGER.exception(
        "deactivate group failed: %s",
        e,
    )

    return False

================================================================

نصب ربات سایلنت - فقط گروه

================================================================

@Client.on_message(
filters.text
& filters.regex(
r"^\sنصب\s+ربات\s+سایلنت\s$"
)
)
async def _on_install_silent(
client: Client,
message: Message,
):
"""
نصب فقط برای گروه.

کانال به این دستور نیازی ندارد.
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

    # کانال عمداً از نصب خارج است
    if chat_type not in (
        "GROUP",
        "SUPERGROUP",
    ):
        return

    chat_id = int(chat.id)

    # ربات باید ادمین باشد
    if not await _is_bot_admin(
        client,
        chat_id,
    ):
        await message.reply_text(
            "❌ اول ربات سایلنت را ادمین گروه کن."
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
        "🎵 پلیر گروه فعال شد.\n"
        "♾️ نصب دائمی است.\n\n"
        "👑 حالا فقط مالک گروه می‌تواند "
        "«ترفیع موزیک» انجام دهد."
    )

except Exception as e:
    LOGGER.exception(
        "silent install failed: %s",
        e,
    )

================================================================

حذف نصب - فقط گروه

================================================================

@Client.on_message(
filters.text
& filters.regex(
r"^\sحذف\s+نصب\s$"
)
)
async def _on_uninstall_silent(
client: Client,
message: Message,
):
"""
حذف نصب فقط برای گروه.
"""

try:
    if not message.chat:
        return

    user = message.from_user

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

    if chat_type not in (
        "GROUP",
        "SUPERGROUP",
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
        "🛑 ربات سایلنت از گروه حذف نصب شد.\n"
        "🎵 پلیر گروه غیرفعال شد.\n\n"
        "برای فعال‌سازی دوباره:\n"
        "نصب ربات سایلنت"
    )

except Exception as e:
    LOGGER.exception(
        "silent uninstall failed: %s",
        e,
    )

================================================================

ترفیع موزیک

================================================================

@Client.on_message(
filters.text
& filters.reply
& filters.regex(
r"^\sترفیع\s+موزیک\s$"
)
)
async def _on_promote_music(
client: Client,
message: Message,
):
"""
فقط مالک همان گروه می‌تواند ترفیع موزیک انجام دهد.

استفاده:

روی پیام کاربر ریپلای کن:
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

    # فقط گروه
    if chat_type not in (
        "GROUP",
        "SUPERGROUP",
    ):
        return

    command_user = message.from_user

    if not command_user:
        return

    # فقط مالک همان گروه
    allowed = await _is_chat_owner(
        client,
        chat.id,
        command_user.id,
    )

    if not allowed:
        await message.reply_text(
            "❌ فقط مالک گروه می‌تواند "
            "مدیر موزیک تعیین کند."
        )
        return

    # گروه باید نصب شده باشد
    if not group_config.is_enabled(
        chat.id
    ):
        await message.reply_text(
            "❌ ربات در این گروه نصب نیست."
        )
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
        await message.reply_text(
            "❌ روی پیام کاربر ریپلای کن."
        )
        return

    target_user = target.from_user

    if not target_user:
        await message.reply_text(
            "❌ روی پیام یک کاربر ریپلای کن."
        )
        return

    # ربات را مدیر موزیک نکن
    me = client.me

    if me is None:
        me = await client.get_me()

    if target_user.id == me.id:
        await message.reply_text(
            "❌ نمی‌توانی خود ربات را مدیر موزیک کنی."
        )
        return

    # مالک گروه را لازم نیست مدیر موزیک کنیم
    if await _is_chat_owner(
        client,
        chat.id,
        target_user.id,
    ):
        await message.reply_text(
            "ℹ️ مالک گروه خودش دسترسی کامل دارد."
        )
        return

    db.add_music_admin(
        chat.id,
        target_user.id,
        _target_name(message),
    )

    await message.reply_text(
        "✅ ترفیع موزیک انجام شد.\n"
        f"👤 {_target_name(message)}\n"
        "🎵 دسترسی مدیریت موزیک فعال شد.\n"
        "♾️ دائمی است تا زمان «عزل موزیک»."
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

================================================================

عزل موزیک

================================================================

@Client.on_message(
filters.text
& filters.reply
& filters.regex(
r"^\sعزل\s+موزیک\s$"
)
)
async def _on_demote_music(
client: Client,
message: Message,
):
"""
فقط مالک همان گروه می‌تواند عزل موزیک انجام دهد.
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

    if chat_type not in (
        "GROUP",
        "SUPERGROUP",
    ):
        return

    command_user = message.from_user

    if not command_user:
        return

    # فقط مالک گروه
    allowed = await _is_chat_owner(
        client,
        chat.id,
        command_user.id,
    )

    if not allowed:
        await message.reply_text(
            "❌ فقط مالک گروه می‌تواند "
            "مدیر موزیک را عزل کند."
        )
        return

    # گروه باید نصب شده باشد
    if not group_config.is_enabled(
        chat.id
    ):
        await message.reply_text(
            "❌ ربات در این گروه نصب نیست."
        )
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
        await message.reply_text(
            "❌ روی پیام مدیر موزیک ریپلای کن."
        )
        return

    target_user = target.from_user

    if not target_user:
        await message.reply_text(
            "❌ روی پیام یک کاربر ریپلای کن."
        )
        return

    # مالک گروه را نمی‌توان عزل کرد
    if await _is_chat_owner(
        client,
        chat.id,
        target_user.id,
    ):
        await message.reply_text(
            "❌ مالک گروه قابل عزل نیست."
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
        f"👤 {_target_name(message)}\n"
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

================================================================

آرشیو آهنگ در کانال

================================================================

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

================================================================

دکمه حذف آرشیو

================================================================

@Client.on_callback_query(
filters.regex(r"^arch|")
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

================================================================

حذف آرشیو با ریپلای

================================================================

@Client.on_message(
filters.channel
& filters.reply
& filters.regex(
r"^\sحذف\s$"
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

================================================================

ثبت عضویت ربات + آماده‌سازی کانال

================================================================

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

    chat_type = str(
        getattr(
            chat,
            "type",
            "",
        )
    ).upper()

    # ========================================================
    # کانال
    # ========================================================
    #
    # کانال هیچ نصب و اشتراکی نمی‌خواهد.
    #
    # وقتی ربات ادمین کانال شد:
    # - کانال در DB ثبت می‌شود
    # - برای پلیر فعال می‌شود
    # - subscription ساخته نمی‌شود
    #
    if (
        "CHANNEL" in chat_type
        and new_status
        == "ADMINISTRATOR"
    ):

        db.add_chat(
            chat.id
        )

        # فعال‌سازی فنی برای پلیر،
        # نه نصب و نه اشتراک.
        group_config.set_enabled(
            chat.id,
            True,
        )

        LOGGER.info(
            "Channel ready for music playback without subscription: %s (%s)",
            title,
            chat.id,
        )

    # ========================================================
    # گروه
    # ========================================================

    if (
        "GROUP" in chat_type
        or "SUPERGROUP" in chat_type
    ):

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
