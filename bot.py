import asyncio
import html
import logging
import os
from dataclasses import dataclass
from typing import Dict, Optional

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# Загружаем переменные окружения из .env (если такой файл есть рядом)
load_dotenv()

# Вставьте сюда токен от BotFather, если не хотите использовать переменные окружения.
# Можно оставить значение по умолчанию и задать TELEGRAM_BOT_TOKEN в системе или .env.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or "8435562409:AAHWegtV8erWmXPlTM_-mTLBzPtThQPxkSM"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Состояния для двух разговоров (настройка профиля и оценка покупки)
(
    SETUP_INCOME,
    SETUP_HOURS,
    SETUP_CURRENCY,
    EVALUATE_NAME,
    EVALUATE_PRICE,
) = range(5)


@dataclass
class UserProfile:
    monthly_income: float
    hours_per_week: float
    currency: str = "RUB"

    @property
    def hourly_rate(self) -> float:
        hours_per_month = self.hours_per_week * 4.33  # среднее количество недель в месяце
        if hours_per_month == 0:
            return 0.0
        return self.monthly_income / hours_per_month


def parse_float(value: str) -> Optional[float]:
    """Пробуем вытащить число из произвольной строки."""
    sanitized = value.replace(" ", "").replace(",", ".")
    number = ""
    has_decimal = False
    for char in sanitized:
        if char.isdigit():
            number += char
        elif char == "." and not has_decimal:
            number += char
            has_decimal = True
        elif number:
            # прекращаем сбор, как только встречаем чужой символ после числа
            break

    if not number:
        return None

    try:
        return float(number)
    except ValueError:
        return None


def format_money(amount: float, currency: str) -> str:
    return f"{amount:,.2f} {currency}".replace(",", " ")


def format_duration(hours: float) -> str:
    total_minutes = max(int(hours * 60), 0)
    hrs = total_minutes // 60
    mins = total_minutes % 60

    if hrs == 0:
        return f"{mins} мин"
    if mins == 0:
        return f"{hrs} ч"
    return f"{hrs} ч {mins} мин"


def esc(text: str) -> str:
    return html.escape(str(text), quote=False)


class ReminderManager:
    """Простое управление задачами-напоминаниями в памяти."""

    def __init__(self) -> None:
        self._tasks: Dict[int, list[asyncio.Task]] = {}

    def schedule(
        self,
        *,
        chat_id: int,
        context: ContextTypes.DEFAULT_TYPE,
        delay_seconds: int,
        message: str,
    ) -> None:
        task = context.application.create_task(
            self._remind(chat_id=chat_id, context=context, delay_seconds=delay_seconds, message=message)
        )
        self._tasks.setdefault(chat_id, []).append(task)

    async def _remind(
        self,
        *,
        chat_id: int,
        context: ContextTypes.DEFAULT_TYPE,
        delay_seconds: int,
        message: str,
    ) -> None:
        try:
            await asyncio.sleep(delay_seconds)
            await context.bot.send_message(chat_id=chat_id, text=message)
        finally:
            # удаляем выполненную задачу
            tasks = self._tasks.get(chat_id, [])
            self._tasks[chat_id] = [task for task in tasks if not task.done()]


reminder_manager = ReminderManager()

