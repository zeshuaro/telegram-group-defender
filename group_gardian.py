#!/usr/bin/env python3
# coding: utf-8

import dotenv
import langdetect
import logging
import mimetypes
import os
import psycopg2
import random
import requests
import smtplib
import string
import time
import urllib.parse

from google.cloud import vision
from urlextract import URLExtract

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, ChatMember, Chat, MessageEntity
from telegram.error import TelegramError
from telegram.ext import Updater, CommandHandler, ConversationHandler, MessageHandler, CallbackQueryHandler, Filters
from telegram.ext.dispatcher import run_async

# Enable logging
logging.basicConfig(format="[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %I:%M:%S %p",
                    level=logging.INFO)
logger = logging.getLogger(__name__)

dotenv_path = os.path.join(os.path.dirname(__file__), ".env")
dotenv.load(dotenv_path)
app_url = os.environ.get("APP_URL")
port = int(os.environ.get("PORT", "5000"))

telegram_token = os.environ.get("TELEGRAM_TOKEN_BETA") if os.environ.get("TELEGRAM_TOKEN_BETA") \
    else os.environ.get("TELEGRAM_TOKEN")
dev_tele_id = int(os.environ.get("DEV_TELE_ID"))
dev_email = os.environ.get("DEV_EMAIL") if os.environ.get("DEV_EMAIL") else "sample@email.com"
dev_email_pw = os.environ.get("DEV_EMAIL_PW")
is_email_feedback = os.environ.get("IS_EMAIL_FEEDBACK")
smtp_host = os.environ.get("SMTP_HOST")

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

safe_browsing_url = "https://safebrowsing.googleapis.com/v4/threatMatches:find"
safe_browsing_token = os.environ.get("SAFE_BROWSING_TOKEN")
vision_image_size_limit = 4000000
likelihood_name = ("UNKNOWN", "VERY UNLIKELY", "UNLIKELY", "POSSIBLE", "LIKELY", "VERY LIKELY")


def main():
    create_db_tables()

    # Create the EventHandler and pass it your bot"s token.
    updater = Updater(telegram_token)

    # Get the dispatcher to register handlers
    dp = updater.dispatcher
    # on different commands - answer in Telegram
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help))
    dp.add_handler(CommandHandler("donate", donate))
    dp.add_handler(MessageHandler((Filters.document & (Filters.forwarded | ~Filters.forwarded)), check_document))
    dp.add_handler(MessageHandler((Filters.photo & (Filters.forwarded | ~Filters.forwarded)), check_image))
    dp.add_handler(MessageHandler((Filters.entity(MessageEntity.URL) & (Filters.forwarded | ~Filters.forwarded)),
                                  check_url))
    dp.add_handler(CallbackQueryHandler(inline_button))
    dp.add_handler(feedback_cov_handler())
    dp.add_handler(CommandHandler("send", send, pass_args=True))

    # log all errors
    dp.add_error_handler(error)

    # Start the Bot
    if app_url:
        updater.start_webhook(listen="0.0.0.0",
                              port=port,
                              url_path=telegram_token)
        updater.bot.set_webhook(app_url + telegram_token)
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

    cur.execute("select * from information_schema.tables where table_name = 'msg_image_info'")
    if cur.fetchone():
        cur.execute("drop table msg_image_info")
    cur.execute("create table msg_image_info (chat_id int, msg_id int, user_name text, image_id text, msg_text text)")

    db.commit()
    db.close()


# Sends start message
@run_async
def start(bot, update):
    tele_id = update.message.chat.id

    if update.message.chat.type != "group":
        message = "Welcome to Group Guardian."

        bot.sendMessage(tele_id, message)


# Sends help message
@run_async
def help(bot, update):
    player_tele_id = update.message.from_user.id

    message = "Help"

    bot.sendMessage(player_tele_id, message)


# Sends donate message
@run_async
def donate(bot, update):
    player_tele_id = update.message.from_user.id
    message = "Want to help keep me online? Please donate to %s through PayPal.\n\nDonations help " \
              "me to stay on my server and keep running." % dev_email
    bot.send_message(player_tele_id, message)


# Checks for image document
def check_document(bot, update):
    doc = update.message.document
    doc_id = doc.file_id
    doc_mime_type = doc.mime_type
    doc_size = doc.file_size

    if doc_mime_type.startswith("image"):
        if doc_size <= vision_image_size_limit:
            image_name = random_string(20)
            image = bot.get_file(doc_id)
            image.download(image_name)

            is_image_safe(bot, update, image_name, "doc", image_id=doc_id)
        else:
            update.message.reply_text("This document of photo can't be checked as it is too large for me to process.")


