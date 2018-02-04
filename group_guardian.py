#!/usr/bin/env python3
# coding: utf-8

import dotenv
import logging
import mimetypes
import os
import psycopg2
import random
import requests
import string
import time
import urllib.parse

from google.cloud import vision
from urlextract import URLExtract

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, ChatMember, Chat, \
    MessageEntity, ChatAction
from telegram.constants import *
from telegram.ext import Updater, CommandHandler, ConversationHandler, MessageHandler, CallbackQueryHandler, Filters
from telegram.ext.dispatcher import run_async

from feedback_bot import feedback_cov_handler

# Enable logging
logging.basicConfig(format="[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %I:%M:%S %p",
                    level=logging.INFO)
LOGGER = logging.getLogger(__name__)

dotenv_path = os.path.join(os.path.dirname(__file__), ".env")
dotenv.load_dotenv(dotenv_path)
APP_URL = os.environ.get("APP_URL")
PORT = int(os.environ.get("PORT", "5000"))

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN_BETA", os.environ.get("TELEGRAM_TOKEN"))
DEV_TELE_ID = int(os.environ.get("DEV_TELE_ID"))
dev_email = os.environ.get("DEV_EMAIL", "sample@email.com")

if os.environ.get("DATABASE_URL"):
    urllib.parse.uses_netloc.append("postgres")
    db_url = urllib.parse.urlparse(os.environ["DATABASE_URL"])

    db_name = db_url.path[1:]
    db_user = db_url.username
    db_pw = db_url.password
    db_host = db_url.hostname
    db_port = db_url.port
else:
    db_name = os.environ.get("DB_NAME")
    db_user = os.environ.get("DB_USER")
    db_pw = os.environ.get("DB_PW")
    db_host = os.environ.get("DB_HOST")
    db_port = os.environ.get("DB_PORT")

SCANNER_TOKEN = os.environ.get("ATTACHMENT_SCANNER_API_TOKEN")
SCANNER_URL = "https://beta.attachmentscanner.com/requests"
SAFE_BROWSING_TOKEN = os.environ.get("SAFE_BROWSING_TOKEN")
SAFE_BROWSING_URL = "https://safebrowsing.googleapis.com/v4/threatMatches:find"

VISION_IMAGE_SIZE_LIMIT = 4000000
LIKELIHOOD_NAME = ("UNKNOWN", "VERY UNLIKELY", "UNLIKELY", "POSSIBLE", "LIKELY", "VERY LIKELY")


def main():
    create_db_tables()

    # Create the EventHandler and pass it your bot"s token.
    updater = Updater(TELEGRAM_TOKEN)

    forward_filter = Filters.forwarded | ~Filters.forwarded

    # Get the dispatcher to register handlers
    dp = updater.dispatcher
    # on different commands - answer in Telegram
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help))
    dp.add_handler(CommandHandler("donate", donate))
    dp.add_handler(MessageHandler(Filters.status_update.new_chat_members, group_greeting))
    dp.add_handler(MessageHandler((Filters.document & forward_filter), check_document))
    dp.add_handler(MessageHandler((Filters.photo & forward_filter), check_image))
    dp.add_handler(MessageHandler((Filters.entity(MessageEntity.URL) & forward_filter), check_url))
    dp.add_handler(MessageHandler(((Filters.audio | Filters.video) & forward_filter), check_audio_video))
    dp.add_handler(CallbackQueryHandler(inline_button))
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


# Connects to database
def connect_db():
    return psycopg2.connect(database=db_name, user=db_user, password=db_pw, host=db_host, port=db_port)


# Creates database tables
def create_db_tables():
    db = connect_db()
    cur = db.cursor()

    cur.execute("select * from information_schema.tables where table_name = 'msg_info'")
    if not cur.fetchone():
        # cur.execute("drop table msg_info")
        cur.execute("create table msg_info (chat_id int, msg_id int, user_name text, file_id text, file_type text, "
                    "msg_text text)")

    db.commit()
    db.close()


# Sends start message
@run_async
def start(bot, update):
    text = "Welcome to Group Guardian!\n\n"
    text += "I can protect you and your group from files or links that may contain threats, and photos or links of " \
            "photos that may contain adult, spoof, medical or violence content.\n\n"
    text += "Type /help to see how to use me."

    try:
        bot.sendMessage(update.message.from_user.id, text)
    except:
        return


