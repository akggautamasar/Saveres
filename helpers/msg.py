import re
from pyrogram.parser import Parser
from pyrogram.utils import get_channel_id
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

PREFIX_NUM_UNDERSCORE_RE = re.compile(r'^\d+_')
PREFIX_NUM_LETTER_RE = re.compile(r'^(\d+)\s*([a-zA-Z])')
PREFIX_NUM_SPACE_RE = re.compile(r'^\d+ ')

def clean_caption(caption: str) -> str:
    if not caption:
        return ""
    
    caption = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', caption)
    caption = re.sub(r'\]\([^)]+\)', '', caption)
    pattern = r'(?:https?://|www\.|t\.me/|telegram\.me/|chat\.whatsapp\.com/|@)\S+'
    caption = re.sub(pattern, '', caption, flags=re.IGNORECASE)
    caption = re.sub(r'https?://\s*', '', caption, flags=re.IGNORECASE)
    
    return caption.strip()

def apply_caption_rules(caption: str, rules: list) -> str:
    if not caption: 
        return ""
    
    for rule in rules:
        if rule == "keep": 
            continue
            
        lines = caption.split('\n')
        
        if rule == "remove_1" and len(lines) > 0:
            caption = '\n'.join(lines[:-1]).strip()
        elif rule == "remove_2" and len(lines) > 1:
            caption = '\n'.join(lines[:-2]).strip()
        elif rule.startswith("remove_text:"):
            text_to_remove = rule.split("remove_text:", 1)[1]
            caption = caption.replace(text_to_remove, "")
            caption = re.sub(r'[ \t]{2,}', ' ', caption)
            caption = re.sub(r' \.', '.', caption)
            caption = re.sub(r'\n[ \t]+\n', '\n\n', caption)
            caption = caption.strip()

    return caption.strip()

def extract_youtube_keyboard(reply_markup) -> InlineKeyboardMarkup | None:
    if not reply_markup or not hasattr(reply_markup, 'inline_keyboard'):
        return None

    valid_buttons = []
    yt_domains = ("youtube.com", "youtu.be")

    for row in reply_markup.inline_keyboard:
        new_row = []
        for button in row:
            if button.url:
                if any(domain in button.url.lower() for domain in yt_domains):
                    new_row.append(InlineKeyboardButton(text=button.text, url=button.url))
        if new_row:
            valid_buttons.append(new_row)

    if valid_buttons:
        return InlineKeyboardMarkup(valid_buttons)
    return None

async def get_parsed_msg(text, entities):
    return Parser.unparse(text, entities or [], is_html=False)
    
def getChatMsgID(link: str):
    if "?" in link:
        link = link.split("?")[0]

    link = link.rstrip("/")

    linkps = link.split("/")
    chat_id, message_thread_id, message_id = None, None, None
    
    try:
        if len(linkps) == 7 and linkps[3] == "c":
            chat_id = get_channel_id(int(linkps[4]))
            message_thread_id = int(linkps[5])
            message_id = int(linkps[6])
        elif len(linkps) == 6:
            if linkps[3] == "c":
                chat_id = get_channel_id(int(linkps[4]))
                message_id = int(linkps[5])
            else:
                chat_id = linkps[3]
                message_thread_id = int(linkps[4])
                message_id = int(linkps[5])
        elif len(linkps) == 5:
            chat_id = linkps[3]
            if chat_id == "m":
                raise ValueError("Invalid ClientType used to parse this message link")
            message_id = int(linkps[4])
    except (ValueError, TypeError):
        raise ValueError("Invalid post URL. Must end with a numeric ID.")

    if not chat_id or not message_id:
        raise ValueError("Please send a valid Telegram post URL.")

    return chat_id, message_id

def get_file_name(message_id: int, chat_message) -> str:
    def clean_name(name):
        if not name:
            return ""

        name = PREFIX_NUM_UNDERSCORE_RE.sub('', name)
        name = name.replace('_', ' ')

        match = PREFIX_NUM_LETTER_RE.match(name)
        if match:
            prefix_num = match.group(1)
            rest_of_text = name[len(match.group(0))-1:] 
            name = f"{prefix_num}) {rest_of_text}"

        if PREFIX_NUM_SPACE_RE.match(name) and not name.startswith(f"{name.split(' ')[0]})"):
            parts = name.split(' ', 1)
            if len(parts) > 1:
                name = f"{parts[0]}) {parts[1]}"
            
        return name

    filename = ""

    if chat_message.document:
        filename = chat_message.document.file_name
    elif chat_message.video:
        filename = chat_message.video.file_name or f"{message_id}.mp4"
    elif chat_message.audio:
        filename = chat_message.audio.file_name or f"{message_id}.mp3"
    elif chat_message.voice:
        filename = f"{message_id}.ogg"
    elif chat_message.video_note:
        filename = f"{message_id}.mp4"
    elif chat_message.animation:
        filename = chat_message.animation.file_name or f"{message_id}.gif"
    elif chat_message.sticker:
        if chat_message.sticker.is_animated:
            filename = f"{message_id}.tgs"
        elif chat_message.sticker.is_video:
            filename = f"{message_id}.webm"
        else:
            filename = f"{message_id}.webp"
    elif chat_message.photo:
        filename = f"{message_id}.jpg"
    
    final_name = clean_name(filename)

    if not final_name or final_name.strip() == "":
        return str(message_id)

    return final_name