"""کانال پرداخت‌ها: ثبت سفارش، تأیید/لغو توسط مالک، اطلاع به خریدار.

جریان تأییدشده:
  کاربر فیش می‌فرستد → عکس فیش + مشخصات سفارش در کانال پرداخت‌ها با
  دکمه‌های [تأیید][لغو] → مالک تصمیم می‌گیرد → همان پیام ویرایش می‌شود
  (دکمه‌ها حذف، بررسی‌کننده ثبت) → به خریدار در PV اطلاع می‌رود:
    · تأیید → «اشتراک شما فعال شد»
    · لغو  → «سفارش شما لغو شد» + دکمه‌ی پشتیبانی

پرداخت استارز آنی است: اشتراک فوراً فعال و سفارش بدون دکمه در کانال ثبت می‌شود.

الگوی callback: `ord|ok|<oid>` و `ord|no|<oid>`
"""
from __future__ import annotations

import logging

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, Message

import config
from bot import auth
from bot import database as db
from bot import subscription as sub
from bot import group_config
from bot import ui
from bot.plugins import buy

LOGGER = logging.getLogger("musicbot.payments")


def channel_id() -> int:
    return config.PAYMENT_CHANNEL


# ---------------------------------------------------------------- متن‌ها
def _order_body(t: ui.Text, order: dict, group_name: str, buyer_name: str,
                buyer_id: int) -> ui.Text:
    """بدنه‌ی مشترک پیام سفارش (در کانال پرداخت‌ها)."""
    method = order.get("method", "")
    months = int(order.get("months") or 0)
    t.field(0, "گروه", ui.trunc(group_name, 34))
    t.field(1, "اشتراک", sub.duration_label(months))
    t.field(2, "مبلغ", ui.fa(_amount_text(order)))
    t.field(3, "روش", sub.METHOD_LABEL.get(method, method))
    t.field(4, "خریدار", mention=(buyer_name, buyer_id))
    t.field(5, "کد سفارش", code=order.get("id", ""))
    return t


def _amount_text(order: dict) -> str:
    """مبلغ ذخیره‌شده را با واحد درست برمی‌گرداند (کریپتو سِنت ذخیره شده)."""
    method = order.get("method", "")
    amount = int(order.get("amount") or 0)
    unit = sub.CURRENCY_LABEL.get(method, "")
    if method == sub.METHOD_CRYPTO:
        return f"{amount / 100:g} {unit}"
    return f"{amount:,} {unit}"


def order_caption(order: dict, group_name: str, buyer_name: str, buyer_id: int):
    """کپشن پیام کانال برای سفارش در انتظار تأیید."""
    t = ui.Text().title(ui.EMO_BELL, ui.BASE_ARROW, "سفارش اشتراک")
    _order_body(t, order, group_name, buyer_name, buyer_id)
    return t.text, t.entities


def order_keyboard(oid: str):
    return ui.kb([[ui.btn("تأیید", f"ord|ok|{oid}", ui.GREEN),
                   ui.btn("لغو", f"ord|no|{oid}", ui.RED)]])


def stars_notice(order: dict, group_name: str, buyer_name: str, buyer_id: int):
    """سفارش استارز — آنی تأیید شده، بدون دکمه."""
    t = ui.Text().title(ui.EMO_BELL, ui.BASE_ARROW, "پرداخت استارز — تأیید آنی")
    _order_body(t, order, group_name, buyer_name, buyer_id)
    t.add("\n")
    t.italic("اشتراک بلافاصله فعال شد.")
    return t.text, t.entities


def decision_caption(order: dict, group_name: str, buyer_name: str, buyer_id: int,
                     approved: bool, status_text: str = ""):
    """کپشن پس از تصمیم مالک (همان پیام ویرایش می‌شود).

    نام بررسی‌کننده عمداً نمایش داده نمی‌شود (تصمیم کاربر) — دکمه‌ها حذف
    می‌شوند و همین نشان می‌دهد سفارش بررسی شده است.
    """
    t = ui.Text().title(ui.EMO_PLAY if approved else ui.EMO_STOP,
                        ui.BASE_ARROW, "تأیید شد" if approved else "لغو شد")
    _order_body(t, order, group_name, buyer_name, buyer_id)
    t.add("\n")
    if approved:
        t.italic(f"اشتراک فعال شد — {status_text}")
    else:
        t.italic("به خریدار اطلاع داده شد.")
    return t.text, t.entities


def buyer_approved(group_name: str, months: int, status_text: str):
    t = ui.Text().title(ui.EMO_PLAY, ui.BASE_ARROW, "اشتراک شما فعال شد")
    t.field(0, "گروه", ui.trunc(group_name, 34))
    t.field(1, "اشتراک", sub.duration_label(months))
    t.field(2, "وضعیت", status_text)
    t.add("\nحالا در گروه بنویس ")
    t.code("پخش اهنگ <اسم>")
    t.add(" تا پخش شروع شود.\n")
    rows = [[ui.btn("اشتراک من", "my|list", ui.PLAIN, ui.EMO_LIST)]]
    return t.text, t.entities, ui.kb(rows)


