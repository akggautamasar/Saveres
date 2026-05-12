import os
import shutil
import psutil
import asyncio
import re
from time import time
from pyrogram.enums import ParseMode
from pyrogram import Client, compose, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from config import PyroConf
from logger import LOGGER

from helpers.files import get_readable_file_size, get_readable_time
from helpers.msg import getChatMsgID, get_parsed_msg
from helpers.jobs import execute_batch, execute_autoforward, handle_download, track_task, get_running_tasks

bot = Client(
    "media_bot",
    api_id=PyroConf.API_ID,
    api_hash=PyroConf.API_HASH,
    bot_token=PyroConf.BOT_TOKEN,
    workers=100,
    parse_mode=ParseMode.MARKDOWN,
    max_concurrent_transmissions=PyroConf.MAX_CONCURRENT_UPLOADS, 
    sleep_threshold=60,
)

user = Client(
    "user_session",
    workers=100,
    session_string=PyroConf.SESSION_STRING,
    max_concurrent_transmissions=PyroConf.MAX_CONCURRENT_DOWNLOADS,
    sleep_threshold=60,
)

BATCH_JOBS = {}
WAITING_FOR_DEST = {}
WAITING_FOR_CAPTION_RULE = {}

async def trigger_caption_setup(bot: Client, user: Client, message: Message, job: dict):
    sample_caption = ""
    for msg_id in range(job["start_id"], min(job["start_id"] + 5, job["end_id"] + 1)):
        try:
            msg_obj = await user.get_messages(chat_id=job["start_chat"], message_ids=msg_id)
            if msg_obj and not getattr(msg_obj, "empty", True):
                raw_text = msg_obj.caption or msg_obj.text
                if raw_text and len(raw_text.strip()) > 50 and '\n' in raw_text:
                    sample_caption = await get_parsed_msg(raw_text, msg_obj.caption_entities or msg_obj.entities)
                    break
        except Exception:
            continue

    job["caption_rules"] = []
    
    if sample_caption:
        user_id = message.from_user.id if hasattr(message, "from_user") and message.from_user else message.chat.id
        job["sample_caption"] = sample_caption 
        WAITING_FOR_CAPTION_RULE[user_id] = job
        job["original_message_id"] = message.id 
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Trim Last Line", callback_data=f"cap_rm1_{message.id}")],
            [InlineKeyboardButton("Trim Last 2 Lines", callback_data=f"cap_rm2_{message.id}")],
            [InlineKeyboardButton("✅ Start", callback_data=f"cap_done_{message.id}")]
        ])
        
        text = (
            f"**Current Caption:**\n\n`{sample_caption[:300]}...`\n\n"
            "🔄 To clean up a caption reply to the message with the exact text you'd like to remove!\n\n"
            f"> 🎯 **Active Rules:** 0 applied"
        )
        
        msg = await message.reply(text, reply_markup=keyboard)
        job["menu_message_id"] = msg.id
    else:
        job["caption_rules"] = ["keep"]
        if job["job_type"] == "batch":
            await track_task(execute_batch(bot, user, job["original_message"], job))
        else:
            await track_task(execute_autoforward(bot, user, job["original_message"], job))

@bot.on_message(filters.command("start") & filters.private)
async def start(_, message: Message):
    welcome_text = (
        "🤖 **Welcome to Save Restricted Bot!**\n\n"
        "I can help you download media from restricted channels and set up auto-forwarding. 🚀\n\n"
        "⚙️ **How to use:**\n"
        "• Just send me any Telegram post link!\n"
        "• Use `/help` to see all commands & examples.\n\n"
        "⚠️ Note: Make sure your user client is already a member of the target chat."
    )
    await message.reply(welcome_text, disable_web_page_preview=True)

@bot.on_message(filters.command("help") & filters.private)
async def help_command(_, message: Message):
    help_text = (
        "💡 **Bot Commands**\n\n"
        "📥 **Single Posts**\n"
        "• Paste any restricted post link directly, or use:\n"
        "`/dl <post_link>`\n\n"
        "📦 **Batch Downloads**\n"
        "• Download a range of restricted posts with optional media filters:\n"
        "`/batch <start_url> <end_url> [filter]`\n"
        "• Filters: `video`, `doc`, `photo`, `audio`\n"
        "• Example: `/batch .../10 .../20 video`\n\n"
        "⚡ **Auto-Forwarding**\n"
        "`/autoforward <from_chat_link> <to_chat_link>`\n\n"
        "✍️ **Caption Editing**\n"
        "• Interactive buttons to clean captions.\n\n"
        "⚙️ **System Controls**\n"
        "• `/stop` - Cancel active tasks\n"
        "• `/stats` - Check bot performance\n"
        "• `/logs` - View system logs\n\n"
        "🔒 **Requirement:** Your user client session must be a member of the source chat."
    )
    await message.reply(help_text, disable_web_page_preview=True)

