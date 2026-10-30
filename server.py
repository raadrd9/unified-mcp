import os
import re
import json
import base64
import hashlib
import requests
from typing import Optional
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

mcp = FastMCP("GitHubUnifiedMCP")

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
    """Extract ONLY pure content, author, time, quoted post, and conversation context—ZERO view/like stats."""
    user = getattr(t, "user", None)
    handle = getattr(user, "screen_name", "unknown") if user else "unknown"
    name = getattr(user, "name", handle) if user else handle
    tid = str(getattr(t, "id", ""))
    text = getattr(t, "full_text", None) or getattr(t, "text", "")
    created_at = getattr(t, "created_at", "")
    item = {
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

def _gh_headers(accept: str = "application/vnd.github+json") -> dict:
    headers = {
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Unified-FastMCP-Agent-V3"
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return headers

@mcp.custom_route("/health", methods=["GET", "HEAD"])
async def health_check(request: Request) -> JSONResponse:
    return JSONResponse({"status": "awake", "version": "3.0-native-x"})

# =====================================================================
# أولاً: محرك X (تويتر) الداخلي الحقيقي (بحث لحظي + تايم لاين + ترندات + نقاشات بدون إحصائيات)
# =====================================================================

@mcp.tool(annotations={"readOnlyHint": True})
async def x_search_posts(query: str, mode: str = "Latest", count: int = 20) -> dict:
    """Search real-time X (Twitter) posts, leaks, and community discussions directly inside X.
    mode can be 'Latest' (for breaking news/leaks from the last minutes/hours) or 'Top' (for major posts).
    Returns pure post text and discussions WITHOUT distracting view/like counts."""
    client = _get_twikit_client()
    if client is not None:
        try:
            product = "Latest" if mode.lower() in ("latest", "live", "recent") else "Top"
            tweets = await client.search_tweet(query, product=product, count=min(count, 40))
            posts = [_clean_tweet(t) for t in tweets]
            # إذا لم يجد في Latest نتائج كافية، يدمج معها أهم نتائج Top تلقائياً
            if len(posts) < 5 and product == "Latest":
                top_tweets = await client.search_tweet(query, product="Top", count=15)
                seen_urls = {p["url"] for p in posts}
                for tt in top_tweets:
                    cleaned = _clean_tweet(tt)
                    if cleaned["url"] not in seen_urls:
                        posts.append(cleaned)
            return {
                "instruction_to_ai": "Focus strictly on the content, leaks, technical details, and community opinions in these posts. Do NOT mention view/like statistics.",
                "query": query,
                "search_mode": product,
                "posts_count": len(posts),
                "posts": posts[:count]
            }
        except Exception as exc:
            err_msg = str(exc)
    else:
        err_msg = "Twikit client not initialized"

    # Fallback إذا لزم الأمر
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
    """Browse the live X (Twitter) Home Timeline ('for_you' or 'following') automatically without needing any query, username, or link."""
    client = _get_twikit_client()
    if client is None:
        return {"error": "X account session not configured."}
    try:
        if feed_type.lower() == "following":
            tweets = await client.get_latest_timeline(count=min(count, 40))
        else:
            tweets = await client.get_timeline(count=min(count, 40))
        posts = [_clean_tweet(t) for t in tweets]
        return {
            "instruction_to_ai": "Summarize the latest topics, news, and discussions from the user's X timeline naturally without mentioning statistics.",
            "feed_type": feed_type,
            "posts_count": len(posts),
            "posts": posts[:count]
        }
    except Exception as exc:
        return {"error": str(exc)}

@mcp.tool(annotations={"readOnlyHint": True})
async def x_get_trends(category: str = "trending") -> dict:
    """Get current live Trending topics and breaking news on X (category: 'trending', 'news', 'for-you')."""
    client = _get_twikit_client()
    if client is None:
        return {"error": "X account session not configured."}
    try:
        trends = await client.get_trends(category)
        items = []
        for tr in trends[:25]:
            items.append({
                "name": getattr(tr, "name", ""),
                "domain_context": getattr(tr, "domain_context", "")
            })
        return {"category": category, "trends": items}
    except Exception as exc:
        return {"error": str(exc)}

@mcp.tool(annotations={"readOnlyHint": True})
async def x_get_user_timeline(username: str, max_results: int = 15) -> dict:
    """Read the latest posts and threads from any X account (@username) directly without statistics."""
    handle = username.strip().lstrip("@").split("/")[-1]
    client = _get_twikit_client()
    if client is not None:
        try:
            user = await client.get_user_by_screen_name(handle)
            tweets = await user.get_tweets("Tweets", count=min(max_results, 30))
            posts = [_clean_tweet(t) for t in tweets]
            return {
                "account": f"@{handle} ({getattr(user, 'name', handle)})",
                "bio": getattr(user, "description", ""),
                "posts": posts[:max_results]
            }
        except Exception:
            pass

    v2_url = f"https://api.fxtwitter.com/2/profile/{handle}/statuses"
    r = requests.get(v2_url, headers={"Accept": "application/json", "User-Agent": "Unified-FastMCP/3.0"}, timeout=15)
    if r.status_code == 200:
        raw_list = r.json().get("results") or r.json().get("statuses") or []
        clean_list = [
            {"author": f"@{handle}", "posted_at": item.get("created_at"), "content": item.get("text"), "url": item.get("url")}
            for item in raw_list[:max_results]
        ]
        return {"account": f"@{handle}", "posts": clean_list}
    return {"error": f"Could not load timeline for @{handle}"}

@mcp.tool(annotations={"readOnlyHint": True})
async def x_get_post_with_replies(tweet_url_or_id: str) -> dict:
    """Read a complete X post AND all community replies/comments and discussions under it (without view/like counts)."""
    m = re.search(r"status/(\d+)", tweet_url_or_id)
    tid = m.group(1) if m else re.sub(r"\D", "", tweet_url_or_id)
    if not tid:
        return {"error": "Invalid tweet URL or ID"}

    client = _get_twikit_client()
    if client is not None:
        try:
            tweet = await client.get_tweet_by_id(tid)
            main_post = _clean_tweet(tweet)
            replies_list = []
            if getattr(tweet, "replies", None):
                for rep in tweet.replies:
                    replies_list.append(_clean_tweet(rep))
            return {
                "instruction_to_ai": "Present the main post and synthesize the community discussion/replies. Ignore any numerical stats.",
                "main_post": main_post,
                "replies_count": len(replies_list),
                "community_replies": replies_list[:35]
            }
        except Exception:
            pass

    st_r = requests.get(f"https://api.fxtwitter.com/status/{tid}", timeout=15)
    if st_r.status_code == 200:
        tw = st_r.json().get("tweet", {})
        return {
            "main_post": {
                "author": f"@{tw.get('author', {}).get('screen_name', '')}",
                "posted_at": tw.get("created_at"),
                "content": tw.get("text"),
                "url": tw.get("url")
            }
        }
    return {"error": "Could not fetch tweet details"}

# =====================================================================
# ثانياً: أدوات GitHub الشاملة (قراءة كل مشاريعك الخاصة والعامة + التعديل والحفظ)
# =====================================================================

@mcp.tool(annotations={"readOnlyHint": True})
def github_list_my_repos(visibility: str = "all", sort: str = "updated", max_results: int = 30) -> dict:
    """List ALL repositories belonging to your authenticated GitHub account (including BOTH Private and Public projects)."""
    url = "https://api.github.com/user/repos"
    params = {"visibility": visibility, "sort": sort, "direction": "desc", "per_page": min(max_results, 100), "affiliation": "owner,collaborator,organization_member"}
    r = requests.get(url, headers=_gh_headers(), params=params, timeout=20)
    if r.status_code != 200:
        return {"error": r.status_code, "message": r.text}
    return {
        "repositories": [
            {
                "full_name": repo.get("full_name"),
                "private": repo.get("private"),
                "html_url": repo.get("html_url"),
                "description": repo.get("description"),
                "language": repo.get("language"),
                "default_branch": repo.get("default_branch"),
                "updated_at": repo.get("updated_at")
            }
            for repo in r.json()[:max_results]
        ]
    }

@mcp.tool(annotations={"readOnlyHint": True})
def github_search_repositories(query: str, sort: str = "stars", max_results: int = 10) -> dict:
    """Search GitHub repositories by keyword, topic, or qualifier."""
    url = "https://api.github.com/search/repositories"
    r = requests.get(url, headers=_gh_headers(), params={"q": query, "sort": sort, "order": "desc", "per_page": min(max_results, 30)}, timeout=20)
    if r.status_code != 200:
        return {"error": r.status_code, "message": r.text}
    data = r.json()
    return {
        "total_count": data.get("total_count", 0),
        "repositories": [
            {
                "full_name": repo.get("full_name"),
                "html_url": repo.get("html_url"),
                "description": repo.get("description"),
                "language": repo.get("language"),
                "default_branch": repo.get("default_branch"),
                "updated_at": repo.get("updated_at")
            }
            for repo in data.get("items", [])[:max_results]
        ]
    }

@mcp.tool(annotations={"readOnlyHint": True})
def github_search_code(query: str, max_results: int = 10) -> dict:
    """Search inside source code across GitHub or inside your repositories."""
    url = "https://api.github.com/search/code"
    r = requests.get(url, headers=_gh_headers("application/vnd.github.text-match+json"), params={"q": query, "per_page": min(max_results, 30)}, timeout=20)
    if r.status_code != 200:
        return {"error": r.status_code, "message": r.text}
    data = r.json()
    return {
        "total_count": data.get("total_count", 0),
        "results": [
            {
                "repository": item.get("repository", {}).get("full_name"),
                "path": item.get("path"),
                "html_url": item.get("html_url"),
                "text_matches": [m.get("fragment", "") for m in item.get("text_matches", [])]
            }
            for item in data.get("items", [])[:max_results]
        ]
    }

@mcp.tool(annotations={"readOnlyHint": True})
def github_get_file_or_dir(owner: str, repo: str, path: str = "", ref: Optional[str] = None) -> dict:
    """Read complete file content or list directory contents inside any public or private GitHub repository."""
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path.lstrip('/')}"
    r = requests.get(url, headers=_gh_headers(), params={"ref": ref} if ref else {}, timeout=20)
    if r.status_code != 200:
        return {"error": r.status_code, "message": r.text}
    data = r.json()
    if isinstance(data, list):
        return {"type": "directory", "path": path, "entries": [{"name": e.get("name"), "path": e.get("path"), "type": e.get("type"), "size": e.get("size")} for e in data]}
    content = ""
    if data.get("encoding") == "base64" and data.get("content"):
        content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
    elif data.get("download_url"):
        content = requests.get(data["download_url"], headers=_gh_headers(), timeout=20).text
    return {"type": "file", "path": data.get("path"), "sha": data.get("sha"), "content": content[:100000]}

@mcp.tool(annotations={"readOnlyHint": True})
def github_list_issues_and_prs(owner: str, repo: str, state: str = "open", max_results: int = 15) -> dict:
    """List Issues and Pull Requests in a repository."""
    url = f"https://api.github.com/repos/{owner}/{repo}/issues"
    r = requests.get(url, headers=_gh_headers(), params={"state": state, "per_page": min(max_results, 50)}, timeout=20)
    if r.status_code != 200:
        return {"error": r.status_code, "message": r.text}
    return {
        "items": [
            {
                "number": i.get("number"),
                "type": "pull_request" if "pull_request" in i else "issue",
                "title": i.get("title"),
                "state": i.get("state"),
                "user": i.get("user", {}).get("login"),
                "created_at": i.get("created_at"),
                "body_preview": (i.get("body") or "")[:500]
            }
            for i in r.json()[:max_results]
        ]
    }

@mcp.tool(annotations={"readOnlyHint": True})
def github_get_issue_or_pr_full_thread(owner: str, repo: str, number: int) -> dict:
    """Get full details of an Issue or Pull Request including ALL discussion comments and review comments."""
    issue_url = f"https://api.github.com/repos/{owner}/{repo}/issues/{number}"
    r = requests.get(issue_url, headers=_gh_headers(), timeout=20)
    if r.status_code != 200:
        return {"error": r.status_code, "message": r.text}
    item = r.json()
    comments_r = requests.get(f"{issue_url}/comments", headers=_gh_headers(), params={"per_page": 100}, timeout=20)
    return {
        "number": item.get("number"),
        "title": item.get("title"),
        "state": item.get("state"),
        "author": item.get("user", {}).get("login"),
        "body": item.get("body"),
        "discussion_comments": [
            {"user": c.get("user", {}).get("login"), "created_at": c.get("created_at"), "body": c.get("body")}
            for c in (comments_r.json() if comments_r.status_code == 200 else [])
        ]
    }

@mcp.tool(annotations={"readOnlyHint": True})
def github_get_commits_and_comments(owner: str, repo: str, sha_or_branch: Optional[str] = None, commit_sha: Optional[str] = None, max_results: int = 15) -> dict:
    """List recent commits on a branch OR inspect a specific commit_sha with its diff patches and comments."""
    if commit_sha:
        c_url = f"https://api.github.com/repos/{owner}/{repo}/commits/{commit_sha}"
        r = requests.get(c_url, headers=_gh_headers(), timeout=20)
        if r.status_code != 200:
            return {"error": r.status_code, "message": r.text}
        c_data = r.json()
        return {
            "sha": c_data.get("sha"),
            "message": c_data.get("commit", {}).get("message"),
            "files": [{"filename": f.get("filename"), "status": f.get("status"), "patch": (f.get("patch") or "")[:4000]} for f in c_data.get("files", [])[:30]]
        }
    url = f"https://api.github.com/repos/{owner}/{repo}/commits"
    params = {"per_page": min(max_results, 50)}
    if sha_or_branch:
        params["sha"] = sha_or_branch
    r = requests.get(url, headers=_gh_headers(), params=params, timeout=20)
    if r.status_code != 200:
        return {"error": r.status_code, "message": r.text}
    return {"commits": [{"sha": c.get("sha"), "date": c.get("commit", {}).get("author", {}).get("date"), "message": c.get("commit", {}).get("message")} for c in r.json()[:max_results]]}

@mcp.tool(annotations={"readOnlyHint": True})
def github_get_actions_runs_and_jobs(owner: str, repo: str, run_id: Optional[int] = None, max_results: int = 10) -> dict:
    """List GitHub Actions workflow runs OR inspect jobs for a specific run_id."""
    if run_id:
        r = requests.get(f"https://api.github.com/repos/{owner}/{repo}/actions/runs/{run_id}/jobs", headers=_gh_headers(), timeout=20)
        return r.json() if r.status_code == 200 else {"error": r.status_code, "message": r.text}
    r = requests.get(f"https://api.github.com/repos/{owner}/{repo}/actions/runs", headers=_gh_headers(), params={"per_page": min(max_results, 30)}, timeout=20)
    if r.status_code != 200:
        return {"error": r.status_code, "message": r.text}
    return {"workflow_runs": [{"id": run.get("id"), "name": run.get("name"), "status": run.get("status"), "conclusion": run.get("conclusion"), "created_at": run.get("created_at")} for run in r.json().get("workflow_runs", [])[:max_results]]}

@mcp.tool()
def github_create_or_update_file(owner: str, repo: str, path: str, content: str, commit_message: str, branch: str = "main") -> dict:
    """Create a new file OR edit and save (commit & push) an existing file in any of your GitHub repositories."""
    clean_path = path.lstrip("/")
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{clean_path}"
    existing_sha = None
    get_r = requests.get(url, headers=_gh_headers(), params={"ref": branch}, timeout=20)
    if get_r.status_code == 200 and isinstance(get_r.json(), dict):
        existing_sha = get_r.json().get("sha")
    payload = {"message": commit_message, "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"), "branch": branch}
    if existing_sha:
        payload["sha"] = existing_sha
    put_r = requests.put(url, headers=_gh_headers(), json=payload, timeout=25)
    if put_r.status_code not in (200, 201):
        return {"error": put_r.status_code, "message": put_r.text}
    resp_data = put_r.json()
    return {"status": "updated" if existing_sha else "created", "path": resp_data.get("content", {}).get("path"), "commit_sha": resp_data.get("commit", {}).get("sha")}

