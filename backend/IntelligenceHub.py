import os
import json
import requests
from datetime import datetime
from dotenv import load_dotenv
from agno.agent import Agent
import logging
from agno.os import AgentOS
from agno.models.google import Gemini
from agno.db.postgres import PostgresDb
from agno.memory import MemoryManager
from fastapi.middleware.cors import CORSMiddleware
import feedparser
import re

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

NEWSAPI_API_KEY = os.getenv("NEWSAPI_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
NEON_DB_URL = os.getenv("NEON_DB_URL")
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY")

storage = PostgresDb(db_url=NEON_DB_URL, memory_table="agent_memory")

memory_manager = MemoryManager(
    db=storage,
    model=Gemini(id='gemini-2.5-flash', api_key=GOOGLE_API_KEY),
    additional_instructions="Always store the price and timestamp when fetching crypto prices. DO NOT STORE news articles or any other data.",
)

def get_crypto_price(symbol: str) -> str:
    """
    Fetch the latest USD price of a cryptocurrency from CoinGecko.
    Args:
        symbol (str): The name/id of the crypto (e.g., 'bitcoin', 'ethereum', 'pi-network').
    Returns:
        str: JSON string with price info.
    """
    
    symbol_mappings = {
        'btc': 'bitcoin',
        'eth': 'ethereum',
        'bnb': 'binancecoin',
        'xrp': 'ripple',
        'ada': 'cardano',
        'doge': 'dogecoin',
        'sol': 'solana',
        'dot': 'polkadot',
        'matic': 'matic-network',
        'shib': 'shiba-inu',
        'avax': 'avalanche-2',
        'pi': 'pi-network',
        'trx': 'tron',
        'link': 'chainlink',
        'atom': 'cosmos',
        'uni': 'uniswap',
        'etc': 'ethereum-classic',
        'ltc': 'litecoin',
        'bch': 'bitcoin-cash',
        'xlm': 'stellar',
        'algo': 'algorand',
    }
    
    original_symbol = symbol
    symbol_lower = symbol.lower().strip()
    
    if symbol_lower in symbol_mappings:
        symbol_lower = symbol_mappings[symbol_lower]
    
    symbol_variations = [
        symbol_lower,
        symbol_lower.replace(' ', '-'),
        symbol_lower.replace('-', ''),
        symbol_lower + '-network' if 'network' not in symbol_lower else symbol_lower,
    ]
    
    symbol_variations = list(dict.fromkeys(symbol_variations))
    
    for symbol_id in symbol_variations:
        try:
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={symbol_id}&vs_currencies=usd&x_cg_demo_api_key={COINGECKO_API_KEY}"
            response = requests.get(url, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                price = data.get(symbol_id, {}).get("usd")
                
                if price:
                    return json.dumps({
                        "symbol": original_symbol.upper(),
                        "price_usd": price,
                        "coingecko_id": symbol_id,
                        "timestamp": datetime.now().isoformat()
                    })
        except Exception:
            continue
    
    try:
        search_url = f"https://api.coingecko.com/api/v3/search?query={original_symbol}&x_cg_demo_api_key={COINGECKO_API_KEY}"
        search_response = requests.get(search_url, timeout=10)
        
        if search_response.status_code == 200:
            search_data = search_response.json()
            coins = search_data.get("coins", [])
            
            if coins:
                coin_id = coins[0].get("id")
                coin_name = coins[0].get("name")
                
                price_url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd&x_cg_demo_api_key={COINGECKO_API_KEY}"
                price_response = requests.get(price_url, timeout=10)
                
                if price_response.status_code == 200:
                    price_data = price_response.json()
                    price = price_data.get(coin_id, {}).get("usd")
                    
                    if price:
                        return json.dumps({
                            "symbol": coin_name,
                            "price_usd": price,
                            "coingecko_id": coin_id,
                            "timestamp": datetime.now().isoformat()
                        })
    except Exception as e:
        pass
    
    return json.dumps({
        "error": f"Could not find price for '{original_symbol}'. Please try using the full CoinGecko ID (e.g., 'bitcoin', 'ethereum', 'pi-network').",
        "tried_variations": symbol_variations[:3],
        "suggestion": "You can search for the correct ID at https://www.coingecko.com/"
    })

def get_crypto_news(symbol: str, num_stories: int = 3) -> str:
    """
    Fetch latest crypto news with advanced relevance filtering to avoid false positives.
    """
    stories = []
    seen_urls = set()
    clean_symbol = symbol.replace('-', ' ')
    
    crypto_keywords = [
        'cryptocurrency', 'crypto', 'blockchain', 'token', 'coin',
        'bitcoin', 'ethereum', 'trading', 'exchange', 'wallet',
        'price', 'market', 'defi', 'nft', 'mining', 'staking',
        'bull', 'bear', 'rally', 'dump', 'pump', 'hodl',
        'binance', 'coinbase', 'kraken', 'uniswap', 'dex',
        'altcoin', 'memecoin', 'web3', 'satoshi', 'ledger',
        'buy', 'sell', 'trade', 'invest', 'capitalization'
    ]
    
    false_positive_indicators = [
        'country', 'nation', 'government', 'politics', 'election',
        'tourism', 'geography', 'city', 'capital', 'president',
        'flower', 'plant', 'garden', 'nature', 'species',
        'movie', 'film', 'actor', 'actress', 'director',
        'restaurant', 'food', 'recipe', 'cooking', 'chef'
    ]
    
    def is_crypto_relevant(text: str, symbol: str) -> bool:
        """
        Advanced relevance check for cryptocurrency articles.
        Filters out false positives like "Oasis (country)" vs "Oasis Network (crypto)"
        """
        text_lower = text.lower()
        symbol_lower = symbol.lower()
        clean_symbol_lower = clean_symbol.lower()
        
        symbol_pattern = r"\b" + re.escape(symbol_lower) + r"\b"
        clean_symbol_pattern = r"\b" + re.escape(clean_symbol_lower) + r"\b"
        
        if not (re.search(symbol_pattern, text_lower) or re.search(clean_symbol_pattern, text_lower)):
            return False
        
        symbol_count = len(re.findall(symbol_pattern, text_lower)) + len(re.findall(clean_symbol_pattern, text_lower))
        
        has_crypto_context = any(keyword in text_lower for keyword in crypto_keywords)
        
        has_false_positive = any(indicator in text_lower for indicator in false_positive_indicators)
        
        # Scoring system:
        # High confidence: Multiple symbol mentions OR single mention with strong crypto context
        # Reject: Single mention with false positive indicators and no crypto context
        
        if symbol_count >= 2:
            return True
        
        if symbol_count == 1:
            if has_crypto_context and not has_false_positive:
                return True
            if has_crypto_context and has_false_positive:
                crypto_count = sum(1 for kw in crypto_keywords if kw in text_lower)
                return crypto_count >= 2
            if has_false_positive and not has_crypto_context:
                return False
        
        return False
    
    search_variants = [
        f"{clean_symbol} cryptocurrency price news",
        f"{clean_symbol} crypto token news",
        f"{symbol} blockchain price",
    ]

    try:
        for q in search_variants:
            if len(stories) >= num_stories:
                break

            try:
                feed = feedparser.parse(f'https://news.google.com/rss/search?q={q}&hl=en&gl=US&ceid=US:en')
                
                if not feed or not hasattr(feed, 'entries'):
                    continue
                
                for entry in feed.entries[:num_stories * 4]:  
                    if len(stories) >= num_stories:
                        break

                    url = entry.get("link")
                    if not url or url in seen_urls:
                        continue

                    title = entry.get("title", "No title")
                    source = "Google News"
                    if " - " in title:
                        parts = title.rsplit(" - ", 1)
                        if len(parts) == 2:
                            title, source = parts

                    desc = re.sub("<[^<]+?>", "", entry.get("summary", title))
                    
                    combined_text = f"{title} {desc}"
                    if not is_crypto_relevant(combined_text, symbol):
                        logger.debug(f"Filtered out: {title[:50]}... (not crypto-relevant)")
                        continue
                    
                    if len(desc) > 200:
                        desc = desc[:200] + "..."
                    
                    stories.append({
                        "title": title,
                        "source": source,
                        "url": url,
                        "published_at": entry.get("published", ""),
                        "description": desc
                    })
                    seen_urls.add(url)
                    logger.debug(f"Added: {title[:50]}...")
                
                if stories:
                    break
            except Exception as e:
                logger.error(f"Google News error: {e}")
                continue
    except Exception as e:
        logger.error(f"Google News outer error: {e}")
        pass

    if len(stories) < num_stories and NEWSAPI_API_KEY:
        try:
            q = f'"{symbol}" (cryptocurrency OR crypto OR blockchain OR token)'
            url = (f"https://newsapi.org/v2/everything?"
                   f"q={q}&sortBy=publishedAt&language=en&pageSize=30&apiKey={NEWSAPI_API_KEY}")
            
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                articles = resp.json().get("articles", [])
                for art in articles:
                    if len(stories) >= num_stories:
                        break
                    
                    article_url = art.get("url")
                    if not article_url or article_url in seen_urls:
                        continue
                    
                    desc = art.get("description", "")
                    combined_text = f"{art.get('title', '')} {desc}"
                    
                    if not is_crypto_relevant(combined_text, symbol):
                        continue
                    
                    if desc and len(desc) > 200:
                        desc = desc[:200] + "..."
                    
                    stories.append({
                        "title": art.get("title", "No title"),
                        "source": art.get("source", {}).get("name", "NewsAPI"),
                        "url": article_url,
                        "published_at": art.get("publishedAt", ""),
                        "description": desc if desc else ""
                    })
                    seen_urls.add(article_url)
        except Exception as e:
            logger.error(f"NewsAPI error: {e}")
            pass

    if len(stories) < num_stories:
        try:
            bing_url = f"https://www.bing.com/news/search?q={symbol}+cryptocurrency&format=rss"
            feed = feedparser.parse(bing_url)
            
            if feed and hasattr(feed, 'entries'):
                for entry in feed.entries[:num_stories * 4]:
                    if len(stories) >= num_stories:
                        break
                    
                    url = entry.get("link")
                    if not url or url in seen_urls:
                        continue
                    
                    title = entry.get("title", "No title")
                    desc = re.sub("<[^<]+?>", "", entry.get("summary", ""))
                    
                    combined_text = f"{title} {desc}"
                    if not is_crypto_relevant(combined_text, symbol):
                        continue
                    
                    if len(desc) > 200:
                        desc = desc[:200] + "..."
                    
                    stories.append({
                        "title": title,
                        "source": "Bing News",
                        "url": url,
                        "published_at": entry.get("published", ""),
                        "description": desc
                    })
                    seen_urls.add(url)
        except Exception as e:
            logger.error(f"Bing News error: {e}")
            pass

    if len(stories) < num_stories:
        crypto_feeds = [
            "https://cointelegraph.com/rss",
            "https://news.bitcoin.com/feed/",
            "https://cryptonews.net/en/news/feed/",
            "https://www.coindesk.com/arc/outboundfeeds/rss/"
        ]
        
        for feed_url in crypto_feeds:
            if len(stories) >= num_stories:
                break
            
            try:
                feed = feedparser.parse(feed_url)
                
                if not feed or not hasattr(feed, 'entries'):
                    continue
                
                for entry in feed.entries[:num_stories * 5]:
                    if len(stories) >= num_stories:
                        break
                    
                    entry_link = entry.get("link")
                    if not entry_link or entry_link in seen_urls:
                        continue
                    
                    title = entry.get('title', '')
                    summary = entry.get('summary', '')
                    text = f"{title} {summary}"
                    
                    if is_crypto_relevant(text, symbol):
                        desc = re.sub("<[^<]+?>", "", summary)
                        if len(desc) > 200:
                            desc = desc[:200] + "..."
                        
                        stories.append({
                            "title": title if title else "No title",
                            "source": "Crypto Feed",
                            "url": entry_link,
                            "published_at": entry.get("published", ""),
                            "description": desc
                        })
                        seen_urls.add(entry_link)
            except Exception as e:
                logger.error(f"Crypto feed error: {e}")
                continue

    if not stories:
        return json.dumps({
            "symbol": symbol.upper(),
            "news": [],
            "count": 0,
            "message": f"No recent crypto news found for {symbol}. It might have limited media coverage or be a very new project.",
            "timestamp": datetime.now().isoformat()
        })

    logger.info(f"Found {len(stories)} relevant articles for {symbol}")
    
    return json.dumps({
        "symbol": symbol.upper(),
        "news": stories,
        "count": len(stories),
        "timestamp": datetime.now().isoformat()
    })

instructions = """
You are a Crypto Intelligence Agent with persistent memory.

TOOLS AVAILABLE:
- get_crypto_price(symbol): Fetches current USD price from CoinGecko  
- update_user_memory: Ability to store/retrieve user-specific data from database. Use it to store the price you've just fetched.
- get_crypto_news(symbol, num_stories): Fetches recent news from Google News, NewsAPI, Bing News, and crypto feeds

WORKFLOW:
1. Call get_crypto_price(symbol) once
2. ALWAYS Call update_user_memory to store the price
3. Call get_crypto_news(symbol, num_stories=3) once
4. Format and present the COMPLETE response to the user with price and news
5. That's it - do not add any additional commentary after presenting the information

MEMORY BEHAVIOR:
- Before fetching new data, check if you have past prices for that crypto
- Always compare new prices with previous prices if available
- Always call update_user_memory after fetching price
- When you fetch price data, remember it for future reference SILENTLY

RESPONSE FORMAT:
- Always fetch and provide the current price, store it then fetch 3 recent news stories
- Always mention if this is the first time checking or if price has changed
- Keep responses concise but informative
- Include relevant timestamps
- Stop after presenting the information, do not add extra commentary
### Response Format:

For first-time checks:
"This is the first time I've checked [Crypto Name]. The current price is $[price] USD as of [readable date and time]."

For subsequent checks:
"The current price of [Crypto Name] is $[price] USD as of [readable date and time].
Previously it was $[previous price] at [previous date and time]. The price has [increased/decreased/remained stable]."

NEWS SECTION:
"Here are [number] recent news stories about [Crypto Name]:
* **[Article Title]** ([Source]) - [Readable Date] - [Description]
* **[Article Title]** ([Source]) - [Readable Date] - [Description]
* **[Article Title]** ([Source]) - [Readable Date] - [Description]"
After presenting the price and news, output exactly:
---END---

then STOP.

IMPORTANT:
- Use bullet points (*) for news items
- Bold article titles with **title**
- Never use ISO timestamps in output - convert to readable dates
- After presenting price and news, your task is complete
- Completing the task means you have no need to call any more tools, any more responses, or store any additional data. You must simply STOP.
- Do not generate any text after ---END---
"""

crypto_agent = Agent(
    name="CryptoIntel",
    model=Gemini(id='gemini-2.5-flash', api_key=GOOGLE_API_KEY),
    instructions=instructions,
    tools=[get_crypto_price, get_crypto_news],
    memory_manager=memory_manager,
    db=storage,
    enable_agentic_memory=True,
    enable_user_memories=True,
    markdown=True,
    debug_mode=True,
    stream=False,
)
crypto_agent_pro = Agent(
    name="CryptoIntelPro",
    model=Gemini(
        id='gemini-2.5-pro', 
        api_key=GOOGLE_API_KEY,
        temperature=0.1, 
        top_p=0.8,
        stop_sequences=["---END---", "\n\nIs there"],
    ),
    instructions=instructions,
    tools=[get_crypto_price, get_crypto_news],
    memory_manager=memory_manager,
    db=storage,
    enable_agentic_memory=True,
    enable_user_memories=True,
    markdown=True,
    debug_mode=True,
    stream=False,
)
agent_os = AgentOS(
    os_id="Crypto-Intelligence-Hub",
    agents=[crypto_agent,crypto_agent_pro],
)

app = agent_os.get_app()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://crypto-intelligence-agent.vercel.app",
        "http://localhost:3000",
        "*" 
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if __name__ == "__main__":
    agent_os.serve(app="IntelligenceHub:app", reload=True, host="0.0.0.0", port=int(os.getenv("PORT",1111)))