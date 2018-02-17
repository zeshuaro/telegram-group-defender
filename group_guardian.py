#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import dotenv
import logging
import mimetypes
import os
import requests
import tempfile

from google.cloud import datastore, vision

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ChatMember, Chat, MessageEntity, ChatAction
from telegram.constants import *
from telegram.error import BadRequest
from telegram.ext import Updater, CommandHandler, MessageHandler, CallbackQueryHandler, Filters
from telegram.ext.dispatcher import run_async

from feedback_bot import feedback_cov_handler

# Enable logging
logging.basicConfig(format="[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %I:%M:%S %p",
                    level=logging.INFO)
LOGGER = logging.getLogger(__name__)

dotenv.load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
GCP_ID = os.environ.get("GOOGLE_CLOUD_PROJECT")
PORT = int(os.environ.get("PORT", "5000"))

if GCP_ID:
    APP_URL = f"https://{GCP_ID}.appspot.com/"
else:
    APP_URL = os.environ.get("APP_URL")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN_BETA", os.environ.get("TELEGRAM_TOKEN"))
DEV_TELE_ID = int(os.environ.get("DEV_TELE_ID"))
DEV_EMAIL = os.environ.get("DEV_EMAIL", "sample@email.com")

SCANNER_TOKEN = os.environ.get("ATTACHMENT_SCANNER_TOKEN")
SAFE_BROWSING_TOKEN = os.environ.get("SAFE_BROWSING_TOKEN")

CHANNEL_NAME = "grpguardianbotdev"  # Channel username
BOT_NAME = "grpguardianbot"  # Bot username

FILE_TYPE_NAMES = {"aud": "audio", "doc": "document", "img": "image", "vid": "video", "url": "url"}
VISION_IMAGE_SIZE_LIMIT = 4000000
SAFE_ANN_THRESHOLD = 3


def main():
    # Create the EventHandler and pass it your bot"s token.
    updater = Updater(TELEGRAM_TOKEN, request_kwargs={"connect_timeout": 20, "read_timeout": 20})

    # Get the dispatcher to register handlers
    dp = updater.dispatcher
    # on different commands - answer in Telegram
    dp.add_handler(CommandHandler("start", start_msg))
    dp.add_handler(CommandHandler("help", help_msg))
    dp.add_handler(CommandHandler("donate", donate_msg))
    dp.add_handler(MessageHandler(Filters.status_update.new_chat_members, group_greeting))
    dp.add_handler(MessageHandler((Filters.audio | Filters.document | Filters.photo | Filters.video), check_file))
    dp.add_handler(MessageHandler(Filters.entity(MessageEntity.URL), check_url))
    dp.add_handler(CallbackQueryHandler(inline_button_handler))
    dp.add_handler(feedback_cov_handler())
    dp.add_handler(CommandHandler("send", send, Filters.user(DEV_TELE_ID), pass_args=True))

    # log all errors
    dp.add_error_handler(error)

    # Start the Bot
    if APP_URL:
        updater.start_webhook(listen="0.0.0.0",
                              port=PORT,
                              url_path=TELEGRAM_TOKEN)
        updater.bot.set_webhook(APP_URL + TELEGRAM_TOKEN)
    else:
        updater.start_polling()

    # Run the bot until the you presses Ctrl-C or the process receives SIGINT,
    # SIGTERM or SIGABRT. This should be used most of the time, since
    # start_polling() is non-blocking and will stop the bot gracefully.
    updater.idle()


# Send start message
@run_async
def start_msg(bot, update):
    text = "Welcome to Group Guardian!\n\n"
    text += "I can protect you and your group from files or links that may contain threats, and photos or urls of " \
            "photos that may contain adult, spoof, medical, violence or racy content.\n\n"
    text += "Type /help to see how to use me."

    update.message.reply_text(text)


