"""
FundzAiBot — Multi-language system.

Free languages: English (en), Spanish (es), French (fr)
VIP-only languages: Arabic (ar), German (de), Portuguese (pt), Yoruba (yo), Chinese (zh)

All functions are synchronous — call via run_in_executor from async handlers.
Language preference is stored per-user in Supabase users table (language column).
Admins always have access to all languages automatically.
"""

from config.settings import is_admin
from utils.logger import get_logger

log = get_logger(__name__)

# ── Language registry ─────────────────────────────────────────────────────────

FREE_LANGUAGES = {
    "en": "🇬🇧 English",
    "es": "🇪🇸 Español",
    "fr": "🇫🇷 Français",
}

VIP_LANGUAGES = {
    "ar": "🇸🇦 العربية",
    "de": "🇩🇪 Deutsch",
    "pt": "🇧🇷 Português",
    "yo": "🇳🇬 Yorùbá",
    "zh": "🇨🇳 中文",
}

ALL_LANGUAGES = {**FREE_LANGUAGES, **VIP_LANGUAGES}

DEFAULT_LANGUAGE = "en"

# ── Translations ──────────────────────────────────────────────────────────────

STRINGS: dict[str, dict[str, str]] = {

    # ── ENGLISH ───────────────────────────────────────────────────────────────
    "en": {
        "welcome_back": "👋 <b>Welcome back, {name}!</b>\n\nWhat shall we do today?",
        "welcome_admin": (
            "🛡️ <b>Welcome back, Admin!</b>\n\n<b>FundzAiBot Control Centre</b>\n\n"
            "You have <b>full access</b> — unlimited chats, unlimited images, all admin controls.\n\n"
            "<b>Bot Status:</b>\n  💬 Chat: {chat_status}\n  🎨 Images: {image_status}\n"
            "  🚧 Maintenance: {maint_status}\n  🌐 New Users: {users_status}\n\n"
            "Use the panel below to manage your bot."
        ),
        "welcome_new": (
            "✨ <b>Welcome to FundzAiBot!</b>\n\n"
            "Your intelligent AI assistant — powered by GPT-4, Gemini &amp; Stable Diffusion.\n\n"
            "<b>What I can do:</b>\n"
            "🤖 <b>AI Chat</b> — Ask me anything, in 8 different styles\n"
            "🎨 <b>Image Gen</b> — Describe a scene and I'll create it\n"
            "📊 <b>Smart Memory</b> — I remember our conversation context\n"
            "🔗 <b>Referral Rewards</b> — Invite friends, earn bonus credits\n"
            "💎 <b>VIP Plans</b> — Unlock unlimited power\n\n"
            "You start with <b>{chat} daily chats</b> and <b>{image} daily images</b>. Free.\n\n"
            "Tap a button below to get started! 👇"
        ),
        "maintenance": "🚧 <b>FundzAiBot is under maintenance.</b>\n\nWe'll be back shortly!",
        "new_users_paused": "🚫 <b>New registrations are currently paused.</b>\n\nPlease try again later.",
        "referral_bonus": "🎁 <b>Referral bonus applied!</b>\nYour friend earned +10 chat &amp; +2 image credits.",
        "choose_language": "🌐 <b>Choose your language</b>\n\nFree users: English, Spanish, French\n💎 VIP users: All languages",
        "language_set": "✅ Language set to <b>{lang}</b>!",
        "language_vip_only": "💎 This language is for VIP users only.\n\nUpgrade your plan with /subscribe to unlock all languages!",
        "pin_label": "📌 Pinned Message",
        "pin_from": "▸ FundzAiBot",
        "btn_support": "🔧 Support",
        "btn_channel": "📢 Channel",
        "btn_community": "👥 Community",
        "btn_language": "🌐 Language",
        "btn_back": "« Main Menu",
        "chat_disabled": "💬 <b>AI Chat is temporarily disabled.</b>\n\nCheck back soon!",
        "image_disabled": "🎨 <b>Image Generation is temporarily disabled.</b>\n\nCheck back soon!",
        "rate_limited": "⏳ Please wait {wait}s before sending another message.",
        "daily_limit": "📊 <b>Daily limit reached!</b>\n\nYou've used all {limit} daily {type} credits.\n\n💎 Upgrade to VIP for up to {vip_limit}x more!",
        "help_title": "<b>🤖 {bot} — Help Guide</b>",
        "settings_title": "⚙️ <b>Settings</b>\n\nCustomise your FundzAiBot experience:",
        "vip_admin_msg": "🛡️ <b>You are the Administrator.</b>\n\nAdmin accounts have <b>unlimited access</b> — no VIP subscription needed.",
    },

    # ── SPANISH ───────────────────────────────────────────────────────────────
    "es": {
        "welcome_back": "👋 <b>¡Bienvenido de nuevo, {name}!</b>\n\n¿Qué hacemos hoy?",
        "welcome_admin": (
            "🛡️ <b>¡Bienvenido de nuevo, Admin!</b>\n\n<b>Centro de Control FundzAiBot</b>\n\n"
            "Tienes <b>acceso completo</b> — chats ilimitados, imágenes ilimitadas, todos los controles.\n\n"
            "<b>Estado del Bot:</b>\n  💬 Chat: {chat_status}\n  🎨 Imágenes: {image_status}\n"
            "  🚧 Mantenimiento: {maint_status}\n  🌐 Nuevos Usuarios: {users_status}\n\n"
            "Usa el panel de abajo para gestionar tu bot."
        ),
        "welcome_new": (
            "✨ <b>¡Bienvenido a FundzAiBot!</b>\n\n"
            "Tu asistente de IA inteligente — impulsado por GPT-4, Gemini y Stable Diffusion.\n\n"
            "<b>Lo que puedo hacer:</b>\n"
            "🤖 <b>Chat con IA</b> — Pregúntame cualquier cosa en 8 estilos\n"
            "🎨 <b>Generar Imágenes</b> — Describe una escena y la crearé\n"
            "📊 <b>Memoria Inteligente</b> — Recuerdo el contexto de nuestra conversación\n"
            "🔗 <b>Recompensas de Referido</b> — Invita amigos, gana créditos\n"
            "💎 <b>Planes VIP</b> — Desbloquea poder ilimitado\n\n"
            "Comienzas con <b>{chat} chats diarios</b> y <b>{image} imágenes diarias</b>. Gratis.\n\n"
            "¡Toca un botón para empezar! 👇"
        ),
        "maintenance": "🚧 <b>FundzAiBot está en mantenimiento.</b>\n\n¡Volvemos pronto!",
        "new_users_paused": "🚫 <b>Los nuevos registros están pausados.</b>\n\nIntenta más tarde.",
        "referral_bonus": "🎁 <b>¡Bono de referido aplicado!</b>\nTu amigo ganó +10 chat y +2 créditos de imagen.",
        "choose_language": "🌐 <b>Elige tu idioma</b>\n\nUsuarios gratis: Inglés, Español, Francés\n💎 VIP: Todos los idiomas",
        "language_set": "✅ Idioma configurado a <b>{lang}</b>!",
        "language_vip_only": "💎 Este idioma es solo para usuarios VIP.\n\n¡Actualiza tu plan con /subscribe!",
        "pin_label": "📌 Mensaje Fijado",
        "pin_from": "▸ FundzAiBot",
        "btn_support": "🔧 Soporte",
        "btn_channel": "📢 Canal",
        "btn_community": "👥 Comunidad",
        "btn_language": "🌐 Idioma",
        "btn_back": "« Menú Principal",
        "chat_disabled": "💬 <b>El chat con IA está temporalmente desactivado.</b>\n\n¡Vuelve pronto!",
        "image_disabled": "🎨 <b>La generación de imágenes está temporalmente desactivada.</b>",
        "rate_limited": "⏳ Espera {wait}s antes de enviar otro mensaje.",
        "daily_limit": "📊 <b>¡Límite diario alcanzado!</b>\n\nHas usado todos tus {limit} créditos de {type}.\n\n💎 ¡Actualiza a VIP para obtener {vip_limit}x más!",
        "help_title": "<b>🤖 {bot} — Guía de Ayuda</b>",
        "settings_title": "⚙️ <b>Configuración</b>\n\nPersonaliza tu experiencia con FundzAiBot:",
        "vip_admin_msg": "🛡️ <b>Eres el Administrador.</b>\n\nLas cuentas admin tienen <b>acceso ilimitado</b> — sin VIP necesario.",
    },

    # ── FRENCH ────────────────────────────────────────────────────────────────
    "fr": {
        "welcome_back": "👋 <b>Bon retour, {name}!</b>\n\nQue faisons-nous aujourd'hui?",
        "welcome_admin": (
            "🛡️ <b>Bon retour, Admin!</b>\n\n<b>Centre de contrôle FundzAiBot</b>\n\n"
            "Vous avez un <b>accès complet</b> — chats illimités, images illimitées.\n\n"
            "<b>Statut du Bot:</b>\n  💬 Chat: {chat_status}\n  🎨 Images: {image_status}\n"
            "  🚧 Maintenance: {maint_status}\n  🌐 Nouveaux Utilisateurs: {users_status}\n\n"
            "Utilisez le panneau ci-dessous pour gérer votre bot."
        ),
        "welcome_new": (
            "✨ <b>Bienvenue sur FundzAiBot!</b>\n\n"
            "Votre assistant IA intelligent — propulsé par GPT-4, Gemini et Stable Diffusion.\n\n"
            "<b>Ce que je peux faire:</b>\n"
            "🤖 <b>Chat IA</b> — Posez-moi n'importe quelle question, dans 8 styles\n"
            "🎨 <b>Génération d'images</b> — Décrivez une scène et je la créerai\n"
            "📊 <b>Mémoire intelligente</b> — Je me souviens de notre contexte\n"
            "🔗 <b>Récompenses de parrainage</b> — Invitez des amis, gagnez des crédits\n"
            "💎 <b>Plans VIP</b> — Débloquez un accès illimité\n\n"
            "Vous commencez avec <b>{chat} chats quotidiens</b> et <b>{image} images quotidiennes</b>. Gratuit.\n\n"
            "Appuyez sur un bouton pour commencer! 👇"
        ),
        "maintenance": "🚧 <b>FundzAiBot est en maintenance.</b>\n\nNous serons bientôt de retour!",
        "new_users_paused": "🚫 <b>Les nouvelles inscriptions sont temporairement suspendues.</b>\n\nRevenez plus tard.",
        "referral_bonus": "🎁 <b>Bonus de parrainage appliqué!</b>\nVotre ami a gagné +10 chat et +2 crédits image.",
        "choose_language": "🌐 <b>Choisissez votre langue</b>\n\nUtilisateurs gratuits: Anglais, Espagnol, Français\n💎 VIP: Toutes les langues",
        "language_set": "✅ Langue définie sur <b>{lang}</b>!",
        "language_vip_only": "💎 Cette langue est réservée aux utilisateurs VIP.\n\nAméliorez votre plan avec /subscribe!",
        "pin_label": "📌 Message Épinglé",
        "pin_from": "▸ FundzAiBot",
        "btn_support": "🔧 Support",
        "btn_channel": "📢 Canal",
        "btn_community": "👥 Communauté",
        "btn_language": "🌐 Langue",
        "btn_back": "« Menu Principal",
        "chat_disabled": "💬 <b>Le chat IA est temporairement désactivé.</b>\n\nRevenez bientôt!",
        "image_disabled": "🎨 <b>La génération d'images est temporairement désactivée.</b>",
        "rate_limited": "⏳ Attendez {wait}s avant d'envoyer un autre message.",
        "daily_limit": "📊 <b>Limite quotidienne atteinte!</b>\n\nVous avez utilisé tous vos {limit} crédits de {type}.\n\n💎 Passez en VIP pour {vip_limit}x plus!",
        "help_title": "<b>🤖 {bot} — Guide d'aide</b>",
        "settings_title": "⚙️ <b>Paramètres</b>\n\nPersonnalisez votre expérience FundzAiBot:",
        "vip_admin_msg": "🛡️ <b>Vous êtes l'Administrateur.</b>\n\nLes comptes admin ont un <b>accès illimité</b> — pas de VIP nécessaire.",
    },

    # ── ARABIC ────────────────────────────────────────────────────────────────
    "ar": {
        "welcome_back": "👋 <b>مرحباً بعودتك، {name}!</b>\n\nماذا نفعل اليوم؟",
        "welcome_admin": (
            "🛡️ <b>مرحباً بعودتك، أيها المسؤول!</b>\n\n<b>مركز تحكم FundzAiBot</b>\n\n"
            "لديك <b>وصول كامل</b> — محادثات غير محدودة، صور غير محدودة.\n\n"
            "<b>حالة البوت:</b>\n  💬 المحادثة: {chat_status}\n  🎨 الصور: {image_status}\n"
            "  🚧 الصيانة: {maint_status}\n  🌐 المستخدمون الجدد: {users_status}\n\n"
            "استخدم اللوحة أدناه لإدارة البوت."
        ),
        "welcome_new": (
            "✨ <b>مرحباً في FundzAiBot!</b>\n\n"
            "مساعدك الذكاء الاصطناعي الذكي — مدعوم بـ GPT-4 وGemini وStable Diffusion.\n\n"
            "ابدأ بـ <b>{chat} محادثة يومية</b> و<b>{image} صور يومية</b>. مجاناً! 👇"
        ),
        "maintenance": "🚧 <b>FundzAiBot تحت الصيانة.</b>\n\nسنعود قريباً!",
        "new_users_paused": "🚫 <b>التسجيل الجديد متوقف حالياً.</b>\n\nحاول مرة أخرى لاحقاً.",
        "referral_bonus": "🎁 <b>تم تطبيق مكافأة الإحالة!</b>\nحصل صديقك على +10 محادثة و+2 رصيد صور.",
        "choose_language": "🌐 <b>اختر لغتك</b>\n\nالمجانيون: الإنجليزية، الإسبانية، الفرنسية\n💎 VIP: جميع اللغات",
        "language_set": "✅ تم ضبط اللغة على <b>{lang}</b>!",
        "language_vip_only": "💎 هذه اللغة للمستخدمين VIP فقط.\n\nرقّ خطتك باستخدام /subscribe!",
        "pin_label": "📌 رسالة مثبتة",
        "pin_from": "▸ FundzAiBot",
        "btn_support": "🔧 الدعم",
        "btn_channel": "📢 القناة",
        "btn_community": "👥 المجتمع",
        "btn_language": "🌐 اللغة",
        "btn_back": "« القائمة الرئيسية",
        "chat_disabled": "💬 <b>المحادثة معطلة مؤقتاً.</b>",
        "image_disabled": "🎨 <b>توليد الصور معطل مؤقتاً.</b>",
        "rate_limited": "⏳ انتظر {wait} ثانية قبل إرسال رسالة أخرى.",
        "daily_limit": "📊 <b>تم الوصول للحد اليومي!</b>\n\n💎 رقّ إلى VIP للحصول على {vip_limit}x أكثر!",
        "help_title": "<b>🤖 {bot} — دليل المساعدة</b>",
        "settings_title": "⚙️ <b>الإعدادات</b>",
        "vip_admin_msg": "🛡️ <b>أنت المسؤول.</b>\n\nلديك وصول غير محدود.",
    },

    # ── GERMAN ────────────────────────────────────────────────────────────────
    "de": {
        "welcome_back": "👋 <b>Willkommen zurück, {name}!</b>\n\nWas machen wir heute?",
        "welcome_admin": (
            "🛡️ <b>Willkommen zurück, Admin!</b>\n\n<b>FundzAiBot Kontrollzentrum</b>\n\n"
            "Du hast <b>vollen Zugriff</b> — unbegrenzte Chats und Bilder.\n\n"
            "<b>Bot-Status:</b>\n  💬 Chat: {chat_status}\n  🎨 Bilder: {image_status}\n"
            "  🚧 Wartung: {maint_status}\n  🌐 Neue Nutzer: {users_status}\n\n"
            "Nutze das Panel unten zum Verwalten."
        ),
        "welcome_new": (
            "✨ <b>Willkommen bei FundzAiBot!</b>\n\n"
            "Dein intelligenter KI-Assistent — betrieben von GPT-4, Gemini und Stable Diffusion.\n\n"
            "Starte mit <b>{chat} täglichen Chats</b> und <b>{image} täglichen Bildern</b>. Kostenlos! 👇"
        ),
        "maintenance": "🚧 <b>FundzAiBot befindet sich in Wartung.</b>\n\nWir sind bald wieder da!",
        "new_users_paused": "🚫 <b>Neue Registrierungen sind momentan pausiert.</b>\n\nBitte versuche es später.",
        "referral_bonus": "🎁 <b>Empfehlungsbonus angewendet!</b>\nDein Freund erhielt +10 Chat- und +2 Bildguthaben.",
        "choose_language": "🌐 <b>Wähle deine Sprache</b>\n\nKostenlose Nutzer: Englisch, Spanisch, Französisch\n💎 VIP: Alle Sprachen",
        "language_set": "✅ Sprache auf <b>{lang}</b> eingestellt!",
        "language_vip_only": "💎 Diese Sprache ist nur für VIP-Nutzer.\n\nUpgrade mit /subscribe!",
        "pin_label": "📌 Angeheftete Nachricht",
        "pin_from": "▸ FundzAiBot",
        "btn_support": "🔧 Support",
        "btn_channel": "📢 Kanal",
        "btn_community": "👥 Community",
        "btn_language": "🌐 Sprache",
        "btn_back": "« Hauptmenü",
        "chat_disabled": "💬 <b>KI-Chat ist vorübergehend deaktiviert.</b>",
        "image_disabled": "🎨 <b>Bildgenerierung ist vorübergehend deaktiviert.</b>",
        "rate_limited": "⏳ Warte {wait}s bevor du eine weitere Nachricht sendest.",
        "daily_limit": "📊 <b>Tageslimit erreicht!</b>\n\n💎 Upgrade auf VIP für {vip_limit}x mehr!",
        "help_title": "<b>🤖 {bot} — Hilfeführer</b>",
        "settings_title": "⚙️ <b>Einstellungen</b>",
        "vip_admin_msg": "🛡️ <b>Du bist der Administrator.</b>\n\nAdmin-Konten haben unbegrenzten Zugang.",
    },

    # ── PORTUGUESE ────────────────────────────────────────────────────────────
    "pt": {
        "welcome_back": "👋 <b>Bem-vindo de volta, {name}!</b>\n\nO que vamos fazer hoje?",
        "welcome_admin": (
            "🛡️ <b>Bem-vindo de volta, Admin!</b>\n\n<b>Centro de Controle FundzAiBot</b>\n\n"
            "Você tem <b>acesso completo</b> — chats ilimitados, imagens ilimitadas.\n\n"
            "<b>Status do Bot:</b>\n  💬 Chat: {chat_status}\n  🎨 Imagens: {image_status}\n"
            "  🚧 Manutenção: {maint_status}\n  🌐 Novos Usuários: {users_status}\n\n"
            "Use o painel abaixo para gerenciar seu bot."
        ),
        "welcome_new": (
            "✨ <b>Bem-vindo ao FundzAiBot!</b>\n\n"
            "Seu assistente de IA inteligente — alimentado por GPT-4, Gemini e Stable Diffusion.\n\n"
            "Comece com <b>{chat} chats diários</b> e <b>{image} imagens diárias</b>. Gratuito! 👇"
        ),
        "maintenance": "🚧 <b>FundzAiBot está em manutenção.</b>\n\nVoltamos em breve!",
        "new_users_paused": "🚫 <b>Novos registros estão pausados.</b>\n\nTente novamente mais tarde.",
        "referral_bonus": "🎁 <b>Bônus de referência aplicado!</b>\nSeu amigo ganhou +10 chat e +2 créditos de imagem.",
        "choose_language": "🌐 <b>Escolha seu idioma</b>\n\nUsuários gratuitos: Inglês, Espanhol, Francês\n💎 VIP: Todos os idiomas",
        "language_set": "✅ Idioma definido para <b>{lang}</b>!",
        "language_vip_only": "💎 Este idioma é apenas para usuários VIP.\n\nAtualize com /subscribe!",
        "pin_label": "📌 Mensagem Fixada",
        "pin_from": "▸ FundzAiBot",
        "btn_support": "🔧 Suporte",
        "btn_channel": "📢 Canal",
        "btn_community": "👥 Comunidade",
        "btn_language": "🌐 Idioma",
        "btn_back": "« Menu Principal",
        "chat_disabled": "💬 <b>Chat IA está temporariamente desativado.</b>",
        "image_disabled": "🎨 <b>Geração de imagens está temporariamente desativada.</b>",
        "rate_limited": "⏳ Aguarde {wait}s antes de enviar outra mensagem.",
        "daily_limit": "📊 <b>Limite diário atingido!</b>\n\n💎 Atualize para VIP para {vip_limit}x mais!",
        "help_title": "<b>🤖 {bot} — Guia de Ajuda</b>",
        "settings_title": "⚙️ <b>Configurações</b>",
        "vip_admin_msg": "🛡️ <b>Você é o Administrador.</b>\n\nContas admin têm acesso ilimitado.",
    },

    # ── YORUBA ────────────────────────────────────────────────────────────────
    "yo": {
        "welcome_back": "👋 <b>Ẹ káàbọ̀ padà, {name}!</b>\n\nKíni a óò ṣe lónìí?",
        "welcome_admin": (
            "🛡️ <b>Ẹ káàbọ̀ padà, Admin!</b>\n\n<b>Ile-iṣẹ Iṣakoso FundzAiBot</b>\n\n"
            "O ní <b>ọ̀nà pípé</b> — ìfọ̀rọ̀wánilẹ́nuwò àìlópin, àwọn àwòrán àìlópin.\n\n"
            "<b>Ipò Bot:</b>\n  💬 Ọ̀rọ̀: {chat_status}\n  🎨 Àwọn Àwòrán: {image_status}\n"
            "  🚧 Ìtọ́jú: {maint_status}\n  🌐 Àwọn Olùmúlò Tuntun: {users_status}"
        ),
        "welcome_new": (
            "✨ <b>Ẹ káàbọ̀ sí FundzAiBot!</b>\n\n"
            "Olùrànlọ́wọ́ AI rẹ — pẹ̀lú GPT-4, Gemini àti Stable Diffusion.\n\n"
            "Bẹ̀rẹ̀ pẹ̀lú <b>{chat} ìfọ̀rọ̀wánilẹ́nuwò ojoojúmọ̀</b> àti <b>{image} àwọn àwòrán</b>. Ọ̀fẹ́! 👇"
        ),
        "maintenance": "🚧 <b>FundzAiBot wà ní ìtọ́jú.</b>\n\nA óò padà lẹ́sẹ̀kẹsẹ̀!",
        "new_users_paused": "🚫 <b>Àwọn forúkọsílẹ̀ tuntun dúró sí i.</b>\n\nJọ̀wọ́ gbìyànjú lẹ́yìn náà.",
        "referral_bonus": "🎁 <b>Ẹ̀bùn àtọ́ka lo!!</b>\nÒré rẹ gba +10 ìfọ̀rọ̀wánilẹ́nuwò àti +2 kirẹditi àwòrán.",
        "choose_language": "🌐 <b>Yan èdè rẹ</b>\n\nOlùmúlò ọ̀fẹ́: Gẹ̀ẹ́sì, Spánì, Faransé\n💎 VIP: Gbogbo èdè",
        "language_set": "✅ Èdè ti yí padà sí <b>{lang}</b>!",
        "language_vip_only": "💎 Èdè yìí fún àwọn olùmúlò VIP nìkan.\n\nGbé ẹ jí pẹ̀lú /subscribe!",
        "pin_label": "📌 Ìfiranṣẹ Tí A Dán",
        "pin_from": "▸ FundzAiBot",
        "btn_support": "🔧 Ìrànlọ́wọ́",
        "btn_channel": "📢 Ìkójọpọ̀",
        "btn_community": "👥 Àwùjọ",
        "btn_language": "🌐 Èdè",
        "btn_back": "« Àkọlé Àkọ́kọ́",
        "chat_disabled": "💬 <b>Ìfọ̀rọ̀wánilẹ́nuwò AI ti dúró sí i.</b>",
        "image_disabled": "🎨 <b>Ṣíṣẹda àwòrán ti dúró sí i.</b>",
        "rate_limited": "⏳ Dúró {wait}s ṣáájú fíránṣẹ̀ ìfiranṣẹ mìíràn.",
        "daily_limit": "📊 <b>Iye ojoojúmọ̀ ti pé!</b>\n\n💎 Gba VIP fún {vip_limit}x díẹ̀ síi!",
        "help_title": "<b>🤖 {bot} — Ìtọ́sọ́nà Ìrànlọ́wọ́</b>",
        "settings_title": "⚙️ <b>Àwọn Ìtòlẹ́sẹẹsẹ</b>",
        "vip_admin_msg": "🛡️ <b>Ìwọ ni Admin.</b>\n\nÀwọn Ọ̀nà Admin ní ọ̀nà àìlópin.",
    },

    # ── CHINESE ───────────────────────────────────────────────────────────────
    "zh": {
        "welcome_back": "👋 <b>欢迎回来，{name}！</b>\n\n今天我们做什么？",
        "welcome_admin": (
            "🛡️ <b>欢迎回来，管理员！</b>\n\n<b>FundzAiBot 控制中心</b>\n\n"
            "您拥有<b>完全访问权限</b> — 无限聊天、无限图片。\n\n"
            "<b>机器人状态：</b>\n  💬 聊天: {chat_status}\n  🎨 图片: {image_status}\n"
            "  🚧 维护: {maint_status}\n  🌐 新用户: {users_status}\n\n"
            "使用下面的面板管理您的机器人。"
        ),
        "welcome_new": (
            "✨ <b>欢迎使用 FundzAiBot！</b>\n\n"
            "您的智能 AI 助手 — 由 GPT-4、Gemini 和 Stable Diffusion 驱动。\n\n"
            "免费开始：每天 <b>{chat} 次聊天</b>和 <b>{image} 张图片</b>！ 👇"
        ),
        "maintenance": "🚧 <b>FundzAiBot 正在维护中。</b>\n\n我们很快就会回来！",
        "new_users_paused": "🚫 <b>新用户注册已暂停。</b>\n\n请稍后再试。",
        "referral_bonus": "🎁 <b>推荐奖励已应用！</b>\n您的朋友获得了 +10 聊天和 +2 图片积分。",
        "choose_language": "🌐 <b>选择您的语言</b>\n\n免费用户：英语、西班牙语、法语\n💎 VIP：所有语言",
        "language_set": "✅ 语言已设置为 <b>{lang}</b>！",
        "language_vip_only": "💎 此语言仅适用于 VIP 用户。\n\n使用 /subscribe 升级您的计划！",
        "pin_label": "📌 置顶消息",
        "pin_from": "▸ FundzAiBot",
        "btn_support": "🔧 支持",
        "btn_channel": "📢 频道",
        "btn_community": "👥 社区",
        "btn_language": "🌐 语言",
        "btn_back": "« 主菜单",
        "chat_disabled": "💬 <b>AI 聊天暂时禁用。</b>",
        "image_disabled": "🎨 <b>图片生成暂时禁用。</b>",
        "rate_limited": "⏳ 请等待 {wait}s 后再发送消息。",
        "daily_limit": "📊 <b>已达每日限制！</b>\n\n💎 升级到 VIP 获得 {vip_limit}x 更多！",
        "help_title": "<b>🤖 {bot} — 帮助指南</b>",
        "settings_title": "⚙️ <b>设置</b>",
        "vip_admin_msg": "🛡️ <b>您是管理员。</b>\n\n管理员账户拥有无限访问权限。",
    },
}