# Sends help message
@run_async
def help(bot, update):
    text = "If you are just chatting with me, simply send me any files or links and I will tell you if they and " \
           "their content (photos only) are safe and appropriate.\n\n"
    text += "If you want me to guard your group, add me into your group and set me as an admin. I will check " \
            "every file and link that is sent to the group and delete it if it is not safe.\n\n"
    text += "As a group admin, you can choose to undo the message that I deleted to review it.\n\n"
    text += "Please note that I can only download files up to 20 MB in size. And for photo content checking, " \
            "I can only handle photos up to 4 MB in size. Any files that have a size greater than the limits " \
            "will be ignored."

    keyboard = [[InlineKeyboardButton("Join Channel", "https://t.me/grpguardianbotdev")],
                [InlineKeyboardButton("Rate me", "https://t.me/storebot?start=grpguardianbot")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        bot.sendMessage(update.message.from_user.id, text, reply_markup=reply_markup)
    except:
        return


# Sends donate message
@run_async
def donate(bot, update):
    text = "Want to help keep me online? Please donate to %s through PayPal.\n\nDonations help me to stay on my " \
           "server and keep running." % dev_email

    try:
        bot.send_message(update.message.from_user.id, text)
    except:
        return


# Greets when bot is added to group and asks for bot admin
@run_async
def group_greeting(bot, update):
    for user in update.message.new_chat_members:
        if user.id == bot.id:
            text = "Hello everyone! I am Group Guardian. Please set me as one of the admins so that I can start " \
                   "guarding your group."
            bot.send_message(update.message.chat_id, text)

            return


# Checks for document
@run_async
def check_document(bot, update):
    update.message.chat.send_action(ChatAction.TYPING)

    doc = update.message.document
    doc_size = doc.file_size

    if doc_size > MAX_FILESIZE_DOWNLOAD:
        return

    doc_id = doc.file_id
    doc_mime_type = doc.mime_type
    doc_file = bot.get_file(doc_id)
    doc_path = doc_file.file_path

    if is_file_safe(bot, update, doc_path, "doc", doc_id):
        if doc_mime_type.startswith("image"):
            if doc_size <= VISION_IMAGE_SIZE_LIMIT:
                is_image_safe(bot, update, doc_path, "doc", image_id=doc_id)
            else:
                text = "This document of photo can't be checked as it is too large for me to process."
                update.message.reply_text(text)


# Checks for image
@run_async
def check_image(bot, update):
    update.message.chat.send_action(ChatAction.TYPING)

    image = update.message.photo[-1]
    image_size = image.file_size

    if image_size > MAX_FILESIZE_DOWNLOAD:
        return

    image_id = image.file_id
    image_file = bot.get_file(image_id)
    image_path = image_file.file_path

    if is_file_safe(bot, update, image_path, "img", image_id):
        if image_size <= VISION_IMAGE_SIZE_LIMIT:
            is_image_safe(bot, update, image_path, "img", image_id=image_id)
        else:
            update.message.reply_text("This photo can't be checked as it is too large for me to process.")


# Checks for url
@run_async
def check_url(bot, update):
    update.message.chat.send_action(ChatAction.TYPING)

    msg_deleted = False
    large_err = ""
    download_err = ""

    text = update.message.text
    chat_type = update.message.chat.type
    extractor = URLExtract()
    urls = extractor.find_urls(text)

    for url in urls:
        mime_type = mimetypes.guess_type(url)[0]

        if not mime_type or (mime_type and not mime_type.startswith("image")):
            if not is_url_safe(bot, update, url, text) and chat_type in (Chat.GROUP, Chat.SUPERGROUP):
                msg_deleted = True
                break
        elif mime_type.startswith("image"):
            response = requests.get(url)

            if response.status_code == 200:
                filename = random_string(20)
                with open(filename, "wb") as f:
                    f.write(response.content)

                if os.path.getsize(filename) <= VISION_IMAGE_SIZE_LIMIT:
                    if not is_image_safe(bot, update, url, "url", msg_text=text, is_image_url=True) and \
                                    chat_type in (Chat.GROUP, Chat.SUPERGROUP):
                        msg_deleted = True
                        break
                else:
                    if chat_type in (Chat.GROUP, Chat.SUPERGROUP):
                        large_err = "Some of the photo links in this message are not checked as they are too large " \
                                    "for me to process."
                    else:
                        update.message.reply_text("%s\nThis photo link can't be checked as it is too large for me to "
                                                  "process." % url)
                os.remove(filename)
            else:
                if chat_type in (Chat.GROUP, Chat.SUPERGROUP):
                    download_err = "Some of the photo links in this message are not checked as I can't retrieve " \
                                   "the photos."
                else:
                    update.message.reply_text("%s\nThis photo link can't be checked as I can't retrieve the photo." %
                                              url)

    if not msg_deleted and (large_err or download_err):
        err_msg = large_err + " " + download_err
        update.message.reply_text(err_msg)


# Checks for audio or video
def check_audio_video(bot, update):
    update.message.chat.send_action(ChatAction.TYPING)

    audio, video = update.message.audio, update.message.video

    if audio:
        file_id = audio.file_id
        file_size = audio.file_size

        if file_size > MAX_FILESIZE_DOWNLOAD:
            return

        file_path = bot.get_file(file_id).file_path
        is_file_safe(bot, update, file_path, "aud", file_id)
    else:
        file_id = video.file_id
        file_size = video.file_size

        if file_size > MAX_FILESIZE_DOWNLOAD:
            return

        file_path = bot.get_file(file_id).file_path
        is_file_safe(bot, update, file_path, "vid", file_id)


# Checks if a file is safe
def is_file_safe(bot, update, file_path, file_type, file_id):
    safe_file = True
    chat_id = update.message.chat_id
    chat_type = update.message.chat.type
    msg_id = update.message.message_id
    user_name = update.message.from_user.first_name

    headers = {"Content-Type": "application/json", "Authorization": "bearer %s" % SCANNER_TOKEN}
    json = {"url": file_path}
    response = requests.post(url=SCANNER_URL, headers=headers, json=json)

    if response.status_code == 200:
        results = response.json()

        if "matches" in results and results["matches"]:
            safe_file = False

            if chat_type in (Chat.GROUP, Chat.SUPERGROUP):
                if bot.get_chat_member(chat_id, bot.id).status != ChatMember.ADMINISTRATOR:
                    return

                while True:
                    try:
                        db = connect_db()
                        break
                    except Exception:
                        time.sleep(1)
                        continue

                cur = db.cursor()
                cur.execute("insert into msg_info (chat_id, msg_id, user_name, file_id, file_type, msg_text) values "
                            "(%s, %s, %s, %s, %s, %s)", (chat_id, msg_id, user_name, file_id, file_type, None))
                db.commit()
                db.close()

                if file_type == "img":
                    text = "{} sent a photo but I deleted it as it contains threats.".format(user_name)
                elif file_type == "aud":
                    text = "{} sent a audio but I deleted it as it contains threats.".format(user_name)
                elif file_type == "vid":
                    text = "{} sent a video but I deleted it as it contains threats.".format(user_name)
                else:
                    text = "{} sent a document but I deleted it as it contains threats.".format(user_name)

                keyboard = [[InlineKeyboardButton(text="Undo", callback_data="undo," + str(msg_id))]]
                reply_markup = InlineKeyboardMarkup(keyboard)

                update.message.delete()
                bot.send_message(chat_id, text, reply_markup=reply_markup)
            else:
                update.message.reply_text("I think this contains threats. Don't download or open it.", quote=True)
        else:
            if chat_type == Chat.PRIVATE:
                update.message.reply_text("I think this doesn't contain threats.", quote=True)

    return safe_file


# Checks if image's content is safe
def is_image_safe(bot, update, image_path, image_type, image_id=None, msg_text=None, is_image_url=False):
    safe_image = True
    chat_id = update.message.chat_id
    chat_type = update.message.chat.type
    msg_id = update.message.message_id
    user_name = update.message.from_user.first_name

    client = vision.ImageAnnotatorClient()
    image = vision.types.Image()
    image.source.image_uri = image_path
    response = client.safe_search_detection(image=image)
    safe = response.safe_search_annotation
    adult, spoof, medical, violence = safe.adult, safe.spoof, safe.medical, safe.violence

    if adult >= 3 or spoof >= 3 or medical >= 3 or violence >= 3:
        safe_image = False

        if chat_type in (Chat.GROUP, Chat.SUPERGROUP):
            if bot.get_chat_member(chat_id, bot.id).status != ChatMember.ADMINISTRATOR:
                return

            while True:
                try:
                    db = connect_db()
                    break
                except Exception:
                    time.sleep(1)
                    continue

            cur = db.cursor()
            cur.execute("insert into msg_info (chat_id, msg_id, user_name, file_id, file_type, msg_text) values "
                        "(%s, %s, %s, %s, %s, %s)", (chat_id, msg_id, user_name, image_id, image_type, msg_text))
            db.commit()
            db.close()

            if image_type == "doc":
                text = "I deleted a document of photo that's "
            elif image_type == "img":
                text = "I deleted a photo that's "
            else:
                text = "I deleted a message that contains a photo link that's "

            if adult >= 3:
                text += "{} to contain adult content, ".format(LIKELIHOOD_NAME[adult])
            if spoof >= 3:
                text += "{} to contain spoof content, ".format(LIKELIHOOD_NAME[spoof])
            if medical >= 3:
                text += "{} to contain medical content, ".format(LIKELIHOOD_NAME[medical])
            if violence >= 3:
                text += "{} to contain violence content, ".format(LIKELIHOOD_NAME[violence])
            text += "which was sent by {}.".format(user_name)

            keyboard = [[InlineKeyboardButton(text="Undo", callback_data="undo," + str(msg_id))]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            update.message.delete()
            bot.send_message(chat_id, text, reply_markup=reply_markup)
        else:
            if is_image_url:
                text = "%s\nThis link is " % image_path
            else:
                text = "This is "

            if adult >= 3:
                text += "{} to contain adult content, ".format(LIKELIHOOD_NAME[adult])
            if spoof >= 3:
                text += "{} to contain spoof content, ".format(LIKELIHOOD_NAME[spoof])
            if medical >= 3:
                text += "{} to contain medical content, ".format(LIKELIHOOD_NAME[medical])
            if violence >= 3:
                text += "{} to contain violence content, ".format(LIKELIHOOD_NAME[violence])
            text = text.rstrip(", ") + "."

            update.message.reply_text(text, quote=True)
    else:
        if chat_type == Chat.PRIVATE:
            update.message.reply_text("I think this does not contain any inappropriate content.", quote=True)

    return safe_image


# Checks if url is safe
def is_url_safe(bot, update, url, msg_text):
    safe_url = True
    chat_id = update.message.chat_id
    chat_type = update.message.chat.type
    msg_id = update.message.message_id
    user_name = update.message.from_user.first_name

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
    response = requests.post(url=SAFE_BROWSING_URL, headers=headers, params=params, json=json)

    if response.status_code == 200:
        results = response.json()

        if "matches" in results and results["matches"]:
            if bot.get_chat_member(chat_id, bot.id).status != ChatMember.ADMINISTRATOR:
                return

            safe_url = False

            if chat_type in (Chat.GROUP, Chat.SUPERGROUP):
                while True:
                    try:
                        db = connect_db()
                        break
                    except Exception:
                        time.sleep(1)
                        continue

                cur = db.cursor()
                cur.execute("insert into msg_info (chat_id, msg_id, user_name, file_id, file_type, msg_text) values "
                            "(%s, %s, %s, %s, %s)", (chat_id, msg_id, user_name, None, None, msg_text))
                db.commit()
                db.close()

                text = "{} sent a message but I deleted it as it contains a link with threats.".format(user_name)
                keyboard = [[InlineKeyboardButton(text="Undo", callback_data="undo," + str(msg_id))]]
                reply_markup = InlineKeyboardMarkup(keyboard)

                update.message.delete()
                bot.send_message(chat_id, text, reply_markup=reply_markup)
            else:
                update.message.reply_text("%s\nThis link contains threats. I don't recommend you to click on it." % url,
                                          quote=True)
        else:
            if chat_type == Chat.PRIVATE:
                update.message.reply_text("%s\nI think this link is safe." % url, quote=True)

    return safe_url


# Handles inline button
@run_async
def inline_button(bot, update):
    query = update.callback_query
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    task, msg_id = query.data.split(",")
    msg_id = int(msg_id)

    if query.message.chat.type in (Chat.GROUP, Chat.SUPERGROUP):
        member = bot.get_chat_member(chat_id, user_id)

        if member.status not in (ChatMember.ADMINISTRATOR, ChatMember.CREATOR):
            return

    if task == "undo":
        while True:
            try:
                db = connect_db()
                break
            except Exception:
                time.sleep(1)
                continue

        cur = db.cursor()
        cur.execute("select user_name, file_id, file_type, msg_text from msg_info where chat_id = %s and msg_id = %s",
                    (chat_id, msg_id))
        user_name, file_id, file_type, msg_text = cur.fetchone()
        cur.execute("delete from msg_info where chat_id = %s and msg_id = %s", (chat_id, msg_id))
        db.commit()
        db.close()

        keyboard = [[InlineKeyboardButton(text="Delete (No Undo)", callback_data="delete," + str(msg_id))]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        query.message.delete()

        if file_id:
            if file_type == "img":
                bot.send_photo(chat_id, file_id, caption="%s sent this." % user_name, reply_markup=reply_markup)
            elif file_type == "aud":
                bot.send_audio(chat_id, file_id, caption="%s sent this." % user_name, reply_markup=reply_markup)
            elif file_type == "vid":
                bot.send_video(chat_id, file_id, caption="%s sent this." % user_name, reply_markup=reply_markup)
            else:
                bot.send_document(chat_id, file_id, caption="%s sent this." % user_name, reply_markup=reply_markup)
        else:
            bot.send_message(chat_id, "%s sent this:\n%s" % (user_name, msg_text), reply_markup=reply_markup)
    elif task == "delete":
        query.message.delete()


# Returns a random string
def random_string(length):
    return "".join(random.choice(string.ascii_letters + string.digits) for _ in range(length))


# Cancels feedback opteration
@run_async
def cancel(bot, update):
    update.message.reply_text("Operation cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


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