@bot.on_message(filters.command("batch") & filters.private)
async def download_range(bot: Client, message: Message):
    args = message.text.split()
    if len(args) < 3 or not all(arg.startswith("https://t.me/") for arg in args[1:3]):
        return await message.reply("🚀**Batch Download**\n> `/batch start_link end_link [filter]`")

    filter_type = args[3].lower() if len(args) > 3 else "all"

    try:
        start_chat, start_id = getChatMsgID(args[1])
        end_chat, end_id = getChatMsgID(args[2])
    except Exception as e:
        return await message.reply(f"**❌ Error parsing links:\n{e}**")

    if start_chat != end_chat: return await message.reply("**❌ Both links must be from the same channel.**")
    if start_id > end_id: return await message.reply("**❌ Invalid range.**")

    BATCH_JOBS[message.id] = {
        "start_chat": start_chat,
        "start_id": start_id,
        "end_id": end_id,
        "filter_type": filter_type,
        "prefix": args[1].rsplit("/", 1)[0],
        "job_type": "batch",
        "original_message": message
    }

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Bot Chat", callback_data=f"batch_bot_{message.id}"),
         InlineKeyboardButton("Channel", callback_data=f"batch_chan_{message.id}")]
    ])
    await message.reply("Where do you want to forward the media?", reply_markup=keyboard)

@bot.on_callback_query(filters.regex(r"^batch_(bot|chan)_(\d+)$"))
async def batch_destination_callback(bot: Client, callback_query: CallbackQuery):
    action, msg_id = callback_query.matches[0].groups()
    msg_id = int(msg_id)

    if msg_id not in BATCH_JOBS:
        return await callback_query.answer("Batch process has expired.", show_alert=True)

    job = BATCH_JOBS.pop(msg_id)
    await callback_query.message.delete()

    if action == "bot":
        job["target_chat"] = callback_query.message.chat.id
        await trigger_caption_setup(bot, user, callback_query.message, job)
    elif action == "chan":
        WAITING_FOR_DEST[callback_query.from_user.id] = job
        await job["original_message"].reply("🔗 Send a post link from the target channel.")

@bot.on_message(filters.command(["autoforward"]) & filters.private)
async def auto_forward_init(bot: Client, message: Message):
    args = message.text.split()
    if len(args) < 3 or not all(arg.startswith("https://t.me/") for arg in args[1:3]):
        return await message.reply("🚀 **Auto-Forward**\n> `/autoforward <start_link> <end_link>`")
    
    try:
        start_chat, start_id = getChatMsgID(args[1])
        end_chat, end_id = getChatMsgID(args[2])
    except Exception as e:
        return await message.reply(f"**❌ Error parsing links:\n{e}**")
    
    if start_chat != end_chat: return await message.reply("**❌ Both links must be from the same channel.**")
    if start_id > end_id: return await message.reply("**❌ Invalid range.**")
    
    job = {
        "start_chat": start_chat,
        "start_id": start_id,
        "end_id": end_id,
        "job_type": "autoforward",
        "original_message": message
    }
    
    WAITING_FOR_DEST[message.from_user.id] = job
    await message.reply("🔗 Send a post link from the target channel.")

@bot.on_callback_query(filters.regex(r"^cap_(rm1|rm2|done)_(\d+)$"))
async def caption_rule_callback(bot: Client, callback_query: CallbackQuery):
    action, msg_id = callback_query.matches[0].groups()
    user_id = callback_query.from_user.id
    
    if user_id not in WAITING_FOR_CAPTION_RULE:
        return await callback_query.answer("Session expired or invalid.", show_alert=True)
    
    job = WAITING_FOR_CAPTION_RULE[user_id]
    
    if action == "done":
        WAITING_FOR_CAPTION_RULE.pop(user_id)
        await callback_query.message.delete()
        if job["job_type"] == "batch":
            await track_task(execute_batch(bot, user, job["original_message"], job))
        else:
            await track_task(execute_autoforward(bot, user, job["original_message"], job))
        return
        
    rule_map = {"rm1": "remove_1", "rm2": "remove_2"}
    rule_to_add = rule_map[action]
    
    if rule_to_add in job["caption_rules"]:
        return await callback_query.answer("⚠️ This rule is already applied!", show_alert=True)
        
    job["caption_rules"].append(rule_to_add)
    await callback_query.answer("✅ Rule Added!", show_alert=False)
    
    rules_count = len(job["caption_rules"])
    text = (
        f"**Current Caption:**\n\n`{job['sample_caption'][:300]}...`\n\n"
        "🔄 To clean up a caption reply to the message with the exact text you'd like to remove!\n\n"
        f"> 🎯 **Active Rules:** {rules_count} applied"
    )
    
    try:
        await callback_query.message.edit_text(text, reply_markup=callback_query.message.reply_markup)
    except Exception: pass

