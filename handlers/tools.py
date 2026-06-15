"""
FundzAiBot — Utility / power-user tool commands.

Commands (all free, no extra API keys required):
  /weather  <city>               — Real-time weather + 3-day forecast (wttr.in)
  /calc     <expression>         — Smart calculator (supports math functions)
  /qr       <text|url>           — Generate QR code as image
  /crypto   [symbol]             — Live crypto prices (CoinGecko)
  /wiki     <topic>              — Wikipedia summary
  /news     [topic]              — Latest news headlines (DuckDuckGo)
  /currency <amount> <from> <to> — Currency converter (Frankfurter/ECB rates)
  /quote                         — Daily inspirational quote

All HTTP calls are synchronous — run via loop.run_in_executor().
"""

import ast
import html
import io
import math
import operator
import asyncio
from urllib.parse import quote as url_quote

import requests

from utils.logger import get_logger

log = get_logger(__name__)

_TIMEOUT = 10

# ── Safe calculator ────────────────────────────────────────────────────────────

_SAFE_OPS: dict = {
    ast.Add:  operator.add,
    ast.Sub:  operator.sub,
    ast.Mult: operator.mul,
    ast.Div:  operator.truediv,
    ast.Pow:  operator.pow,
    ast.Mod:  operator.mod,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

_SAFE_FUNCS: dict = {
    "abs":   abs,
    "round": round,
    "sin":   math.sin,   "cos":  math.cos,  "tan":  math.tan,
    "asin":  math.asin,  "acos": math.acos, "atan": math.atan,
    "sqrt":  math.sqrt,  "log":  math.log,  "log2": math.log2,
    "log10": math.log10, "ceil": math.ceil, "floor": math.floor,
    "exp":   math.exp,   "pi":   math.pi,   "e":    math.e,
    "degrees": math.degrees, "radians": math.radians,
    "factorial": math.factorial,
}


def _safe_eval(node):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in _SAFE_FUNCS:
            return _SAFE_FUNCS[node.id]
        raise ValueError(f"Unknown name: {node.id}")
    if isinstance(node, ast.BinOp):
        op = _SAFE_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        return op(_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp):
        op = _SAFE_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"Unsupported unary: {type(node.op).__name__}")
        return op(_safe_eval(node.operand))
    if isinstance(node, ast.Call):
        func = _safe_eval(node.func)
        if not callable(func):
            raise ValueError("Not callable")
        args = [_safe_eval(a) for a in node.args]
        return func(*args)
    raise ValueError(f"Unsupported node: {type(node).__name__}")


def _calculate(expr: str) -> str:
    try:
        tree  = ast.parse(expr.strip(), mode="eval")
        value = _safe_eval(tree.body)
        if isinstance(value, float):
            formatted = f"{value:.10g}"
        else:
            formatted = str(value)
        return formatted
    except ZeroDivisionError:
        return "ERROR: Division by zero"
    except (ValueError, TypeError, OverflowError) as exc:
        return f"ERROR: {exc}"
    except SyntaxError:
        return "ERROR: Invalid expression"


# ── Weather (wttr.in) ──────────────────────────────────────────────────────────

def _fetch_weather(city: str) -> dict | None:
    try:
        url = f"https://wttr.in/{url_quote(city)}?format=j1"
        resp = requests.get(url, timeout=_TIMEOUT, headers={"User-Agent": "FundzAiBot/4"})
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        log.warning("Weather fetch failed: %s", exc)
        return None


