import os
import re
import time
import json
import html
import xml.etree.ElementTree as ET
import requests
from bs4 import BeautifulSoup
from fastmcp import FastMCP

try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    cffi_requests = None

try:
    from ddgs import DDGS
except ImportError:
    DDGS = None

READ_ONLY_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False
}

OMNI_INSTRUCTIONS = (
    "Omni Search provides deep, real-time extraction across Reddit (posts and full comment trees), "
    "YouTube (full timestamped transcripts via ANDROID_VR, descriptions, and viewer comments), "
    "TikTok (video search, metadata, and comments), Telegram public channels (t.me/s/ posts, links, and files), "
    "Frida CodeShare, StackOverflow/StackExchange, Hacker News, and Android APK releases (GitHub Releases & IzzyOnDroid). "
    "Supports lossless pagination (page=1, 2, 3...) for unlimited reading depth."
)

mcp = FastMCP("Omni Search", instructions=OMNI_INSTRUCTIONS)

_PAGE_CACHE = {}
MAX_PAGE_CHARS = 40000

def _paginate_items(cache_key: str, items: list, page: int = 1, extra_meta: dict = None) -> str:
    now = time.time()
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
        "page": idx + 1,
        "total_pages": total_pages,
        "has_more": (idx + 1) < total_pages,
        "next_page": (idx + 2) if (idx + 1) < total_pages else None
    }
    if extra_meta:
        payload.update(extra_meta)
    payload["items"] = pages[idx]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

def _http_get(url: str, params: dict = None, headers: dict = None, timeout: int = 18):
    hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}
    if headers:
        hdrs.update(headers)
    if cffi_requests is not None:
        try:
            return cffi_requests.get(url, params=params, headers=hdrs, impersonate="chrome124", timeout=timeout)
        except Exception:
            pass
    return requests.get(url, params=params, headers=hdrs, timeout=timeout)

# =====================================================================
# 1. محرك Reddit المعماري الثلاثي (Arctic Shift + PullPush — مقاوم لحظر 403 السحابي)
# =====================================================================

@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def reddit_deep_search(query: str, subreddit: str = "", sort: str = "desc", limit: int = 40, page: int = 1) -> str:
    """Searches Reddit posts and discussions across all subreddits or a specific subreddit using datacenter-safe Arctic Shift and PullPush APIs.
    Returns post IDs, titles, full selftext, subreddit, and comment counts with lossless pagination."""
    clean_sub = subreddit.strip().lstrip("r/").strip("/")
    cache_key = f"rsearch:{query}:{clean_sub}:{sort}:{limit}"
    if page > 1 and cache_key in _PAGE_CACHE:
        return _paginate_items(cache_key, [], page=page, extra_meta={"query": query, "subreddit": clean_sub})

    posts = []
    seen_ids = set()

    # المسار 1: Arctic Shift API
    try:
        as_params = {"query": query, "limit": min(limit, 100), "sort": sort}
        if clean_sub and clean_sub.lower() != "all":
            as_params["subreddit"] = clean_sub
        r = _http_get("https://arctic-shift.photon-reddit.com/api/posts/search", params=as_params, timeout=15)
        if r.status_code == 200:
            for p in r.json().get("data", []):
                pid = p.get("id")
                if pid and pid not in seen_ids:
                    seen_ids.add(pid)
                    posts.append({
                        "id": pid,
                        "sub": f"r/{p.get('subreddit', '')}",
                        "a": p.get("author", ""),
                        "title": p.get("title", ""),
                        "text": (p.get("selftext") or "")[:3000],
                        "comments": p.get("num_comments", 0),
                        "url": f"https://www.reddit.com/r/{p.get('subreddit', '')}/comments/{pid}/"
                    })
    except Exception:
        pass

    # المسار 2: PullPush API (لضمان التغطية الكاملة)
    if len(posts) < 15:
        try:
            pp_params = {"q": query, "size": min(limit, 50)}
            if clean_sub and clean_sub.lower() != "all":
                pp_params["subreddit"] = clean_sub
            r2 = _http_get("https://api.pullpush.io/reddit/search/submission/", params=pp_params, timeout=15)
            if r2.status_code == 200:
                for p in r2.json().get("data", []):
                    pid = p.get("id")
                    if pid and pid not in seen_ids:
                        seen_ids.add(pid)
                        posts.append({
                            "id": pid,
                            "sub": f"r/{p.get('subreddit', '')}",
                            "a": p.get("author", ""),
                            "title": p.get("title", ""),
                            "text": (p.get("selftext") or "")[:3000],
                            "comments": p.get("num_comments", 0),
                            "url": f"https://www.reddit.com/r/{p.get('subreddit', '')}/comments/{pid}/"
                        })
        except Exception:
            pass

    return _paginate_items(cache_key, posts, page=page, extra_meta={"query": query, "total_posts": len(posts)})

