import asyncio
import os
import subprocess
import uuid
import time
from dataclasses import dataclass
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, FSInputFile,
    InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery
)
from aiogram.filters import CommandStart

TOKEN = "8721546200:AAHANmwzeo_xIEVpIZ9zp87oGLCMsP3lGgc"
CHANNEL = "@balimusic1"

bot = Bot(token=TOKEN)
dp = Dispatcher()

BASE_PATH = "/root/bot/project"
FACEFUSION_PATH = "/root/bot/project/facefusion"

USER_STATE = {}

QUEUE = asyncio.Queue()
WORKERS_COUNT = 1

WAITING_SUB = set()

ACTIVE_USERS = set()
USER_COOLDOWN = {}
COOLDOWN_SEC = 30


@dataclass
class Job:
    message: Message
    user_photo: str
    result_photo: str
    banner_path: str


# 🔍 подписка
async def check_sub(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(CHANNEL, user_id)
        return member.status in ("member", "administrator", "creator")
    except:
        return False


# 🚀 start
@dp.message(CommandStart())
async def start(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👦 Пацан короткие", callback_data="boy_short"),
            InlineKeyboardButton(text="👦 Пацан длинные", callback_data="boy_long"),
        ],
        [
            InlineKeyboardButton(text="👧 Девушка короткие", callback_data="girl_short"),
            InlineKeyboardButton(text="👧 Девушка длинные", callback_data="girl_long"),
        ],
    ])

    await message.answer(
        "Здарова, ща мы тебя вклеим в баннер / обложку и ты будешь прямо как авторы \"JEALOUS\"!\n\nВыбери тип 👇",
        reply_markup=kb
    )


# ⚙️ выбор типа + подписка
@dp.callback_query()
async def choose_template(callback: CallbackQuery):
    user_id = callback.from_user.id

    # шаг 1: выбор персонажа
    if callback.data in ["boy_short", "boy_long", "girl_short", "girl_long"]:
        USER_STATE[user_id] = {
            "template": callback.data
        }

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🖼 Баннер", callback_data="type_banner"),
                InlineKeyboardButton(text="📕 Обложка", callback_data="type_cover"),
            ]
        ])

        await callback.message.answer("Теперь выбери формат 👇", reply_markup=kb)
        await callback.answer()
        return

    # шаг 2: выбор типа результата
    if callback.data in ["type_banner", "type_cover"]:
        if user_id not in USER_STATE:
            await callback.message.answer("👉 /start сначала")
            await callback.answer()
            return

        USER_STATE[user_id]["output_type"] = "banner" if callback.data == "type_banner" else "cover"

        if await check_sub(user_id):
            await callback.message.answer("✅ Ты уже подписан!\n📸 Отправь фото")
        else:
            WAITING_SUB.add(user_id)

            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📢 Я подписался", callback_data="check_sub")]
            ])

            await callback.message.answer(
                f"❗️ Подпишись:\n{CHANNEL}\n\nИ нажми кнопку 👇",
                reply_markup=kb
            )

        await callback.answer()
        return


@dp.callback_query(F.data == "check_sub")
async def confirm_sub(callback: CallbackQuery):
    user_id = callback.from_user.id

    if await check_sub(user_id):
        WAITING_SUB.discard(user_id)
        await callback.message.answer("🔥 Окей, теперь кидай фото 📸")
    else:
        await callback.message.answer("❌ Ты не подписан")
        WAITING_SUB.add(user_id)

    await callback.answer()


async def subscription_watcher():
    while True:
        for user_id in list(WAITING_SUB):
            if await check_sub(user_id):
                WAITING_SUB.discard(user_id)
                try:
                    await bot.send_message(user_id, "🔥 Подписка подтверждена")
                except:
                    pass
        await asyncio.sleep(10)


@dp.message(F.text)
async def fallback(message: Message):
    if message.text.startswith("/"):
        return
    await message.answer("👉 /start → выбери тип")


