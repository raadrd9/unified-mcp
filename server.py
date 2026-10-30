import os
import re
import base64
import xml.etree.ElementTree as ET
import requests
from typing import Optional
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

try:
    from ddgs import DDGS
except ImportError:
    DDGS = None

SECRET_PATH = os.environ.get("MCP_SECRET_PATH", "default-secret-uuid").strip("/")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()

mcp = FastMCP("GitHubUnifiedMCP")

def _gh_headers(accept: str = "application/vnd.github+json") -> dict:
    headers = {
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Unified-FastMCP-Agent-V2"
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return headers

def _fx_headers() -> dict:
    return {
        "Accept": "application/json",
        "User-Agent": "Unified-FastMCP-Agent/2.0"
    }

@mcp.custom_route("/health", methods=["GET", "HEAD"])
async def health_check(request: Request) -> JSONResponse:
    return JSONResponse({"status": "awake", "version": "2.0"})

# =====================================================================
# أولاً: أدوات منصة X (تويتر) الذاتية (بحث + تصفح حسابات + تعليقات بدون رابط)
# =====================================================================

@mcp.tool(annotations={"readOnlyHint": True})
def x_get_user_timeline(username: str, max_results: int = 15) -> dict:
    """Fetch the latest posts/tweets and profile info of any X (Twitter) account directly by @username without needing any link."""
    handle = username.strip().lstrip("@").split("/")[-1]
    profile_data = {}

    prof_r = requests.get(f"https://api.fxtwitter.com/{handle}", headers=_fx_headers(), timeout=15)
    if prof_r.status_code == 200:
        profile_data = prof_r.json().get("user", {})

    # المحاولة 1: FxTwitter v2 statuses endpoint
    v2_url = f"https://api.fxtwitter.com/2/profile/{handle}/statuses"
    r = requests.get(v2_url, headers=_fx_headers(), params={"count": min(max_results, 30)}, timeout=15)
    if r.status_code == 200:
        data = r.json()
        results = data.get("results") or data.get("statuses") or data.get("tweets") or []
        if results:
            return {"handle": handle, "profile": profile_data, "posts": results[:max_results]}

    # المحاولة 2: FxTwitter RSS Feed
    rss_url = f"https://fxtwitter.com/{handle}/feed.xml"
    rss_r = requests.get(rss_url, headers={"User-Agent": "Unified-FastMCP-Agent/2.0"}, timeout=15)
    if rss_r.status_code == 200 and "<item>" in rss_r.text:
        try:
            root = ET.fromstring(rss_r.text)
            posts = []
            for item in root.findall(".//item")[:max_results]:
                link = item.findtext("link") or ""
                title = item.findtext("title") or ""
                desc = item.findtext("description") or ""
                pub_date = item.findtext("pubDate") or ""
                posts.append({"url": link, "title": title, "content": desc, "published_at": pub_date})
            if posts:
                return {"handle": handle, "profile": profile_data, "posts": posts}
        except Exception:
            pass

    # المحاولة 3: البحث التلقائي عن أحدث منشورات الحساب وسحبها كاملة
    return x_search_posts(f"from:{handle}", max_results=max_results)

@mcp.tool(annotations={"readOnlyHint": True})
def x_search_posts(query: str, max_results: int = 10) -> dict:
    """Autonomously search X (Twitter) posts by keyword, hashtag, or topic without needing a URL, and return full hydrated tweet contents."""
    # المحاولة 1: إذا كان البحث عن حساب معين مثل @username
    clean_q = query.strip()
    if clean_q.startswith("@") and " " not in clean_q:
        return x_get_user_timeline(clean_q, max_results=max_results)

    # المحاولة 2: FxTwitter v2 Search API
    s_url = "https://api.fxtwitter.com/2/search"
    r = requests.get(s_url, headers=_fx_headers(), params={"q": query, "feed": "latest"}, timeout=15)
    if r.status_code == 200:
        data = r.json()
        results = data.get("results") or data.get("statuses") or []
        if results:
            return {"query": query, "source": "fxtwitter_v2_search", "posts": results[:max_results]}

    # المحاولة 3: البحث عبر DDGS عن روابط التغريدات ثم سحب محتواها الكامل تلقائياً عبر FxTwitter
    found_tweet_ids = []
    raw_search_hits = []
    if DDGS is not None:
        try:
            with DDGS() as ddgs:
                ddg_query = f"site:x.com {query}"
                hits = list(ddgs.text(ddg_query, max_results=max_results * 2))
                for h in hits:
                    href = h.get("href", "")
                    raw_search_hits.append({
                        "title": h.get("title"),
                        "url": href,
                        "snippet": h.get("body")
                    })
                    m = re.search(r"(?:x\.com|twitter\.com)/([^/]+)/status/(\d+)", href)
                    if m:
                        tid = m.group(2)
                        if tid not in found_tweet_ids:
                            found_tweet_ids.append((m.group(1), tid))
        except Exception as e:
            raw_search_hits.append({"ddgs_warning": str(e)})

    hydrated_tweets = []
    for handle, tid in found_tweet_ids[:max_results]:
        st_r = requests.get(f"https://api.fxtwitter.com/{handle}/status/{tid}", headers=_fx_headers(), timeout=12)
        if st_r.status_code == 200:
            tw = st_r.json().get("tweet")
            if tw:
                hydrated_tweets.append({
                    "id": tid,
                    "url": tw.get("url") or f"https://x.com/{handle}/status/{tid}",
                    "author": tw.get("author", {}).get("screen_name"),
                    "author_name": tw.get("author", {}).get("name"),
                    "created_at": tw.get("created_at"),
                    "text": tw.get("text"),
                    "likes": tw.get("likes"),
                    "retweets": tw.get("retweets"),
                    "replies": tw.get("replies"),
                    "views": tw.get("views"),
                    "media": tw.get("media"),
                    "quote": tw.get("quote")
                })

    return {
        "query": query,
        "hydrated_posts_count": len(hydrated_tweets),
        "posts": hydrated_tweets,
        "web_search_matches": raw_search_hits[:max_results]
    }

@mcp.tool(annotations={"readOnlyHint": True})
def x_get_post_with_replies(tweet_url_or_id: str) -> dict:
    """Read a complete X (Twitter) post, its unrolled author thread, AND its conversation replies/comments."""
    m = re.search(r"status/(\d+)", tweet_url_or_id)
    tid = m.group(1) if m else re.sub(r"\D", "", tweet_url_or_id)
    if not tid:
        return {"error": "Invalid tweet URL or ID"}

    result = {"tweet_id": tid}

    # 1. سحب المحادثة الكاملة مع الردود والتعليقات (Conversation v2)
    conv_r = requests.get(f"https://api.fxtwitter.com/2/conversation/{tid}", headers=_fx_headers(), timeout=15)
    if conv_r.status_code == 200:
        result["conversation_and_replies"] = conv_r.json()

    # 2. سحب الثريد المتسلسل (Thread v2)
    th_r = requests.get(f"https://api.fxtwitter.com/2/thread/{tid}", headers=_fx_headers(), timeout=15)
    if th_r.status_code == 200:
        result["unrolled_thread"] = th_r.json()

    # 3. سحب تفاصيل التغريدة الأساسية (Status v1)
    st_r = requests.get(f"https://api.fxtwitter.com/status/{tid}", headers=_fx_headers(), timeout=15)
    if st_r.status_code == 200:
        result["tweet_details"] = st_r.json().get("tweet")

    return result

# =====================================================================
# ثانياً: أدوات GitHub الشاملة (رؤية كل مشاريعك الخاصة والعامة + القراءة العميقة)
# =====================================================================

@mcp.tool(annotations={"readOnlyHint": True})
def github_list_my_repos(visibility: str = "all", sort: str = "updated", max_results: int = 30) -> dict:
    """List ALL repositories belonging to your authenticated GitHub account (including BOTH Private and Public projects)."""
    url = "https://api.github.com/user/repos"
    params = {
        "visibility": visibility,
        "sort": sort,
        "direction": "desc",
        "per_page": min(max_results, 100),
        "affiliation": "owner,collaborator,organization_member"
    }
    r = requests.get(url, headers=_gh_headers(), params=params, timeout=20)
    if r.status_code != 200:
        return {"error": r.status_code, "message": r.text}
    repos = []
    for repo in r.json()[:max_results]:
        repos.append({
            "full_name": repo.get("full_name"),
            "private": repo.get("private"),
            "html_url": repo.get("html_url"),
            "description": repo.get("description"),
            "language": repo.get("language"),
            "default_branch": repo.get("default_branch"),
            "stars": repo.get("stargazers_count"),
            "open_issues": repo.get("open_issues_count"),
            "updated_at": repo.get("updated_at"),
            "pushed_at": repo.get("pushed_at")
        })
    return {"count": len(repos), "repositories": repos}

@mcp.tool(annotations={"readOnlyHint": True})
def github_search_repositories(query: str, sort: str = "stars", max_results: int = 10) -> dict:
    """Search GitHub repositories by keyword, topic, or qualifier."""
    url = "https://api.github.com/search/repositories"
    params = {"q": query, "sort": sort, "order": "desc", "per_page": min(max_results, 30)}
    r = requests.get(url, headers=_gh_headers(), params=params, timeout=20)
    if r.status_code != 200:
        return {"error": r.status_code, "message": r.text}
    data = r.json()
    items = []
    for repo in data.get("items", [])[:max_results]:
        items.append({
            "full_name": repo.get("full_name"),
            "private": repo.get("private"),
            "html_url": repo.get("html_url"),
            "description": repo.get("description"),
            "stars": repo.get("stargazers_count"),
            "forks": repo.get("forks_count"),
            "open_issues": repo.get("open_issues_count"),
            "language": repo.get("language"),
            "default_branch": repo.get("default_branch"),
            "updated_at": repo.get("updated_at")
        })
    return {"total_count": data.get("total_count", 0), "repositories": items}

@mcp.tool(annotations={"readOnlyHint": True})
def github_search_code(query: str, max_results: int = 10) -> dict:
    """Search inside source code across GitHub or inside your repositories."""
    url = "https://api.github.com/search/code"
    params = {"q": query, "per_page": min(max_results, 30)}
    r = requests.get(url, headers=_gh_headers("application/vnd.github.text-match+json"), params=params, timeout=20)
    if r.status_code != 200:
        return {"error": r.status_code, "message": r.text}
    data = r.json()
    results = []
    for item in data.get("items", [])[:max_results]:
        matches = [m.get("fragment", "") for m in item.get("text_matches", [])]
        results.append({
            "repository": item.get("repository", {}).get("full_name"),
            "path": item.get("path"),
            "name": item.get("name"),
            "html_url": item.get("html_url"),
            "text_matches": matches
        })
    return {"total_count": data.get("total_count", 0), "results": results}

@mcp.tool(annotations={"readOnlyHint": True})
def github_get_file_or_dir(owner: str, repo: str, path: str = "", ref: Optional[str] = None) -> dict:
    """Read complete file content or list directory contents inside any public or private GitHub repository."""
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path.lstrip('/')}"
    params = {"ref": ref} if ref else {}
    r = requests.get(url, headers=_gh_headers(), params=params, timeout=20)
    if r.status_code != 200:
        return {"error": r.status_code, "message": r.text}
    data = r.json()
    if isinstance(data, list):
        return {
            "type": "directory",
            "path": path,
            "entries": [
                {"name": e.get("name"), "path": e.get("path"), "type": e.get("type"), "size": e.get("size")}
                for e in data
            ]
        }
    content = ""
    if data.get("encoding") == "base64" and data.get("content"):
        try:
            content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        except Exception as exc:
            content = f"Decode error: {exc}"
    elif data.get("download_url"):
        raw_r = requests.get(data["download_url"], headers=_gh_headers(), timeout=20)
        content = raw_r.text[:100000]
    return {
        "type": "file",
        "path": data.get("path"),
        "size": data.get("size"),
        "sha": data.get("sha"),
        "html_url": data.get("html_url"),
        "content": content[:100000]
    }