REMINDER_CHOICES = [
    ("30 минут", 30 * 60),
    ("1 час", 60 * 60),
    ("2 часа", 2 * 60 * 60),
    ("8 часов", 8 * 60 * 60),
    ("24 часа", 24 * 60 * 60),
]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 <b>Привет!</b>\n"
        "Я помогу понять, сколько рабочего времени уйдёт на конкретную покупку.\n\n"
        "1️⃣ /setup_profile — расскажи о доходе и графике.\n"
        "2️⃣ /evaluate — посчитаем покупку и при необходимости поставим напоминание.\n"
        "ℹ️ /profile — посмотреть сохранённые данные, /cancel — выйти из любого шага.",
        parse_mode=ParseMode.HTML,
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🧭 <b>Подсказки</b>\n"
        "• <b>/setup_profile</b> — задать доход и график.\n"
        "• <b>/evaluate</b> — ввести товар и цену, увидеть расчёты.\n"
        "• <b>/profile</b> — напомнить текущую ставку.\n"
        "• <b>/cancel</b> — выйти из текущего диалога.\n"
        "• После расчёта можно выбрать напоминание или отказаться от покупки.",
        parse_mode=ParseMode.HTML,
    )


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    profile: Optional[UserProfile] = context.user_data.get("profile")
    if not profile:
        await update.message.reply_text("⚠️ Профиль ещё не настроен. Наберите /setup_profile, чтобы начать.")
        return

    hourly_rate = profile.hourly_rate
    await update.message.reply_text(
        "📊 <b>Ваши данные</b>\n"
        f"• Доход в месяц: <b>{esc(format_money(profile.monthly_income, profile.currency))}</b>\n"
        f"• Часов в неделю: <b>{profile.hours_per_week:.2f}</b>\n"
        f"• Почасовая ставка: <b>{esc(format_money(hourly_rate, profile.currency))}/ч</b>",
        parse_mode=ParseMode.HTML,
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("↩️ Диалог завершён. Можно начать заново нужной командой.")
    context.user_data.pop("profile_setup", None)
    context.user_data.pop("purchase_in_progress", None)
    return ConversationHandler.END


async def start_setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["profile_setup"] = {}
    await update.message.reply_text("Какой у вас средний чистый доход в месяц? (например, 120000)")
    return SETUP_INCOME


async def collect_income(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = parse_float(update.message.text)
    if value is None or value <= 0:
        await update.message.reply_text("Не получилось прочитать число. Укажите сумму цифрами, например 95000.")
        return SETUP_INCOME

    context.user_data["profile_setup"]["monthly_income"] = value
    await update.message.reply_text("Сколько часов в неделю вы обычно работаете? (например, 38.5)")
    return SETUP_HOURS


async def collect_hours(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    hours = parse_float(update.message.text)
    if hours is None or hours <= 0:
        await update.message.reply_text("Похоже, введено недопустимое значение. Укажите количество часов цифрами.")
        return SETUP_HOURS

    context.user_data["profile_setup"]["hours_per_week"] = hours
    await update.message.reply_text(
        "В какой валюте считать? Напишите код (например, RUB, KZT, USD) или просто название."
    )
    return SETUP_CURRENCY


async def collect_currency(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw_value = update.message.text.strip()
    currency = "".join(char for char in raw_value if char.isalpha()).upper() or "RUB"

    profile_data = context.user_data.get("profile_setup", {})
    profile = UserProfile(
        monthly_income=profile_data["monthly_income"],
        hours_per_week=profile_data["hours_per_week"],
        currency=currency,
    )
    context.user_data["profile"] = profile
    context.user_data.pop("profile_setup", None)

    hourly_rate = profile.hourly_rate
    await update.message.reply_text(
        "Готово! Вы зарабатываете примерно "
        f"{format_money(hourly_rate, profile.currency)} в час.\n"
        "Теперь можно перейти к /evaluate, чтобы проверить покупку."
    )
    return ConversationHandler.END


async def start_evaluation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    profile: Optional[UserProfile] = context.user_data.get("profile")
    if not profile:
        await update.message.reply_text("Сначала выполните /setup_profile, чтобы я понимал ваш доход.")
        return ConversationHandler.END

    context.user_data["purchase_in_progress"] = {}
    await update.message.reply_text("Какую покупку рассматриваем? Опишите её кратко.")
    return EVALUATE_NAME


async def collect_purchase_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("Нужно название или описание покупки.")
        return EVALUATE_NAME

    context.user_data["purchase_in_progress"]["name"] = name
    await update.message.reply_text("Сколько она стоит? Укажите цену цифрами.")
    return EVALUATE_PRICE


async def collect_purchase_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    profile: Optional[UserProfile] = context.user_data.get("profile")
    if not profile:
        await update.message.reply_text("Профиль не найден, повторите /setup_profile.")
        return ConversationHandler.END

    price = parse_float(update.message.text)
    if price is None or price <= 0:
        await update.message.reply_text("Не могу обработать цену. Напишите сумму цифрами, например 15999.")
        return EVALUATE_PRICE

    purchase = context.user_data.get("purchase_in_progress", {})
    item_name = purchase.get("name", "покупка")

    hourly_rate = profile.hourly_rate
    if hourly_rate <= 0:
        await update.message.reply_text("Сначала настройте корректные данные о доходе через /setup_profile.")
        return ConversationHandler.END

    hours_needed = price / hourly_rate
    weeks_equivalent = hours_needed / profile.hours_per_week if profile.hours_per_week else 0.0

    evaluation_text = (
        f"🛍️ <b>{esc(item_name)}</b>\n"
        f"💰 Цена: <b>{esc(format_money(price, profile.currency))}</b>\n"
        f"💼 Ваша ставка: <b>{esc(format_money(hourly_rate, profile.currency))}/ч</b>\n"
        f"⌛ Нужно работать: <b>{format_duration(hours_needed)}</b>\n"
        f"📅 Это примерно <b>{weeks_equivalent:.2f}</b> рабочих недель при {profile.hours_per_week:.2f} ч/нед."
    )

    await update.message.reply_text(evaluation_text, parse_mode=ParseMode.HTML)

    context.user_data["last_purchase"] = {"name": item_name, "price": price}
    context.user_data.pop("purchase_in_progress", None)

    keyboard = [
        [InlineKeyboardButton(label, callback_data=f"reminder:{seconds}")]
        for (label, seconds) in REMINDER_CHOICES
    ]
    keyboard.append([InlineKeyboardButton("🔕 Не напоминать", callback_data="reminder:skip")])
    keyboard.append([InlineKeyboardButton("🚫 Отказаться", callback_data="decision:reject")])

    await update.message.reply_text(
        "🤔 <b>Отложим решение или сразу откажемся?</b>\n"
        "Выберите таймер напоминания или жмите «Отказаться».",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML,
    )

    return ConversationHandler.END


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if not query or not query.data:
        return

    namespace, payload = query.data.split(":", maxsplit=1)

    if namespace == "reminder":
        if payload == "skip":
            await query.edit_message_text("✅ Решение принято без напоминаний.")
            return

        try:
            delay_seconds = int(payload)
        except ValueError:
            await query.edit_message_text("Что-то пошло не так с напоминанием.")
            return

        profile: Optional[UserProfile] = context.user_data.get("profile")
        last_purchase = context.user_data.get("last_purchase")

        if not profile or not last_purchase:
            await query.edit_message_text("Нет данных о последней покупке — попробуйте снова через /evaluate.")
            return

        reminder_text = (
            f"⏰ Напоминание: вы собирались решить, покупать ли «{last_purchase['name']}» "
            f"за {format_money(last_purchase['price'], profile.currency)}."
        )

        reminder_manager.schedule(
            chat_id=query.message.chat_id,
            context=context,
            delay_seconds=delay_seconds,
            message=reminder_text,
        )

        await query.edit_message_text("🔔 Напоминание поставлено! Возвращаюсь к вам позже.")
        return

    if namespace == "decision" and payload == "reject":
        last_purchase = context.user_data.get("last_purchase")
        item_text = esc(last_purchase["name"]) if last_purchase else "покупку"
        await query.edit_message_text(
            f"🚫 Вы отказались от {item_text}. Отличное решение, если оно делает вас спокойнее!",
            parse_mode=ParseMode.HTML,
        )
        return

    await query.edit_message_text("Команда не распознана. Попробуйте снова через /evaluate.")


def main() -> None:
    token = TELEGRAM_BOT_TOKEN
    if token == "PASTE_YOUR_BOT_TOKEN_HERE" or not token:
        raise RuntimeError(
            "Не найден TELEGRAM_BOT_TOKEN. Вставьте свой токен в переменную TELEGRAM_BOT_TOKEN в bot.py "
            "или задайте его в системе/файле .env."
        )

    application = Application.builder().token(token).build()

    setup_handler = ConversationHandler(
        entry_points=[CommandHandler("setup_profile", start_setup)],
        states={
            SETUP_INCOME: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect_income)],
            SETUP_HOURS: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect_hours)],
            SETUP_CURRENCY: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect_currency)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    evaluation_handler = ConversationHandler(
        entry_points=[CommandHandler("evaluate", start_evaluation)],
        states={
            EVALUATE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect_purchase_name)],
            EVALUATE_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect_purchase_price)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("profile", profile_command))
    application.add_handler(setup_handler)
    application.add_handler(evaluation_handler)
    application.add_handler(CallbackQueryHandler(handle_callback, pattern=r"^(?:reminder|decision):"))

    logger.info("Бот запущен. Ожидаю обновления...")
    application.run_polling()


if __name__ == "__main__":
    main()