# ── Helper functions ──────────────────────────────────────────────────────────

def get_string(lang: str, key: str, **kwargs) -> str:
    """Get a translated string for the given language and key."""
    lang = lang if lang in STRINGS else DEFAULT_LANGUAGE
    template = STRINGS[lang].get(key) or STRINGS[DEFAULT_LANGUAGE].get(key, "")
    try:
        return template.format(**kwargs) if kwargs else template
    except KeyError:
        return STRINGS[DEFAULT_LANGUAGE].get(key, template)


def is_free_language(lang: str) -> bool:
    return lang in FREE_LANGUAGES


def is_vip_language(lang: str) -> bool:
    return lang in VIP_LANGUAGES


def can_use_language(lang: str, user: dict, user_id: int) -> bool:
    """Check if a user can use the given language."""
    if is_admin(user_id):
        return True
    if is_free_language(lang):
        return True
    return bool(user.get("is_vip"))


def get_user_language(user: dict | None, user_id: int = 0) -> str:
    """Get the effective language for a user, defaulting to 'en'."""
    if not user:
        return DEFAULT_LANGUAGE
    lang = (user.get("language") or DEFAULT_LANGUAGE).lower().strip()
    return lang if lang in ALL_LANGUAGES else DEFAULT_LANGUAGE


def save_user_language(user_id: int, lang: str) -> bool:
    """Persist language choice to Supabase. Returns True on success."""
    try:
        from services.database import update_user
        update_user(user_id, language=lang)
        return True
    except Exception as exc:
        log.error("save_user_language(%s, %s): %s", user_id, lang, exc)
        return False