@mcp.tool(annotations={"readOnlyHint": True})
def github_list_issues_and_prs(owner: str, repo: str, state: str = "open", max_results: int = 15) -> dict:
    """List Issues and Pull Requests in a repository."""
    url = f"https://api.github.com/repos/{owner}/{repo}/issues"
    params = {"state": state, "per_page": min(max_results, 50), "sort": "updated", "direction": "desc"}
    r = requests.get(url, headers=_gh_headers(), params=params, timeout=20)
    if r.status_code != 200:
        return {"error": r.status_code, "message": r.text}
    items = []
    for item in r.json()[:max_results]:
        items.append({
            "number": item.get("number"),
            "type": "pull_request" if "pull_request" in item else "issue",
            "title": item.get("title"),
            "state": item.get("state"),
            "user": item.get("user", {}).get("login"),
            "comments_count": item.get("comments"),
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
            "html_url": item.get("html_url"),
            "body_preview": (item.get("body") or "")[:500]
        })
    return {"owner": owner, "repo": repo, "items": items}

@mcp.tool(annotations={"readOnlyHint": True})
def github_get_issue_or_pr_full_thread(owner: str, repo: str, number: int) -> dict:
    """Get full details of an Issue or Pull Request including ALL discussion comments, review comments, and changed files."""
    issue_url = f"https://api.github.com/repos/{owner}/{repo}/issues/{number}"
    r = requests.get(issue_url, headers=_gh_headers(), timeout=20)
    if r.status_code != 200:
        return {"error": r.status_code, "message": r.text}
    item = r.json()
    is_pr = "pull_request" in item

    comments_r = requests.get(f"{issue_url}/comments", headers=_gh_headers(), params={"per_page": 100}, timeout=20)
    issue_comments = [
        {"id": c.get("id"), "user": c.get("user", {}).get("login"), "created_at": c.get("created_at"), "body": c.get("body")}
        for c in (comments_r.json() if comments_r.status_code == 200 else [])
    ]

    result = {
        "number": item.get("number"),
        "type": "pull_request" if is_pr else "issue",
        "title": item.get("title"),
        "state": item.get("state"),
        "author": item.get("user", {}).get("login"),
        "created_at": item.get("created_at"),
        "body": item.get("body"),
        "html_url": item.get("html_url"),
        "discussion_comments": issue_comments
    }

    if is_pr:
        pr_review_r = requests.get(
            f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}/comments",
            headers=_gh_headers(),
            params={"per_page": 100},
            timeout=20
        )
        if pr_review_r.status_code == 200:
            result["code_review_comments"] = [
                {"user": rc.get("user", {}).get("login"), "path": rc.get("path"), "body": rc.get("body"), "diff_hunk": rc.get("diff_hunk")}
                for rc in pr_review_r.json()
            ]
    return result

