import os
import json
import asyncio
import logging
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)
from patchright.async_api import async_playwright
import config
import main

# Setup logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Env vars
TOKEN = os.getenv("TELEGRAM_TOKEN")
AUTH_DIR = os.getenv("AUTH_DIR", "/app/auth/users")
LOGIN_SCRIPT_URL = "https://github.com/koboshchan/patchrightKahootBot/blob/main/login.py"


def user_auth_path(user_id):
    return os.path.join(AUTH_DIR, f"{int(user_id)}.json")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "").lower().replace("@", "")

# Admin management
admins = {ADMIN_USERNAME} if ADMIN_USERNAME else set()

# Session storage
# { session_id: { "browser": browser, "playwright": p, "game_name": str, "pin": str, "num_clients": int, "clients": list, "chat_id": int, "message_id": int, "last_update": float, "update_pending": bool } }
sessions = {}

# States
SELECTING_GAME, ENTERING_CUSTOM_KWARGS, ENTERING_PIN, ENTERING_CLIENTS, ENTERING_SCREEN_CLIENT, UPLOADING_SESSION = range(6)


class ClientBrowser:
    """Wraps the shared browser so we know which pages each client opened."""

    def __init__(self, browser):
        self._browser = browser
        self.pages = []

    async def new_page(self, *args, **kwargs):
        page = await self._browser.new_page(*args, **kwargs)
        self.pages.append(page)
        return page

    def __getattr__(self, name):
        return getattr(self._browser, name)


def is_admin(username):
    if not username:
        return False
    return username.lower().replace("@", "") in admins


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Start the global updater loop if not already running
    if not context.application.bot_data.get("updater_running"):
        context.application.bot_data["updater_running"] = True
        asyncio.create_task(status_updater_loop(context))

    user = update.effective_user
    if not is_admin(user.username):
        await update.message.reply_text("You are not authorized to use this bot.")
        return ConversationHandler.END

    keyboard = []
    for game_name in config.supported_games.keys():
        keyboard.append(
            [InlineKeyboardButton(game_name.capitalize(), callback_data=game_name)]
        )

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Please select a game:", reply_markup=reply_markup)
    return SELECTING_GAME


async def game_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    game_name = query.data
    context.user_data["game_choice"] = game_name
    game_class = config.supported_games[game_name]

    if game_class.use_custom_run_client and game_class.custom_run_client_custom_kargs:
        kargs_list = game_class.custom_run_client_custom_kargs
        context.user_data["custom_kwargs_queue"] = list(kargs_list)
        context.user_data["custom_kwargs"] = {}

        first_karg = kargs_list[0]
        await query.edit_message_text(
            text=f"Selected {game_name.capitalize()}. {first_karg.get('prompt', 'Enter value:')}"
        )
        return ENTERING_CUSTOM_KWARGS
    elif game_class.use_custom_run_client:
        await query.edit_message_text(
            text=f"Selected {game_name.capitalize()}. Please enter the number of clients:"
        )
        return ENTERING_CLIENTS
    else:
        await query.edit_message_text(
            text=f"Selected {game_name.capitalize()}. Please enter the game PIN:"
        )
        return ENTERING_PIN


