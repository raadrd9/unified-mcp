import os
import re
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

X_INSTRUCTIONS = """
You are connected to X_Live, the real-time X (Twitter) intelligence engine.
AUTOMATIC ROUTING RULES:
- Whenever the user asks about ANY news, leaks, updates, trends, public opinion, community discussions, or posts on X/Twitter (even if they do NOT mention @X_Live or tool names), AUTOMATICALLY call `x_deep_search` or `x_browse_home_feed`.
- NEVER display view counts, like counts, or retweet numbers.
- Format all findings using detailed markdown bullet points (`-`) covering every post, leak, and community comment in depth.
"""

mcp = FastMCP("X_Live", instructions=X_INSTRUCTIONS)

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

def _clean_tweet(t) -> dict:
    user = getattr(t, "user", None)
    handle = getattr(user, "screen_name", "unknown") if user else "unknown"
    name = getattr(user, "name", handle) if user else handle
    tid = str(getattr(t, "id", ""))
    text = getattr(t, "full_text", None) or getattr(t, "text", "")
    created_at = getattr(t, "created_at", "")
    item = {
        "id": tid,
        "author": f"@{handle} ({name})",
        "posted_at": created_at,
        "content": text,
        "url": f"https://x.com/{handle}/status/{tid}" if tid else ""
    }
    quote = getattr(t, "quote", None)
    if quote:
        q_user = getattr(quote, "user", None)
        q_handle = getattr(q_user, "screen_name", "unknown") if q_user else "unknown"
        q_text = getattr(quote, "full_text", None) or getattr(quote, "text", "")
        item["quoted_post"] = {"author": f"@{q_handle}", "content": q_text}
    return item

@mcp.custom_route("/health", methods=["GET", "HEAD"])
async def health_check(request: Request) -> JSONResponse:
    return JSONResponse({"status": "awake", "service": "X_Live"})

@mcp.tool(annotations={"readOnlyHint": True})
async def x_deep_search(query: str, include_replies: bool = True, count: int = 20) -> dict:
    """AUTOMATICALLY use this tool whenever the user asks to search X/Twitter, check breaking news, leaks, recent updates, or see what people are saying about any topic.
    It automatically searches BOTH Latest (real-time) and Top posts AND automatically extracts community replies/comments in a single step!"""
    client = _get_twikit_client()
    if client is not None:
        try:
            latest_task = client.search_tweet(query, product="Latest", count=min(count, 25))
            top_task = client.search_tweet(query, product="Top", count=15)
            latest_res, top_res = await asyncio.gather(latest_task, top_task, return_exceptions=True)

            combined = []
            seen_ids = set()
            for batch_label, batch in [("LATEST_REALTIME", latest_res), ("TOP_HIGHLIGHT", top_res)]:
                if not isinstance(batch, Exception) and batch:
                    for t in batch:
                        cleaned = _clean_tweet(t)
                        tid = cleaned.get("id")
                        if tid and tid not in seen_ids:
                            seen_ids.add(tid)
                            cleaned["feed_source"] = batch_label
                            combined.append((t, cleaned))

            # سحب الردود والتعليقات تلقائياً لأبرز 4 منشورات في نفس الضربة بدون تدخل المستخدم!
            if include_replies and combined:
                async def _fetch_replies_for(tid_str: str):
                    try:
                        full_t = await client.get_tweet_by_id(tid_str)
                        reps = []
                        if getattr(full_t, "replies", None):
                            for r in full_t.replies[:8]:
                                reps.append(_clean_tweet(r))
                        return tid_str, reps
                    except Exception:
                        return tid_str, []

                target_ids = [c[1]["id"] for c in combined[:4] if c[1].get("id")]
                rep_results = await asyncio.gather(*[_fetch_replies_for(tid) for tid in target_ids])
                rep_map = dict(rep_results)
                for _, item_dict in combined:
                    tid = item_dict.get("id")
                    if tid in rep_map and rep_map[tid]:
                        item_dict["community_replies_and_comments"] = rep_map[tid]

            final_posts = [c[1] for c in combined[:count]]
            return {
                "formatting_directive": "Use detailed bullet points (-) for every finding. Focus 100% on post contents, leaks, timestamps, and community replies. NEVER mention view/like counts.",
                "query": query,
                "total_posts_retrieved": len(final_posts),
                "posts_with_embedded_replies": final_posts
            }
        except Exception as exc:
            err_msg = str(exc)
    else:
        err_msg = "Twikit client unavailable"

    posts = []
    if DDGS is not None:
        try:
            with DDGS() as ddgs:
                for h in list(ddgs.text(f"site:x.com {query}", max_results=count)):
                    posts.append({"title": h.get("title"), "content": h.get("body"), "url": h.get("href")})
        except Exception:
            pass
    return {"query": query, "note": err_msg, "posts": posts}

