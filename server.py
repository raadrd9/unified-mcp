import os
import re
import time
import json
import base64
import hashlib
import asyncio
import requests
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

try:
    from twikit import Client as TwikitClient
except ImportError:
    TwikitClient = None

try:
    from ddgs import DDGS
except ImportError:
    DDGS = None

SECRET_PATH = os.environ.get("MCP_SECRET_PATH", "default-secret-uuid").strip("/")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
ENCRYPTED_X_BLOB = "49sGGjg92w0M46+tnNS3PkW3J1DWodzKay3Pt3hxvsg4AgnzscgoB7ieEBupJqRkzh8ycudmEBS8Hvo4xWtjXymmwQiVS3m+Ocn0SmQrGuoOycBaJAIvy4RZx70X3f3/YwOg325euOD4aRmRKAm6OGJPhUmcGaSzynUdBqYSec+L5LNzxImstUjUG50DYAGaLdpww1Te7Yqnf0Br/qhydhi5fgJC6NNYBbbWDYYZzi6gg+monQAQUCadHBUVfRyB4tIHcNLEAyiIb+x39vwln4pYH2jOxIC1wKaKZmyMmvYZCreICp5eS68ikvQTvBpnxywvBtE="

READ_ONLY_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False
}

X_INSTRUCTIONS = (
    "Provides real-time access to live global posts, breaking news, match predictions, "
    "sports analysis, technology updates, and community discussions on X/Twitter. "
    "Supports lossless compact JSON pagination (page=1, 2, 3...) so you can retrieve unlimited posts and replies across parallel calls."
)

mcp = FastMCP("X Twitter", instructions=X_INSTRUCTIONS)

# ذاكرة كاش سريعة للصفحات (تمنع فقدان أي تغريدة وتمنع تجاوز سقف 42KB للبوابة)
_PAGE_CACHE = {}
MAX_PAGE_CHARS = 40000

def _paginate_items(cache_key: str, items: list, page: int = 1, extra_meta: dict = None) -> str:
    now = time.time()
    # تنظيف الكاش القديم بعد 300 ثانية
    expired = [k for k, v in _PAGE_CACHE.items() if now - v["ts"] > 300]
    for k in expired:
        _PAGE_CACHE.pop(k, None)

    if cache_key in _PAGE_CACHE and page > 1:
        pages = _PAGE_CACHE[cache_key]["pages"]
    else:
        pages = []
        current_page = []
        current_len = 300
        for item in items:
            s = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
            if current_page and (current_len + len(s) > MAX_PAGE_CHARS):
                pages.append(current_page)
                current_page = [item]
                current_len = 300 + len(s)
            else:
                current_page.append(item)
                current_len += len(s) + 1
        if current_page or not pages:
            pages.append(current_page)
        _PAGE_CACHE[cache_key] = {"ts": now, "pages": pages}

    total_pages = len(pages)
    idx = max(0, min(page - 1, total_pages - 1))
    payload = {
        "_schema": {"i": "tweet_id", "a": "author", "t": "posted_at", "c": "content", "q": "quoted_post", "r": "community_replies"},
        "page": idx + 1,
        "total_pages": total_pages,
        "has_more": (idx + 1) < total_pages,
        "next_page": (idx + 2) if (idx + 1) < total_pages else None
    }
    if extra_meta:
        payload.update(extra_meta)
    payload["items"] = pages[idx]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

def _get_x_credentials() -> tuple[str, str]:
    env_auth = os.environ.get("X_AUTH_TOKEN", "").strip()
    env_ct0 = os.environ.get("X_CT0", "").strip()
    if env_auth and env_ct0:
        return env_auth, env_ct0
    if GITHUB_TOKEN and ENCRYPTED_X_BLOB:
        try:
            raw = base64.b64decode(ENCRYPTED_X_BLOB)
            salt, cipher = raw[:16], raw[16:]
            ks = hashlib.pbkdf2_hmac("sha256", GITHUB_TOKEN.encode("utf-8"), salt, 100000, dklen=len(cipher))
            plain = bytes(a ^ b for a, b in zip(cipher, ks)).decode("utf-8")
            d = json.loads(plain)
            return d.get("auth_token", ""), d.get("ct0", "")
        except Exception:
            pass
    return "", ""

_x_client = None

