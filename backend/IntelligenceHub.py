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
        symbol (str): The name/id of the crypto (e.g., 'bitcoin', 'ethereum').

    Returns:
        str: JSON string with price info.
    """
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={symbol.lower()}&vs_currencies=usd&x_cg_demo_api_key={COINGECKO_API_KEY}"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            price = data.get(symbol.lower(), {}).get("usd")
            if price:
                return json.dumps({
                    "symbol": symbol.upper(), 
                    "price_usd": price,
                    "timestamp": datetime.now().isoformat()
                })
            return json.dumps({"error": f"Price not found for {symbol}"})
        return json.dumps({"error": f"API returned status {response.status_code} for {symbol}"})
    except Exception as e:
        return json.dumps({"error": f"Failed to fetch price for {symbol}: {str(e)}"})

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
- get_crypto_news(symbol, num_stories): Fetches recent news from NewsAPI and if failed, fall back to CryptoPanic

WORKFLOW - FOLLOW EXACTLY:
1. When user asks about a cryptocurrency:
   a. Call get_crypto_price(symbol) ONCE
   b. Call get_crypto_news(symbol, num_stories=3) ONCE
   c. Format and present the complete response to the user
   d. Call update_user_memory to store the price
   e. DO NOT generate another response after updating memory
2. After calling update_user_memory, your task is COMPLETE - do not respond again

MEMORY BEHAVIOR:
- You have persistent memory that automatically stores our conversation history
- When you fetch price/news data, always remember the price and = ONLY THE PRICE for future reference
- Before fetching new data, check if you have any past prices about that crypto
- Always compare new prices with any previous prices you remember
- If you have previous data, mention the comparison (price change, time elapsed)

RESPONSE FORMAT:
- Always fetch and provide both current price and 3 recent news stories
- Always mention if this is the first time checking or if price has changed
- Keep responses concise but informative
- Include relevant timestamps

BEHAVIOR:
- Remember all crypto data you've fetched across sessions
- When asked about historical data, recall from your memory
- Always fetch fresh data unless specifically asked for stored data only
- Never provide financial advice - only factual market information

### When presenting cryptocurrency price and news information, you MUST follow this exact format:

For first-time checks:
"This is the first time I've checked [Crypto Name]. The current price of [Crypto Name] is $[price] USD as of [readable date and time]."

For subsequent checks:
"I've checked [Crypto Name] for you. The current price of [Crypto Name] is $[price] USD as of [readable date and time].
I previously noted the price of [Crypto Name] was $[previous price] USD at [previous date and time]. The price has [increased/decreased/remained stable] since then."

NEWS SECTION FORMAT:

If news is available:
"Here are [number] recent news stories about [Crypto Name]:
* **[Article Title]** ([Source]) - [Readable Date] - [Description]
* **[Article Title]** ([Source]) - [Readable Date] - [Description]
* **[Article Title]** ([Source]) - [Readable Date] - [Description]"

IMPORTANT:
- Always use bullet points (*) for news items
- Bold the article titles using **title**
- Include source in parentheses
- Separate title, source, date, and description with " - "
- Keep descriptions concise (around 200 characters max)
- Never use ISO timestamp format in the output - always convert to readable dates
- IMPORTANT: After you call update_user_memory, DO NOT generate another response
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
)
crypto_agent_pro = Agent(
    name="CryptoIntelPro",
    model=Gemini(id='gemini-2.5-pro', api_key=GOOGLE_API_KEY),
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
    agents=[crypto_agent, crypto_agent_pro],
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