@mcp.tool(annotations={"readOnlyHint": True})
def github_get_commits_and_comments(owner: str, repo: str, sha_or_branch: Optional[str] = None, commit_sha: Optional[str] = None, max_results: int = 15) -> dict:
    """List recent commits on a branch OR inspect a specific commit_sha with its diff patches and commit comments."""
    if commit_sha:
        c_url = f"https://api.github.com/repos/{owner}/{repo}/commits/{commit_sha}"
        r = requests.get(c_url, headers=_gh_headers(), timeout=20)
        if r.status_code != 200:
            return {"error": r.status_code, "message": r.text}
        c_data = r.json()
        comm_r = requests.get(f"{c_url}/comments", headers=_gh_headers(), timeout=20)
        comments = comm_r.json() if comm_r.status_code == 200 else []
        return {
            "sha": c_data.get("sha"),
            "author": c_data.get("commit", {}).get("author"),
            "message": c_data.get("commit", {}).get("message"),
            "stats": c_data.get("stats"),
            "files": [{"filename": f.get("filename"), "status": f.get("status"), "patch": (f.get("patch") or "")[:4000]} for f in c_data.get("files", [])[:30]],
            "commit_comments": [{"user": cm.get("user", {}).get("login"), "path": cm.get("path"), "body": cm.get("body")} for cm in comments]
        }

    url = f"https://api.github.com/repos/{owner}/{repo}/commits"
    params = {"per_page": min(max_results, 50)}
    if sha_or_branch:
        params["sha"] = sha_or_branch
    r = requests.get(url, headers=_gh_headers(), params=params, timeout=20)
    if r.status_code != 200:
        return {"error": r.status_code, "message": r.text}
    return {
        "owner": owner,
        "repo": repo,
        "commits": [
            {
                "sha": c.get("sha"),
                "author": c.get("commit", {}).get("author", {}).get("name"),
                "date": c.get("commit", {}).get("author", {}).get("date"),
                "message": c.get("commit", {}).get("message"),
                "html_url": c.get("html_url")
            }
            for c in r.json()[:max_results]
        ]
    }

