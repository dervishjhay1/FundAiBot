# FundAiBot 🤖

> **Your Intelligent AI Assistant** — A premium AI platform inside Telegram, powered by GPT-4, Gemini, and Stable Diffusion.

---

## ✨ Features

| Category | Features |
|---|---|
| **AI Chat** | Multi-provider (OpenRouter→Gemini→HuggingFace), 8 personalities, conversation memory |
| **Image Gen** | Stable Diffusion XL, 6 art styles, prompt enhancement |
| **Users** | Profiles, daily credits, usage stats, settings, notifications |
| **Referrals** | Deep-link referral codes, automatic bonus credit rewards |
| **VIP System** | 3 tiers (Basic/Pro/Elite), credit multipliers (structure ready) |
| **Admin Panel** | Full dashboard, user management, VIP/credit/ban control, broadcast, analytics |
| **Database** | Supabase (PostgreSQL) — users, credits, conversations, image history, referrals, error logs |
| **Infrastructure** | Flask keep-alive, Railway /health endpoint, rate limiting, queue system, global error handler |

---

## 🗂️ Project Structure

```
FundAiBot/
├── main.py                    ← Entry point
├── requirements.txt
├── Procfile                   ← Railway/Heroku process file
├── runtime.txt                ← Python 3.11.9
├── supabase_schema.sql        ← Run once in Supabase SQL Editor
├── .env.example
├── .gitignore
├── README.md
│
├── config/
│   └── settings.py            ← All environment variables & constants
│
├── handlers/                  ← One file per feature area
│   ├── start.py               ← /start + referral deep-links
│   ├── help.py                ← /help, /about
│   ├── chat.py                ← AI chat + /clear
│   ├── image.py               ← /image + style flow
│   ├── profile.py             ← /profile, /stats, /referral, /history
│   ├── style.py               ← /style
│   ├── admin.py               ← All /admin_* commands
│   └── callbacks.py           ← All inline keyboard routing
│
├── services/                  ← Business logic & external APIs
│   ├── ai_service.py          ← OpenRouter → Gemini → HuggingFace fallback
│   ├── image_service.py       ← Stable Diffusion XL via HuggingFace
│   ├── database.py            ← All Supabase operations
│   ├── queue_manager.py       ← Async task queue with worker pool
│   └── keepalive.py           ← Flask server + /health endpoint
│
├── utils/
│   ├── logger.py              ← Rotating file + console logger
│   ├── keyboards.py           ← All InlineKeyboardMarkup factories
│   ├── helpers.py             ← Text utils, progress bars, formatting
│   └── rate_limiter.py        ← Sliding-window spam protection
│
├── assets/                    ← Static files
├── data/                      ← Local fallback data (gitignored)
└── logs/                      ← Log files (gitignored)
```

---

## 🚀 Quick Start (Local)

### 1. Clone

```bash
git clone https://github.com/YOUR_USERNAME/FundAiBot.git
cd FundAiBot
```

### 2. Python virtual environment

```bash
python -m venv venv
source venv/bin/activate    # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Environment variables

```bash
cp .env.example .env
# Edit .env with your real keys
```

### 4. Set up Supabase

1. Create a free project at [supabase.com](https://supabase.com)
2. Go to **SQL Editor** → **New Query**
3. Paste the contents of `supabase_schema.sql` and click **Run**
4. Copy your **Project URL** and **service_role key** from Settings → API

### 5. Run

```bash
python main.py
```

---

## ☁️ Deploy to Railway (GitHub Auto-Deploy)

### Step 1 — Push to GitHub

```bash
git init
git add .
git commit -m "Initial FundAiBot setup"
git remote add origin https://github.com/YOUR_USERNAME/FundAiBot.git
git branch -M main
git push -u origin main
```

> ⚠️ Never commit `.env`. It is in `.gitignore`.

### Step 2 — Connect to Railway

1. Go to [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo**
2. Select your `FundAiBot` repository
3. Railway auto-detects Python and uses the `Procfile`

### Step 3 — Set Environment Variables in Railway

Go to your service → **Variables** tab → Add all variables:

| Variable | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | From @BotFather |
| `ADMIN_USER_ID` | Your Telegram numeric ID |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_SERVICE_KEY` | Supabase service_role key |
| `OPENROUTER_API_KEY` | openrouter.ai API key |
| `GEMINI_API_KEY` | Google AI Studio key |
| `HUGGINGFACE_API_KEY` | HuggingFace token |

### Step 4 — Railway Health Check

Railway will automatically ping `/health`. The Flask server returns:
```json
{"status": "ok"}
```

### Step 5 — GitHub Auto-Deploy

Every `git push` to `main` triggers an automatic redeploy on Railway.

---

## 🤖 Bot Commands

### User Commands

| Command | Description |
|---|---|
| `/start` | Main menu |
| `/help` | Full help guide |
| `/about` | About FundAiBot |
| `/chat` | AI conversation |
| `/image <prompt>` | Generate an AI image |
| `/style` | Change AI personality (8 modes) |
| `/profile` | Your profile & credits |
| `/stats` | Usage statistics |
| `/referral` | Referral link & rewards |
| `/history` | Image generation history |
| `/clear` | Clear conversation memory |

### Admin Commands (ADMIN_USER_ID only)

| Command | Description |
|---|---|
| `/admin` | Dashboard with live stats |
| `/admin_users` | List latest 20 users |
| `/admin_user <id>` | Full user info |
| `/admin_ban <id> [reason]` | Ban a user |
| `/admin_unban <id>` | Unban a user |
| `/admin_setvip <id> <basic\|pro\|elite\|none>` | Set VIP tier |
| `/admin_addcredits <id> <chat\|image> <n>` | Add bonus credits |
| `/admin_broadcast <message>` | Broadcast to all users |
| `/admin_stats` | Platform analytics |
| `/admin_logs` | Recent error logs |
| `/admin_images` | Recent image generations |

---

## 🧠 AI Providers — Fallback Chain

```
Request → OpenRouter (GPT-4/Claude/Mixtral)
              ↓ (fail)
          Gemini Pro
              ↓ (fail)
          HuggingFace Mistral-7B
              ↓ (fail)
          User-friendly error message
```

---

## 💳 Credit System

| Event | Change |
|---|---|
| New user signup | 30 chat + 5 image credits/day (free tier) |
| Successful referral | Referrer earns +10 chat +2 image bonus |
| VIP Basic | 500 chat + 50 image credits/day |
| VIP Pro | Unlimited chat + priority queue |
| VIP Elite | Everything + custom AI persona |

Credits reset daily at midnight UTC.

---

## 🛡️ Security

- All admin commands gated by `ADMIN_USER_ID` from environment variables
- Rate limiting (5 messages/minute sliding window)
- Input sanitisation on all prompts
- No secrets ever logged
- `.env` excluded from git via `.gitignore`

---

## 🔧 Extending FundAiBot

**Add a new AI model:** Edit `services/ai_service.py` — add a new provider function and include it in `get_ai_response()`'s providers list.

**Add a new command:** Create a handler in `handlers/`, register in `main.py` with `app.add_handler(CommandHandler(...))`.

**Add payment (Stripe/Crypto):** Wire into `handlers/callbacks.py` `vip:` callbacks and `utils/keyboards.py` VIP buttons.

**Switch to webhooks:** Replace `app.run_polling()` in `main.py` with `app.run_webhook()` and configure the Railway URL.

---

## 📄 License

MIT — free to use, fork, and build on.