@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def reddit_get_full_thread(post_id_or_url: str, page: int = 1) -> str:
    """Retrieves a Reddit submission AND its full un-truncated comment tree using Arctic Shift and PullPush APIs with lossless 40KB pagination."""
    m = re.search(r"comments/([a-z0-9]+)", post_id_or_url)
    pid = m.group(1) if m else post_id_or_url.strip().replace("t3_", "")
    cache_key = f"rthread:{pid}"
    if page > 1 and cache_key in _PAGE_CACHE:
        return _paginate_items(cache_key, [], page=page, extra_meta={"post_id": pid})

    post_meta = {"post_id": pid}
    comments = []
    seen_cids = set()

    # سحب بيانات المنشور الأصلي
    try:
        pr = _http_get(f"https://arctic-shift.photon-reddit.com/api/posts/ids?ids={pid}", timeout=12)
        if pr.status_code == 200 and pr.json().get("data"):
            p0 = pr.json()["data"][0]
            post_meta = {
                "post_id": pid,
                "sub": f"r/{p0.get('subreddit', '')}",
                "author": p0.get("author", ""),
                "title": p0.get("title", ""),
                "body": p0.get("selftext", "")
            }
    except Exception:
        pass

    # سحب شجرة التعليقات من Arctic Shift
    try:
        cr = _http_get("https://arctic-shift.photon-reddit.com/api/comments/search", params={"link_id": f"t3_{pid}", "limit": 100, "sort": "desc"}, timeout=15)
        if cr.status_code == 200:
            for c in cr.json().get("data", []):
                cid = c.get("id")
                body = c.get("body", "")
                if cid and cid not in seen_cids and body not in ("[deleted]", "[removed]", ""):
                    seen_cids.add(cid)
                    comments.append({"id": cid, "parent": c.get("parent_id", ""), "a": c.get("author", ""), "c": body})
    except Exception:
        pass

    # مسار PullPush الاحتياطي للتعليقات
    if len(comments) < 10:
        try:
            cr2 = _http_get("https://api.pullpush.io/reddit/comment/search/", params={"link_id": pid, "size": 100}, timeout=15)
            if cr2.status_code == 200:
                for c in cr2.json().get("data", []):
                    cid = c.get("id")
                    body = c.get("body", "")
                    if cid and cid not in seen_cids and body not in ("[deleted]", "[removed]", ""):
                        seen_cids.add(cid)
                        comments.append({"id": cid, "a": c.get("author", ""), "c": body})
        except Exception:
            pass

    return _paginate_items(cache_key, comments, page=page, extra_meta={"post": post_meta, "total_comments": len(comments)})

# =====================================================================
# 2. محرك YouTube الخارق (Innertube ANDROID_VR Quest 3: النص المفرغ + الوصف + التعليقات بدون حظر IP)
# =====================================================================

def _extract_yt_id(url_or_id: str) -> str:
    m = re.search(r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})", url_or_id)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url_or_id.strip()):
        return url_or_id.strip()
    return ""

def _fetch_yt_comments_innertube(video_id: str, max_comments: int = 40) -> list:
    """Fetches top YouTube viewer comments and pinned comments directly via Innertube /youtubei/v1/next without requiring an API key."""
    web_ctx = {"client": {"clientName": "WEB", "clientVersion": "2.20261028.01.00", "hl": "en", "gl": "US"}}
    try:
        r1 = requests.post(
            "https://www.youtube.com/youtubei/v1/next?prettyPrint=false",
            json={"context": web_ctx, "videoId": video_id},
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0"},
            timeout=15
        )
        if r1.status_code != 200:
            return []
        text1 = r1.text
        tokens = re.findall(r'"continuationCommand":\{"token":"([^"]+)"', text1)
        if not tokens:
            return []
        # الرمز الأخير عادة هو رمز تحميل قسم التعليقات
        comment_token = tokens[-1]
        r2 = requests.post(
            "https://www.youtube.com/youtubei/v1/next?prettyPrint=false",
            json={"context": web_ctx, "continuation": comment_token},
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0"},
            timeout=15
        )
        if r2.status_code != 200:
            return []
        data2 = r2.json()
        comments = []
        mutations = data2.get("frameworkUpdates", {}).get("entityBatchUpdate", {}).get("mutations", [])
        for mut in mutations:
            payload = mut.get("payload", {}).get("commentEntityPayload", {})
            if payload:
                props = payload.get("properties", {})
                author = payload.get("author", {}).get("displayName", "")
                content = props.get("content", {}).get("content", "")
                pub_time = props.get("publishedTime", "")
                if content:
                    comments.append({"a": author, "t": pub_time, "c": content})
        if comments:
            return comments[:max_comments]
    except Exception:
        pass
    return []