def buyer_rejected(group_name: str, months: int, oid: str, support_url: str):
    t = ui.Text().title(ui.EMO_STOP, ui.BASE_ARROW, "سفارش شما لغو شد")
    t.field(0, "گروه", ui.trunc(group_name, 34))
    t.field(1, "اشتراک", sub.duration_label(months))
    t.field(2, "کد سفارش", code=oid)
    t.add("\n")
    t.italic("در صورت اعتراض با پشتیبانی تماس بگیرید.")
    rows = []
    if support_url:
        rows.append([ui.btn("پشتیبانی", None, ui.BLUE, None, url=support_url)])
    return t.text, t.entities, ui.kb(rows)


# ---------------------------------------------------------------- دریافت فیش
@Client.on_message(filters.private & (filters.photo | filters.document))
async def receipt_handler(client: Client, message: Message):
    """عکس فیش کاربری که «پرداخت کردم» را زده، به کانال پرداخت‌ها می‌رود."""
    if not message.from_user:
        return
    uid = message.from_user.id
    oid = buy.awaiting_receipt(uid)
    if not oid:
        return

    order = db.order_get(oid)
    if not order or order.get("status") != "pending":
        buy.clear_awaiting(uid)
        return

    ch = channel_id()
    if not ch:
        LOGGER.error("PAYMENT_CHANNEL تنظیم نشده — فیش سفارش %s جایی ثبت نشد", oid)
        t = ui.Text().title(ui.EMO_STOP, ui.BASE_ARROW, "ثبت فیش ناموفق بود")
        t.why("کانال پرداخت‌ها تنظیم نشده است.")
        t.how("با پشتیبانی تماس بگیر.")
        await message.reply_text(t.text, entities=t.entities)
        return

    group_name = await buy.chat_title(client, int(order["chat_id"]))
    buyer_name = message.from_user.first_name or str(uid)
    caption, ents = order_caption(order, group_name, buyer_name, uid)

    try:
        await message.copy(ch, caption=caption, caption_entities=ents,
                           reply_markup=order_keyboard(oid))
    except Exception as e:  # noqa: BLE001
        LOGGER.error("ارسال فیش به کانال پرداخت: %s", e)
        t = ui.Text().title(ui.EMO_STOP, ui.BASE_ARROW, "ثبت فیش ناموفق بود")
        t.why("ارسال به کانال پرداخت‌ها انجام نشد.")
        t.how("دوباره امتحان کن یا با پشتیبانی تماس بگیر.")
        await message.reply_text(t.text, entities=t.entities)
        return

    buy.clear_awaiting(uid)
    text, ents2, kb = buy.page_awaiting(oid)
    await message.reply_text(text, entities=ents2, reply_markup=kb)


# ---------------------------------------------------------------- تصمیم مالک
@Client.on_callback_query(filters.regex(r"^ord\|"))
async def order_decision_cb(client: Client, cq: CallbackQuery):
    if not cq.from_user or cq.from_user.id != auth.OWNER_ID:
        await cq.answer("فقط مالک می‌تواند سفارش را بررسی کند.", show_alert=True)
        return

    parts = str(cq.data or "").split("|")
    action = parts[1] if len(parts) > 1 else ""
    oid = parts[2] if len(parts) > 2 else ""
    order = db.order_get(oid)
    if not order:
        await cq.answer("سفارش پیدا نشد.", show_alert=True)
        return

    # idempotency: دو بار تأیید نباید دو بار اشتراک بدهد
    if order.get("status") != "pending":
        await cq.answer(f"این سفارش قبلاً بررسی شده ({order.get('status')}).",
                        show_alert=True)
        return

    chat_id = int(order["chat_id"])
    buyer_id = int(order["buyer_id"])
    months = int(order.get("months") or 0)
    group_name = await buy.chat_title(client, chat_id)
    buyer_name = await _user_name(client, buyer_id)

    if action == "ok":
        db.order_set_status(oid, "paid", f"تأیید مالک {cq.from_user.id}")
        sub.activate(chat_id, months, buyer_id=buyer_id)

        # بعد از تأیید اشتراک، این گروه هم خودکار فعال شود
        group_config.set_enabled(chat_id, True)

        status = sub.status_text(chat_id)
        cap, ents = decision_caption(order, group_name, buyer_name, buyer_id,
                                     True, status)
        await _edit_channel_msg(cq, cap, ents)
        await _notify_buyer_ok(client, buyer_id, group_name, months, status)
        await _notify_group(client, chat_id, group_name, status)
        await _log_sub(True, group_name, chat_id, months,
                      order.get("method", ""), status, oid)
        await cq.answer("تأیید شد و اشتراک فعال شد")
        return

    if action == "no":
        db.order_set_status(oid, "rejected", f"لغو مالک {cq.from_user.id}")
        cap, ents = decision_caption(order, group_name, buyer_name, buyer_id,
                                     False)
        await _edit_channel_msg(cq, cap, ents)
        await _notify_buyer_no(client, buyer_id, group_name, months, oid)
        await _log_sub(False, group_name, chat_id, months,
                      order.get("method", ""), "", oid)
        await cq.answer("سفارش لغو شد")
        return

    await cq.answer()


