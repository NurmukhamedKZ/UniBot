"""Thin aiogram bot process (Task 14, Phase 4 of docs/plan.md).

Only job: ``/start`` -> WebApp button (+ deep-link referral registration,
mirroring ``apps.api.auth.current_user``'s welcome-credit/referral logic so a
user who only ever talks to the bot -- never opens the Mini App -- still gets
credited once), Stars invoices/checkout, and a catch-all nudge back to the
Mini App. Everything else (catalog, watch, payments bookkeeping) lives in
apps/api and apps/worker; this process never touches watch_tasks.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    MenuButtonWebApp,
    PreCheckoutQuery,
    WebAppInfo,
)

from packages.db import ledger, referrals
from packages.db.pool import create_pool
from packages.db.payments import record_payment_and_credit
from packages.db.users import get_or_create_user
from packages.shared.config import (
    ADMIN_ID,
    BOT_USERNAME,
    PACKAGES,
    TELEGRAM_TOKEN,
    WEBAPP_URL,
    WELCOME_CREDITS,
)

logger = logging.getLogger(__name__)

router = Router()


def _webapp_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Открыть UniBot", web_app=WebAppInfo(url=WEBAPP_URL))]
        ]
    )


async def _ensure_user_and_referral(pool, telegram_id: int, username, first_name, referrer_tid) -> None:
    """Same atomic create+welcome-credit+referral dance as ``apps.api.auth.current_user``.

    Needed here too: a user who only ever talks to the bot (never opens the
    Mini App) still has to get their welcome credits and, if they arrived via
    a ``ref_<tid>`` deep link, their referral registered. Both are gated on
    ``was_created`` so a user who later opens the Mini App (where
    ``current_user`` runs the identical logic) can never be double-credited --
    ``get_or_create_user``'s insert-first race-safety works the same way
    regardless of which process wins the race to create the row.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            user, was_created = await get_or_create_user(conn, telegram_id, username, first_name)
            if was_created:
                await ledger.add_entry(conn, user_id=user["id"], delta=WELCOME_CREDITS, reason="bonus")
                if referrer_tid is not None:
                    await referrals.register(conn, referrer_tid=referrer_tid, referred_user_id=user["id"])


def _parse_referrer_tid(args: str | None) -> int | None:
    if not args or not args.startswith("ref_"):
        return None
    try:
        return int(args.removeprefix("ref_"))
    except ValueError:
        return None


@router.message(CommandStart())
async def handle_start(message: Message, command) -> None:
    pool = message.bot.pool  # attached in main()
    referrer_tid = _parse_referrer_tid(command.args)

    await _ensure_user_and_referral(
        pool,
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        referrer_tid=referrer_tid,
    )

    await message.answer(
        "UniBot — автопросмотр лекций UniX за кредиты + бесплатные транскрибы и ответы на тесты.\n\n"
        "Открой приложение 👇",
        reply_markup=_webapp_keyboard(),
    )


async def send_package_invoice(bot: Bot, chat_id: int, package: str) -> None:
    """Send a Telegram Stars invoice for one of the S/M/L packages.

    Currency ``XTR`` (Telegram Stars), ``provider_token=""`` (empty string --
    required for Stars, not a placeholder), amount is the raw Stars count
    (no multiplier), per the legacy bot's working flow this is copied from.
    """
    pkg = PACKAGES[package]
    await bot.send_invoice(
        chat_id=chat_id,
        title=f"Пакет {package} — {pkg['credits']} видео",
        description=f"{pkg['credits']} автопросмотров, не сгорают",
        payload=f"pkg_{package}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=f"Пакет {package}", amount=pkg["stars"])],
    )


@router.message(Command("buy"))
async def handle_buy(message: Message) -> None:
    args = message.text.split(maxsplit=1)
    package = args[1].strip().upper() if len(args) > 1 else None
    if package not in PACKAGES:
        await message.answer("Выбери пакет в приложении 👇", reply_markup=_webapp_keyboard())
        return
    await send_package_invoice(message.bot, message.chat.id, package)


@router.pre_checkout_query()
async def handle_pre_checkout(pre_checkout: PreCheckoutQuery) -> None:
    await pre_checkout.bot.answer_pre_checkout_query(pre_checkout.id, ok=True)


@router.message(F.successful_payment)
async def handle_successful_payment(message: Message) -> None:
    pool = message.bot.pool
    pay = message.successful_payment
    package = pay.invoice_payload.removeprefix("pkg_")
    if package not in PACKAGES:
        logger.error("successful_payment with unknown payload=%s", pay.invoice_payload)
        return

    user, _ = await get_or_create_user(
        pool, message.from_user.id, message.from_user.username, message.from_user.first_name
    )
    pkg = PACKAGES[package]

    credited = await record_payment_and_credit(
        pool,
        user_id=user["id"],
        provider="stars",
        external_id=pay.telegram_payment_charge_id,
        package=package,
        credits=pkg["credits"],
        amount=pay.total_amount,
        currency="XTR",
        raw=pay.model_dump(),
    )

    balance = await ledger.get_balance(pool, user["id"])
    if credited:
        await message.answer(f"Оплата получена! Начислено {pkg['credits']} кредитов. Баланс: {balance}.")
    else:
        # Duplicate successful_payment update (Telegram retry) -- credits
        # already granted by an earlier delivery of the same charge_id.
        await message.answer(f"Оплата уже была учтена ранее. Баланс: {balance}.")

    if credited and ADMIN_ID:
        try:
            await message.bot.send_message(
                int(ADMIN_ID),
                f"💰 Stars-оплата: @{message.from_user.username or message.from_user.id} "
                f"пакет {package} ({pkg['stars']} ⭐), charge_id={pay.telegram_payment_charge_id}",
            )
        except Exception:
            logger.exception("failed to notify admin of stars payment")


@router.message()
async def handle_fallback(message: Message) -> None:
    await message.answer("Открой приложение 👇", reply_markup=_webapp_keyboard())


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    bot = Bot(TELEGRAM_TOKEN)
    bot.pool = await create_pool()  # stashed on the Bot instance for handler access

    if BOT_USERNAME:
        await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="UniBot", web_app=WebAppInfo(url=WEBAPP_URL)))

    dp = Dispatcher()
    dp.include_router(router)

    try:
        await dp.start_polling(bot)
    finally:
        await bot.pool.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