@bot.on_message(filters.private & filters.text & ~filters.command(["start", "help", "dl", "stats", "logs", "stop", "autoforward", "batch"]))
async def handle_any_message(bot: Client, message: Message):
    user_id = message.from_user.id

    if user_id in WAITING_FOR_DEST:
        job = WAITING_FOR_DEST.pop(user_id)
        try:
            target_chat_id, _ = getChatMsgID(message.text)
            job["target_chat"] = target_chat_id
            await trigger_caption_setup(bot, user, message, job)
        except Exception as e:
            await message.reply(f"**❌ Error parsing target link:\n{e}**")
        return
    
    if user_id in WAITING_FOR_CAPTION_RULE:
        job = WAITING_FOR_CAPTION_RULE[user_id]
        
        new_rule = f"remove_text:{message.text}"
        if new_rule in job["caption_rules"]:
            await message.reply("⚠️ This text is already in the removal list!")
            return
            
        job["caption_rules"].append(new_rule)

        rules_count = len(job["caption_rules"])
        text = (
            f"**Current Caption:**\n\n`{job['sample_caption'][:300]}...`\n\n"
            "🔄 To clean up a caption reply to the message with the exact text you'd like to remove!\n\n"
            f"> 🎯 **Active Rules:** {rules_count} applied"
        )
        try:
            await bot.edit_message_text(
                chat_id=message.chat.id, 
                message_id=job["menu_message_id"], 
                text=text, 
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Trim Last Line", callback_data=f"cap_rm1_{job['original_message_id']}")],
                    [InlineKeyboardButton("Trim Last 2 Lines", callback_data=f"cap_rm2_{job['original_message_id']}")],
                    [InlineKeyboardButton("✅ Start", callback_data=f"cap_done_{job['original_message_id']}")]
                ])
            )
        except Exception: pass
        
        await message.reply("✅ **Text rule added.** You can add more text to remove, or click **Start** on the menu.")
        return

    if re.search(r"t\.me\/", message.text):
        await track_task(handle_download(bot, user, message, message.text))

@bot.on_message(filters.command("dl") & filters.private)
async def download_media(bot: Client, message: Message):
    if len(message.command) < 2: return await message.reply("**Provide a post URL after the /dl command.**")
    await track_task(handle_download(bot, user, message, message.command[1]))

@bot.on_message(filters.command("stats") & filters.private)
async def stats(_, message: Message):
    currentTime = get_readable_time(time() - PyroConf.BOT_START_TIME)
    def get_sys_stats():
        t, u, f = shutil.disk_usage(".")
        return (
            get_readable_file_size(t), get_readable_file_size(f),
            get_readable_file_size(psutil.net_io_counters().bytes_sent),
            get_readable_file_size(psutil.net_io_counters().bytes_recv),
            psutil.cpu_percent(interval=0.5), psutil.virtual_memory().percent,
            psutil.disk_usage("/").percent, round(psutil.Process(os.getpid()).memory_info()[0] / 1024**2)
        )

    total, free, sent, recv, cpuUsage, memory, disk, proc_mem = await asyncio.to_thread(get_sys_stats)
    
    await message.reply(
        "**Bot's Live and Running Successfully.**\n\n"
        f"**Uptime:** {currentTime} | **Mem:** {proc_mem} MiB\n"
        f"**Free Disk:** {free} of {total}\n"
        f"**Traffic:** 🔼 {sent} | 🔽 {recv}\n"
        f"**System:** CPU: {cpuUsage}% | RAM: {memory}% | DISK: {disk}%"
    )

@bot.on_message(filters.command("logs") & filters.private)
async def logs(_, message: Message):
    if os.path.exists("logs.txt"): await message.reply_document(document="logs.txt", caption="**Logs**")
    else: await message.reply("**Not exists**")

@bot.on_message(filters.command("stop") & filters.private)
async def cancel_all_tasks(_, message: Message):
    cancelled = 0
    for task in list(get_running_tasks()):
        if not task.done():
            task.cancel()
            cancelled += 1
    await message.reply(f"**Cancelled {cancelled} running task(s).**")

if __name__ == "__main__":
    if os.path.exists("downloads"):
        try:
            shutil.rmtree("downloads")
            LOGGER(__name__).info("Cleaned up orphaned files in downloads folder.")
        except Exception as e:
            LOGGER(__name__).error(f"Failed to clean downloads directory: {e}")
    os.makedirs("downloads", exist_ok=True)

    LOGGER(__name__).info("Bot Started!")
    try: compose([bot, user])
    except KeyboardInterrupt: pass
    except Exception as e: LOGGER(__name__).error(f"Bot Crashed: {e}")
    finally: LOGGER(__name__).info("Bot Stopped.")