@mcp.tool()
def github_delete_file(owner: str, repo: str, path: str, commit_message: str, branch: str = "main") -> dict:
    """Delete a file from a GitHub repository and commit the change."""
    clean_path = path.lstrip("/")
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{clean_path}"
    get_r = requests.get(url, headers=_gh_headers(), params={"ref": branch}, timeout=20)
    if get_r.status_code != 200 or not isinstance(get_r.json(), dict):
        return {"error": get_r.status_code, "message": "File not found"}
    del_r = requests.delete(url, headers=_gh_headers(), json={"message": commit_message, "sha": get_r.json().get("sha"), "branch": branch}, timeout=20)
    return {"status": "deleted", "path": clean_path} if del_r.status_code == 200 else {"error": del_r.status_code, "message": del_r.text}

@mcp.tool()
def github_create_repository(name: str, description: str = "", private: bool = False, auto_init: bool = True) -> dict:
    """Create a brand new GitHub repository (project) in your account (public or private)."""
    r = requests.post("https://api.github.com/user/repos", headers=_gh_headers(), json={"name": name, "description": description, "private": private, "auto_init": auto_init}, timeout=20)
    return r.json() if r.status_code in (200, 201) else {"error": r.status_code, "message": r.text}

@mcp.tool()
def github_create_issue_or_comment(owner: str, repo: str, body: str, title: Optional[str] = None, issue_number: Optional[int] = None) -> dict:
    """Create a new Issue in a repository OR post a comment on an existing Issue / Pull Request."""
    if issue_number is not None:
        r = requests.post(f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}/comments", headers=_gh_headers(), json={"body": body}, timeout=20)
        return {"status": "comment_created", "html_url": r.json().get("html_url")} if r.status_code in (200, 201) else {"error": r.status_code, "message": r.text}
    r = requests.post(f"https://api.github.com/repos/{owner}/{repo}/issues", headers=_gh_headers(), json={"title": title or "New Issue", "body": body}, timeout=20)
    return {"status": "issue_created", "number": r.json().get("number"), "html_url": r.json().get("html_url")} if r.status_code in (200, 201) else {"error": r.status_code, "message": r.text}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    mcp.run(transport="streamable-http", host="0.0.0.0", port=port, path=f"/{SECRET_PATH}/mcp")