def _format_weather(city: str, data: dict) -> str:
    try:
        cur   = data["current_condition"][0]
        temp  = cur.get("temp_C", "?")
        feels = cur.get("FeelsLikeC", "?")
        desc  = cur["weatherDesc"][0]["value"] if cur.get("weatherDesc") else "?"
        humid = cur.get("humidity", "?")
        wind  = cur.get("windspeedKmph", "?")
        vis   = cur.get("visibility", "?")
        uv    = cur.get("uvIndex", "?")

        def _emj(d: str) -> str:
            d = d.lower()
            if "sunny" in d or "clear" in d:   return "☀️"
            if "cloud" in d or "overcast" in d: return "☁️"
            if "rain" in d or "drizzle" in d:   return "🌧️"
            if "thunder" in d or "storm" in d:  return "⛈️"
            if "snow" in d or "blizzard" in d:  return "❄️"
            if "fog" in d or "mist" in d:       return "🌫️"
            return "🌤️"

        emoji = _emj(desc)

        lines = [
            f"🌍 <b>Weather — {html.escape(city.title())}</b>",
            "",
            f"{emoji} <b>{html.escape(desc)}</b>",
            f"🌡️ Temp: <b>{temp}°C</b>  (feels {feels}°C)",
            f"💧 Humidity: <b>{humid}%</b>",
            f"💨 Wind: <b>{wind} km/h</b>",
            f"👁️ Visibility: <b>{vis} km</b>",
            f"🔆 UV Index: <b>{uv}</b>",
            "",
            "📅 <b>3-Day Forecast:</b>",
        ]

        for day in data.get("weather", [])[:3]:
            date   = day.get("date", "?")
            maxC   = day.get("maxtempC", "?")
            minC   = day.get("mintempC", "?")
            desc2  = day["hourly"][4]["weatherDesc"][0]["value"] if day.get("hourly") else "?"
            lines.append(f"  📆 {date}: {_emj(desc2)} {html.escape(desc2)}  {minC}–{maxC}°C")

        return "\n".join(lines)
    except Exception as exc:
        log.warning("Weather format error: %s", exc)
        return f"🌤️ Weather data received but could not be formatted: {exc}"


# ── QR Code ───────────────────────────────────────────────────────────────────

def _generate_qr(text: str) -> io.BytesIO | None:
    try:
        import qrcode
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=4,
        )
        qr.add_data(text)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.name = "qr.png"
        buf.seek(0)
        return buf
    except ImportError:
        log.warning("qrcode not installed")
        return None
    except Exception as exc:
        log.warning("QR generation error: %s", exc)
        return None


# ── Crypto prices (CoinGecko) ──────────────────────────────────────────────────

_CRYPTO_IDS: dict[str, str] = {
    "BTC":   "bitcoin",        "ETH":  "ethereum",
    "SOL":   "solana",         "BNB":  "binancecoin",
    "XRP":   "ripple",         "ADA":  "cardano",
    "DOT":   "polkadot",       "DOGE": "dogecoin",
    "AVAX":  "avalanche-2",    "MATIC":"matic-network",
    "LINK":  "chainlink",      "UNI":  "uniswap",
    "LTC":   "litecoin",       "ATOM": "cosmos",
    "NEAR":  "near",           "ARB":  "arbitrum",
    "OP":    "optimism",       "SUI":  "sui",
    "TRX":   "tron",           "SHIB": "shiba-inu",
    "PEPE":  "pepe",           "TON":  "the-open-network",
}


def _fetch_crypto(symbols: list[str]) -> dict | None:
    ids = []
    sym_map: dict[str, str] = {}
    for s in symbols[:5]:
        s = s.upper()
        gid = _CRYPTO_IDS.get(s)
        if gid:
            ids.append(gid)
            sym_map[gid] = s
        else:
            ids.append(s.lower())
            sym_map[s.lower()] = s
    if not ids:
        return None
    try:
        url  = f"https://api.coingecko.com/api/v3/simple/price"
        resp = requests.get(url, params={
            "ids":            ",".join(ids),
            "vs_currencies":  "usd,eur,gbp",
            "include_24hr_change": "true",
            "include_market_cap":  "true",
        }, timeout=_TIMEOUT)
        resp.raise_for_status()
        return {"data": resp.json(), "sym_map": sym_map}
    except Exception as exc:
        log.warning("Crypto fetch error: %s", exc)
        return None