async def custom_kwarg_entered(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kwarg = context.user_data["custom_kwargs_queue"].pop(0)
    key = kwarg.get("key", "value")
    context.user_data["custom_kwargs"][key] = update.message.text

    if context.user_data["custom_kwargs_queue"]:
        next_karg = context.user_data["custom_kwargs_queue"][0]
        await update.message.reply_text(text=next_karg.get("prompt", "Enter value:"))
        return ENTERING_CUSTOM_KWARGS
    else:
        await update.message.reply_text("Please enter the number of clients:")
        return ENTERING_CLIENTS


async def pin_entered(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["pin"] = update.message.text
    await update.message.reply_text("Please enter the number of clients:")
    return ENTERING_CLIENTS


async def clients_entered(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        num_clients = int(update.message.text)
    except ValueError:
        await update.message.reply_text("Please enter a valid number.")
        return ENTERING_CLIENTS

    game_choice = context.user_data["game_choice"]
    game_class = config.supported_games[game_choice]
    pin = context.user_data.get("pin", "")
    custom_kwargs = context.user_data.get("custom_kwargs", {})

    session_id = f"{update.effective_chat.id}_{int(time.time())}"
    await update.message.reply_text(
        f"Launching {num_clients} clients for {game_choice.capitalize()}..."
    )

    asyncio.create_task(
        run_session(
            session_id,
            game_choice,
            pin,
            num_clients,
            update.effective_chat.id,
            context,
            custom_kwargs,
            update.effective_user.id,
        )
    )

    return ConversationHandler.END


async def run_session(
    session_id, game_name, pin, num_clients, chat_id, context, custom_kwargs=None, user_id=None
):
    if custom_kwargs is None:
        custom_kwargs = {}
    game_class = config.supported_games[game_name]

    try:
        p = await async_playwright().start()
        browser = await p.chromium.launch(
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disk-cache-dir=/tmp/chrome-cache",
                "--use-fake-ui-for-media-stream",
                "--allow-http-screen-capture",
                "--enable-usermedia-screen-capturing",
                "--auto-select-desktop-capture-source=Entire screen",
            ]
        )

        # Load this user's saved logins (uploaded via /setlogin) if present
        auth_path = user_auth_path(user_id) if user_id is not None else None
        if auth_path and os.path.exists(auth_path):
            browser_ctx = await browser.new_context(storage_state=auth_path)
        else:
            browser_ctx = browser

        session = {
            "playwright": p,
            "browser": browser,
            "browser_ctx": browser_ctx,
            "game_name": game_name,
            "pin": pin,
            "num_clients": num_clients,
            "custom_kwargs": custom_kwargs,
            "clients": [
                {"id": i, "status": "Initializing"} for i in range(num_clients)
            ],
            "start_time": time.time(),
            "chat_id": chat_id,
            "message_id": None,
            "last_update": 0,
            "update_pending": False,
        }
        sessions[session_id] = session

        # Initial status message
        status_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=get_status_text(session_id),
            reply_markup=get_status_markup(session_id),
        )
        session["message_id"] = status_msg.message_id

        async def run_one_client(client_idx):
            session["clients"][client_idx]["status"] = "Joining"
            client_browser = ClientBrowser(browser_ctx)
            session["clients"][client_idx]["browser"] = client_browser
            await update_status_message(session_id, context)

            screenshot_path = f"/tmp/error_screenshot_{session_id}.png" if client_idx == 0 else None
            try:
                if game_class.use_custom_run_client:
                    await game_class.run_client(pin, client_browser, **custom_kwargs)
                else:
                    await main.run_client(pin, client_browser, game_class, screenshot_path=screenshot_path)
                session["clients"][client_idx]["status"] = "Joined ✅"
            except Exception as e:
                session["clients"][client_idx]["status"] = "Error ❌"
                logger.error(f"Client {client_idx} failed: {e}")
                if client_idx == 0 and screenshot_path:
                    import os
                    if os.path.exists(screenshot_path):
                        try:
                            with open(screenshot_path, "rb") as f:
                                await context.bot.send_photo(
                                    chat_id=chat_id,
                                    photo=f,
                                    caption=f"❌ Client 0 error:\n<code>{str(e)[:900]}</code>",
                                    parse_mode="HTML",
                                )
                        except Exception as send_err:
                            logger.error(f"Failed to send error screenshot: {send_err}")
                        finally:
                            os.remove(screenshot_path)

            await update_status_message(session_id, context)

        # Launch clients concurrently
        for i in range(num_clients):
            asyncio.create_task(run_one_client(i))

        # Auto-close task in 10 minutes
        asyncio.create_task(auto_close_session(session_id, 600, context))

    except Exception as e:
        logger.error(f"Failed to start session: {e}")
        await context.bot.send_message(
            chat_id=chat_id, text=f"Failed to start session: {e}"
        )


async def auto_close_session(session_id, delay, context):
    await asyncio.sleep(delay)
    if session_id in sessions:
        await close_session(session_id, context)


async def close_session(session_id, context):
    if session_id in sessions:
        session = sessions.pop(session_id)
        try:
            await session["browser"].close()
            await session["playwright"].stop()
        except Exception as e:
            logger.error(f"Error closing browser/playwright: {e}")

        try:
            await context.bot.edit_message_text(
                chat_id=session["chat_id"],
                message_id=session["message_id"],
                text=f"Session {session_id} has been closed (Auto-close or manual).",
            )
        except Exception as e:
            logger.error(f"Error updating status message on close: {e}")


def get_status_text(session_id):
    if session_id not in sessions:
        return "Session not found or already closed."
    session = sessions[session_id]
    lines = [
        f"🎮 Game: {session['game_name'].capitalize()}",
        f"👥 Clients: {session['num_clients']}",
    ]
    if session.get("pin"):
        lines.append(f"🔢 PIN: {session['pin']}")
    if session.get("custom_kwargs"):
        for key, value in session["custom_kwargs"].items():
            lines.append(f"📝 {key}: {value}")
    lines.append("----------------")
    for client in session["clients"]:
        lines.append(f"Client {client['id'] + 1}: {client['status']}")
    return "\n".join(lines)


def get_status_markup(session_id):
    keyboard = [
        [
            InlineKeyboardButton(
                "📸 Send Screen", callback_data=f"screen_{session_id}"
            )
        ],
        [
            InlineKeyboardButton(
                "🛑 Close All Clients", callback_data=f"close_{session_id}"
            )
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


async def update_status_message(session_id, context):
    if session_id not in sessions:
        return
    session = sessions[session_id]

    current_time = time.time()
    # If we are within the 750ms window since the last update
    if current_time - session["last_update"] < 0.75:
        # Just mark that there is a newer state to be sent
        session["update_pending"] = True
        return

    # Otherwise, update immediately
    await _perform_update(session_id, context)


async def _perform_update(session_id, context):
    if session_id not in sessions:
        return
    session = sessions[session_id]

    session["last_update"] = time.time()
    session["update_pending"] = False

    try:
        await context.bot.edit_message_text(
            chat_id=session["chat_id"],
            message_id=session["message_id"],
            text=get_status_text(session_id),
            reply_markup=get_status_markup(session_id),
        )
    except Exception as e:
        if "Message is not modified" not in str(e):
            logger.debug(f"Update status message error: {e}")


async def status_updater_loop(context):
    """Global loop to check all sessions for pending updates every 750ms."""
    while True:
        await asyncio.sleep(0.75)
        for session_id in list(sessions.keys()):
            session = sessions.get(session_id)
            if session and session.get("update_pending"):
                await _perform_update(session_id, context)


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.startswith("close_"):
        session_id = query.data.split("_", 1)[1]
        await close_session(session_id, context)


async def screen_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.username):
        return ConversationHandler.END
    session_id = query.data.split("_", 1)[1]
    if session_id not in sessions:
        await context.bot.send_message(
            chat_id=update.effective_chat.id, text="Session not found or already closed."
        )
        return ConversationHandler.END
    context.user_data["screen_session"] = session_id
    n = sessions[session_id]["num_clients"]
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"Enter the client ID (1-{n}):",
    )
    return ENTERING_SCREEN_CLIENT


async def screen_client_entered(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session_id = context.user_data.get("screen_session")
    session = sessions.get(session_id)
    if not session:
        await update.message.reply_text("Session not found or already closed.")
        return ConversationHandler.END
    try:
        client_num = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("Please enter a valid number.")
        return ENTERING_SCREEN_CLIENT
    if not 1 <= client_num <= session["num_clients"]:
        await update.message.reply_text(
            f"Client ID must be between 1 and {session['num_clients']}."
        )
        return ENTERING_SCREEN_CLIENT

    client = session["clients"][client_num - 1]
    client_browser = client.get("browser")
    pages = [pg for pg in (client_browser.pages if client_browser else []) if not pg.is_closed()]
    if not pages:
        await update.message.reply_text(f"Client {client_num} has no open page.")
        return ConversationHandler.END
    try:
        png = await pages[-1].screenshot()
        await update.message.reply_photo(
            photo=png,
            caption=f"📸 Client {client_num}: {client['status']}",
        )
    except Exception as e:
        logger.error(f"Screenshot failed for client {client_num}: {e}")
        await update.message.reply_text(f"Screenshot failed: {e}")
    return ConversationHandler.END


async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.username.lower().replace("@", "") != ADMIN_USERNAME:
        await update.message.reply_text("Only the main admin can add other admins.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /addadmin @username")
        return

    new_admin = context.args[0].lower().replace("@", "")
    admins.add(new_admin)
    await update.message.reply_text(f"Admin @{new_admin} added.")


async def set_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.username):
        await update.message.reply_text("You are not authorized to use this bot.")
        return ConversationHandler.END
    await update.message.reply_text(
        "Send your session.json file (or /cancel).\n\n"
        f"Make one with this script on your computer:\n{LOGIN_SCRIPT_URL}\n"
        "Run it, log into your sites, press Enter, then upload the file here."
    )
    return UPLOADING_SESSION


async def session_uploaded(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if doc.file_size and doc.file_size > 5 * 1024 * 1024:
        await update.message.reply_text("File too big (max 5 MB).")
        return UPLOADING_SESSION
    data = await (await doc.get_file()).download_as_bytearray()
    try:
        state = json.loads(bytes(data))
        assert isinstance(state, dict) and isinstance(state.get("cookies"), list)
    except Exception:
        await update.message.reply_text("That isn't a valid session.json. Try again or /cancel.")
        return UPLOADING_SESSION
    os.makedirs(AUTH_DIR, exist_ok=True)
    path = user_auth_path(update.effective_user.id)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    try:
        await update.message.delete()  # don't leave cookies sitting in the chat
    except Exception:
        pass
    await update.message.reply_text(
        f"Saved ({len(state['cookies'])} cookies). Your next sessions will be logged in. /clearlogin to remove."
    )
    return ConversationHandler.END


async def session_not_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Please send the session.json as a file, or /cancel.")
    return UPLOADING_SESSION


async def clear_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    path = user_auth_path(update.effective_user.id)
    if os.path.exists(path):
        os.remove(path)
        await update.message.reply_text("Saved login removed.")
    else:
        await update.message.reply_text("You don't have a saved login.")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Operation cancelled.")
    return ConversationHandler.END


def main_bot():
    if not TOKEN:
        print("Error: TELEGRAM_TOKEN environment variable not set.")
        return

    application = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SELECTING_GAME: [
                CallbackQueryHandler(game_selected, pattern="^(?!close_|screen_)")
            ],
            ENTERING_CUSTOM_KWARGS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, custom_kwarg_entered)
            ],
            ENTERING_PIN: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, pin_entered)
            ],
            ENTERING_CLIENTS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, clients_entered)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    screen_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(screen_button, pattern="^screen_")],
        states={
            ENTERING_SCREEN_CLIENT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, screen_client_entered)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    login_handler = ConversationHandler(
        entry_points=[CommandHandler("setlogin", set_login)],
        states={
            UPLOADING_SESSION: [
                MessageHandler(filters.Document.ALL, session_uploaded),
                MessageHandler(filters.ALL & ~filters.COMMAND, session_not_file),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(login_handler)
    application.add_handler(CommandHandler("clearlogin", clear_login))
    application.add_handler(screen_handler)
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("addadmin", add_admin))
    application.add_handler(CallbackQueryHandler(button_handler, pattern="^close_"))

    print("Bot is starting...")
    application.run_polling()


if __name__ == "__main__":
    main_bot()
