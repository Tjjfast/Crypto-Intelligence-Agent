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
    Fetch the latest news about a cryptocurrency from NewsAPI.

    Args:
        symbol (str): The name/id of the crypto (e.g., 'bitcoin', 'ethereum').
        num_stories (int): Number of news stories to fetch.

    Returns:
        str: JSON string of news stories.
    """
    if not NEWSAPI_API_KEY:
        return json.dumps({"error": "No NewsAPI API key found in .env"})

    search_query = f"{symbol} cryptocurrency OR {symbol} crypto OR {symbol} price"
    
    url = (
        f"https://newsapi.org/v2/everything?"
        f"q={search_query}&"
        f"sortBy=publishedAt&"
        f"pageSize={num_stories}&"
        f"apiKey={NEWSAPI_API_KEY}"
    )
    
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            articles = data.get("articles", [])[:num_stories]
            stories = []
            for article in articles:
                stories.append({
                    "title": article.get("title", "No title"),
                    "source": article.get("source", {}).get("name", "Unknown Source"),
                    "url": article.get("url", ""),
                    "published_at": article.get("publishedAt", ""),
                    "description": article.get("description", "")[:200] + "..." if article.get("description") and len(article.get("description", "")) > 200 else article.get("description", "")
                })
            return json.dumps({
                "symbol": symbol.upper(), 
                "news": stories,
                "timestamp": datetime.now().isoformat()
            })
        elif response.status_code == 426:
            return json.dumps({"error": f"NewsAPI upgrade required. Status: {response.status_code}"})
        elif response.status_code == 429:
            return json.dumps({"error": f"NewsAPI rate limit exceeded. Status: {response.status_code}"})
        else:
            return json.dumps({"error": f"NewsAPI returned status {response.status_code} for {symbol}"})
    except Exception as e:
        return json.dumps({"error": f"Failed to fetch news for {symbol}: {str(e)}"})

instructions = """
You are a Crypto Intelligence Agent with persistent memory.

TOOLS AVAILABLE:
- get_crypto_price(symbol): Fetches current USD price from CoinGecko  
- get_crypto_news(symbol, num_stories): Fetches recent news from NewsAPI

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

agent_os = AgentOS(
    os_id="Crypto-Intelligence-Hub",
    agents=[crypto_agent],
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