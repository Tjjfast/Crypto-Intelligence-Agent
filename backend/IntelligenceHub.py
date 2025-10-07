import os
import json
import requests
from datetime import datetime
from dotenv import load_dotenv
from agno.agent import Agent
from agno.os import AgentOS
from agno.models.google import Gemini
from agno.db.postgres import PostgresDb
from agno.memory import MemoryManager
from fastapi.middleware.cors import CORSMiddleware
import feedparser
import re

load_dotenv()

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
    
    # Common symbol variations/mappings
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
    
    # Normalize symbol
    original_symbol = symbol
    symbol_lower = symbol.lower().strip()
    
    # Try mapped ID first
    if symbol_lower in symbol_mappings:
        symbol_lower = symbol_mappings[symbol_lower]
    
    # Try variations
    symbol_variations = [
        symbol_lower,
        symbol_lower.replace(' ', '-'),
        symbol_lower.replace('-', ''),
        symbol_lower + '-network' if 'network' not in symbol_lower else symbol_lower,
    ]
    
    # Remove duplicates while preserving order
    symbol_variations = list(dict.fromkeys(symbol_variations))
    
    # Try each variation
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
    
    # If all variations fail, try searching for the coin
    try:
        search_url = f"https://api.coingecko.com/api/v3/search?query={original_symbol}&x_cg_demo_api_key={COINGECKO_API_KEY}"
        search_response = requests.get(search_url, timeout=10)
        
        if search_response.status_code == 200:
            search_data = search_response.json()
            coins = search_data.get("coins", [])
            
            if coins:
                # Get the first match
                coin_id = coins[0].get("id")
                coin_name = coins[0].get("name")
                
                # Now fetch the price with the correct ID
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
    
    # If everything fails, return error with suggestions
    return json.dumps({
        "error": f"Could not find price for '{original_symbol}'. Please try using the full CoinGecko ID (e.g., 'bitcoin', 'ethereum', 'pi-network').",
        "tried_variations": symbol_variations[:3],
        "suggestion": "You can search for the correct ID at https://www.coingecko.com/"
    })

def get_crypto_news(symbol: str, num_stories: int = 3) -> str:
    """
    Fetch latest crypto news from multiple sources (Google News, NewsAPI, Bing News, and crypto feeds).
    """
    stories = []
    seen_urls = set()
    clean_symbol = symbol.replace('-', ' ')
    
    search_variants = [
        f"{clean_symbol} cryptocurrency",
        f"{clean_symbol} crypto",
        f"{symbol} coin",
        f"{symbol} token",
        f"{symbol} blockchain"
    ]

    try:
        for q in search_variants:
            if len(stories) >= num_stories:
                break

            try:
                feed = feedparser.parse(f'https://news.google.com/rss/search?q={q}&hl=en&gl=US&ceid=US:en')
                
                if not feed or not hasattr(feed, 'entries'):
                    continue
                
                for entry in feed.entries[:num_stories * 2]:
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
                
                if stories:
                    break
            except Exception as e:
                continue
    except Exception as e:
        pass

    if len(stories) < num_stories and NEWSAPI_API_KEY:
        try:
            q = f"{symbol} cryptocurrency OR {symbol} crypto"
            url = (f"https://newsapi.org/v2/everything?"
                   f"q={q}&sortBy=publishedAt&language=en&pageSize=20&apiKey={NEWSAPI_API_KEY}")
            
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
            pass

    if len(stories) < num_stories:
        try:
            bing_url = f"https://www.bing.com/news/search?q={symbol}+cryptocurrency&format=rss"
            feed = feedparser.parse(bing_url)
            
            if feed and hasattr(feed, 'entries'):
                for entry in feed.entries[:num_stories * 2]:
                    if len(stories) >= num_stories:
                        break
                    
                    url = entry.get("link")
                    if not url or url in seen_urls:
                        continue
                    
                    title = entry.get("title", "No title")
                    desc = re.sub("<[^<]+?>", "", entry.get("summary", ""))
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
                
                for entry in feed.entries[:num_stories * 3]:
                    if len(stories) >= num_stories:
                        break
                    
                    entry_link = entry.get("link")
                    if not entry_link or entry_link in seen_urls:
                        continue
                    
                    text = f"{entry.get('title', '')} {entry.get('summary', '')}".lower()
                    
                    if symbol.lower() in text:
                        desc = re.sub("<[^<]+?>", "", entry.get("summary", ""))
                        if len(desc) > 200:
                            desc = desc[:200] + "..."
                        
                        stories.append({
                            "title": entry.get("title", "No title"),
                            "source": "Crypto Feed",
                            "url": entry_link,
                            "published_at": entry.get("published", ""),
                            "description": desc
                        })
                        seen_urls.add(entry_link)
            except Exception as e:
                continue

    if not stories:
        return json.dumps({
            "symbol": symbol.upper(),
            "news": [],
            "count": 0,
            "message": f"No recent news found for {symbol}. It might have limited media coverage.",
            "timestamp": datetime.now().isoformat()
        })

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
- get_crypto_news(symbol, num_stories): Fetches recent news from Google News, NewsAPI, Bing News, and crypto feeds

WORKFLOW:
1. Call get_crypto_price(symbol) once
2. Call get_crypto_news(symbol, num_stories=3) once
3. Format and present the COMPLETE response to the user with price and news
4. That's it - do not add any additional commentary after presenting the information

MEMORY BEHAVIOR:
- Before fetching new data, check if you have past prices for that crypto
- Always compare new prices with previous prices if available
- Your memory system automatically stores conversation history
- When you fetch price data, the system will remember it for future reference

RESPONSE FORMAT:
- Always fetch and provide both current price and 3 recent news stories
- Always mention if this is the first time checking or if price has changed
- Keep responses concise but informative
- Include relevant timestamps

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

IMPORTANT:
- Use bullet points (*) for news items
- Bold article titles with **title**
- Never use ISO timestamps in output - convert to readable dates
- After presenting price and news, your task is complete
After presenting the price and news, output exactly:
---END---

Do not generate any text after ---END---
"""

crypto_agent = Agent(
    name="CryptoIntel",
    model=Gemini(id='gemini-2.5-flash', api_key=GOOGLE_API_KEY, stop_sequences=["---END---", "\n\nIs there"],),
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