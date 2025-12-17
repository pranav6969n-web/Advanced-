import telebot
from telebot import types
import pymongo
import time
import threading
import os
import requests
import random
import string
from flask import Flask

# ==========================================
#              CONFIGURATION
# ==========================================
# Get these from Environment Variables (Recommended for Render)
# Or replace os.environ.get(...) with your actual strings for testing

BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
MONGO_URL = os.environ.get("MONGO_URL", "YOUR_MONGODB_CONNECTION_STRING")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "123456789")) # Your Telegram User ID
FORCE_SUB_CHANNEL = os.environ.get("FORCE_SUB_CHANNEL", "-1001234567890") # Channel ID
FORCE_SUB_URL = os.environ.get("FORCE_SUB_URL", "https://t.me/yourchannel") # Channel Link

# Shortener Config (Optional)
USE_SHORTENER = True # Set False to disable
SHORTENER_API_KEY = os.environ.get("SHORTENER_API", "YOUR_API_KEY")
SHORTENER_DOMAIN = os.environ.get("SHORTENER_DOMAIN", "gplinks.in") # e.g., gplinks.in, adflow.in

# ==========================================
#           DATABASE CONNECTION
# ==========================================
try:
    client = pymongo.MongoClient(MONGO_URL)
    db = client["TelegramMovieBot"]
    users_col = db["users"]
    movies_col = db["movies"]
    settings_col = db["settings"]
    print("✅ Database Connected Successfully")
except Exception as e:
    print(f"❌ Database Connection Failed: {e}")

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# ==========================================
#           HELPER FUNCTIONS
# ==========================================

def get_shortlink(url):
    if not USE_SHORTENER:
        return url
    try:
        # Generic API format for most shorteners (GPlinks, Droplink, etc.)
        api_url = f"https://{SHORTENER_DOMAIN}/api?api={SHORTENER_API_KEY}&url={url}"
        response = requests.get(api_url).json()
        if 'shortenedUrl' in response:
            return response['shortenedUrl']
        else:
            return url
    except:
        return url

def is_subscribed(user_id):
    if FORCE_SUB_CHANNEL == "":
        return True
    try:
        user_status = bot.get_chat_member(FORCE_SUB_CHANNEL, user_id).status
        if user_status in ['creator', 'administrator', 'member']:
            return True
    except:
        # If bot is not admin in channel, this fails. Assume true to avoid blocking.
        return True 
    return False

def save_user(message):
    if not users_col.find_one({"_id": message.from_user.id}):
        users_col.insert_one({
            "_id": message.from_user.id,
            "first_name": message.from_user.first_name,
            "points": 0,
            "referrals": 0,
            "joined_at": time.time()
        })
        # Check for referrer
        args = message.text.split()
        if len(args) > 1 and args[1].startswith("ref_"):
            referrer_id = int(args[1].split("_")[1])
            if referrer_id != message.from_user.id:
                users_col.update_one({"_id": referrer_id}, {"$inc": {"points": 1, "referrals": 1}})
                bot.send_message(referrer_id, f"🎉 You got a new referral! +1 Point.")

# ==========================================
#           BOT COMMAND HANDLERS
# ==========================================

@bot.message_handler(commands=['start'])
def start_command(message):
    save_user(message)
    user_id = message.from_user.id

    # 1. Check Force Subscribe
    if not is_subscribed(user_id):
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔔 Join Channel", url=FORCE_SUB_URL))
        # Pass the original arguments back so they don't lose the movie link
        start_arg = message.text.split()[1] if len(message.text.split()) > 1 else "none"
        markup.add(types.InlineKeyboardButton("✅ I Joined", url=f"https://t.me/{bot.get_me().username}?start={start_arg}"))
        bot.send_message(user_id, "⚠️ **You must join our channel to use this bot.**", parse_mode="Markdown", reply_markup=markup)
        return

    # 2. Check Deep Linking (Movie Download)
    text = message.text.split()
    if len(text) > 1:
        payload = text[1]
        
        # Handling Movie Download
        if payload.startswith("get_"):
            file_unique_id = payload.split("_")[1]
            movie = movies_col.find_one({"unique_id": file_unique_id})
            if movie:
                # Send the file
                bot.send_document(user_id, movie['file_id'], caption=f"🎬 **{movie['name']}**\n\n🤖 via @{bot.get_me().username}")
                # Increment views
                movies_col.update_one({"_id": movie["_id"]}, {"$inc": {"views": 1}})
            else:
                bot.send_message(user_id, "❌ Movie not found or deleted.")
            return

    # 3. Normal Welcome Message
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("🔍 Search Movie", "👤 My Profile")
    markup.add("🔥 Trending", "🎲 Random Movie")
    
    welcome_text = (
        f"👋 Hello **{message.from_user.first_name}**!\n\n"
        "🎬 I am your **Movie File Store Bot**.\n"
        "🔎 Simply send me the **Movie Name** or use the buttons below."
    )
    bot.send_message(user_id, welcome_text, parse_mode="Markdown", reply_markup=markup)

