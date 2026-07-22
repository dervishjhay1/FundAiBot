# FundzAiBot

**An AI Assistant developed by Fundz Company Ltd.**

> FundzAiBot serves users. It does not govern.  
> Executive authority belongs to **Fundz Company Headquarters**.

---

## What FundzAiBot Does

FundzAiBot is a Telegram-based AI assistant that provides:

| Capability | Commands |
|-----------|----------|
| AI Conversations | Just send a message |
| Quick Q&A | `/ask <question>` |
| Writing Assistance | `/ask`, `/summarize` |
| Code Generation & Debugging | `/code <request>` |
| Translations | `/translate <lang> <text>` |
| Image Analysis | `/analyze` (reply to a photo) |
| Image Generation | `/image <description>` |
| Business Assistance | `/style business` |
| Education & Tutoring | `/style teacher` |
| Prompt Engineering | `/ask` with detailed prompts |
| Productivity Tools | `/tools` (weather, crypto, QR, wiki, news...) |
| AI Model Selection | `/model` |
| AI Personality Modes | `/style` |

---

## Architecture

```
GitHub (source of truth)
     ↓  push
Railway (production deployment — ONLY)
     ↑  reports events
Fundz Company Headquarters (executive governance)
```

- **FundzAiBot** — AI assistant. Serves users. Reports to HQ.
- **Fundz Company Headquarters** — Executive governance. Receives reports. Makes decisions.
- **FundzMarket** — Independent marketplace product.

---

## Product Identity

FundzAiBot identifies itself as:
- **Name:** FundzAiBot
- **Role:** AI Assistant
- **Developer:** Fundz Company Ltd.
- **Version:** 5.0.1
- **GitHub:** https://github.com/dervishjhay1/FundAiBot
- **Deployment:** Railway (production only)

FundzAiBot **never** identifies itself as TestAudit, Headquarters, or claims executive authority.

---

## Headquarters Communication

FundzAiBot continuously synchronizes significant user activity to Headquarters via `services/hq_sync.py`.

**Events synchronized:**
- User registration, first conversation, returning user
- Prompts submitted (category, AI provider used)
- AI provider failures and rate limit events
- Abuse and spam detection
- Language changes, subscription changes
- System restart and health events
- Executive request routing

**Offline resilience:**
- Events are queued in memory
- Automatic retry with exponential back-off
- No event is ever lost on Railway restart

**Executive Request Routing:**  
When a user performs an action requiring executive authority (fraud reports, marketplace issues, executive complaints), FundzAiBot creates an Executive Request, transmits it to HQ, and waits for the HQ decision. FundzAiBot never processes executive decisions locally.

---

## Environment Variables

| Variable | Required | Description |
|---------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | ✅ | Telegram bot token |
| `ADMIN_USER_ID` | ✅ | Primary admin Telegram user ID |
| `SUPABASE_URL` | ✅ | Supabase project URL |
| `SUPABASE_SERVICE_KEY` | ✅ | Supabase service role key |
| `OPENROUTER_API_KEY` | ✅* | OpenRouter API key |
| `GEMINI_API_KEY` | ✅* | Google Gemini API key |
| `HUGGINGFACE_API_KEY` | ✅* | HuggingFace API key |
| `OPENAI_API_KEY` | Optional | OpenAI API key (priority provider) |
| `HQ_API_URL` | Optional | Fundz Company Headquarters API URL |
| `HQ_API_KEY` | Optional | HQ API authentication key |
| `REFERRAL_LINK` | Optional | Official referral link (sourced from HQ) |
| `SESSION_SECRET` | Recommended | Flask session secret |

*At least one AI key is required.

---

## Deployment

**Railway is the ONLY production deployment platform.**  
Replit is for development and GitHub sync only.

Polling starts automatically on Railway when `RAILWAY_ENVIRONMENT` is detected.  
Non-Railway environments run Flask keep-alive only (no Telegram polling).

### Railway Setup
1. Connect this GitHub repo to a Railway project.
2. Set all required environment variables in Railway → Variables.
3. Railway auto-deploys on push to `main`.

### GitHub Setup
Every verified change must be committed and pushed to GitHub.  
GitHub is the permanent source of truth.

---

## Development

```bash
# Clone
git clone https://github.com/dervishjhay1/FundAiBot
cd FundAiBot

# Install dependencies
pip install -r requirements.txt

# Configure (copy and fill in)
cp .env.example .env

# Run (non-Railway — Flask keep-alive only, no Telegram polling)
python main.py
```

---

## Referral Policy

FundzAiBot **never hardcodes referral links**.  
Official referral links are fetched from the HQ Product Registry at runtime.  
When HQ updates a referral link, FundzAiBot automatically uses the latest version.

---

## Workspace

```
handlers/          # Telegram command and event handlers
services/          # Business logic and external services
  ai_service.py    # Multi-provider AI (OpenAI/OpenRouter/Gemini/HuggingFace)
  database.py      # Supabase data layer
  hq_sync.py       # Headquarters event synchronization ← NEW v5.0.1
  product_metadata.py  # Product metadata for HQ ← NEW v5.0.1
  keepalive.py     # Flask keep-alive for Railway health check
  queue_manager.py # Message queue (prevents overload)
  ...
config/
  settings.py      # All environment variables and constants
utils/
  keyboards.py     # Telegram inline keyboard factories
  logger.py        # Structured logging
locales/           # Translation strings (11 languages)
```

---

## License

Proprietary — Fundz Company Ltd.  
All rights reserved.
