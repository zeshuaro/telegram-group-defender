import os
import re

from dotenv import load_dotenv
from telegram import LabeledPrice, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ConversationHandler, MessageHandler, CommandHandler, Filters
from telegram.ext.dispatcher import run_async

from group_defender.constants import PAYMENT_THANKS, PAYMENT_COFFEE, PAYMENT_BEER, PAYMENT_MEAL, PAYMENT_CUSTOM, \
    PAYMENT_DICT, PAYMENT_PAYLOAD, PAYMENT_PARA, PAYMENT_CURRENCY, WAIT_PAYMENT
from group_defender.utils import cancel, get_settings

load_dotenv()
STRIPE_TOKEN = os.environ.get('STRIPE_TOKEN', os.environ.get('STRIPE_TOKEN_BETA'))
BOT_NAME = 'Group Defender'

if STRIPE_TOKEN is None:
    STRIPE_TOKEN = get_settings('STRIPE_TOKEN')


def payment_cov_handler():
    """
    Create a payment conversation handler object
    Returns:
        The conversation handler object
    """
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(Filters.regex(rf'^{re.escape(PAYMENT_CUSTOM)}$'), custom_amount)],
        states={
            WAIT_PAYMENT: [MessageHandler(Filters.text, receive_custom_amount)]
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        allow_reentry=True
    )

    return conv_handler


@run_async
def custom_amount(update, _):
    update.message.reply_text(f'Send me the amount that you\'ll like to support {BOT_NAME} or /cancel this.',
                              reply_markup=ReplyKeyboardRemove())

    return WAIT_PAYMENT


@run_async
def receive_custom_amount(update, context):
    try:
        amount = round(float(update.message.text))
        if amount <= 0:
            raise ValueError
    except ValueError:
        update.message.reply_text('The amount you sent is invalid, try again.')

        return WAIT_PAYMENT

    return send_payment_invoice(update, context, amount)


@run_async
def send_payment_options(update, context, user_id=None):
    keyboard = [[PAYMENT_THANKS, PAYMENT_COFFEE, PAYMENT_BEER], [PAYMENT_MEAL, PAYMENT_CUSTOM]]
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
    text = f'Select how you want to support {BOT_NAME}'

    if user_id is None:
        update.message.reply_text(text, reply_markup=reply_markup)
    else:
        context.bot.send_message(user_id, text, reply_markup=reply_markup)


@run_async
def send_payment_invoice(update, context, amount=None):
    chat_id = update.message.chat_id
    title = f'Support {BOT_NAME}'
    description = f'Say thanks to {BOT_NAME} and help keep it running'
    payload = PAYMENT_PAYLOAD
    provider_token = STRIPE_TOKEN
    start_parameter = PAYMENT_PARA
    currency = PAYMENT_CURRENCY

    if amount is None:
        label = update.message.text
        price = PAYMENT_DICT[update.message.text]
    else:
        label = PAYMENT_CUSTOM
        price = amount

    prices = [LabeledPrice(re.sub(r'\s\(.*', '', label), price * 100)]

    context.bot.send_invoice(chat_id, title, description, payload, provider_token, start_parameter, currency, prices)


@run_async
def precheckout_check(update, _):
    query = update.pre_checkout_query
    if query.invoice_payload != PAYMENT_PAYLOAD:
        query.answer(ok=False, error_message="Something went wrong")
    else:
        query.answer(ok=True)


def successful_payment(update, _):
    update.message.reply_text('Thank you for your support!', reply_markup=ReplyKeyboardRemove())