def _format_crypto(result: dict) -> str:
    data    = result["data"]
    sym_map = result["sym_map"]
    if not data:
        return "❌ No data found. Check the symbol (e.g. /crypto BTC ETH SOL)"

    lines = ["₿ <b>Live Crypto Prices</b>\n"]
    for gid, vals in data.items():
        sym    = sym_map.get(gid, gid.upper())
        usd    = vals.get("usd",    "?")
        chg    = vals.get("usd_24h_change", 0)
        mcap   = vals.get("usd_market_cap", 0)
        arrow  = "🟢 ▲" if (chg or 0) >= 0 else "🔴 ▼"
        chg_s  = f"{chg:+.2f}%" if isinstance(chg, (int, float)) else "?"
        usd_s  = f"${usd:,.4f}" if isinstance(usd, (int, float)) and usd < 1 else (f"${usd:,.2f}" if isinstance(usd, (int, float)) else str(usd))
        mcap_s = _fmt_mcap(mcap)
        lines.append(
            f"<b>{sym}</b>\n"
            f"  💵 {usd_s}  {arrow} {chg_s}\n"
            f"  📊 Mkt Cap: {mcap_s}"
        )
    lines.append("\n<i>Powered by CoinGecko • refreshed live</i>")
    return "\n".join(lines)


def _fmt_mcap(v) -> str:
    if not isinstance(v, (int, float)) or v == 0:
        return "N/A"
    if v >= 1e12: return f"${v/1e12:.2f}T"
    if v >= 1e9:  return f"${v/1e9:.2f}B"
    if v >= 1e6:  return f"${v/1e6:.2f}M"
    return f"${v:,.0f}"


# ── Wikipedia ─────────────────────────────────────────────────────────────────

def _fetch_wiki(topic: str) -> dict | None:
    try:
        url  = f"https://en.wikipedia.org/api/rest_v1/page/summary/{url_quote(topic)}"
        resp = requests.get(url, timeout=_TIMEOUT, headers={"User-Agent": "FundzAiBot/4"})
        if resp.status_code == 404:
            return {"error": f"No Wikipedia page found for <b>{html.escape(topic)}</b>."}
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        log.warning("Wiki fetch error: %s", exc)
        return None


def _format_wiki(data: dict) -> str:
    if "error" in data:
        return f"❌ {data['error']}"
    title   = html.escape(data.get("title", "?"))
    extract = html.escape(data.get("extract", "No summary available.")[:800])
    url     = data.get("content_urls", {}).get("desktop", {}).get("page", "")
    desc    = html.escape(data.get("description", ""))
    lines   = [f"📖 <b>{title}</b>"]
    if desc:
        lines.append(f"<i>{desc}</i>")
    lines += ["", extract]
    if url:
        lines.append(f"\n🔗 <a href='{url}'>Read full article →</a>")
    return "\n".join(lines)


# ── News (DuckDuckGo) ─────────────────────────────────────────────────────────

def _fetch_news(topic: str) -> list[dict]:
    try:
        from duckduckgo_search import DDGS
        results = []
        with DDGS() as ddgs:
            for r in ddgs.news(topic or "world news today", max_results=6):
                results.append({
                    "title":  (r.get("title") or "")[:120],
                    "url":    r.get("url") or r.get("href") or "",
                    "source": r.get("source") or r.get("publisher", ""),
                    "date":   (r.get("date") or "")[:10],
                    "body":   (r.get("body") or "")[:150],
                })
        return results
    except ImportError:
        log.warning("duckduckgo_search not installed")
        return []
    except Exception as exc:
        log.warning("News fetch error: %s", exc)
        return []


def _format_news(topic: str, results: list[dict]) -> str:
    if not results:
        return f"📰 No news found for <b>{html.escape(topic or 'world')}</b>. Try a different topic."
    lines = [f"📰 <b>Latest News{': ' + html.escape(topic.title()) if topic else ''}</b>\n"]
    for i, r in enumerate(results, 1):
        title  = html.escape(r["title"])
        source = html.escape(r["source"])
        date   = r["date"]
        url    = r["url"]
        meta   = " · ".join(filter(None, [source, date]))
        lines.append(f"{i}. <a href='{url}'><b>{title}</b></a>")
        if meta:
            lines.append(f"   <i>{meta}</i>")
    lines.append("\n<i>Powered by DuckDuckGo News</i>")
    return "\n".join(lines)


# ── Currency converter (Frankfurter API — ECB rates) ──────────────────────────

def _fetch_currency(amount: float, from_: str, to: str) -> dict | None:
    try:
        url  = "https://api.frankfurter.app/latest"
        resp = requests.get(url, params={
            "amount": amount, "from": from_.upper(), "to": to.upper(),
        }, timeout=_TIMEOUT)
        if resp.status_code == 422:
            return {"error": f"Unsupported currency code. Use 3-letter codes like USD, EUR, GBP, NGN."}
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        log.warning("Currency fetch error: %s", exc)
        return None