# Send help message
@run_async
def help_msg(bot, update):
    text = "If you're just chatting with me, simply send me a file or a url and I'll tell you if it is safe. " \
           "If it is a photo, I'll also tell you if it is appropriate.\n\n"
    text += "If you want me to guard your group, add me into your group and set me as an admin. " \
            "I'll check every file and url sent to the group and delete it if it is not safe.\n\n"
    text += "As a group admin, you can choose to undo the message that I deleted to review it.\n\n"
    text += "Please note that I can only download files up to 20 MB in size. And for photo content checking, " \
            "I can only handle photos up to 4 MB in size. Any files that have a size greater than the limits " \
            "will be ignored in groups."

    keyboard = [[InlineKeyboardButton("Join Channel", f"https://t.me/{CHANNEL_NAME}"),
                 InlineKeyboardButton("Rate me", f"https://t.me/storebot?start={BOT_NAME}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    update.message.reply_text(text, reply_markup=reply_markup)


# Send donate message
@run_async
def donate_msg(bot, update):
    text = f"Want to help keep me online? Please donate to {DEV_EMAIL} through PayPal.\n\n" \
           f"Donations help me to stay on my server and keep running."

    update.message.reply_text(text)


# Greet when bot is added to group and asks for bot admin
@run_async
def group_greeting(bot, update):
    for user in update.message.new_chat_members:
        if user.id == bot.id:
            text = "Hello everyone! I am Group Guardian. Please set me as one of the admins " \
                   "so that I can start guarding your group."
            bot.send_message(update.message.chat.id, text)


# Check for file
@run_async
def check_file(bot, update):
    # Check if bot in group and if bot is a group admin, if not, files will not be checked
    if update.message.chat.type in (Chat.GROUP, Chat.SUPERGROUP) and \
            bot.get_chat_member(update.message.chat_id, bot.id).status != ChatMember.ADMINISTRATOR:
        update.message.reply_text("Please set me as a group admin so that I can start checking files like this.")

    # Grab the received file
    update.message.chat.send_action(ChatAction.TYPING)
    files = [update.message.document, update.message.audio, update.message.video, update.message.photo]
    index, file = next(x for x in enumerate(files) if x[1] is not None)

    file_types = ("doc", "aud", "vid", "img")
    file_type = file_types[index]
    file = file[-1] if file_type == "img" else file
    file_size = file.file_size

    # Check if file is too large for bot to download
    if file_size > MAX_FILESIZE_DOWNLOAD:
        if update.message.chat.type == Chat.PRIVATE:
            text = f"Your {FILE_TYPE_NAMES[file_type]} is too large for me to download and process, sorry."
            update.message.reply_text(text)

        return

    file_id = file.file_id
    file_mime_type = "image" if file_type == "img" else file.mime_type

    _, text = is_malware_and_vision_safe(bot, update, file_type, file_mime_type, file_size, file_id)
    if text:
        update.message.reply_text(text, quote=True)


# Master function for checking malware and vision
def is_malware_and_vision_safe(bot, update, file_type, file_mime_type, file_size, file_id=None, file_url=None):
    if file_id is None and file_url is None:
        raise ValueError("You must provide either file_id or file_url")

    # Setup return variables
    safe = True
    reply_text = ""

    # Grab info from message
    chat_type = update.message.chat.type
    chat_id = update.message.chat_id
    msg_id = update.message.message_id
    user_name = update.message.from_user.first_name
    msg_text = update.message.text

    if not is_malware_safe(bot, file_id, file_url):
        safe = False

        # Delete message if it is a group chat
        if chat_type in (Chat.GROUP, Chat.SUPERGROUP):
            store_msg(chat_id, msg_id, user_name, file_id, file_type, msg_text)

            text = f"I deleted a {FILE_TYPE_NAMES[file_type]} that contains threats (sent by {user_name})."
            keyboard = [[InlineKeyboardButton(text="Undo", callback_data=f"undo,{msg_id}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            update.message.delete()
            bot.send_message(chat_id, text, reply_markup=reply_markup)
        else:
            if file_url:
                reply_text = f"{file_url}\n⬆ I think it contains threats, don't download or open it."
            else:
                reply_text = "I think it contains threats, don't download or open it."
    else:
        if file_url:
            reply_text = f"{file_url}\n"
        else:
            reply_text = ""

        if chat_type == Chat.PRIVATE:
            reply_text += "⬆ I think it doesn't contain threats. "

        if file_type == "img" or file_mime_type.startswith("image"):
            if file_size <= VISION_IMAGE_SIZE_LIMIT:
                vision_safe, vision_results = is_vision_safe(bot, file_id, file_url)
                safe_ann_index = next((x[0] for x in enumerate(vision_results) if x[1] > SAFE_ANN_THRESHOLD), 0)
                safe_ann_value = vision_results[safe_ann_index]

                if not vision_safe:
                    safe = False
                    safe_ann_likelihoods = ("unknown", "very likely", "unlikely", "possible", "likely", "very likely")
                    safe_ann_types = ("adult", "spoof", "medical", "violence", "racy")

                    # Delete message if it is a group chat
                    if chat_type in (Chat.GROUP, Chat.SUPERGROUP):
                        store_msg(chat_id, msg_id, user_name, file_id, file_type, msg_text)

                        if file_url:
                            text = "I deleted a message which contains a link of photo that's "
                        else:
                            text = "I deleted a photo that's "

                        text += f"{safe_ann_likelihoods[safe_ann_value]} to contain " \
                                f"{safe_ann_types[safe_ann_index]} content (sent by {user_name})."

                        keyboard = [[InlineKeyboardButton(text="Undo", callback_data=f"undo,{msg_id}")]]
                        reply_markup = InlineKeyboardMarkup(keyboard)

                        update.message.delete()
                        bot.send_message(chat_id, text, reply_markup=reply_markup)
                    else:
                        reply_text += f"But I think it is {safe_ann_likelihoods[safe_ann_value]} " \
                                      f"to contain {safe_ann_types[safe_ann_index]} content."
                else:
                    if chat_type == Chat.PRIVATE:
                        reply_text += "And I think it doesn't contain any inappropriate content."
            else:
                if update.message.chat.type == Chat.PRIVATE:
                    reply_text += "But it is too large for me to check for inappropriate content."

    return safe, reply_text


# Check if the file is malware safe
def is_malware_safe(bot, file_id, file_url):
    safe = True
    url = "https://beta.attachmentscanner.com/requests"

    if file_id:
        # Download file and open it for upload
        tf = tempfile.NamedTemporaryFile()
        tf_name = tf.name
        file = bot.get_file(file_id)
        file.download(tf_name)
        files = {"file": open(tf_name, "rb")}
        tf.close()

        # Make the request to check for malware
        headers = {
            "accept": "application/json",
            "authorization": f"bearer {SCANNER_TOKEN}"
        }
        response = requests.post(url=url, headers=headers, files=files)
    else:
        headers = {
            "content-type": "application/json",
            "authorization": f"bearer {SCANNER_TOKEN}"
        }
        json = {"url": file_url}
        response = requests.post(url=url, headers=headers, json=json)

    if response.status_code == 200:
        results = response.json()
        if "matches" in results and results["matches"]:
            safe = False

    return safe


# Check if the image is vision safe
def is_vision_safe(bot, file_id, file_url):
    safe = True

    if file_id:
        # Download file and open it for upload
        tf = tempfile.NamedTemporaryFile()
        tf_name = tf.name
        file = bot.get_file(file_id)
        file.download(tf_name)
        with open(tf_name, 'rb') as f:
            content = f.read()
        tf.close()

        image = vision.types.Image(content=content)
    else:
        image = vision.types.Image()
        image.source.image_uri = file_url

    client = vision.ImageAnnotatorClient()
    response = client.safe_search_detection(image=image)
    safe_ann = response.safe_search_annotation
    safe_ann_results = [safe_ann.adult, safe_ann.spoof, safe_ann.medical, safe_ann.violence, safe_ann.racy]

    if any(x > SAFE_ANN_THRESHOLD for x in safe_ann_results):
        safe = False

    return safe, safe_ann_results


# Store message information on Google Datastore
def store_msg(chat_id, msg_id, user_name, file_id, file_type, msg_text):
    client = datastore.Client()
    key = client.key("ChatID", chat_id, "MsgID", msg_id)
    entity = datastore.Entity(key)
    entity.update({
        "user_name": user_name,
        "file_id": file_id,
        "file_type": file_type,
        "msg_text": msg_text
    })
    client.put(entity)


# Check for url
@run_async
def check_url(bot, update):
    # Check if bot in group and if bot is a group admin, if not, urls will not be checked
    if update.message.chat.type in (Chat.GROUP, Chat.SUPERGROUP) and \
            bot.get_chat_member(update.message.chat_id, bot.id).status != ChatMember.ADMINISTRATOR:
        update.message.reply_text("Please set me as a group admin so that I can start checking urls like this.")

    update.message.chat.send_action(ChatAction.TYPING)
    chat_type = update.message.chat.type
    chat_id = update.message.chat_id
    msg_id = update.message.message_id
    user_name = update.message.from_user.first_name
    msg_text = update.message.text

    entities = update.message.parse_entities([MessageEntity.URL])
    urls = entities.values()
    reply_text = ""

    for url in urls:
        mime_type = mimetypes.guess_type(url)[0]
        if mime_type:
            response = requests.get(url)
            if response.status_code == 200:
                safe, text = is_malware_and_vision_safe(
                    bot, update, "url", mime_type, len(response.content), file_url=url)
                reply_text += f"{text}\n\n"

                if not safe and chat_type in (Chat.GROUP, Chat.SUPERGROUP):
                    break
            else:
                if chat_type == Chat.PRIVATE:
                    reply_text += f"{url}\n⬆ I couldn't check it as I couldn't access it.\n\n"
        else:
            if not is_url_safe(url):
                # Delete message if it is a group chat
                if chat_type in (Chat.GROUP, Chat.SUPERGROUP):
                    store_msg(chat_id, msg_id, user_name, None, "url", msg_text)

                    text = f"I deleted a message that contains urls with threats (sent by {user_name})."
                    keyboard = [[InlineKeyboardButton(text="Undo", callback_data=f"undo,{msg_id}")]]
                    reply_markup = InlineKeyboardMarkup(keyboard)

                    update.message.delete()
                    bot.send_message(chat_id, text, reply_markup=reply_markup)
                    break
                else:
                    reply_text += f"{url}\n⬆ I think it contains threats, don't open it.\n\n"
            else:
                if chat_type == Chat.PRIVATE:
                    reply_text += f"{url}\n⬆ I think it is safe.\n\n"

    if chat_type == Chat.PRIVATE:
        update.message.reply_text(reply_text, quote=True, disable_web_page_preview=True)


# Check if url is safe
def is_url_safe(url):
    safe_url = True

    safe_browsing_url = "https://safebrowsing.googleapis.com/v4/threatMatches:find"
    headers = {"Content-Type": "application/json"}
    params = {"key": SAFE_BROWSING_TOKEN}
    json = {
        "threatInfo": {
            "threatTypes": ["MALWARE", "SOCIAL_ENGINEERING"],
            "platformTypes": ["ANY_PLATFORM"],
            "threatEntryTypes": ["URL"],
            "threatEntries": [{"url": url}]
        }
    }
    response = requests.post(url=safe_browsing_url, headers=headers, params=params, json=json)

    if response.status_code == 200:
        results = response.json()

        if "matches" in results and results["matches"]:
            safe_url = False

    return safe_url


# Handle inline button
@run_async
def inline_button_handler(bot, update):
    query = update.callback_query
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    task, msg_id = query.data.split(",")
    msg_id = int(msg_id)

    if query.message.chat.type in (Chat.GROUP, Chat.SUPERGROUP) and \
            bot.get_chat_member(chat_id, user_id).status not in (ChatMember.ADMINISTRATOR, ChatMember.CREATOR):
        return

    if task == "undo":
        client = datastore.Client()
        key = client.key("ChatID", chat_id, "MsgID", msg_id)
        entity = client.get(key)

        if entity:
            user_name = entity["user_name"]
            file_id = entity["file_id"]
            file_type = entity["file_type"]
            msg_text = entity["msg_text"]
            client.delete(key)

            try:
                query.message.delete()
            except BadRequest:
                return

            keyboard = [[InlineKeyboardButton(text="Delete (No Undo)", callback_data="delete," + str(msg_id))]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            if file_id:
                if file_type == "img":
                    bot.send_photo(chat_id, file_id, caption=f"{user_name} sent this.", reply_markup=reply_markup)
                elif file_type == "aud":
                    bot.send_audio(chat_id, file_id, caption=f"{user_name} sent this.", reply_markup=reply_markup)
                elif file_type == "vid":
                    bot.send_video(chat_id, file_id, caption=f"{user_name} sent this.", reply_markup=reply_markup)
                else:
                    bot.send_document(chat_id, file_id, caption=f"{user_name} sent this.", reply_markup=reply_markup)
            else:
                bot.send_message(chat_id, f"{user_name} sent this:\n{msg_text}", reply_markup=reply_markup)
    elif task == "delete":
        try:
            query.message.delete()
        except BadRequest:
            pass


# Send a message to a specified user
def send(bot, update, args):
    tele_id = int(args[0])
    message = " ".join(args[1:])

    try:
        bot.send_message(tele_id, message)
    except Exception as e:
        LOGGER.exception(e)
        bot.send_message(DEV_TELE_ID, "Failed to send message")


def error(bot, update, error):
    LOGGER.warning("Update '%s' caused error '%s'" % (update, error))


if __name__ == "__main__":
    main()
