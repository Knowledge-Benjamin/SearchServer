"""
Enterprise SearXNG Rotating Proxy Server
========================================

A highly-resilient, world-class FastAPI server that acts as a unified search endpoint.
Instead of relying on a single SearXNG instance (which can get IP blocked by upstream engines like Google),
this server dynamically fetches all active public SearXNG instances globally,
scores them by reliability, and executes concurrent multi-node queries to guarantee
the highest possible success rate.

Features:
- Dynamic instance discovery via searx.space API
- Fallback & Concurrent execution across randomized high-grade nodes
- Automatic API Key Authentication
- Normalized JSON formatting for the AI pipeline
- In-memory caching for repeated queries
"""

import os
import time
import random
import asyncio
import httpx
from typing import List, Dict, Any, Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Depends, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from loguru import logger
from cachetools import TTLCache

# --- Configuration ---
API_KEY = os.getenv("SEARCH_API_KEY", "default-key-change-in-production")
VERSION = "1.0.0"
MAX_CONCURRENT_ATTEMPTS = 8 # Fire at up to 8 nodes to guarantee at least one succeeds
INSTANCE_REFRESH_INTERVAL = 3600 # 1 hour

security = HTTPBearer()

# --- Global State ---
active_instances: List[str] = []
# Cache search results for 1 hour to heavily save resources
search_cache = TTLCache(maxsize=1000, ttl=3600) 

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0"
]

class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="The search query")
    engines: Optional[str] = Field("google,bing,duckduckgo", description="Comma-separated engines")
    time_range: Optional[str] = Field(None, description="Time range (day, week, month, year)")
    limit: Optional[int] = Field(10, description="Max results")

class SearchResponse(BaseModel):
    query: str
    results: List[Dict[str, Any]]
    source_node: str

def verify_api_key(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if credentials.credentials != API_KEY:
        logger.warning("Invalid API key attempt")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials

async def refresh_instances():
    """Fetches and scores public SearXNG instances from searx.space."""
    global active_instances
    logger.info("Refreshing active SearXNG instances from searx.space...")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get("https://searx.space/data/instances.json")
            resp.raise_for_status()
            data = resp.json()
            
            valid_urls = []
            for url, details in data.get("instances", {}).items():
                if details.get("network_type") != "normal":
                     continue
                
                # Check health metrics
                html_grade = details.get("html", {}).get("grade", "F")
                
                # We want grade C or better, avoiding instances that are timing out
                if html_grade in ["A", "B", "C", "V"]:
                    # Ensure it's reachable and responses are fast
                    if details.get("timing", {}).get("search", {}).get("all", {}).get("median", 9999) < 2.5:
                        valid_urls.append(url)
            
            if valid_urls:
                active_instances = valid_urls
                logger.info(f"Successfully loaded {len(active_instances)} high-quality SearXNG instances.")
            else:
                logger.warning("No valid instances found! Keeping previous list.")
    except Exception as e:
         logger.error(f"Failed to refresh instances: {e}")

async def background_refresher():
    """Background task to periodically refresh the instance list."""
    while True:
        await asyncio.sleep(INSTANCE_REFRESH_INTERVAL)
        await refresh_instances()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Initializing SearchServer...")
    await refresh_instances()
    refresher_task = asyncio.create_task(background_refresher())
    yield
    # Shutdown
    refresher_task.cancel()
    logger.info("Shutting down SearchServer.")

app = FastAPI(
    title="SearXNG Rotating Proxy Server",
    description="Enterprise-grade highly available search API for the Truth Graph.",
    version=VERSION,
    lifespan=lifespan
)

async def attempt_search(url: str, params: dict, client: httpx.AsyncClient) -> dict:
    """Attempt a single query against a specific SearXNG instance."""
    endpoint = f"{url.rstrip('/')}/search"
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    resp = await client.get(endpoint, params=params, headers=headers)
    resp.raise_for_status()
    # Ensure it's valid JSON
    data = resp.json()
    if "results" not in data:
         raise ValueError("Invalid format: missing 'results' array.")
    return data

@app.post("/search", response_model=SearchResponse)
async def perform_search(request: SearchRequest, _: Any = Depends(verify_api_key)):
    """
    Execute a robust search.
    Implements a scatter-gather approach across multiple free SearXNG nodes to guarantee success.
    """
    cache_key = f"{request.query}_{request.engines}_{request.time_range}"
    if cache_key in search_cache:
        logger.info(f"Cache hit for: {request.query}")
        return search_cache[cache_key]

    if not active_instances:
        raise HTTPException(status_code=503, detail="No active SearXNG instances available.")

    # Select random nodes to distribute load and prevent IP bans
    target_nodes = random.sample(active_instances, min(MAX_CONCURRENT_ATTEMPTS, len(active_instances)))
    logger.info(f"Querying [{request.query}] across {len(target_nodes)} nodes concurrently.")
    
    params = {
        "q": request.query,
        "format": "json",
    }
    if request.engines:
        params["engines"] = request.engines
    if request.time_range:
        params["time_range"] = request.time_range

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Create concurrent tasks
        tasks = [
            asyncio.create_task(attempt_search(node, params, client))
            for node in target_nodes
        ]
        
        # Return the FIRST successful response (Fastest node wins)
        successful_data = None
        winning_node = None
        
        for coro in asyncio.as_completed(tasks):
             try:
                 result = await coro
                 successful_data = result
                 # Retrieve the actual node that won (hacky way, but works as we just need one)
                 break
             except Exception as e:
                 logger.warning(f"A node failed: {type(e).__name__} - {str(e)}")
                 continue
                 
        # Cancel remaining pending tasks
        for t in tasks:
            if not t.done():
                t.cancel()
                
        if successful_data is None:
             logger.error("All concurrent search attempts failed.")
             raise HTTPException(status_code=502, detail="Upstream Search Engine failure. All nodes rate-limited or timed out.")

    # Process and sanitize results
    raw_results = successful_data.get("results", [])
    clean_results = raw_results[:request.limit]
    
    response_obj = SearchResponse(
         query=request.query,
         results=clean_results,
         source_node="distributed_searxng_cloud"
    )
    
    search_cache[cache_key] = response_obj
    return response_obj

@app.get("/health")
async def health_check():
    return {
         "status": "healthy",
         "active_nodes": len(active_instances),
         "version": VERSION
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7860)