def run_facefusion(source, target, output):
    result = subprocess.run([
        "/root/bot/project/venv/bin/python",
        "facefusion.py",
        "headless-run",
        "-s", source,
        "-t", target,
        "-o", output,
        "--face-mask-types", "box",
        "--face-mask-padding", "0.3",
        "--face-mask-blur", "0.1"
    ], cwd=FACEFUSION_PATH, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FaceFusion error:\n{result.stderr}")


def safe_remove(path: str):
    try:
        if path and os.path.exists(path):
            for _ in range(3):
                try:
                    os.remove(path)
                    break
                except:
                    time.sleep(0.3)
    except:
        pass


async def worker(worker_id: int):
    while True:
        job = await QUEUE.get()
        user_id = job.message.from_user.id

        try:
            await job.message.answer("⚙️ Генерация... Ожидай 20-30 секунд!")

            await asyncio.to_thread(
                run_facefusion,
                job.user_photo,
                job.banner_path,
                job.result_photo
            )

            # 🔍 быстрый чек результата (нет смысла ждать 60+ сек)
            ok = False
            for _ in range(150):  # 150 * 0.2 = 30 секунд
                if os.path.exists(job.result_photo) and os.path.getsize(job.result_photo) > 0:
                    ok = True
                    break
                await asyncio.sleep(0.2)

            # ❌ НЕТ ЛИЦА / ОШИБКА
            if not ok:
                await job.message.answer("❌ Лицо не найдено. Скинь другую фотку")
                return

            # ✅ УСПЕХ
            await job.message.answer_photo(FSInputFile(job.result_photo))

            await asyncio.sleep(1)

        except Exception as e:
            await job.message.answer(f"❌ Ошибка: {e}")

        finally:
            safe_remove(job.user_photo)
            safe_remove(job.result_photo)

            ACTIVE_USERS.discard(user_id)
            USER_COOLDOWN[user_id] = time.time()

            QUEUE.task_done()


async def start_workers():
    for i in range(WORKERS_COUNT):
        asyncio.create_task(worker(i + 1))


@dp.message(F.photo)
async def handle_photo(message: Message):
    user_id = message.from_user.id

    if user_id not in USER_STATE:
        await message.answer("👉 /start сначала")
        return

    if not await check_sub(user_id):
        WAITING_SUB.add(user_id)
        await message.answer(f"❗️ Подпишись:\n{CHANNEL}")
        return

    now = time.time()

    if user_id in ACTIVE_USERS:
        await message.answer("⛔ Уже идёт генерация")
        return

    if now - USER_COOLDOWN.get(user_id, 0) < COOLDOWN_SEC:
        await message.answer("⛔ Кулдаун! Подожди еще 10 секунд!")
        return

    state = USER_STATE[user_id]
    template = state["template"]
    output_type = state.get("output_type", "banner")

    assets = {
        "banner": {
            "boy_short": "/root/bot/project/banners/boy_short.jpg",
            "boy_long": "/root/bot/project/banners/boy_long.jpg",
            "girl_short": "/root/bot/project/banners/girl_short.jpg",
            "girl_long": "/root/bot/project/banners/girl_long.jpg",
        },
        "cover": {
            "boy_short": "/root/bot/project/covers/boy_short.jpg",
            "boy_long": "/root/bot/project/covers/boy_long.jpg",
            "girl_short": "/root/bot/project/covers/girl_short.jpg",
            "girl_long": "/root/bot/project/covers/girl_long.jpg",
        }
    }

    output_type = state.get("output_type", "banner")
    template = state["template"]

    if output_type not in assets:
        output_type = "banner"

    if template not in assets[output_type]:
        await message.answer("❌ Ошибка шаблона, /start сначала")
        return

    banner_path = assets[output_type][template]

    uid = str(uuid.uuid4())
    user_photo = os.path.join(BASE_PATH, f"{uid}_user.jpg")
    result_photo = os.path.join(BASE_PATH, f"{uid}_result.jpg")

    file = await bot.get_file(message.photo[-1].file_id)
    await bot.download_file(file.file_path, user_photo)

    ACTIVE_USERS.add(user_id)

    pos = QUEUE.qsize() + len(ACTIVE_USERS)
    await message.answer(f"📥 Ты в очереди: #{pos}\n⏳ Подожди немного!")

    await QUEUE.put(Job(
    message=message,
    user_photo=user_photo,
    result_photo=result_photo,
    banner_path=banner_path
    ))


async def main():
    await start_workers()
    asyncio.create_task(subscription_watcher())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

#venv\Scripts\activate
#python bot.py