@mcp.tool(annotations={"readOnlyHint": True})
async def x_browse_home_feed(feed_type: str = "for_you", count: int = 20) -> dict:
    """AUTOMATICALLY use this tool when the user asks 'what is new on X', 'browse my timeline', or wants a general briefing from X without a specific topic."""
    client = _get_twikit_client()
    if client is None:
        return {"error": "X session not configured."}
    try:
        tweets = await (client.get_latest_timeline(count=min(count, 35)) if feed_type.lower() == "following" else client.get_timeline(count=min(count, 35)))
        posts = [_clean_tweet(t) for t in tweets]
        return {
            "formatting_directive": "Organize all posts into detailed bullet points (-). Do NOT mention statistics.",
            "feed_type": feed_type,
            "posts": posts[:count]
        }
    except Exception as exc:
        return {"error": str(exc)}

@mcp.tool(annotations={"readOnlyHint": True})
async def x_get_trends(category: str = "trending") -> dict:
    """AUTOMATICALLY use this tool when the user asks about current trends, what is viral, or breaking topics on X."""
    client = _get_twikit_client()
    if client is None:
        return {"error": "X session not configured."}
    try:
        trends = await client.get_trends(category)
        return {"category": category, "trends": [{"name": getattr(tr, "name", ""), "context": getattr(tr, "domain_context", "")} for tr in trends[:25]]}
    except Exception as exc:
        return {"error": str(exc)}

@mcp.tool(annotations={"readOnlyHint": True})
async def x_get_user_timeline(username: str, max_results: int = 15) -> dict:
    """AUTOMATICALLY use this tool when the user asks about a specific X account or person's latest posts."""
    handle = username.strip().lstrip("@").split("/")[-1]
    client = _get_twikit_client()
    if client is not None:
        try:
            user = await client.get_user_by_screen_name(handle)
            tweets = await user.get_tweets("Tweets", count=min(max_results, 30))
            return {"account": f"@{handle}", "bio": getattr(user, "description", ""), "posts": [_clean_tweet(t) for t in tweets[:max_results]]}
        except Exception:
            pass
    r = requests.get(f"https://api.fxtwitter.com/2/profile/{handle}/statuses", timeout=15)
    if r.status_code == 200:
        raw_list = r.json().get("results") or r.json().get("statuses") or []
        return {"account": f"@{handle}", "posts": [{"posted_at": i.get("created_at"), "content": i.get("text"), "url": i.get("url")} for i in raw_list[:max_results]]}
    return {"error": f"Could not load @{handle}"}

@mcp.tool(annotations={"readOnlyHint": True})
async def x_get_post_with_replies(tweet_url_or_id: str) -> dict:
    """Read a specific X post and all community replies/comments under it."""
    m = re.search(r"status/(\d+)", tweet_url_or_id)
    tid = m.group(1) if m else re.sub(r"\D", "", tweet_url_or_id)
    client = _get_twikit_client()
    if client is not None and tid:
        try:
            tweet = await client.get_tweet_by_id(tid)
            reps = [_clean_tweet(rep) for rep in (getattr(tweet, "replies", None) or [])[:35]]
            return {"main_post": _clean_tweet(tweet), "community_replies": reps}
        except Exception:
            pass
    return {"error": "Could not fetch post replies"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    mcp.run(transport="streamable-http", host="0.0.0.0", port=port, path=f"/{SECRET_PATH}/mcp")