@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def youtube_deep_inspect(video_url_or_query: str, lang: str = "en", page: int = 1) -> str:
    """Inspects a YouTube video (or searches YouTube if given keywords) using Innertube ANDROID_VR spoofing.
    Extracts full video description, links, full timestamped transcript/captions (bypassing cloud IP blocks), and top viewer comments."""
    vid = _extract_yt_id(video_url_or_query)
    cache_key = f"yt:{video_url_or_query}:{lang}"
    if page > 1 and cache_key in _PAGE_CACHE:
        return _paginate_items(cache_key, [], page=page, extra_meta={"target": video_url_or_query})

    # إذا أعطى المستخدم كلمات بحث بدلاً من رابط فيديو، نبحث عن أفضل الفيديوهات ونفحص الأول تلقائياً
    search_results = []
    if not vid and DDGS is not None:
        try:
            with DDGS() as ddgs:
                for h in list(ddgs.text(f"site:youtube.com/watch {video_url_or_query}", max_results=8)):
                    href = h.get("href", "")
                    found_id = _extract_yt_id(href)
                    search_results.append({"video_id": found_id, "title": h.get("title"), "url": href, "snippet": h.get("body")})
                    if not vid and found_id:
                        vid = found_id
        except Exception:
            pass

    if not vid:
        return json.dumps({"error": "No valid YouTube video found", "search_results": search_results}, ensure_ascii=False, separators=(",", ":"))

    vr_headers = {
        "Content-Type": "application/json",
        "User-Agent": "com.google.android.apps.youtube.vr.oculus/1.71.26 (Linux; U; Android 12L; eureka-user Build/SQ3A.220605.009.A1) gzip",
        "X-YouTube-Client-Name": "28",
        "X-YouTube-Client-Version": "1.71.26"
    }
    vr_payload = {
        "context": {
            "client": {
                "clientName": "ANDROID_VR",
                "clientVersion": "1.71.26",
                "deviceMake": "Oculus",
                "deviceModel": "Quest 3",
                "androidSdkVersion": 32,
                "osName": "Android",
                "osVersion": "12L",
                "hl": lang,
                "gl": "US"
            }
        },
        "videoId": vid
    }

    r = requests.post("https://www.youtube.com/youtubei/v1/player?prettyPrint=false", headers=vr_headers, json=vr_payload, timeout=18)
    if r.status_code != 200:
        return json.dumps({"error": f"Player HTTP {r.status_code}", "search_results": search_results}, ensure_ascii=False, separators=(",", ":"))

    pdata = r.json()
    vdetails = pdata.get("videoDetails", {})
    meta = {
        "video_id": vid,
        "url": f"https://www.youtube.com/watch?v={vid}",
        "title": vdetails.get("title", ""),
        "channel": vdetails.get("author", ""),
        "length_seconds": vdetails.get("lengthSeconds", ""),
        "description": (vdetails.get("shortDescription") or "")[:4000]
    }
    if search_results:
        meta["related_videos"] = search_results[:5]

    # استخراج النص المفرغ (Transcript) عبر رابط ANDROID_VR الموقع
    transcript_segments = []
    tracks = pdata.get("captions", {}).get("playerCaptionsTracklistRenderer", {}).get("captionTracks", [])
    if tracks:
        chosen_track = tracks[0]
        for tr in tracks:
            if tr.get("languageCode", "").startswith(lang):
                chosen_track = tr
                break
        base_url = re.sub(r"&fmt=[^&]+", "", chosen_track.get("baseUrl", ""))
        if base_url:
            cap_r = requests.get(base_url, headers={"User-Agent": vr_headers["User-Agent"]}, timeout=15)
            if cap_r.status_code == 200 and cap_r.text:
                try:
                    root = ET.fromstring(cap_r.text)
                    for node in root.findall(".//text"):
                        start_sec = float(node.attrib.get("start", "0"))
                        mm, ss = divmod(int(start_sec), 60)
                        clean_txt = html.unescape("".join(node.itertext())).replace("
", " ").strip()
                        if clean_txt:
                            transcript_segments.append({"t": f"{mm:02d}:{ss:02d}", "c": clean_txt})
                except Exception:
                    pass

    # استخراج تعليقات المشاهدين تحت الفيديو
    comments = _fetch_yt_comments_innertube(vid, max_comments=40)

    combined_items = []
    if comments:
        combined_items.append({"section": "VIEWER_COMMENTS", "count": len(comments), "comments": comments})
    if transcript_segments:
        # دمج كل 6 جمل متتالية لتقليل حجم الهياكل مع الحفاظ على التوقيت والنص الكامل 100%
        chunked_transcript = []
        for i in range(0, len(transcript_segments), 6):
            batch = transcript_segments[i:i+6]
            chunked_transcript.append({
                "time": batch[0]["t"],
                "text": " ".join(x["c"] for x in batch)
            })
        combined_items.extend(chunked_transcript)

    return _paginate_items(cache_key, combined_items, page=page, extra_meta=meta)

# =====================================================================
# 3. محرك TikTok (البحث + تفاصيل الفيديو + تعليقات الناس عبر بوابة tikwm المجانية)
# =====================================================================

@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def tiktok_deep_inspect(query_or_url: str, page: int = 1) -> str:
    """Searches TikTok videos by keyword OR inspects a specific TikTok video URL including its full caption and top viewer comments."""
    cache_key = f"tiktok:{query_or_url}"
    if page > 1 and cache_key in _PAGE_CACHE:
        return _paginate_items(cache_key, [], page=page, extra_meta={"query": query_or_url})

    items = []
    if "tiktok.com" in query_or_url:
        try:
            r = _http_get("https://www.tikwm.com/api/", params={"url": query_or_url}, timeout=15)
            if r.status_code == 200 and r.json().get("data"):
                d = r.json()["data"]
                vid_id = d.get("id", "")
                video_info = {
                    "id": vid_id,
                    "author": f"@{d.get('author', {}).get('unique_id', '')}",
                    "title": d.get("title", ""),
                    "duration": d.get("duration", 0),
                    "play_url": d.get("play", ""),
                    "music_url": d.get("music", "")
                }
                comments = []
                cr = _http_get("https://www.tikwm.com/api/comment/list", params={"url": query_or_url, "count": 40}, timeout=15)
                if cr.status_code == 200 and cr.json().get("data", {}).get("comments"):
                    for c in cr.json()["data"]["comments"]:
                        comments.append({"a": f"@{c.get('user', {}).get('unique_id', '')}", "c": c.get("text", "")})
                video_info["viewer_comments"] = comments
                items.append(video_info)
        except Exception as exc:
            items.append({"error": str(exc)})
    else:
        try:
            sr = _http_get("https://www.tikwm.com/api/feed/search", params={"keywords": query_or_url, "count": 20}, timeout=15)
            if sr.status_code == 200 and sr.json().get("data", {}).get("videos"):
                for v in sr.json()["data"]["videos"]:
                    items.append({
                        "video_id": v.get("video_id", ""),
                        "author": f"@{v.get('author', {}).get('unique_id', '')}",
                        "title": v.get("title", ""),
                        "url": f"https://www.tiktok.com/@{v.get('author', {}).get('unique_id', '')}/video/{v.get('video_id', '')}"
                    })
        except Exception:
            pass
        if not items and DDGS is not None:
            try:
                with DDGS() as ddgs:
                    for h in list(ddgs.text(f"site:tiktok.com {query_or_url}", max_results=15)):
                        items.append({"title": h.get("title"), "snippet": h.get("body"), "url": h.get("href")})
            except Exception:
                pass

    return _paginate_items(cache_key, items, page=page, extra_meta={"query_or_url": query_or_url})

# =====================================================================
# 4. محرك Telegram الآمن 100% (قراءة وبحث القنوات العامة والملفات عبر t.me/s/ بدون حساب)
# =====================================================================

@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def telegram_public_search(channel_or_topic: str, search_query: str = "", page: int = 1) -> str:
    """Reads and searches public Telegram channels (e.g. Android RE, Frida, MT Manager, news channels) via t.me/s/ with zero risk to personal accounts.
    If channel_or_topic is a channel username (e.g. 'Termux' or '@ channel'), reads its posts directly; otherwise searches across public Telegram channels."""
    cache_key = f"tg:{channel_or_topic}:{search_query}"
    if page > 1 and cache_key in _PAGE_CACHE:
        return _paginate_items(cache_key, [], page=page, extra_meta={"target": channel_or_topic})

    clean_target = channel_or_topic.strip().lstrip("@")
    m_ch = re.search(r"t\.me/(?:s/)?([A-Za-z0-9_]{4,})", clean_target)
    if m_ch:
        clean_target = m_ch.group(1)

    posts = []
    # إذا كان اسم قناة صالحاً (بدون مسافات) نسحب من t.me/s/ مباشرة
    if re.fullmatch(r"[A-Za-z0-9_]{4,64}", clean_target):
        url = f"https://t.me/s/{clean_target}"
        params = {"q": search_query} if search_query else {}
        r = _http_get(url, params=params, timeout=18)
        if r.status_code == 200 and "tgme_widget_message" in r.text:
            soup = BeautifulSoup(r.text, "html.parser")
            for msg in reversed(soup.select(".tgme_widget_message_wrap")):
                text_el = msg.select_one(".tgme_widget_message_text")
                date_el = msg.select_one("time.time")
                link_el = msg.select_one(".tgme_widget_message_date")
                doc_el = msg.select_one(".tgme_widget_message_document_title")
                txt = text_el.get_text("
", strip=True) if text_el else ""
                doc_title = doc_el.get_text(strip=True) if doc_el else ""
                if txt or doc_title:
                    item = {
                        "post_url": link_el["href"] if (link_el and link_el.has_attr("href")) else "",
                        "posted_at": date_el["datetime"] if (date_el and date_el.has_attr("datetime")) else "",
                        "content": txt
                    }
                    if doc_title:
                        item["attached_file"] = doc_title
                    posts.append(item)

    # إذا كان بحثاً عاماً أو لم توجد القناة مباشرة، نبحث عبر جميع قنوات تيليجرام العامة المفهرسة
    if not posts and DDGS is not None:
        try:
            q_full = f"site:t.me/s/ {channel_or_topic} {search_query}".strip()
            with DDGS() as ddgs:
                for h in list(ddgs.text(q_full, max_results=20)):
                    posts.append({"title": h.get("title"), "content": h.get("body"), "url": h.get("href")})
        except Exception:
            pass

    return _paginate_items(cache_key, posts, page=page, extra_meta={"channel_or_topic": channel_or_topic, "search_query": search_query, "total_messages": len(posts)})

# =====================================================================
# 5. ترسانة الهندسة العكسية والمطورين (Frida CodeShare + StackOverflow + Hacker News + APK Releases)
# =====================================================================

@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def re_and_dev_search(query: str, target: str = "all", page: int = 1) -> str:
    """Searches Android Reverse Engineering & Developer sources: Frida CodeShare scripts, StackOverflow accepted answers, Hacker News, XDA, and 4PDA."""
    cache_key = f"redev:{query}:{target}"
    if page > 1 and cache_key in _PAGE_CACHE:
        return _paginate_items(cache_key, [], page=page, extra_meta={"query": query})

    results = []
    t_low = target.lower()

    # 1. StackOverflow / StackExchange API (مع النص الكامل)
    if t_low in ("all", "stackoverflow", "so"):
        try:
            so_r = _http_get(
                "https://api.stackexchange.com/2.3/search/advanced",
                params={"order": "desc", "sort": "relevance", "q": query, "site": "stackoverflow", "filter": "withbody", "pagesize": 10},
                timeout=15
            )
            if so_r.status_code == 200:
                for item in so_r.json().get("items", []):
                    results.append({
                        "source": "StackOverflow",
                        "title": html.unescape(item.get("title", "")),
                        "is_answered": item.get("is_answered"),
                        "url": item.get("link"),
                        "body_excerpt": BeautifulSoup(item.get("body", ""), "html.parser").get_text("
", strip=True)[:2500]
                    })
        except Exception:
            pass

    # 2. Hacker News Algolia API
    if t_low in ("all", "hn", "hackernews"):
        try:
            hn_r = _http_get("https://hn.algolia.com/api/v1/search", params={"query": query, "hitsPerPage": 10}, timeout=12)
            if hn_r.status_code == 200:
                for hit in hn_r.json().get("hits", []):
                    results.append({
                        "source": "HackerNews",
                        "title": hit.get("title") or hit.get("story_title"),
                        "url": hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
                        "comment_text": BeautifulSoup(hit.get("comment_text") or "", "html.parser").get_text("
", strip=True)[:1500]
                    })
        except Exception:
            pass

    # 3. Frida CodeShare & XDA / 4PDA
    if DDGS is not None and t_low in ("all", "frida", "xda", "4pda"):
        try:
            with DDGS() as ddgs:
                dork = f"(site:codeshare.frida.re OR site:xdaforums.com OR site:4pda.to) {query}"
                for h in list(ddgs.text(dork, max_results=15)):
                    href = h.get("href", "")
                    entry = {"source": "RE_Community", "title": h.get("title"), "snippet": h.get("body"), "url": href}
                    # إذا كان رابط سكربت في Frida CodeShare نسحب كود الجافاسكربت الكامل تلقائياً!
                    if "codeshare.frida.re/@" in href:
                        try:
                            fr_page = _http_get(href, timeout=12)
                            if fr_page.status_code == 200:
                                soup = BeautifulSoup(fr_page.text, "html.parser")
                                code_el = soup.select_one("pre code") or soup.select_one("pre")
                                if code_el:
                                    entry["frida_script_code"] = code_el.get_text()
                        except Exception:
                            pass
                    results.append(entry)
        except Exception:
            pass

    return _paginate_items(cache_key, results, page=page, extra_meta={"query": query, "total_results": len(results)})

@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def apk_and_release_inspector(query_or_repo: str) -> str:
    """Finds the latest APK, binary releases, changelogs, and direct download URLs from GitHub Releases, F-Droid, and PyPI."""
    out = {"query": query_or_repo, "releases": []}

    # 1. إذا كان بصيغة owner/repo نفحص أحدث إصداراته مباشرة من GitHub Releases
    if "/" in query_or_repo and " " not in query_or_repo.strip():
        r = _http_get(f"https://api.github.com/repos/{query_or_repo.strip().strip('/')}/releases?per_page=5", timeout=15)
        if r.status_code == 200:
            for rel in r.json():
                out["releases"].append({
                    "source": "GitHub_Release",
                    "tag": rel.get("tag_name"),
                    "name": rel.get("name"),
                    "published_at": rel.get("published_at"),
                    "body": (rel.get("body") or "")[:2500],
                    "assets": [{"name": a.get("name"), "size_mb": round(a.get("size", 0)/1048576, 2), "download_url": a.get("browser_download_url")} for a in rel.get("assets", [])]
                })

    # 2. البحث في F-Droid API الرسمي عن حزم الأندرويد مفتوحة المصدر
    try:
        fd_r = _http_get("https://search.f-droid.org/api/search_apps", params={"q": query_or_repo}, timeout=12)
        if fd_r.status_code == 200:
            for app in fd_r.json().get("apps", [])[:8]:
                out["releases"].append({
                    "source": "F-Droid",
                    "name": app.get("name"),
                    "summary": app.get("summary"),
                    "url": app.get("url")
                })
    except Exception:
        pass

    return json.dumps(out, ensure_ascii=False, separators=(",", ":"))

@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def meta_and_web_search(query: str, platform: str = "all", page: int = 1) -> str:
    """Safely searches public indexed posts, Reels, and discussions across Instagram, Facebook, and global forums without risking account bans."""
    cache_key = f"metaweb:{query}:{platform}"
    if page > 1 and cache_key in _PAGE_CACHE:
        return _paginate_items(cache_key, [], page=page, extra_meta={"query": query})

    items = []
    if DDGS is not None:
        p_low = platform.lower()
        if p_low == "instagram":
            dork = f"site:instagram.com {query}"
        elif p_low == "facebook":
            dork = f"site:facebook.com {query}"
        else:
            dork = f"(site:instagram.com OR site:facebook.com) {query}"
        try:
            with DDGS() as ddgs:
                for h in list(ddgs.text(dork, max_results=20)):
                    items.append({"title": h.get("title"), "snippet": h.get("body"), "url": h.get("href")})
        except Exception:
            pass
    return _paginate_items(cache_key, items, page=page, extra_meta={"query": query, "platform": platform})
