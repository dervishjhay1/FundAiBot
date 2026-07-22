# FundzAiBot — Changelog

## v5.0.1 — Ecosystem Restructuring (2026-07-22)

### Executive Summary
FundzAiBot has been restructured as a dedicated AI assistant product.
Executive governance has permanently moved to Fundz Company Headquarters.
FundzAiBot never makes executive decisions.

---

### REMOVED — Executive Features
- **TestAudit** — entire `services/testaudit_core.py` removed
- **CEO Office** — `handlers/ceo_office.py` and `services/ceo_office.py` removed
- **Audit Handler** — `/testaudit` command and audit UI removed
- **Executive Assistant** — morning/evening CEO briefs removed
- **Autonomous Operations Mode** — self-governance removed
- **Executive Chat** — CEO-mode AI sessions removed
- **Decision Engine** — autonomous decision system removed
- **Meeting Manager** — company meeting scheduling removed
- **Channel Manager** — TestAudit-role channel publishing removed
- **Channel Publisher** — automated AI content publishing removed
- **Community Manager** — TestAudit group persona removed
- **Company Constitution** — `services/constitution.py` removed
- **Department Registry** — org chart registry removed
- **DM Operations** — executive DM operations removed
- **Feature Tracker** — product backlog management removed
- **Customer Success Manager** — re-engagement manager removed
- **Product Registry** — TestAudit product registry removed

### REMOVED — Administration Features
- **Admin Panel** — `/admin` command and full admin dashboard removed
- **Admin Dashboard** — all admin UI panels removed
- **Announcement System** — `handlers/announcements.py` removed (managed by HQ)
- **Admin Manager Service** — `services/admin_manager.py` removed
- **Broadcast Commands** — all broadcast/announce commands removed
- **CEO-only Commands** — `/ceo_office`, `/schedule_meeting` removed
- **TestAudit Mention Handler** — group TestAudit persona removed
- **Smart Community Handler** — autonomous group engagement removed
- **Group AI Handler** — AI in groups removed (private DMs only)
- **Marketplace Administration** — seller/product approval removed

### REMOVED — TestAudit Identity
- FundzAiBot no longer identifies itself as TestAudit
- FundzAiBot no longer claims executive authority
- All TestAudit branding removed from responses and commands

---

### ADDED — Headquarters Integration

#### `services/hq_sync.py` — Executive Event Engine
- Queues every significant user activity as a structured event
- Background daemon delivers events to HQ via HTTP POST
- Offline queue with exponential back-off retry (no event ever lost)
- Survives Railway restart — queue replayed on reconnect
- Full event schema: event_id, timestamp, source, user_id, username, event_type, category, priority, metadata, status

#### Event coverage includes:
- User registration, first conversation, returning user
- Prompt submitted, conversation started/completed
- Feature used, AI provider used, AI provider failure
- Rate limit events, errors, warnings
- Abuse detection, spam detection
- Language changes, profile changes
- Subscription changes, session events
- System restart, health changes
- Executive request routing to HQ

#### `services/product_metadata.py` — Product Registry Metadata
- Exposes structured product metadata for HQ consumption
- Fetches official referral links from HQ Product Registry (never hardcoded)
- Pushes metadata to HQ on startup
- Refreshes referral links dynamically from HQ

---

### CHANGED — Architecture

#### Product Identity
- FundzAiBot identifies itself as: **FundzAiBot — An AI Assistant developed by Fundz Company Ltd.**
- Never identifies as TestAudit, Headquarters, or claims executive authority

#### Group Chat Behavior
- `handlers/group.py` simplified: spam filter + welcome message only
- AI conversations are private DM only
- No TestAudit persona in groups

#### Callback Dispatcher
- `handlers/callbacks.py` rewritten: only user-facing callbacks remain
- All CEO/testaudit/audit callbacks removed

#### Keyboard Menus
- `utils/keyboards.py` cleaned: executive admin menus removed
- `admin_main_menu()` kept as minimal operational menu only

#### Admin Guard
- `utils/admin_guard.py` updated to use `config.settings.is_admin` directly
- No longer depends on deleted `services/admin_manager`

#### Startup
- `main.py` rewritten: starts HQ sync daemon in `post_init()`
- Refreshes product metadata from HQ on startup
- Reports system restart event to HQ
- Bot version bumped to **5.0.1**

---

### PRESERVED — AI Features
All core AI assistant features remain intact:
- Multi-provider AI chat (OpenAI → OpenRouter → Gemini → HuggingFace)
- Writing assistance (`/ask`, `/summarize`, `/translate`)
- Code generation and debugging (`/code`)
- Image generation (`/image`, 6 styles)
- Image analysis via Gemini Vision (`/analyze`)
- AI personality styles (`/style`, 8 modes)
- AI model switcher (`/model`)
- Voice transcription (`/voice`)
- Web search (DuckDuckGo)
- Useful tools (`/tools`: weather, crypto, QR, wiki, news, currency, calculator)
- User profiles and stats (`/profile`, `/stats`)
- Referral and credit system
- VIP subscription plans (Telegram Stars)
- Multi-language support (11 languages)
- Conversation memory
- Daily credit wallet
- Onboarding flow
- Feedback and leaderboard

---

### Files Modified
| File | Change |
|------|--------|
| `main.py` | Rewritten — removed executive imports, added HQ sync startup |
| `config/settings.py` | Added HQ_API_URL, HQ_API_KEY, HQ_SYNC_ENABLED, referral config |
| `handlers/callbacks.py` | Rewritten — only user-facing callbacks |
| `handlers/group.py` | Simplified — spam filter + welcome only |
| `utils/keyboards.py` | Cleaned — executive menus removed |
| `utils/admin_guard.py` | Updated — uses config.settings directly |
| `handlers/start.py` | Fixed — removed deleted announcements import |
| `handlers/ai_commands.py` | Fixed — removed testbroadcast HQ dependency |
| `handlers/onboarding.py` | Fixed — removed deleted announcements import |

### Files Added
| File | Purpose |
|------|---------|
| `services/hq_sync.py` | Headquarters event synchronization engine |
| `services/product_metadata.py` | Product metadata for HQ consumption |

### Files Deleted (21 executive/admin files)
`handlers/admin.py`, `handlers/audit.py`, `handlers/ceo_office.py`,
`handlers/announcements.py`, `services/testaudit_core.py`,
`services/audit_service.py`, `services/ceo_office.py`,
`services/autonomous_mode.py`, `services/executive_assistant.py`,
`services/executive_chat.py`, `services/channel_manager.py`,
`services/channel_publisher.py`, `services/community_manager.py`,
`services/constitution.py`, `services/decision_engine.py`,
`services/department_registry.py`, `services/dm_operations.py`,
`services/feature_tracker.py`, `services/meeting_manager.py`,
`services/admin_manager.py`, `services/customer_success.py`,
`services/product_registry.py`

---

## v5.0.0 — Previous Version
See git history for earlier changelog entries.