def _get_twikit_client():
    global _x_client
    if _x_client is not None:
        return _x_client
    auth_token, ct0 = _get_x_credentials()
    if not auth_token or not ct0 or TwikitClient is None:
        return None
    try:
        client = TwikitClient("en-US", impersonate="chrome124")
    except TypeError:
        client = TwikitClient("en-US")
    client.set_cookies({"auth_token": auth_token, "ct0": ct0})
    _x_client = client
    return _x_client

def _clean_tweet_compact(t) -> dict:
    user = getattr(t, "user", None)
    handle = getattr(user, "screen_name", "unknown") if user else "unknown"
    name = getattr(user, "name", handle) if user else handle
    tid = str(getattr(t, "id", ""))
    text = getattr(t, "full_text", None) or getattr(t, "text", "")
    created_at = getattr(t, "created_at", "")
    item = {
        "i": tid,
        "a": f"@{handle} ({name})",
        "t": created_at,
        "c": text
    }
    quote = getattr(t, "quote", None)
    if quote:
        q_user = getattr(quote, "user", None)
        q_handle = getattr(q_user, "screen_name", "unknown") if q_user else "unknown"
        q_text = getattr(quote, "full_text", None) or getattr(quote, "text", "")
        item["q"] = {"a": f"@{q_handle}", "c": q_text}
    return item

@mcp.custom_route("/health", methods=["GET", "HEAD"])
async def health_check(request: Request) -> JSONResponse:
    return JSONResponse({"status": "awake", "service": "X Twitter V6 Lossless"})

@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
async def x_deep_search(query: str, include_replies: bool = True, count: int = 25, page: int = 1) -> str:
    """Retrieves real-time global posts, live updates, predictions, leaks, and community discussions on X/Twitter.
    Supports lossless pagination: if has_more is true, call again with page=2, 3, etc. to read more posts without truncation."""
    cache_key = f"xsearch:{query}:{include_replies}:{count}"
    if page > 1 and cache_key in _PAGE_CACHE:
        return _paginate_items(cache_key, [], page=page, extra_meta={"query": query})

    client = _get_twikit_client()
    if client is not None:
        try:
            latest_task = client.search_tweet(query, product="Latest", count=min(count, 30))
            top_task = client.search_tweet(query, product="Top", count=20)
            latest_res, top_res = await asyncio.gather(latest_task, top_task, return_exceptions=True)

            combined = []
            seen_ids = set()
            for batch_label, batch in [("latest", latest_res), ("top", top_res)]:
                if not isinstance(batch, Exception) and batch:
                    for t in batch:
                        cleaned = _clean_tweet_compact(t)
                        tid = cleaned.get("i")
                        if tid and tid not in seen_ids:
                            seen_ids.add(tid)
                            cleaned["f"] = batch_label
                            combined.append((t, cleaned))

            if include_replies and combined:
                async def _fetch_replies_for(tid_str: str):
                    try:
                        full_t = await client.get_tweet_by_id(tid_str)
                        reps = [_clean_tweet_compact(r) for r in (getattr(full_t, "replies", None) or [])[:8]]
                        return tid_str, reps
                    except Exception:
                        return tid_str, []

                target_ids = [c[1]["i"] for c in combined[:4] if c[1].get("i")]
                rep_results = await asyncio.gather(*[_fetch_replies_for(tid) for tid in target_ids])
                rep_map = dict(rep_results)
                for _, item_dict in combined:
                    tid = item_dict.get("i")
                    if tid in rep_map and rep_map[tid]:
                        item_dict["r"] = rep_map[tid]

            final_posts = [c[1] for c in combined[:count]]
            return _paginate_items(cache_key, final_posts, page=page, extra_meta={"query": query, "total_posts": len(final_posts)})
        except Exception as exc:
            err_msg = str(exc)
    else:
        err_msg = "Client not initialized"

    posts = []
    if DDGS is not None:
        try:
            with DDGS() as ddgs:
                for h in list(ddgs.text(f"site:x.com {query}", max_results=count)):
                    posts.append({"t": h.get("title"), "c": h.get("body"), "u": h.get("href")})
        except Exception:
            pass
    return _paginate_items(cache_key, posts, page=page, extra_meta={"query": query, "status_note": err_msg})

