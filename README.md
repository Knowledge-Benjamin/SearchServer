---
title: Rotating SearchServer Proxy
emoji: 🔍
colorFrom: green
colorTo: blue
sdk: docker
app_port: 8080
---

# Enterprise Search Server (Rotating Proxy)

A highly-resilient, world-class FastAPI server that acts as a unified search endpoint for the Truth Graph pipeline. Instead of relying on a single SearXNG instance (which can get IP blocked by upstream engines), this server dynamically fetches active public SearXNG instances globally, scores them, and executes concurrent multi-node queries.

## Features

- **Dynamic Discovery**: Auto-refreshes high-grade public nodes via `searx.space`
- **Concurrent Execution**: Fires searches at 3 nodes simultaneously to guarantee success and low latency.
- **Security**: API key authentication (`HTTPBearer`).
- **Caching**: Local memory caching to prevent redundant upstream hits.
- **Dockerized**: Specifically optimized for Hugging Face Spaces deployment.

## API Usage

### `POST /search`

Execute a search against the rotating proxy.

**Body:**
```json
{
    "query": "OpenAI new models",
    "engines": "google,bing,duckduckgo,wikipedia",
    "time_range": "day",
    "limit": 10
}
```

**Headers:**
`Authorization: Bearer <SEARCH_API_KEY>`

**Response:**
```json
{
    "query": "OpenAI new models",
    "source_node": "distributed_searxng_cloud",
    "results": [
        {
            "url": "https://example.com/news",
            "title": "Example Title",
            "content": "...",
            "engine": "google"
        }
    ]
}
```

### `GET /health`
Returns the status, version, and the current number of active SearXNG nodes in the pool.

## Deployment

Deploy this space to Hugging Face by pushing to the GitHub repository: `https://github.com/Knowledge-Benjamin/SearchServer.git`.

### Environment Variables required in HF Spaces Settings:
- `SEARCH_API_KEY`: A secure generated string matching the one in your Pipeline's `.env`.
