import asyncio
import os
import logging
import html
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from database import (
    init_db, get_new_applications, get_applications, get_application_detail,
    get_latest_application_id, toggle_application_status
)
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

# --------------------------------------------------
# ЛОГИРОВАНИЕ (systemd / journalctl)
# --------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# --------------------------------------------------
# ENV
# --------------------------------------------------
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в .env файле")

ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "")
if not ADMIN_IDS_STR:
    raise ValueError("ADMIN_IDS не найден в .env файле")

ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_STR.split(",") if x.strip()]
if not ADMIN_IDS:
    raise ValueError("ADMIN_IDS должен содержать хотя бы один ID")

# --------------------------------------------------
# BOT
# --------------------------------------------------
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

last_checked_id: int | None = None

# --------------------------------------------------
# HELPERS
# --------------------------------------------------
def format_date(dt_str: str) -> str:
    gmt4 = timezone(timedelta(hours=4))
    dt = datetime.fromisoformat(dt_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(gmt4).strftime("%d.%m.%Y %H:%M")

def format_application(app) -> str:
    app_id, phone, comment, created_at, status = app

    text = (
        f"<b>Новая заявка</b>\n"
        f"Дата: {format_date(created_at)}\n\n"
        f"Телефон: <code>{html.escape(phone)}</code>\n"
    )

    if comment:
        text += f"\nКомментарий:\n{html.escape(comment)}\n"

    text += f"\nСтатус: {status}"
    return text

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# --------------------------------------------------
# BACKGROUND TASK
# --------------------------------------------------
async def check_new_applications():
    global last_checked_id

    last_checked_id = get_latest_application_id() or 0
    logger.info(f"Bot started. last_checked_id = {last_checked_id}")

    while True:
        try:
            new_apps = get_new_applications(last_checked_id)

            for app in new_apps:
                app_id = app[0]

                # ВАЖНО: обновляем ДО отправки
                last_checked_id = app_id

                text = format_application(app)
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text="Закрыть заявку" if app[4] == "Новая" else "Открыть заявку",
                        callback_data=f"toggle_{app_id}_0"
                    )]
                ])

                for admin_id in ADMIN_IDS:
                    await bot.send_message(
                        chat_id=admin_id,
                        text=text,
                        reply_markup=keyboard,
                        parse_mode="HTML"
                    )

                logger.info(f"Notification sent for application id={app_id}")

        except Exception as e:
            logger.exception("Error while checking new applications")

        await asyncio.sleep(10)

# --------------------------------------------------
# COMMANDS
# --------------------------------------------------
@dp.message(Command(commands=["list"]))
async def applications_list(message: types.Message, offset: int = 0, limit: int = 5):
    if not is_admin(message.from_user.id):
        await message.answer("У вас нет прав для выполнения этой команды.")
        return

    apps = get_applications(offset=offset, limit=limit)
    if not apps:
        await message.answer("Заявок пока нет.")
        return

    buttons = [
        [InlineKeyboardButton(
            text=f"{format_date(created_at)} - {phone} ({status})",
            callback_data=f"view_{app_id}_{offset}"
        )]
        for app_id, phone, created_at, status in reversed(apps)
    ]

    if len(apps) == limit:
        buttons.append([
            InlineKeyboardButton(
                text="Следующие",
                callback_data=f"next_{offset + limit}"
            )
        ])

    await message.answer(
        "Список заявок:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )

# --------------------------------------------------
# CALLBACKS
# --------------------------------------------------
@dp.callback_query()
async def applications_callback_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return

    data = callback.data

    if data.startswith("view_"):
        _, app_id, offset = data.split("_")
        app = get_application_detail(int(app_id))

        if app:
            await callback.message.edit_text(
                format_application(app),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text="Закрыть заявку" if app[4] == "Новая" else "Открыть заявку",
                        callback_data=f"toggle_{app_id}_{offset}"
                    )],
                    [InlineKeyboardButton(
                        text="Назад",
                        callback_data=f"back_{offset}"
                    )]
                ])
            )

    elif data.startswith(("next_", "back_")):
        offset = int(data.split("_")[1])
        apps = get_applications(offset=offset, limit=5)

        buttons = [
            [InlineKeyboardButton(
                text=f"{format_date(created_at)} - {phone} ({status})",
                callback_data=f"view_{app_id}_{offset}"
            )]
            for app_id, phone, created_at, status in reversed(apps)
        ]

        if len(apps) == 5:
            buttons.append([
                InlineKeyboardButton(
                    text="Следующие",
                    callback_data=f"next_{offset + 5}"
                )
            ])

        await callback.message.edit_text(
            "Список заявок:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
        )

    elif data.startswith("toggle_"):
        _, app_id, offset = data.split("_")
        new_status = toggle_application_status(int(app_id))
        app = get_application_detail(int(app_id))

        if app:
            await callback.message.edit_text(
                format_application(app),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text="Закрыть заявку" if new_status == "Новая" else "Открыть заявку",
                        callback_data=f"toggle_{app_id}_{offset}"
                    )],
                    [InlineKeyboardButton(
                        text="Назад",
                        callback_data=f"back_{offset}"
                    )]
                ])
            )

# --------------------------------------------------
# MAIN
# --------------------------------------------------
async def main():
    init_db()
    asyncio.create_task(check_new_applications())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())