@bot.message_handler(commands=['stats'])
def admin_stats(message):
    if message.from_user.id != ADMIN_ID: return
    
    total_users = users_col.count_documents({})
    total_movies = movies_col.count_documents({})
    bot.reply_to(message, f"📊 **Bot Statistics**\n\n👥 Users: {total_users}\n🎥 Movies: {total_movies}")

@bot.message_handler(commands=['broadcast'])
def broadcast(message):
    if message.from_user.id != ADMIN_ID: return
    
    msg = bot.reply_to(message, "Send the message/file you want to broadcast (Reply with /cancel to stop).")
    bot.register_next_step_handler(msg, process_broadcast)

def process_broadcast(message):
    if message.text == "/cancel":
        bot.send_message(message.chat.id, "Broadcast cancelled.")
        return

    users = users_col.find({}, {"_id": 1})
    success = 0
    failed = 0
    
    status_msg = bot.send_message(message.chat.id, "🚀 Broadcast started...")
    
    for user in users:
        try:
            bot.copy_message(user["_id"], message.chat.id, message.message_id)
            success += 1
        except:
            failed += 1
            
    bot.edit_message_text(f"✅ Broadcast Complete\nSuccessful: {success}\nFailed: {failed}", message.chat.id, status_msg.message_id)

# ==========================================
#           MOVIE UPLOAD (ADMIN)
# ==========================================

@bot.message_handler(content_types=['document', 'video'])
def handle_file_upload(message):
    # Only Admin can upload files to DB
    if message.from_user.id != ADMIN_ID:
        return # Ignore user files

    file_id = message.document.file_id if message.document else message.video.file_id
    file_name = message.document.file_name if message.document else message.video.file_name or "Unknown Video"
    
    # Generate a short unique ID for the database
    unique_id = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
    
    # Clean file name
    clean_name = file_name.replace("_", " ").replace(".", " ")
    
    movie_data = {
        "file_id": file_id,
        "unique_id": unique_id,
        "name": clean_name,
        "caption": message.caption or "",
        "views": 0,
        "added_at": time.time()
    }
    
    movies_col.insert_one(movie_data)
    bot.reply_to(message, f"✅ **Movie Saved!**\n\n📂 Name: {clean_name}\n🆔 ID: `{unique_id}`")

# ==========================================
#           SEARCH & RESULTS SYSTEM
# ==========================================

@bot.message_handler(func=lambda message: True)
def handle_search(message):
    query = message.text
    
    if query == "🔍 Search Movie":
        bot.send_message(message.chat.id, "👇 Type the movie name you want to search:")
        return
    elif query == "👤 My Profile":
        user = users_col.find_one({"_id": message.from_user.id})
        ref_link = f"https://t.me/{bot.get_me().username}?start=ref_{message.from_user.id}"
        msg = f"👤 **User Profile**\n\n💰 Points: {user.get('points', 0)}\n👥 Referrals: {user.get('referrals', 0)}\n🔗 **Your Referral Link:**\n`{ref_link}`"
        bot.send_message(message.chat.id, msg, parse_mode="Markdown")
        return
    elif query == "🔥 Trending":
        # Get top 5 most viewed movies
        results = movies_col.find().sort("views", -1).limit(5)
    elif query == "🎲 Random Movie":
         results = list(movies_col.aggregate([{"$sample": {"size": 1}}]))
    else:
        # Regex search (case insensitive)
        results = list(movies_col.find({"name": {"$regex": query, "$options": "i"}}).limit(10))

    if not results:
        bot.reply_to(message, "❌ No movies found. Try checking the spelling.")
        return

    keyboard = types.InlineKeyboardMarkup()
    for movie in results:
        # Create a deep link to the file
        deep_link = f"https://t.me/{bot.get_me().username}?start=get_{movie['unique_id']}"
        
        if USE_SHORTENER:
            final_link = get_shortlink(deep_link)
            btn_text = f"🔓 Unlock: {movie['name'][:20]}..."
        else:
            final_link = deep_link
            btn_text = f"⬇️ {movie['name'][:25]}"
            
        keyboard.add(types.InlineKeyboardButton(btn_text, url=final_link))

    bot.send_message(message.chat.id, f"🔎 Results for: **{query}**", parse_mode="Markdown", reply_markup=keyboard)


# ==========================================
#           WEB SERVER (RENDER)
# ==========================================

@app.route('/')
def home():
    return "Bot is running!"

def run_flask():
    # Render assigns a port in env, default to 5000 if not found
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

def run_bot():
    print("🤖 Bot started polling...")
    bot.infinity_polling()

if __name__ == "__main__":
    # Start Flask in a separate thread to keep Render happy
    t1 = threading.Thread(target=run_flask)
    t1.start()
    
    # Start Bot in main thread
    run_bot()