# Checks for image
def check_image(bot, update):
    image = update.message.photo[-1]
    image_id = image.file_id
    image_size = image.file_size

    if image_size <= vision_image_size_limit:
        image_name = random_string(20)
        image = bot.get_file(image_id)
        image.download(image_name)

        is_image_safe(bot, update, image_name, "img", image_id=image_id)
    else:
        update.message.reply_text("This photo can't be checked as it is too large for me to process.")


# Checks for url
def check_url(bot, update):
    msg_deleted = False
    large_err = ""
    download_err = ""
    text = update.message.text
    chat_type = update.message.chat.type
    extractor = URLExtract()
    urls = extractor.find_urls(text)

    for url in urls:
        mime_type = mimetypes.guess_type(url)[0]

        if mime_type and mime_type.startswith("image"):
            response = requests.get(url)

            if response.status_code == 200:
                if int(response.headers["content-length"]) <= vision_image_size_limit:
                    image_name = random_string(20)
                    with open(image_name, "wb") as f:
                        f.write(response.content)

                    if not is_image_safe(bot, update, image_name, "url", image_url=url, msg_text=text) and \
                            chat_type in (Chat.GROUP, Chat.SUPERGROUP):
                        msg_deleted = True
                        break
                else:
                    large_err = "Some of the links of photos in this message can't be checked as they are too large " \
                                "for me to process."
            else:
                download_err = "Some of the links of photos in this message can't be checked as I can't retrieve " \
                               "the photos."
        else:
            if not is_url_safe(bot, update, url, text) and chat_type in (Chat.GROUP, Chat.SUPERGROUP):
                msg_deleted = True
                break

    if not msg_deleted and (large_err or download_err):
        err_msg = large_err + " " + download_err
        update.message.reply_text(err_msg)


# Checks if image is safe
def is_image_safe(bot, update, image_name, image_type, image_id=None, image_url=None, msg_text=None):
    safe_image = True
    chat_id = update.message.chat_id
    chat_type = update.message.chat.type
    msg_id = update.message.message_id
    user_name = update.message.from_user.first_name
    client = vision.ImageAnnotatorClient()

    with open(image_name, "rb") as f:
        response = client.safe_search_detection(f)

    os.remove(image_name)
    safe = response.safe_search_annotation
    adult, spoof, medical, violence = safe.adult, safe.spoof, safe.medical, safe.violence

    if adult >= 3 or spoof >= 3 or medical >= 3 or violence >= 3:
        safe_image = False

        if chat_type in (Chat.GROUP, Chat.SUPERGROUP):
            while True:
                try:
                    db = connect_db()
                    break
                except Exception:
                    time.sleep(1)
                    continue

            cur = db.cursor()
            cur.execute("insert into msg_image_info (chat_id, msg_id, user_name, image_id, msg_text) values "
                        "(%s, %s, %s, %s, %s)", (chat_id, msg_id, user_name, image_id, msg_text))
            db.commit()
            db.close()

            if image_type == "doc":
                text = "I deleted a document of photo that's "
            elif image_type == "img":
                text = "I deleted a photo that's "
            else:
                text = "I deleted a message that contains a link of photo that's "

            if adult >= 3:
                text += "{} to contain adult content, ".format(likelihood_name[adult])
            if spoof >= 3:
                text += "{} to contain spoof content, ".format(likelihood_name[spoof])
            if medical >= 3:
                text += "{} to contain medical content, ".format(likelihood_name[medical])
            if violence >= 3:
                text += "{} to contain violence content, ".format(likelihood_name[violence])
            text += "which was sent by {}.".format(user_name)

            keyboard = [[InlineKeyboardButton(text="Undo", callback_data="undo," + str(msg_id))]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            update.message.delete()
            bot.send_message(chat_id, text, reply_markup=reply_markup)
        elif chat_type == Chat.PRIVATE:
            if image_type == "doc":
                text = "Your document of photo is "
            elif image_type == "img":
                text = "Your photo is "
            else:
                text = "Your link of photo (%s) is " % image_url

            if adult >= 3:
                text += "{} to contain adult content, ".format(likelihood_name[adult])
            if spoof >= 3:
                text += "{} to contain spoof content, ".format(likelihood_name[spoof])
            if medical >= 3:
                text += "{} to contain medical content, ".format(likelihood_name[medical])
            if violence >= 3:
                text += "{} to contain violence content, ".format(likelihood_name[violence])
            text = text.rstrip(", ") + "."

            update.message.reply_text(text, quote=True)

    return safe_image


# Checks if url is safe
def is_url_safe(bot, update, url, msg_text):
    safe_url = True
    chat_id = update.message.chat_id
    chat_type = update.message.chat.type
    msg_id = update.message.message_id
    user_name = update.message.from_user.first_name

    headers = {"Content-Type": "application/json"}
    params = {"key": safe_browsing_token}
    json = {"threatInfo": {
        "threatTypes": ["MALWARE", "SOCIAL_ENGINEERING"],
        "platformTypes": ["ANY_PLATFORM"],
        "threatEntryTypes": ["URL"],
        "threatEntries": [{"url": url}]
    }}
    response = requests.post(url=safe_browsing_url, headers=headers, params=params, json=json)

    if response.status_code == 200:
        results = response.json()

        if "matches" in results and results["matches"]:
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
                cur.execute("insert into msg_image_info (chat_id, msg_id, user_name, image_id, msg_text) values "
                            "(%s, %s, %s, %s, %s)", (chat_id, msg_id, user_name, None, msg_text))
                db.commit()
                db.close()

                text = "I deleted a url that contains threats which was sent by {}.".format(user_name)
                keyboard = [[InlineKeyboardButton(text="Undo", callback_data="undo," + str(msg_id))]]
                reply_markup = InlineKeyboardMarkup(keyboard)

                update.message.delete()
                bot.send_message(chat_id, text, reply_markup=reply_markup)
            elif chat_type == Chat.PRIVATE:
                update.message.reply_text("%s\nThis link contains threats. I don't recommend you to click on it." % url,
                                          quote=True)

    return safe_url


def inline_button(bot, update):
    query = update.callback_query
    chat_id = query.message.chat_id
    user_id = query.message.from_user.id
    task, msg_id = query.data.split(",")
    msg_id = int(msg_id)

    if query.message.chat.type in (Chat.GROUP, Chat.SUPERGROUP):
        member = bot.get_chat_member(chat_id, user_id)

        if member.status != ChatMember.ADMINISTRATOR:
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
        cur.execute("select user_name, image_id, msg_text from msg_image_info where chat_id = %s and msg_id = %s",
                    (chat_id, msg_id))
        user_name, image_id, msg_text = cur.fetchone()
        cur.execute("delete from msg_image_info where chat_id = %s and msg_id = %s", (chat_id, msg_id))
        db.commit()
        db.close()

        keyboard = [[InlineKeyboardButton(text="Delete (No Undo)", callback_data="delete," + str(msg_id))]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        query.message.delete()

        if image_id:
            try:
                bot.send_document(chat_id, image_id, caption="%s sent this." % user_name, reply_markup=reply_markup)
            except TelegramError:
                bot.send_photo(chat_id, image_id, caption="%s sent this." % user_name, reply_markup=reply_markup)
        else:
            bot.send_message(chat_id, "%s sent this:\n%s" % (user_name, msg_text), reply_markup=reply_markup)
    elif task == "delete":
        query.message.delete()


# Returns a random string
def random_string(length):
    return "".join(random.choice(string.ascii_letters + string.digits) for _ in range(length))


# Creates a feedback conversation handler
def feedback_cov_handler():
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("feedback", feedback)],

        states={
            0: [MessageHandler(Filters.text, receive_feedback)],
        },

        fallbacks=[CommandHandler("cancel", cancel)],

        allow_reentry=True
    )

    return conv_handler