@mcp.tool(annotations={"readOnlyHint": True})
def github_get_actions_runs_and_jobs(owner: str, repo: str, run_id: Optional[int] = None, max_results: int = 10) -> dict:
    """List GitHub Actions workflow runs for a repository OR inspect jobs and steps for a specific run_id."""
    if run_id:
        jobs_url = f"https://api.github.com/repos/{owner}/{repo}/actions/runs/{run_id}/jobs"
        r = requests.get(jobs_url, headers=_gh_headers(), timeout=20)
        if r.status_code != 200:
            return {"error": r.status_code, "message": r.text}
        return {"run_id": run_id, "jobs": r.json().get("jobs", [])}

    runs_url = f"https://api.github.com/repos/{owner}/{repo}/actions/runs"
    r = requests.get(runs_url, headers=_gh_headers(), params={"per_page": min(max_results, 30)}, timeout=20)
    if r.status_code != 200:
        return {"error": r.status_code, "message": r.text}
    return {
        "owner": owner,
        "repo": repo,
        "workflow_runs": [
            {
                "id": run.get("id"),
                "name": run.get("name"),
                "status": run.get("status"),
                "conclusion": run.get("conclusion"),
                "created_at": run.get("created_at"),
                "html_url": run.get("html_url")
            }
            for run in r.json().get("workflow_runs", [])[:max_results]
        ]
    }

