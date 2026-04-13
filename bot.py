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
COOLDOWN_SEC = 300

QUEUE_LIST = []
QUEUE_MESSAGES = {}  # user_id -> message for editing

USERS_FILE = "users.txt"


def warmup_facefusion():
    subprocess.run([
        "/root/bot/project/venv/bin/python",
        "facefusion.py",
        "headless-run",
        "-s", "/root/bot/project/test.jpg",
        "-t", "/root/bot/project/test.jpg",
        "-o", "/root/bot/project/warmup.jpg",
        "--execution-providers", "cpu",
        "--face-mask-types", "box"
    ], cwd=FACEFUSION_PATH)

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

@dp.message(F.text == "/admin")
async def admin_panel(message: Message):
    if message.from_user.id != ADMIN_ID:
        return  # никто кроме тебя не увидит

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

async def update_queue_positions():
    while True:
        try:
            for i, job in enumerate(QUEUE_LIST):
                user_id = job.message.from_user.id
                pos = i + 1

                try:
                    # создаём/обновляем сообщение очереди
                    if user_id in QUEUE_MESSAGES:
                        await QUEUE_MESSAGES[user_id].edit_text(
                            f"📥 Ты в очереди: #{pos}\n⏳ Подожди немного!"
                        )
                except:
                    pass

        except:
            pass

        await asyncio.sleep(3)

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
@dp.callback_query(F.data.in_([
    "boy_short", "boy_long",
    "girl_short", "girl_long",
    "type_banner", "type_cover"
]))
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

        # ✅ ТОЛЬКО ДЛЯ COVER — выбор волос
        if USER_STATE[user_id]["output_type"] == "cover":
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="🌑 Тёмные волосы", callback_data="hair_dark"),
                    InlineKeyboardButton(text="🌕 Светлые волосы", callback_data="hair_light"),
                ]
            ])

            await callback.message.answer("Выбери цвет волос 👇", reply_markup=kb)
            await callback.answer()
            return

        # ✅ BANNER — сразу подписка
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
        await callback.message.answer("🔥 Окей, теперь кидай фото")
    else:
        await callback.message.answer("❌ Ты не подписан")
        WAITING_SUB.add(user_id)

    await callback.answer()

@dp.callback_query(F.data.in_(["hair_light", "hair_dark"]))
async def choose_hair(callback: CallbackQuery):
    user_id = callback.from_user.id

    if user_id not in USER_STATE:
        await callback.message.answer("👉 /start сначала")
        await callback.answer()
        return

    USER_STATE[user_id]["hair"] = "light" if callback.data == "hair_light" else "dark"

    # дальше подписка
    if await check_sub(user_id):
        await callback.message.answer("🔥 Окей, теперь кидай фото 📸")
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

async def subscription_watcher():
    while True:
        for user_id in list(WAITING_SUB):
            if await check_sub(user_id):
                WAITING_SUB.discard(user_id)
                try:
                    await bot.send_message(user_id, "🔥 Подписка подтверждена! Отправь фото!")
                except:
                    pass
        await asyncio.sleep(10)


@dp.message(F.text)
async def fallback(message: Message):
    if message.text.startswith("/"):
        return
    await message.answer("👉 /start → выбери тип")


def run_facefusion(source, target, output):
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    result = subprocess.run([
        "/root/bot/project/venv/bin/python",
        "facefusion.py",
        "headless-run",
        "-s", source,
        "-t", target,
        "-o", output,
        "--execution-providers", "cpu",
        "--face-detector-score", "0.3",
        "--face-mask-types", "box",
        "--face-mask-padding", "0.15",
        "--face-mask-blur", "0"
    ], env=env,cwd=FACEFUSION_PATH, capture_output=True, text=True)

    print("=== FACEFUSION STDOUT ===")
    print(result.stdout)

    print("=== FACEFUSION STDERR ===")
    print(result.stderr)

    print("RETURN CODE:", result.returncode)

    if result.returncode != 0:
        raise RuntimeError(result.stderr)


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
            await job.message.answer("⚙️ Генерация... Ожидай 10 секунд!")
            
            await asyncio.to_thread(resize_image, job.user_photo)

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
            await job.message.answer(f"❌ Ошибка: Не найдено лицо!")

        finally:
            safe_remove(job.user_photo)
            safe_remove(job.result_photo)

            ACTIVE_USERS.discard(user_id)
            USER_COOLDOWN[user_id] = time.time()
            
            try:
                QUEUE_LIST.remove(job)
            except:
                pass

            QUEUE_MESSAGES.pop(user_id, None)

            QUEUE.task_done()


async def start_workers():
    warmup_facefusion()
    for i in range(WORKERS_COUNT):
        asyncio.create_task(worker(i + 1))

    asyncio.create_task(update_queue_positions())


@dp.message(F.photo)
async def handle_photo(message: Message):
    user_id = message.from_user.id

    if user_id not in ALL_USERS:
        ALL_USERS.add(user_id)
        save_user(user_id)
    
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
        await message.answer("⛔️ Кулдаун! Сейчас огромная очередь, поэтому он временно повышен до 5 минут!")
        return

    state = USER_STATE[user_id]
    
    template = state["template"]

    if state.get("output_type") == "cover":
        hair = state.get("hair")
        if not hair:
            await message.answer("👉 Сначала выбери цвет волос")
            return

        template = f"{template}_{hair}"
    
    output_type = state.get("output_type", "banner")

    assets = {
        "banner": {
            "boy_short": "/root/bot/project/banners/boy_short.jpg",
            "boy_long": "/root/bot/project/banners/boy_long.jpg",
            "girl_short": "/root/bot/project/banners/girl_short.jpg",
            "girl_long": "/root/bot/project/banners/girl_long.jpg",
        },
        "cover": {
            "boy_short_light": "/root/bot/project/covers/boy_short_dark.jpg",
            "boy_short_dark": "/root/bot/project/covers/boy_short_dark.jpg",

            "boy_long_light": "/root/bot/project/covers/boy_long_light.jpg",
            "boy_long_dark": "/root/bot/project/covers/boy_long_dark.jpg",

            "girl_short_light": "/root/bot/project/covers/girl_short_light.jpg",
            "girl_short_dark": "/root/bot/project/covers/girl_short_dark.jpg",

            "girl_long_light": "/root/bot/project/covers/girl_long_light.jpg",
            "girl_long_dark": "/root/bot/project/covers/girl_long_dark.jpg",
        }
    }

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

    pos = len(QUEUE_LIST) + len(ACTIVE_USERS)
    msg = await message.answer(f"📥 Ты в очереди: #{pos}\n⏳ Подожди немного!")
    QUEUE_MESSAGES[user_id] = msg

    job = Job(
        message=message,
        user_photo=user_photo,
        result_photo=result_photo,
        banner_path=banner_path
    )

    QUEUE_LIST.append(job)
    await QUEUE.put(job)

async def main():
    await start_workers()
    asyncio.create_task(subscription_watcher())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

#venv\Scripts\activate
#python bot.py