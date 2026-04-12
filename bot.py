import asyncio
import os
import subprocess
import uuid
import time
from dataclasses import dataclass

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import CommandStart

from PIL import Image

TOKEN = "8721546200:AAHANmwzeo_xIEVpIZ9zp87oGLCMsP3lGgc"
CHANNEL = "@balimusic1"

bot = Bot(token=TOKEN)
dp = Dispatcher()

BASE_PATH = "/root/bot/project"
FACEFUSION_PATH = "/root/bot/project/facefusion"

QUEUE = asyncio.Queue()
WORKERS_COUNT = 5

USER_STATE = {}
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


# ================= IMAGE FIX =================

def fix_image(path: str):
    try:
        img = Image.open(path).convert("RGB")
        img.save(path, "JPEG", quality=95)
    except:
        pass


# ================= SUB CHECK =================

async def check_sub(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(CHANNEL, user_id)
        return member.status in ("member", "administrator", "creator")
    except:
        return False


# ================= START =================

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
        "Здарова, ща мы тебя вклеим в баннер и ты будешь прямо как авторы \"JEALOUS\"!\n\nВыбери тип 👇",
        reply_markup=kb
    )


# ================= CALLBACK =================

@dp.callback_query(F.data.in_({
    "boy_short", "boy_long", "girl_short", "girl_long",
    "banner", "cover", "check_sub"
}))
async def callback_router(callback: CallbackQuery):
    user_id = callback.from_user.id
    data = callback.data

    try:
        # STEP 1: template
        if data in ["boy_short", "boy_long", "girl_short", "girl_long"]:
            USER_STATE[user_id] = {"template": data}

            kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="🖼 Баннер", callback_data="banner"),
                    InlineKeyboardButton(text="📕 Обложка", callback_data="cover"),
                ]
            ])

            await callback.message.answer("Теперь выбери формат 👇", reply_markup=kb)
            await callback.answer()
            return

        # STEP 2: format
        if data in ["banner", "cover"]:
            state = USER_STATE.get(user_id)
            if not state or "template" not in state:
                await callback.message.answer("👉 /start сначала")
                await callback.answer()
                return

            USER_STATE[user_id]["output_type"] = data

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

        # STEP 3: subscription check
        if data == "check_sub":
            if await check_sub(user_id):
                WAITING_SUB.discard(user_id)
                await callback.message.answer("🔥 Окей, теперь кидай фото 📸")
            else:
                WAITING_SUB.add(user_id)
                await callback.message.answer("❌ Ты не подписан")

            await callback.answer()
            return

    except Exception as e:
        print("CALLBACK ERROR:", e)
        await callback.answer("Ошибка", show_alert=True)


# ================= FACEFUSION =================

def run_facefusion(source, target, output):
    try:
        subprocess.run([
            "/root/bot/project/venv/bin/python",
            "facefusion.py",
            "headless-run",
            "-s", source,
            "-t", target,
            "-o", output,
            "--face-mask-padding", "0.0",
            "--face-mask-blur", "0.0",
        ],
        cwd=FACEFUSION_PATH,
        capture_output=True,
        text=True)

    except Exception as e:
        print("FACEFUSION ERROR:", e)


# ================= WORKER =================

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

            ok = False
            for _ in range(30):
                if os.path.exists(job.result_photo) and os.path.getsize(job.result_photo) > 0:
                    ok = True
                    break
                await asyncio.sleep(0.2)

            if not ok:
                await job.message.answer("❌ Лицо не найдено. Скинь другую фотку")
                return

            await job.message.answer_photo(FSInputFile(job.result_photo))

        except Exception as e:
            print("WORKER ERROR:", e)
            await job.message.answer("❌ Ошибка генерации")

        finally:
            try:
                if os.path.exists(job.user_photo):
                    os.remove(job.user_photo)
                if os.path.exists(job.result_photo):
                    os.remove(job.result_photo)
            except:
                pass

            ACTIVE_USERS.discard(user_id)
            USER_COOLDOWN[user_id] = time.time()
            QUEUE.task_done()


# ================= PHOTO =================

@dp.message(F.photo)
async def handle_photo(message: Message):
    try:
        user_id = message.from_user.id

        if user_id in ACTIVE_USERS:
            await message.answer("⛔ Уже идёт генерация")
            return

        now = time.time()
        if now - USER_COOLDOWN.get(user_id, 0) < COOLDOWN_SEC:
            await message.answer("⛔ Кулдаун")
            return

        state = USER_STATE.get(user_id)
        if not state:
            await message.answer("👉 /start сначала")
            return

        template = state.get("template")
        output_type = state.get("output_type", "banner")

        if not template:
            await message.answer("👉 /start сначала")
            return

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

        if output_type not in assets:
            output_type = "banner"

        if template not in assets[output_type]:
            await message.answer("❌ Ошибка шаблона")
            return

        banner_path = assets[output_type][template]

        uid = str(uuid.uuid4())

        user_photo = os.path.join(BASE_PATH, f"{uid}.jpg")
        result_photo = f"/root/bot/project/output/{uid}.jpg"

        os.makedirs("/root/bot/project/output", exist_ok=True)

        file = await bot.get_file(message.photo[-1].file_id)
        await bot.download_file(file.file_path, user_photo)

        fix_image(user_photo)

        ACTIVE_USERS.add(user_id)

        pos = QUEUE.qsize() + len(ACTIVE_USERS)
        await message.answer(f"📥 Ты в очереди: #{pos}\n⏳ Подожди немного!")

        await QUEUE.put(Job(
            message=message,
            user_photo=user_photo,
            result_photo=result_photo,
            banner_path=banner_path
        ))

    except Exception as e:
        print("PHOTO ERROR:", e)
        await message.answer("❌ Ошибка обработки фото")


# ================= START =================

async def start_workers():
    for i in range(WORKERS_COUNT):
        asyncio.create_task(worker(i))


async def main():
    await start_workers()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())