def _format_currency(amount: float, from_: str, data: dict) -> str:
    if "error" in data:
        return f"❌ {data['error']}"
    rates   = data.get("rates", {})
    base    = data.get("base", from_.upper())
    date    = data.get("date", "?")
    lines   = [f"💱 <b>Currency Converter</b>  <i>({date})</i>\n",
               f"<b>{amount:,.2f} {base}</b> =\n"]
    for code, val in rates.items():
        lines.append(f"  💰 <b>{val:,.4f} {code}</b>")
    lines.append("\n<i>Source: European Central Bank (Frankfurter API)</i>")
    return "\n".join(lines)


# ── Inspirational quote ───────────────────────────────────────────────────────

def _fetch_quote() -> dict | None:
    # ZenQuotes.io — free, no API key, generous limits
    try:
        resp = requests.get("https://zenquotes.io/api/random", timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and data:
            return {"quote": data[0].get("q", ""), "author": data[0].get("a", "")}
        return None
    except Exception:
        pass
    # Fallback: quotable.io
    try:
        resp = requests.get("https://api.quotable.io/random", timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return {"quote": data.get("content", ""), "author": data.get("author", "")}
    except Exception as exc:
        log.warning("Quote fetch error: %s", exc)
        return None


# ── Telegram handlers ─────────────────────────────────────────────────────────

from telegram import Update
from telegram.ext import ContextTypes


async def weather_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/weather <city>"""
    msg  = update.effective_message
    city = " ".join(context.args).strip() if context.args else ""
    if not city:
        await msg.reply_text(
            "🌤️ <b>Usage:</b> /weather &lt;city&gt;\n\n"
            "Examples:\n• /weather London\n• /weather New York\n• /weather Lagos",
            parse_mode="HTML",
        )
        return
    thinking = await msg.reply_text("🌍 <i>Fetching weather…</i>", parse_mode="HTML")
    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, _fetch_weather, city)
    if not data:
        await thinking.edit_text("❌ Could not fetch weather. Check the city name and try again.")
        return
    text = _format_weather(city, data)
    await thinking.edit_text(text, parse_mode="HTML", disable_web_page_preview=True)


async def calc_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/calc <expression>"""
    msg  = update.effective_message
    expr = " ".join(context.args).strip() if context.args else (msg.text or "").replace("/calc", "").strip()
    if not expr:
        await msg.reply_text(
            "🧮 <b>Calculator</b>\n\n"
            "Usage: /calc &lt;expression&gt;\n\n"
            "Examples:\n"
            "• /calc 2 + 2\n"
            "• /calc sqrt(144)\n"
            "• /calc sin(pi/6)\n"
            "• /calc 2**32\n"
            "• /calc log(1000, 10)",
            parse_mode="HTML",
        )
        return
    loop   = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _calculate, expr)
    is_err = result.startswith("ERROR")
    icon   = "❌" if is_err else "✅"
    await msg.reply_text(
        f"🧮 <b>Calculator</b>\n\n"
        f"<code>{html.escape(expr)}</code>\n"
        f"{icon} <b>{html.escape(result)}</b>",
        parse_mode="HTML",
    )


async def qr_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/qr <text or URL>"""
    msg  = update.effective_message
    text = " ".join(context.args).strip() if context.args else ""
    if not text:
        await msg.reply_text(
            "📱 <b>QR Code Generator</b>\n\n"
            "Usage: /qr &lt;text or URL&gt;\n\n"
            "Examples:\n"
            "• /qr https://t.me/YourChannel\n"
            "• /qr Hello World\n"
            "• /qr +234 800 000 0000",
            parse_mode="HTML",
        )
        return
    if len(text) > 500:
        await msg.reply_text("❌ Text too long (max 500 characters).")
        return
    thinking = await msg.reply_text("📱 <i>Generating QR code…</i>", parse_mode="HTML")
    loop = asyncio.get_running_loop()
    buf  = await loop.run_in_executor(None, _generate_qr, text)
    if not buf:
        await thinking.edit_text(
            "❌ QR code generation unavailable. Install the <code>qrcode[pil]</code> package.",
            parse_mode="HTML",
        )
        return
    try:
        await thinking.delete()
    except Exception:
        pass
    caption = f"📱 QR code for:\n<code>{html.escape(text[:200])}</code>"
    await msg.reply_photo(buf, caption=caption, parse_mode="HTML")


async def crypto_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/crypto [BTC ETH SOL …]"""
    msg     = update.effective_message
    symbols = list(context.args) if context.args else ["BTC", "ETH", "SOL"]
    thinking = await msg.reply_text("₿ <i>Fetching prices…</i>", parse_mode="HTML")
    loop   = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _fetch_crypto, symbols)
    if not result:
        await thinking.edit_text(
            "❌ Crypto data unavailable.\n\n"
            "Examples: /crypto BTC  •  /crypto ETH SOL BNB\n\n"
            f"Supported: {', '.join(_CRYPTO_IDS.keys())}",
        )
        return
    text = _format_crypto(result)
    await thinking.edit_text(text, parse_mode="HTML", disable_web_page_preview=True)