# Sends a feedback message
@run_async
def feedback(bot, update):
    update.message.reply_text("Please send me your feedback or type /cancel to cancel this operation. My developer "
                              "can understand English and Chinese.")

    return 0


# Saves a feedback
@run_async
def receive_feedback(bot, update):
    feedback_msg = update.message.text
    valid_lang = False
    langdetect.DetectorFactory.seed = 0
    langs = langdetect.detect_langs(feedback_msg)

    for lang in langs:
        if lang.lang in ("en", "zh-tw", "zh-cn"):
            valid_lang = True
            break

    if not valid_lang:
        update.message.reply_text("The feedback you sent is not in English or Chinese. Please try again.")
        return 0

    update.message.reply_text("Thank you for your feedback, I will let my developer know.")

    if is_email_feedback:
        server = smtplib.SMTP(smtp_host)
        server.ehlo()
        server.starttls()
        server.login(dev_email, dev_email_pw)

        text = "Feedback received from %d\n\n%s" % (update.message.from_user.id, update.message.text)
        message = "Subject: %s\n\n%s" % ("Telegram Big Two Bot Feedback", text)
        server.sendmail(dev_email, dev_email, message)
    else:
        logger.info("Feedback received from %d: %s" % (update.message.from_user.id, update.message.text))

    return ConversationHandler.END


# Cancels feedback opteration
@run_async
def cancel(bot, update):
    update.message.reply_text("Operation cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# Sends a message to a specified user
def send(bot, update, args):
    if update.message.from_user.id == dev_tele_id:
        tele_id = int(args[0])
        message = " ".join(args[1:])

        try:
            bot.send_message(tele_id, message)
        except Exception as e:
            logger.exception(e)
            bot.send_message(dev_tele_id, "Failed to send message")


def error(bot, update, error):
    logger.warning("Update '%s' caused error '%s'" % (update, error))


if __name__ == "__main__":
    main()
