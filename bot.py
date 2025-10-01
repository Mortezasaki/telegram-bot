# bot.py
import os
import requests
import logging
from pathlib import Path
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import PyPDF2
import docx
import time

# ---------- Logging ----------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- متغیرهای محیطی ----------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

if not TELEGRAM_TOKEN or not OPENROUTER_API_KEY:
    logger.error("لطفاً متغیرهای محیطی TELEGRAM_TOKEN و OPENROUTER_API_KEY را تنظیم کنید.")
    raise SystemExit("Missing environment variables")

# ---------- مسیر ذخیره موقت فایل‌ها ----------
DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

# ---------- حافظه برای هر چت ----------
conversations = {}   # chat_id -> [ {"role": "user"/"assistant", "content": "..."} ]
documents_text = {}  # chat_id -> "متن همه اسناد آپلود شده"

# ---------- توابع کمکی ----------
def read_pdf(path: Path) -> str:
    text = ""
    try:
        with open(path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                p = page.extract_text()
                if p:
                    text += p + "\n"
    except Exception as e:
        logger.exception(f"خطا در خواندن PDF: {path}")
    return text

def read_docx(path: Path) -> str:
    text = ""
    try:
        doc = docx.Document(path)
        for para in doc.paragraphs:
            text += para.text + "\n"
    except Exception as e:
        logger.exception(f"خطا در خواندن DOCX: {path}")
    return text

def call_openrouter(messages, retries=2):
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "openai/gpt-3.5-turbo",
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 600
    }
    for attempt in range(retries + 1):
        try:
            r = requests.post(url, headers=headers, json=data, timeout=30)
            r.raise_for_status()
            j = r.json()
            return j["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning(f"OpenRouter attempt {attempt+1} failed: {e}")
            time.sleep(2)
    return "⚠️ خطا در ارتباط با سرویس هوش مصنوعی. لطفاً بعداً تلاش کنید."

# ---------- هندلرها ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "سلام! من ربات هوش مصنوعی هستم. لطفاً فایل PDF یا Word بفرست یا سوالت را تایپ کن."
    )

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    doc = update.message.document
    file_name = doc.file_name or f"{doc.file_id}"
    local_path = DOWNLOAD_DIR / f"{chat_id}_{file_name}"

    try:
        file_obj = await doc.get_file()
        await file_obj.download_to_drive(str(local_path))
    except Exception as e:
        logger.exception("خطا در دانلود فایل")
        await update.message.reply_text("⚠️ خطا در دانلود فایل. لطفاً دوباره امتحان کنید.")
        return

    text = ""
    if file_name.lower().endswith(".pdf"):
        text = read_pdf(local_path)
    elif file_name.lower().endswith(".docx"):
        text = read_docx(local_path)
    else:
        await update.message.reply_text("❌ فقط فایل‌های PDF یا DOCX پشتیبانی می‌شوند.")
        local_path.unlink(missing_ok=True)
        return

    # ذخیره متن سند در حافظه
    documents_text.setdefault(chat_id, "")
    documents_text[chat_id] += "\n\n" + text

    # پاکسازی فایل محلی
    local_path.unlink(missing_ok=True)

    await update.message.reply_text(f"✅ فایل «{file_name}» ذخیره شد. حالا می‌توانی سوال بپرسی و من جواب می‌دهم.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_text = update.message.text

    # مقداردهی اولیه حافظه مکالمه
    conversations.setdefault(chat_id, [])
    documents_text.setdefault(chat_id, "")

    # محدود کردن حافظه گفتگو به آخرین ۲۰ پیام
    conversations[chat_id] = conversations[chat_id][-20:]
    conversations[chat_id].append({"role": "user", "content": user_text})

    # پیام سیستم
    system_msg = {
        "role": "system",
        "content": "شما یک دستیار فارسی مودب و دقیق هستید. "
                   "اگر پاسخ دقیق از اسناد موجود شد، منبع (فایل) را ذکر کنید. "
                   "اگر سند مرتبط نبود، صادقانه بگویید اطلاعات کافی نیست."
    }

    messages = [system_msg]

    # اضافه کردن کانتکست اسناد (حداکثر 4000 کاراکتر)
    if documents_text.get(chat_id):
        messages.append({"role": "system", "content": "Context:\n" + documents_text[chat_id][:4000]})

    # اضافه کردن ۶ پیام آخر گفتگو
    messages.extend(conversations[chat_id][-6:])

    # فراخوانی سرویس هوش مصنوعی
    reply = call_openrouter(messages)

    # ذخیره پاسخ در حافظه و ارسال به کاربر
    conversations[chat_id].append({"role": "assistant", "content": reply})
    await update.message.reply_text(reply)

# ---------- Main ----------
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info("🤖 ربات روشن شد (Polling)")
    app.run_polling()

if __name__ == "__main__":
    main()