# =====================================================================
# ثالثاً: أدوات التعديل والحفظ والإنشاء في GitHub (Write / Edit / Save Tools)
# =====================================================================

@mcp.tool()
def github_create_or_update_file(owner: str, repo: str, path: str, content: str, commit_message: str, branch: str = "main") -> dict:
    """Create a new file OR edit and save (commit & push) an existing file in any of your GitHub repositories."""
    clean_path = path.lstrip("/")
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{clean_path}"

    # فحص ما إذا كان الملف موجوداً مسبقاً للحصول على الـ SHA الخاص به
    existing_sha = None
    get_r = requests.get(url, headers=_gh_headers(), params={"ref": branch}, timeout=20)
    if get_r.status_code == 200 and isinstance(get_r.json(), dict):
        existing_sha = get_r.json().get("sha")

    encoded_content = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    payload = {
        "message": commit_message,
        "content": encoded_content,
        "branch": branch
    }
    if existing_sha:
        payload["sha"] = existing_sha

    put_r = requests.put(url, headers=_gh_headers(), json=payload, timeout=25)
    if put_r.status_code not in (200, 201):
        return {"error": put_r.status_code, "message": put_r.text}

    resp_data = put_r.json()
    return {
        "status": "updated" if existing_sha else "created",
        "path": resp_data.get("content", {}).get("path"),
        "commit_sha": resp_data.get("commit", {}).get("sha"),
        "html_url": resp_data.get("content", {}).get("html_url")
    }

@mcp.tool()
def github_delete_file(owner: str, repo: str, path: str, commit_message: str, branch: str = "main") -> dict:
    """Delete a file from a GitHub repository and commit the change."""
    clean_path = path.lstrip("/")
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{clean_path}"
    get_r = requests.get(url, headers=_gh_headers(), params={"ref": branch}, timeout=20)
    if get_r.status_code != 200 or not isinstance(get_r.json(), dict):
        return {"error": get_r.status_code, "message": "File not found or path is a directory."}

    sha = get_r.json().get("sha")
    del_r = requests.delete(url, headers=_gh_headers(), json={"message": commit_message, "sha": sha, "branch": branch}, timeout=20)
    if del_r.status_code != 200:
        return {"error": del_r.status_code, "message": del_r.text}
    return {"status": "deleted", "path": clean_path, "commit_sha": del_r.json().get("commit", {}).get("sha")}

@mcp.tool()
def github_create_repository(name: str, description: str = "", private: bool = False, auto_init: bool = True) -> dict:
    """Create a brand new GitHub repository (project) in your account (public or private)."""
    url = "https://api.github.com/user/repos"
    payload = {"name": name, "description": description, "private": private, "auto_init": auto_init}
    r = requests.post(url, headers=_gh_headers(), json=payload, timeout=20)
    if r.status_code not in (200, 201):
        return {"error": r.status_code, "message": r.text}
    data = r.json()
    return {
        "status": "created",
        "full_name": data.get("full_name"),
        "private": data.get("private"),
        "html_url": data.get("html_url"),
        "default_branch": data.get("default_branch")
    }

@mcp.tool()
def github_create_issue_or_comment(owner: str, repo: str, body: str, title: Optional[str] = None, issue_number: Optional[int] = None) -> dict:
    """Create a new Issue in a repository OR post a comment on an existing Issue / Pull Request."""
    if issue_number is not None:
        url = f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}/comments"
        r = requests.post(url, headers=_gh_headers(), json={"body": body}, timeout=20)
        if r.status_code not in (200, 201):
            return {"error": r.status_code, "message": r.text}
        return {"status": "comment_created", "html_url": r.json().get("html_url")}

    url = f"https://api.github.com/repos/{owner}/{repo}/issues"
    r = requests.post(url, headers=_gh_headers(), json={"title": title or "New Issue", "body": body}, timeout=20)
    if r.status_code not in (200, 201):
        return {"error": r.status_code, "message": r.text}
    return {"status": "issue_created", "number": r.json().get("number"), "html_url": r.json().get("html_url")}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=port,
        path=f"/{SECRET_PATH}/mcp"
    )
