# Telegram Group Guardian

Telegram Bot that provides guards your group

Connect to [Bot](https://t.me/groupguardianbot)

Stay tuned for updates and new releases on the [Telegram Channel](https://t.me/groupguardianbotdev)

## Getting Started

These instructions will get you a copy of the project up and running on your local machine for development and 
testing purposes

### Prerequisites

Run the following command to install the required libraries:

```
pip install -r requirements.txt
```

Below is a list of the main libraries that are included:

* [Python Telegram Bot](https://github.com/python-telegram-bot/python-telegram-bot)
* [Google Cloud Vision](https://github.com/GoogleCloudPlatform/google-cloud-python/tree/master/vision)

You will also need postgres to be set up and running.

The bot uses [Google Cloud Vision](https://cloud.google.com/vision/) to check for inappropriate content in images, 
[Google Safe Browsing](https://developers.google.com/safe-browsing/) to check for threats in links, and
 [Attachment Scanner](http://www.attachmentscanner.com/) to scan for virus and malware.

You should also download a service account JSON keyfile from Google Cloud Console and point to it using an 
environment variable.

```angular2html
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/keyfile.json"
```

Make a `.env` file and put your telegram token in there. 

You will also need to include the tokens of Google Safe Browsing and Attachment Scanner, and your database settings.



If you want to use the webhook method to run the bot, also include `APP_URL` and `PORT` in the `.env` file. If you 
want to use polling instead, do not include `APP_URL` in your `.env` file.

Below is an example:

```
TELEGRAM_TOKEN=<telegram_token>
DB_NAME=<database_name
DB_USER=<database_username>
DB_PW=<database_password>
DB_HOST=<database_host>
DB_PORT=<database_port>
SAFE_BROWSING_TOKEN=<safe_browsing_token>
SCANNER_TOKEN=<scanner_token>
```