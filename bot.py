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
from PIL import Image

TOKEN = "8721546200:AAHANmwzeo_xIEVpIZ9zp87oGLCMsP3lGgc"
CHANNEL = "@balimusic1"
ADMIN_ID = 1220199944

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

QUEUE_LIST = []
QUEUE_MESSAGES = {}  # user_id -> message for editing

USERS_FILE = "users.txt"


# =========================
# ⚡ FACEFUSION DAEMON (NEW)
# =========================

FF_QUEUE = asyncio.Queue()

def resize_image(path):
    img = Image.open(path)
    img = img.resize((512, 512))
    img.save(path)


def load_users():
    if not os.path.exists(USERS_FILE):
        return set()

    with open(USERS_FILE, "r") as f:
        return set(int(line.strip()) for line in f if line.strip())


def save_user(user_id: int):
    with open(USERS_FILE, "a") as f:
        f.write(f"{user_id}\n")

ALL_USERS = load_users()


@dataclass
class Job:
    message: Message
    user_photo: str
    result_photo: str
    banner_path: str


# =========================
# 🚀 FACEFUSION WORKER (DAEMON)
# =========================

async def facefusion_worker():
    print("🚀 FaceFusion daemon started")

    while True:
        job = await FF_QUEUE.get()

        try:
            await asyncio.to_thread(resize_image, job.user_photo)

            result = subprocess.run([
                "/root/bot/project/venv/bin/python",
                "/root/bot/project/facefusion/facefusion.py",
                "headless-run",
                "-s", job.user_photo,
                "-t", job.banner_path,
                "-o", job.result_photo,
                "--execution-providers", "cpu",
                "--face-mask-types", "box",
                "--face-mask-padding", "0.15",
                "--face-mask-blur", "0"
            ], capture_output=True, text=True)

            print(result.stdout)
            print(result.stderr)

            ok = False
            for _ in range(150):
                if os.path.exists(job.result_photo) and os.path.getsize(job.result_photo) > 0:
                    ok = True
                    break
                await asyncio.sleep(0.2)

            if not ok:
                await job.message.answer("❌ Лицо не найдено. Скинь другую фотку")
            else:
                await job.message.answer_photo(FSInputFile(job.result_photo))

        except Exception:
            await job.message.answer("❌ Ошибка: Не найдено лицо!")

        finally:
            try:
                os.remove(job.user_photo)
                os.remove(job.result_photo)
            except:
                pass

            ACTIVE_USERS.discard(job.message.from_user.id)
            USER_COOLDOWN[job.message.from_user.id] = time.time()

            try:
                QUEUE_LIST.remove(job)
            except:
                pass

            QUEUE_MESSAGES.pop(job.message.from_user.id, None)

            FF_QUEUE.task_done()


# =========================
# 📊 ADMIN
# =========================

@dp.message(F.text == "/admin")
async def admin_panel(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    queue_users = [job.message.from_user.id for job in QUEUE_LIST]

    text = (
        f"👤 Всего пользователей: {len(ALL_USERS)}\n"
        f"👥 В очереди: {len(QUEUE_LIST)}\n"
        f"⚙️ Активных: {len(ACTIVE_USERS)}\n"
        f"⏳ Ждут подписку: {len(WAITING_SUB)}\n\n"
        f"📋 Очередь:\n"
    )

    if queue_users:
        for i, uid in enumerate(queue_users, start=1):
            text += f"{i}. {uid}\n"
    else:
        text += "пусто"

    await message.answer(text)


# =========================
# 📥 QUEUE UI
# =========================

async def update_queue_positions():
    while True:
        for i, job in enumerate(QUEUE_LIST):
            user_id = job.message.from_user.id
            pos = i + 1

            if user_id in QUEUE_MESSAGES:
                try:
                    await QUEUE_MESSAGES[user_id].edit_text(
                        f"📥 Ты в очереди: #{pos}\n⏳ Подожди немного!"
                    )
                except:
                    pass

        await asyncio.sleep(3)


# =========================
# 🔍 SUB CHECK
# =========================

async def check_sub(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(CHANNEL, user_id)
        return member.status in ("member", "administrator", "creator")
    except:
        return False


# =========================
# 🚀 START
# =========================

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


# =========================
# ⚙️ CALLBACKS
# =========================

@dp.callback_query()
async def choose_template(callback: CallbackQuery):
    user_id = callback.from_user.id

    if callback.data in ["boy_short", "boy_long", "girl_short", "girl_long"]:
        USER_STATE[user_id] = {"template": callback.data}

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🖼 Баннер", callback_data="type_banner"),
                InlineKeyboardButton(text="📕 Обложка", callback_data="type_cover"),
            ]
        ])

        await callback.message.answer("Теперь выбери формат 👇", reply_markup=kb)
        await callback.answer()
        return

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


# =========================
# 📸 PHOTO HANDLER
# =========================

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

    if user_id in ACTIVE_USERS:
        await message.answer("⛔ Уже идёт генерация")
        return

    now = time.time()
    if now - USER_COOLDOWN.get(user_id, 0) < COOLDOWN_SEC:
        await message.answer("⛔ Кулдаун! Подожди еще 20 секунд!")
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

    banner_path = assets[output_type][template]

    uid = str(uuid.uuid4())
    user_photo = os.path.join(BASE_PATH, f"{uid}_user.jpg")
    result_photo = os.path.join(BASE_PATH, f"{uid}_result.jpg")

    file = await bot.get_file(message.photo[-1].file_id)
    await bot.download_file(file.file_path, user_photo)

    ACTIVE_USERS.add(user_id)

    msg = await message.answer("📥 Ты в очереди...")
    QUEUE_MESSAGES[user_id] = msg

    job = Job(message, user_photo, result_photo, banner_path)

    QUEUE_LIST.append(job)

    await FF_QUEUE.put(job)


# =========================
# 🚀 STARTUP
# =========================

async def main():
    asyncio.create_task(facefusion_worker())
    asyncio.create_task(update_queue_positions())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())