@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
async def x_browse_home_feed(feed_type: str = "for_you", count: int = 25, page: int = 1) -> str:
    """Retrieves the latest posts and discussions from the live X home timeline ('for_you' or 'following') with lossless pagination."""
    cache_key = f"xhome:{feed_type}:{count}"
    if page > 1 and cache_key in _PAGE_CACHE:
        return _paginate_items(cache_key, [], page=page, extra_meta={"feed_type": feed_type})
    client = _get_twikit_client()
    if client is None:
        return json.dumps({"error": "X session not configured."}, separators=(",", ":"))
    try:
        tweets = await (client.get_latest_timeline(count=min(count, 40)) if feed_type.lower() == "following" else client.get_timeline(count=min(count, 40)))
        posts = [_clean_tweet_compact(t) for t in tweets]
        return _paginate_items(cache_key, posts, page=page, extra_meta={"feed_type": feed_type})
    except Exception as exc:
        return json.dumps({"error": str(exc)}, separators=(",", ":"))

@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
async def x_get_trends(category: str = "trending") -> str:
    """Retrieves current live trending topics and breaking news categories on X."""
    client = _get_twikit_client()
    if client is None:
        return json.dumps({"error": "X session not configured."}, separators=(",", ":"))
    try:
        trends = await client.get_trends(category)
        return json.dumps({
            "category": category,
            "trends": [{"name": getattr(tr, "name", ""), "context": getattr(tr, "domain_context", "")} for tr in trends[:30]]
        }, ensure_ascii=False, separators=(",", ":"))
    except Exception as exc:
        return json.dumps({"error": str(exc)}, separators=(",", ":"))

@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
async def x_get_user_timeline(username: str, max_results: int = 20, page: int = 1) -> str:
    """Retrieves recent posts and profile information for a specific X account handle."""
    handle = username.strip().lstrip("@").split("/")[-1]
    cache_key = f"xuser:{handle}:{max_results}"
    if page > 1 and cache_key in _PAGE_CACHE:
        return _paginate_items(cache_key, [], page=page, extra_meta={"account": f"@{handle}"})
    client = _get_twikit_client()
    if client is not None:
        try:
            user = await client.get_user_by_screen_name(handle)
            tweets = await user.get_tweets("Tweets", count=min(max_results, 35))
            posts = [_clean_tweet_compact(t) for t in tweets[:max_results]]
            return _paginate_items(cache_key, posts, page=page, extra_meta={"account": f"@{handle}", "bio": getattr(user, "description", "")})
        except Exception:
            pass
    r = requests.get(f"https://api.fxtwitter.com/2/profile/{handle}/statuses", timeout=15)
    if r.status_code == 200:
        raw_list = r.json().get("results") or r.json().get("statuses") or []
        posts = [{"t": i.get("created_at"), "c": i.get("text"), "i": i.get("id")} for i in raw_list[:max_results]]
        return _paginate_items(cache_key, posts, page=page, extra_meta={"account": f"@{handle}"})
    return json.dumps({"error": f"Could not load @{handle}"}, separators=(",", ":"))

@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
async def x_get_post_with_replies(tweet_url_or_id: str, page: int = 1) -> str:
    """Retrieves a specific X post along with its full thread and community replies using lossless pagination."""
    m = re.search(r"status/(\d+)", tweet_url_or_id)
    tid = m.group(1) if m else re.sub(r"\D", "", tweet_url_or_id)
    cache_key = f"xpost:{tid}"
    if page > 1 and cache_key in _PAGE_CACHE:
        return _paginate_items(cache_key, [], page=page, extra_meta={"tweet_id": tid})
    client = _get_twikit_client()
    if client is not None and tid:
        try:
            tweet = await client.get_tweet_by_id(tid)
            main_post = _clean_tweet_compact(tweet)
            reps = [_clean_tweet_compact(rep) for rep in (getattr(tweet, "replies", None) or [])[:50]]
            return _paginate_items(cache_key, reps, page=page, extra_meta={"main_post": main_post, "total_replies": len(reps)})
        except Exception:
            pass
    return json.dumps({"error": "Could not fetch post replies"}, separators=(",", ":"))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    mcp.run(transport="streamable-http", host="0.0.0.0", port=port, path=f"/{SECRET_PATH}/mcp")