async def _log_sub(approved: bool, group: str, chat_id: int, months: int,
                   method: str, status: str, oid: str) -> None:
    """ثبت رویداد اشتراک در کانال لاگ — قبلاً هیچ‌جا ثبت نمی‌شد."""
    try:
        from bot import channel
        from bot import channel_ui as cui
        label = sub.METHOD_LABEL.get(method, method)
        if approved:
            await channel.log(*cui.sub_activated(group, chat_id, months, label,
                                                status))
        else:
            await channel.log(*cui.sub_rejected(group, chat_id, oid))
    except Exception as e:  # noqa: BLE001
        LOGGER.debug("log sub event: %s", e)


async def _edit_channel_msg(cq: CallbackQuery, caption: str, ents) -> None:
    """پیام کانال را ویرایش و دکمه‌ها را حذف می‌کند تا دوباره زده نشود."""
    try:
        if cq.message.photo or cq.message.document:
            await cq.message.edit_caption(caption, caption_entities=ents,
                                          reply_markup=None)
        else:
            await cq.message.edit_text(caption, entities=ents, reply_markup=None)
    except Exception as e:  # noqa: BLE001
        LOGGER.debug("edit channel msg: %s", e)


async def _user_name(client: Client, user_id: int) -> str:
    try:
        u = await client.get_users(user_id)
        if isinstance(u, list):
            u = u[0] if u else None
        return (getattr(u, "first_name", None) or str(user_id)) if u else str(user_id)
    except Exception:  # noqa: BLE001
        return str(user_id)


async def _notify_buyer_ok(client: Client, buyer_id: int, group_name: str,
                           months: int, status: str) -> None:
    text, ents, kb = buyer_approved(group_name, months, status)
    try:
        await client.send_message(buyer_id, text, entities=ents, reply_markup=kb)
    except Exception as e:  # noqa: BLE001
        LOGGER.warning("اطلاع تأیید به خریدار %s: %s", buyer_id, e)


async def _notify_buyer_no(client: Client, buyer_id: int, group_name: str,
                           months: int, oid: str) -> None:
    url = await auth.resolve_support_url(client)
    text, ents, kb = buyer_rejected(group_name, months, oid, url)
    try:
        await client.send_message(buyer_id, text, entities=ents, reply_markup=kb)
    except Exception as e:  # noqa: BLE001
        LOGGER.warning("اطلاع لغو به خریدار %s: %s", buyer_id, e)


async def _notify_group(client: Client, chat_id: int, group_name: str,
                        status: str) -> None:
    t = ui.Text().title(ui.EMO_PLAY, ui.BASE_ARROW, "اشتراک این گروه فعال شد")
    t.field(0, "وضعیت", status)
    t.add("\nبرای شروع بنویس ")
    t.code("پخش اهنگ <اسم>")
    t.add("\n")
    try:
        await client.send_message(chat_id, t.text, entities=t.entities)
    except Exception as e:  # noqa: BLE001
        LOGGER.debug("اطلاع فعال‌سازی در گروه %s: %s", chat_id, e)


# ---------------------------------------------------------------- استارز
@Client.on_message(filters.successful_payment)
async def stars_paid(client: Client, message: Message):
    """پرداخت استارز آنی است: فعال‌سازی فوری + ثبت در کانال بدون دکمه."""
    sp = message.successful_payment
    if not sp:
        return
    payload = str(getattr(sp, "invoice_payload", "") or "")
    if not payload.startswith(buy.PAYLOAD_PREFIX):
        return
    oid = payload.split("|", 1)[1] if "|" in payload else ""
    order = db.order_get(oid)
    if not order:
        LOGGER.warning("پرداخت استارز با سفارش ناشناس: %s", oid)
        return
    if order.get("status") == "paid":
        return                              # idempotency

    chat_id = int(order["chat_id"])
    buyer_id = int(order["buyer_id"])
    months = int(order.get("months") or 0)

    db.order_set_status(oid, "paid", "استارز")
    sub.activate(chat_id, months, buyer_id=buyer_id)

    # پرداخت استارز هم گروه را خودکار فعال می‌کند
    group_config.set_enabled(chat_id, True)

    status = sub.status_text(chat_id)

    group_name = await buy.chat_title(client, chat_id)
    buyer_name = str((message.from_user.first_name if message.from_user else "")
                     or buyer_id)

    text, ents, kb = buyer_approved(group_name, months, status)
    await message.reply_text(text, entities=ents, reply_markup=kb)
    await _notify_group(client, chat_id, group_name, status)

    ch = channel_id()
    if ch:
        cap, cents = stars_notice(order, group_name, buyer_name, buyer_id)
        try:
            await client.send_message(ch, cap, entities=cents)
        except Exception as e:  # noqa: BLE001
            LOGGER.debug("ثبت پرداخت استارز در کانال: %s", e)