async def wiki_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/wiki <topic>"""
    msg   = update.effective_message
    topic = " ".join(context.args).strip() if context.args else ""
    if not topic:
        await msg.reply_text(
            "📖 <b>Wikipedia</b>\n\nUsage: /wiki &lt;topic&gt;\n\n"
            "Examples:\n• /wiki Artificial Intelligence\n• /wiki Bitcoin\n• /wiki Nigeria",
            parse_mode="HTML",
        )
        return
    thinking = await msg.reply_text("📖 <i>Looking up Wikipedia…</i>", parse_mode="HTML")
    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, _fetch_wiki, topic)
    if not data:
        await thinking.edit_text("❌ Wikipedia lookup failed. Try again.")
        return
    text = _format_wiki(data)
    await thinking.edit_text(text, parse_mode="HTML", disable_web_page_preview=True)


async def news_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/news [topic]"""
    msg   = update.effective_message
    topic = " ".join(context.args).strip() if context.args else ""
    thinking = await msg.reply_text("📰 <i>Fetching latest news…</i>", parse_mode="HTML")
    loop    = asyncio.get_running_loop()
    results = await loop.run_in_executor(None, _fetch_news, topic)
    text    = _format_news(topic, results)
    await thinking.edit_text(text, parse_mode="HTML", disable_web_page_preview=True)


async def currency_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/currency <amount> <FROM> <TO>"""
    msg = update.effective_message
    if not context.args or len(context.args) < 3:
        await msg.reply_text(
            "💱 <b>Currency Converter</b>\n\n"
            "Usage: /currency &lt;amount&gt; &lt;FROM&gt; &lt;TO&gt;\n\n"
            "Examples:\n"
            "• /currency 100 USD EUR\n"
            "• /currency 1 BTC USD\n"
            "• /currency 50000 NGN USD\n"
            "• /currency 200 GBP JPY",
            parse_mode="HTML",
        )
        return
    try:
        amount = float(context.args[0].replace(",", ""))
        from_  = context.args[1].upper()
        to_    = context.args[2].upper()
    except (ValueError, IndexError):
        await msg.reply_text("❌ Usage: /currency 100 USD EUR")
        return
    thinking = await msg.reply_text("💱 <i>Converting…</i>", parse_mode="HTML")
    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, _fetch_currency, amount, from_, to_)
    if not data:
        await thinking.edit_text("❌ Currency data unavailable. Try again.")
        return
    text = _format_currency(amount, from_, data)
    await thinking.edit_text(text, parse_mode="HTML", disable_web_page_preview=True)


async def quote_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/quote"""
    msg      = update.effective_message
    loop     = asyncio.get_running_loop()
    thinking = await msg.reply_text("💬 <i>Finding inspiration…</i>", parse_mode="HTML")
    data     = await loop.run_in_executor(None, _fetch_quote)
    if not data or not data.get("quote"):
        await thinking.edit_text("💬 <i>\"The only way to do great work is to love what you do.\"</i>\n\n— Steve Jobs")
        return
    q = html.escape(data["quote"])
    a = html.escape(data.get("author") or "Unknown")
    await thinking.edit_text(
        f"💬 <i>\"{q}\"</i>\n\n— <b>{a}</b>",
        parse_mode="HTML",
    )
