"""竞品参考 (IG edition) — benchmark account analysis from imported JSON / sample data, knowledge base,
imitation drafts and a local publishing plan. Nothing is posted and nothing is fetched live: Instagram data
needs the official Instagram Graph API (business/creator account + Meta app review), which is not wired yet.
(Original X notes below are kept for reference only.)

Local only: data lives in data/bench/<username>/ (account.json, analysis.json, kb/) and data/bench/queue.json.
Data in:  X API v2 with X_BEARER_TOKEN (optional) or an imported JSON file (see samples/SCHEMA.md).
Posting:  X API v2 POST /2/tweets, OAuth 1.0a user keys X_API_KEY / X_API_SECRET / X_ACCESS_TOKEN / X_ACCESS_SECRET.
          Without keys nothing is posted; the UI offers an x.com/intent/post link instead.
Wired into server.py via bench.init(server_module); routes handled by bench.handle_get / bench.handle_post.
"""
import base64, hashlib, hmac, json, os, re, secrets, threading, time, urllib.parse, urllib.request, urllib.error
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

S = None  # server module (call_model, AGENTS, reviewed_role, ...)
LOCK = threading.Lock()
UNAME_RE = re.compile(r"^(?!\.)(?!.*\.\.)[A-Za-z0-9_.]{1,30}$")   # Instagram usernames
API = os.environ.get("X_API_BASE", "https://api.x.com")
WEEK = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
SCHED_INTERVAL = int(os.environ.get("SCHEDULER_INTERVAL", "30"))


def init(server):
    global S
    S = server
    threading.Thread(target=scheduler_loop, daemon=True, name="ig-plan-scheduler").start()


def bench_dir():
    d = os.path.join(S.acct_dir(), "bench")
    os.makedirs(d, exist_ok=True)
    return d


def queue_file():
    return os.path.join(bench_dir(), "queue.json")


def env(k):
    return os.environ.get(k, "").strip()


def post_keys_ok():
    return all(env(k) for k in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET"))


IG_API_NOTE = ("Instagram 版暂不支持实时抓取或自动发布：读取账号数据和发布内容都需要官方 Instagram Graph API"
               "（Instagram 商业 / 创作者账号 + 关联 Facebook 主页 + Meta 开发者应用审核）。"
               "现在请用「导入 JSON」或「载入示例数据」，发布请在 Instagram App 里手动完成。")


def config():
    return {"platform": "instagram", "liveFetch": apify_ok(), "apifyConfigured": apify_ok(), "apifyMaxPosts": APIFY_MAX_POSTS, "apifyCacheHours": APIFY_CACHE_HOURS, "autoPost": False, "apiNote": IG_API_NOTE,
            "bearerConfigured": False, "keylessFetch": None, "postKeysConfigured": False,
            "captionLimit": 2200, "hashtagLimit": 30, "account": S.active(),
            "zenmuxConfigured": bool(S.api_key()), "tz": "Asia/Shanghai (UTC+8)",
            "samples": sorted(f[:-5] for f in os.listdir(os.path.join(S.BASE, "samples")) if f.endswith(".json"))
            if os.path.isdir(os.path.join(S.BASE, "samples")) else []}


# ---------------------------------------------------------------------------
# Normalisation: our schema (luca-xops-bench/v1) or raw X API v2 responses
# ---------------------------------------------------------------------------
def _num(x):
    try:
        return int(x or 0)
    except (TypeError, ValueError):
        return 0


def norm_user(u):
    pm = u.get("public_metrics") or {}
    url = u.get("url") or ""
    for e in ((u.get("entities") or {}).get("url") or {}).get("urls", []):
        url = e.get("expanded_url") or url
    return {"id": str(u.get("id") or ""), "username": u.get("username") or "", "name": u.get("name") or "",
            "description": u.get("description") or "", "created_at": u.get("created_at"),
            "location": u.get("location") or "", "url": url, "verified": bool(u.get("verified")),
            "profile_image_url": u.get("profile_image_url") or "",
            "followers": _num(pm.get("followers_count", u.get("followers"))),
            "following": _num(pm.get("following_count", u.get("following"))),
            "tweets": _num(pm.get("tweet_count", u.get("tweets"))),
            "listed": _num(pm.get("listed_count", u.get("listed")))}


def norm_post(p, media_map=None, username=""):
    m = p.get("metrics")
    if not m:
        pm = p.get("public_metrics") or {}
        m = {"likes": pm.get("like_count"), "reposts": pm.get("retweet_count"), "replies": pm.get("reply_count"),
             "quotes": pm.get("quote_count"), "bookmarks": pm.get("bookmark_count"), "views": pm.get("impression_count")}
    m = {k: _num(m.get(k)) for k in ("likes", "reposts", "replies", "quotes", "bookmarks", "views")}
    media = p.get("media")
    if media is None:
        media = []
        for k in (p.get("attachments") or {}).get("media_keys", []):
            x = (media_map or {}).get(k)
            if x:
                media.append({"type": x.get("type"), "url": x.get("url") or x.get("preview_image_url"),
                              "preview_image_url": x.get("preview_image_url")})
    urls = p.get("urls")
    if urls is None:
        urls = [u.get("expanded_url") for u in (p.get("entities") or {}).get("urls", [])
                if u.get("expanded_url") and not u.get("media_key")]
    pid = str(p.get("id") or "")
    return {"id": pid, "text": p.get("text") or "", "created_at": p.get("created_at"), "lang": p.get("lang"),
            "url": p.get("url") or ("https://www.instagram.com/p/%s/" % pid),
            "metrics": m, "media": [x for x in media if x.get("url")], "urls": [u for u in urls if u]}


def normalize(doc):
    if not isinstance(doc, dict):
        raise ValueError("JSON 顶层必须是对象")
    media_map = {x["media_key"]: x for x in ((doc.get("includes") or {}).get("media") or []) if x.get("media_key")}
    user = doc.get("user")
    if not user:
        us = (doc.get("includes") or {}).get("users") or []
        user = us[0] if us else None
    if not user:
        raise ValueError("缺少 user（账号资料）")
    user = norm_user(user)
    raw = doc.get("posts") if doc.get("posts") is not None else doc.get("data")
    if not isinstance(raw, list):
        raise ValueError("缺少 posts 数组（或 X API 的 data 数组）")
    if not UNAME_RE.match(user["username"]):
        raise ValueError("user.username 不合法")
    seen, posts = set(), []
    for p in raw:
        if not isinstance(p, dict) or not p.get("id") or str(p["id"]) in seen:
            continue
        seen.add(str(p["id"]))
        posts.append(norm_post(p, media_map, user["username"]))
    if not posts:
        raise ValueError("posts 为空")
    posts.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return {"schema": "luca-xops-bench/v1", "source": doc.get("source") or "import",
            "fetched_at": doc.get("fetched_at") or S.iso(), "user": user, "posts": posts[:1000]}


# ---------------------------------------------------------------------------
# X API v2 (read with bearer, write with OAuth 1.0a)
# ---------------------------------------------------------------------------
def x_get(path, params):
    url = API + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + env("X_BEARER_TOKEN"),
                                               "User-Agent": "LucaXOps/1.4"})
    try:
        with urllib.request.urlopen(req, timeout=30, context=S.SSL_CTX) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError("X API HTTP %s：%s" % (e.code, e.read().decode(errors="replace")[:300]))


def fetch_x_api(username, max_posts=300):
    u = x_get("/2/users/by/username/" + username,
              {"user.fields": "created_at,description,public_metrics,verified,location,profile_image_url,url,entities"})
    if not u.get("data"):
        raise RuntimeError("找不到账号 @%s：%s" % (username, json.dumps(u.get("errors"), ensure_ascii=False)[:200]))
    user = u["data"]
    posts, media, token = [], {}, None
    while len(posts) < max_posts:
        params = {"max_results": 100, "exclude": "retweets,replies",
                  "tweet.fields": "created_at,public_metrics,lang,entities,attachments,conversation_id",
                  "expansions": "attachments.media_keys", "media.fields": "type,url,preview_image_url,public_metrics"}
        if token:
            params["pagination_token"] = token
        j = x_get("/2/users/%s/tweets" % user["id"], params)
        posts += j.get("data") or []
        for m in (j.get("includes") or {}).get("media") or []:
            media[m["media_key"]] = m
        token = (j.get("meta") or {}).get("next_token")
        if not token or not j.get("data"):
            break
    return normalize({"source": "x-api-v2", "user": user, "data": posts[:max_posts], "includes": {"media": list(media.values())}})


FX_API = os.environ.get("FX_API_BASE", "https://api.fxtwitter.com")


def fx_get(path, params=None):
    url = FX_API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh) LucaXOps/1.5",
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25, context=S.SSL_CTX) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError("公开接口 HTTP %s" % e.code)
    except ValueError:
        raise RuntimeError("公开接口返回的不是 JSON（可能被风控拦截）")


def _fx_iso(t):
    ts = t.get("created_timestamp")
    if ts:
        return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    try:
        return datetime.strptime(t.get("created_at", ""), "%a %b %d %H:%M:%S %z %Y").astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    except Exception:
        return None


def fetch_fx(username, max_posts=200, max_pages=12):
    """Keyless: public FxTwitter API (api.fxtwitter.com). Own original posts only (no reposts / replies)."""
    u = fx_get("/" + username)
    if u.get("code") != 200 or not u.get("user"):
        raise RuntimeError("找不到账号 @%s（%s）" % (username, u.get("message") or u.get("code")))
    fu = u["user"]
    if fu.get("protected"):
        raise RuntimeError("@%s 是受保护账号，无法读取帖子" % username)
    user = {"id": fu.get("id"), "username": fu.get("screen_name") or username, "name": fu.get("name"),
            "description": fu.get("description"), "location": fu.get("location"),
            "url": (fu.get("website") or {}).get("url") or "", "profile_image_url": fu.get("avatar_url") or "",
            "verified": bool((fu.get("verification") or {}).get("verified")), "created_at": fu.get("joined"),
            "public_metrics": {"followers_count": fu.get("followers"), "following_count": fu.get("following"),
                               "tweet_count": fu.get("tweets"), "listed_count": 0}}
    posts, cursor, seen = [], None, set()
    for _ in range(max_pages):
        try:
            j = fx_get("/2/profile/%s/statuses" % username, {"cursor": cursor} if cursor else None)
        except RuntimeError:
            if posts:
                break
            raise
        res = j.get("results") or []
        for t in res:
            if t.get("reposted_by") or t.get("replying_to") or str(t.get("id")) in seen:
                continue
            if ((t.get("author") or {}).get("screen_name") or "").lower() != username.lower():
                continue
            seen.add(str(t.get("id")))
            media = [{"type": m.get("type") if m.get("type") != "gif" else "animated_gif",
                      "url": m.get("url") if m.get("type") == "photo" else (m.get("thumbnail_url") or m.get("url")),
                      "preview_image_url": m.get("thumbnail_url") or ""} for m in ((t.get("media") or {}).get("all") or [])]
            urls = [f.get("replacement") for f in ((t.get("raw_text") or {}).get("facets") or [])
                    if f.get("type") == "url" and f.get("replacement")]
            posts.append({"id": str(t["id"]), "text": t.get("text") or "", "created_at": _fx_iso(t), "lang": t.get("lang"),
                          "url": t.get("url"), "media": media, "urls": urls,
                          "metrics": {k: t.get(k) for k in ("likes", "reposts", "replies", "quotes", "bookmarks", "views")}})
        cursor = (j.get("cursor") or {}).get("bottom")
        if len(posts) >= max_posts or not res or not cursor:
            break
        time.sleep(0.4)
    if not posts:
        raise RuntimeError("@%s 没有读到原创帖子" % username)
    return normalize({"source": "fxtwitter-public (keyless)", "user": user, "posts": posts[:max_posts]})



# ---------------------------------------------------------------------------
# Apify (Instagram public data): actor apify/instagram-profile-scraper, run-sync-get-dataset-items.
# One profile = one dataset item (includes ~12 latestPosts), so a fetch costs ~1 result (~$0.0027).
# Results are cached in data/apify_cache/<username>.json for APIFY_CACHE_HOURS (default 24 h).
# ---------------------------------------------------------------------------
APIFY_ACTOR = os.environ.get("APIFY_ACTOR", "apify~instagram-profile-scraper")
APIFY_CACHE_HOURS = float(os.environ.get("APIFY_CACHE_HOURS", "24"))
APIFY_MAX_POSTS = int(os.environ.get("APIFY_MAX_POSTS", "12"))


def apify_ok():
    return bool(env("APIFY_TOKEN"))


def _apify_cache(u):
    d = os.path.join(S.DATA_ROOT, "apify_cache")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, u.lower() + ".json")


def apify_profile(username, force=False):
    """Return (item, cached_bool). Raises RuntimeError with a readable Chinese message."""
    u = username.lstrip("@")
    cf = _apify_cache(u)
    c = jload(cf)
    if c and not force and time.time() - c.get("_ts", 0) < APIFY_CACHE_HOURS * 3600:
        return c["item"], True
    if not apify_ok():
        raise RuntimeError("没有配置 APIFY_TOKEN（.env.local），无法读取 Instagram 公开数据。可以用「导入 JSON」或「载入示例数据」。")
    url = "https://api.apify.com/v2/acts/%s/run-sync-get-dataset-items?timeout=120&format=json" % APIFY_ACTOR
    body = json.dumps({"usernames": [u], "resultsLimit": APIFY_MAX_POSTS}).encode()
    t0, items = time.time(), None
    for attempt in range(3):
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + env("APIFY_TOKEN")})
        try:
            with urllib.request.urlopen(req, timeout=135, context=S.SSL_CTX) as r:
                items = json.loads(r.read().decode() or "[]")
            if items or time.time() - t0 > 90:
                break
            err = RuntimeError("Apify 返回空结果")
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")[:300]
            if e.code in (401, 403):
                raise RuntimeError("Apify 拒绝请求（HTTP %s）：token 无效或额度用完。%s" % (e.code, msg))
            if e.code == 402:
                raise RuntimeError("Apify 额度不足（HTTP 402），请到 Apify 控制台查看用量。")
            err = RuntimeError("Apify HTTP %s：%s" % (e.code, msg))
        except Exception as e:
            err = RuntimeError("连接 Apify 失败：%s" % e)
        if time.time() - t0 > 90:      # stay well inside the 300 s function cap
            raise err
        time.sleep(2 * (attempt + 1))
    else:
        raise err
    items = items or []
    it = next((x for x in items if isinstance(x, dict) and (x.get("username") or "").lower() == u.lower()), None) \
        or (items[0] if items and isinstance(items[0], dict) else None)
    if not it or it.get("error") or not it.get("username"):
        raise RuntimeError("Apify 没有返回 @%s 的资料（账号不存在、改名或被限制）：%s" % (u, (it or {}).get("errorDescription") or (it or {}).get("error") or "空结果"))
    jsave(cf, {"_ts": time.time(), "fetched_at": S.iso(), "item": it})
    return it, False


def ig_avatar(u, url):
    """Instagram CDN blocks hotlinking, so keep a local copy and serve it from /api/bench/avatar/<u>."""
    if not url:
        return ""
    d = os.path.join(S.DATA_ROOT, "apify_cache", "img")
    os.makedirs(d, exist_ok=True)
    f = os.path.join(d, u.lower() + ".jpg")
    if not os.path.isfile(f) or os.path.getsize(f) < 200:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20, context=S.SSL_CTX) as r:
                data = r.read(3_000_000)
            if len(data) > 200:
                open(f, "wb").write(data)
        except Exception as e:
            print("[apify] avatar %s: %s" % (u, e))
            return url
    return "/api/bench/avatar/%s?v=%d" % (u.lower(), int(os.path.getmtime(f)))


def ig_doc(it, cached=False):
    """Apify profile item -> our bench schema (luca-xops-bench/v1). Metrics: likes, replies=comments, views=video views."""
    u = it.get("username")
    posts = []
    for p in (it.get("latestPosts") or [])[:APIFY_MAX_POSTS]:
        typ = (p.get("type") or "").lower()
        media = []
        if p.get("displayUrl"):
            media.append({"type": "video" if typ == "video" else "photo", "url": p.get("displayUrl"), "preview_image_url": p.get("displayUrl")})
        posts.append({"id": str(p.get("id") or p.get("shortCode")), "text": p.get("caption") or "",
                      "created_at": p.get("timestamp") or S.iso(), "lang": "",
                      "url": p.get("url") or ("https://www.instagram.com/p/%s/" % p.get("shortCode")),
                      "metrics": {"likes": max(0, p.get("likesCount") or 0), "replies": max(0, p.get("commentsCount") or 0),
                                  "reposts": 0, "quotes": 0, "bookmarks": 0,
                                  "views": max(0, p.get("videoViewCount") or p.get("videoPlayCount") or 0)},
                      "media": media, "urls": [], "format": {"sidecar": "carousel", "video": "reels", "image": "photo"}.get(typ, typ)})
    return {"schema": "luca-xops-bench/v1", "source": "Apify · instagram-profile-scraper" + ("（缓存）" if cached else ""),
            "fetched_at": S.iso(),
            "user": {"id": str(it.get("id") or u), "username": u, "name": it.get("fullName") or u, "description": it.get("biography") or "",
                     "created_at": "", "location": "", "url": it.get("externalUrl") or "",
                     "profile_image_url": ig_avatar(u, it.get("profilePicUrlHD") or it.get("profilePicUrl") or ""), "verified": bool(it.get("verified")),
                     "public_metrics": {"followers_count": it.get("followersCount") or 0, "following_count": it.get("followsCount") or 0,
                                        "tweet_count": it.get("postsCount") or 0, "listed_count": 0}},
            "posts": posts, "_ig": {"category": it.get("businessCategoryName") or "", "private": bool(it.get("private")),
                                    "postsCount": it.get("postsCount"), "isBusiness": bool(it.get("isBusinessAccount"))}}


def fetch_x(username, max_posts=300, force=False):
    """IG edition: public profile + latest posts via Apify (cached)."""
    it, cached = apify_profile(username, force)
    raw = ig_doc(it, cached)
    posts = []
    for p in raw["posts"]:
        q = norm_post(p, None, raw["user"]["username"])
        q["format"] = p.get("format")
        posts.append(q)
    posts.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return {"schema": raw["schema"], "source": raw["source"], "fetched_at": raw["fetched_at"],
            "user": norm_user(raw["user"]), "posts": posts, "_ig": raw["_ig"]}


def fetch_x_disabled(username, max_posts=300):
    """(X edition) (a) X API v2 if X_BEARER_TOKEN is set; (b) keyless public FxTwitter API; else a clear Chinese error."""
    errs = []
    if env("X_BEARER_TOKEN"):
        try:
            return fetch_x_api(username, max_posts)
        except Exception as e:
            errs.append("X API：%s" % e)
    try:
        return fetch_fx(username, min(max_posts, 200))
    except Exception as e:
        errs.append("免密公开接口(api.fxtwitter.com)：%s" % e)
    raise RuntimeError("抓取 @%s 失败。%s。可以稍后重试、换个网络，或在 .env.local 配置 X_BEARER_TOKEN，或用「导入 JSON」/「载入示例」。"
                       % (username, "；".join(errs)))


def _pct(s):
    return urllib.parse.quote(str(s), safe="~-._")


def oauth1_header(method, url):
    oauth = {"oauth_consumer_key": env("X_API_KEY"), "oauth_nonce": secrets.token_hex(16),
             "oauth_signature_method": "HMAC-SHA1", "oauth_timestamp": str(int(time.time())),
             "oauth_token": env("X_ACCESS_TOKEN"), "oauth_version": "1.0"}
    params = "&".join("%s=%s" % (_pct(k), _pct(v)) for k, v in sorted(oauth.items()))  # JSON body is not signed
    base = "&".join([method.upper(), _pct(url), _pct(params)])
    key = _pct(env("X_API_SECRET")) + "&" + _pct(env("X_ACCESS_SECRET"))
    oauth["oauth_signature"] = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    return "OAuth " + ", ".join('%s="%s"' % (_pct(k), _pct(v)) for k, v in sorted(oauth.items()))


def post_tweet(text):
    url = API + "/2/tweets"
    req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(), method="POST", headers={
        "Authorization": oauth1_header("POST", url), "Content-Type": "application/json", "User-Agent": "LucaXOps/1.4"})
    try:
        with urllib.request.urlopen(req, timeout=30, context=S.SSL_CTX) as r:
            return json.loads(r.read().decode())["data"]["id"]
    except urllib.error.HTTPError as e:
        raise RuntimeError("X API HTTP %s：%s" % (e.code, e.read().decode(errors="replace")[:300]))


def intent_url(text):
    return "https://www.instagram.com/"   # IG has no web compose intent; the owner posts in the Instagram app


def ig_len(text):
    return len(text or "")


def ig_tags(text):
    return len(S.HASHTAG_RE.findall(text or ""))


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def _t(zh, en):
    return en if S.req_lang() == "en" else zh


def _err(e):
    m = str(e)
    return S.en_error(m) if S.req_lang() == "en" and re.search(r"[\u4e00-\u9fff]", m) else m

def an_file(u, lang=None):
    """AI analysis is stored per UI language, so English mode never shows a Chinese analysis (and vice versa)."""
    lang = lang or S.req_lang()
    return os.path.join(adir(u), "analysis.en.json" if lang == "en" else "analysis.json")

def adir(u):
    return os.path.join(bench_dir(), u.lower())


def jload(p, default=None):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return default


def jsave(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    json.dump(obj, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def save_account(doc):
    u = doc["user"]["username"]
    jsave(os.path.join(adir(u), "account.json"), doc)
    return u


def list_accounts():
    out = []
    BENCH_DIR = bench_dir()
    for d in sorted(os.listdir(BENCH_DIR)):
        acc = jload(os.path.join(BENCH_DIR, d, "account.json"))
        if not acc:
            continue
        an = jload(os.path.join(BENCH_DIR, d, "analysis.json")) or {}
        out.append({"username": acc["user"]["username"], "name": acc["user"]["name"], "followers": acc["user"]["followers"],
                    "posts": len(acc["posts"]), "source": acc.get("source"), "fetched_at": acc.get("fetched_at"),
                    "analyzed_at": an.get("analyzed_at"), "kb": len((jload(os.path.join(BENCH_DIR, d, "kb", "kb.json")) or {}).get("items", []))})
    return out


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
def interactions(m):
    return m["likes"] + m["reposts"] + m["replies"] + m["quotes"]


def score(m):  # weighted engagement used for ranking ("热度")
    return m["likes"] + 2 * m["reposts"] + 1.5 * m["replies"] + 2 * m["quotes"] + 0.5 * m["bookmarks"]


def local_dt(s):
    return datetime.fromisoformat((s or "").replace("Z", "+00:00")).astimezone(S.TZ)


MONEY_RULES = [
    ("自有网站/博客", r"baoyu\.io|\.substack\.com|medium\.com|mp\.weixin\.qq\.com|xiaohongshu|bilibili\.com|youtube\.com|youtu\.be"),
    ("付费社群/订阅", r"zsxq\.com|知识星球|xiaobot\.net|patreon\.com|ko-fi\.com|buymeacoffee|付费|订阅|会员|newsletter|Subscribe"),
    ("课程/电子书/产品", r"gumroad\.com|lemonsqueezy|课程|训练营|电子书|小册|教程合集|我的产品|上线了|Product Hunt|producthunt\.com"),
    ("推广/联盟链接", r"[?&](ref|via|aff|affiliate|invite|code|inviteCode)=|amzn\.to|amazon\.[a-z.]+/.*tag=|邀请码|返佣|优惠码|折扣码|推荐码"),
    ("广告/赞助合作", r"#ad\b|#sponsored|赞助|广告|合作推广|商务合作|恰饭|sponsored|partnered with"),
    ("交易所/币圈导流", r"binance\.com|okx\.com|bybit\.com|bitget\.com|gate\.io|pump\.fun|空投|airdrop"),
]


def monetization_signals(posts, user):
    hits, domains = defaultdict(list), Counter()
    for p in posts:
        blob = p["text"] + " " + " ".join(p["urls"])
        for u in p["urls"]:
            d = urllib.parse.urlparse(u).netloc.lower().replace("www.", "")
            if d and d not in ("twitter.com", "x.com", "instagram.com"):
                domains[d] += 1
        for name, rx in MONEY_RULES:
            if re.search(rx, blob, re.I):
                hits[name].append(p["id"])
    bio = user.get("description", "") + " " + user.get("url", "")
    return {"signals": [{"type": k, "count": len(v), "examples": v[:5]} for k, v in sorted(hits.items(), key=lambda x: -len(x[1]))],
            "topDomains": domains.most_common(10), "bioLink": user.get("url", ""),
            "postsWithLinks": sum(1 for p in posts if any(urllib.parse.urlparse(u).netloc.lower().replace("www.", "") not in ("twitter.com", "x.com", "instagram.com") for u in p["urls"])),
            "bio": bio.strip()}


def compute_stats(acc):
    posts, user = acc["posts"], acc["user"]
    n = len(posts)
    followers = max(user["followers"], 1)
    tot = Counter()
    er_view, er_f = [], []
    for p in posts:
        m = p["metrics"]
        for k, v in m.items():
            tot[k] += v
        er_f.append(interactions(m) / followers)
        if m["views"]:
            er_view.append(interactions(m) / m["views"])
    avg = {k: round(tot[k] / n, 1) for k in ("likes", "reposts", "replies", "quotes", "bookmarks", "views")}
    times = [local_dt(p["created_at"]) for p in posts if p.get("created_at")]
    span_days = max((max(times) - min(times)).total_seconds() / 86400, 1) if times else 1
    heat = [[{"n": 0, "sum": 0.0} for _ in range(24)] for _ in range(7)]
    by_hour, by_wd = defaultdict(list), defaultdict(list)
    for p in posts:
        if not p.get("created_at"):
            continue
        t = local_dt(p["created_at"])
        s = score(p["metrics"])
        heat[t.weekday()][t.hour]["n"] += 1
        heat[t.weekday()][t.hour]["sum"] += s
        by_hour[t.hour].append(s)
        by_wd[t.weekday()].append(s)
    hours = [{"hour": h, "posts": len(by_hour[h]), "avgScore": round(sum(by_hour[h]) / len(by_hour[h]), 1) if by_hour[h] else 0} for h in range(24)]
    wds = [{"weekday": d, "label": WEEK[d], "posts": len(by_wd[d]), "avgScore": round(sum(by_wd[d]) / len(by_wd[d]), 1) if by_wd[d] else 0} for d in range(7)]
    # best slots: (weekday, hour) with >=2 posts ranked by avg score; fall back to best hours (any day)
    cells = [(d, h, c["n"], c["sum"] / c["n"]) for d in range(7) for h in range(24) for c in [heat[d][h]] if c["n"] >= 2]
    cells.sort(key=lambda x: -x[3])
    best_slots = [{"weekday": d, "hour": h, "label": "%s %02d:00" % (WEEK[d], h), "posts": c, "avgScore": round(a, 1)} for d, h, c, a in cells[:8]]
    best_hours = sorted([x for x in hours if x["posts"] >= 3], key=lambda x: -x["avgScore"])[:5]
    ranked = sorted(posts, key=lambda p: -score(p["metrics"]))
    median = sorted(score(p["metrics"]) for p in posts)[n // 2]
    media_posts = [p for p in posts if p["media"]]
    link_posts = [p for p in posts if p["urls"]]
    def avg_s(ps):
        return round(sum(score(p["metrics"]) for p in ps) / len(ps), 1) if ps else 0
    er_view_avg = sum(er_view) / len(er_view) if er_view else 0
    er_f_avg = sum(er_f) / n
    # 粉丝指数 0-100: log-scaled followers (60%) + engagement per view vs 2% benchmark (40%)
    import math
    fan_index = round(min(60, 60 * math.log10(followers) / 7) + min(40, 40 * er_view_avg / 0.02), 1)
    return {
        "posts": n, "spanDays": round(span_days, 1), "from": min(times).isoformat() if times else None,
        "to": max(times).isoformat() if times else None,
        "fan": {"followers": user["followers"], "following": user["following"], "tweets": user["tweets"],
                "listed": user["listed"], "ratio": round(user["followers"] / max(user["following"], 1), 1),
                "avg": avg, "avgInteractions": round(sum(interactions(p["metrics"]) for p in posts) / n, 1),
                "erPerView": round(er_view_avg * 100, 2), "erPerFollower": round(er_f_avg * 100, 3),
                "fanIndex": fan_index},
        "frequency": {"perDay": round(n / span_days, 2), "perWeek": round(n * 7 / span_days, 1),
                      "hours": hours, "weekdays": wds,
                      "heatmap": [[{"n": c["n"], "avg": round(c["sum"] / c["n"], 1) if c["n"] else 0} for c in row] for row in heat],
                      "bestSlots": best_slots, "bestHours": best_hours},
        "format": {"withMedia": len(media_posts), "withMediaAvg": avg_s(media_posts),
                   "textOnlyAvg": avg_s([p for p in posts if not p["media"]]),
                   "withAnyLink_inclXcom": len(link_posts), "withAnyLinkAvg": avg_s(link_posts), "medianScore": median,
                   "avgLen": round(sum(S.x_weighted_len(p["text"]) for p in posts) / n)},
        "top": [{"id": p["id"], "score": round(score(p["metrics"]), 1)} for p in ranked[:10]],
    }


ANALYST = {"model": None}


def llm(agent_id, system, user, max_tokens=4000):
    a = dict(S.AGENTS[agent_id])
    return S.call_model(a, [{"role": "system", "content": system}, {"role": "user", "content": user}],
                        max_tokens=max_tokens, timeout=S.STRATEGY_TIMEOUT)["content"]


TOPIC_SYS = ("你是 Instagram 内容分析师。给你一个博主的帖子列表（编号、热度分、正文节选），请把它们聚类成 5–8 个主题方向。"
             "只输出一个 JSON 对象，不要代码块，不要解释：{\"topics\":[{\"name\":\"主题名（≤10字）\",\"desc\":\"一句话说明这个方向写什么\","
             "\"hook\":\"这一类爆款常用的写法/钩子（一句话）\",\"posts\":[编号,...]}]}。每个编号只能属于一个主题，尽量覆盖全部编号。")


def cluster_topics(acc):
    posts = acc["posts"]
    lines = []
    for i, p in enumerate(posts):
        t = re.sub(r"https?://\S+", "", p["text"]).replace("\n", " ").strip()[:160]
        lines.append("%d | %d | %s" % (i, score(p["metrics"]), t))
    agent = os.environ.get("BENCH_TOPIC_AGENT", "community")  # Gemini: fast + good JSON
    last = None
    for aid in (agent, "researcher"):
        try:
            out = llm(aid, TOPIC_SYS, "博主：@%s（%s）\n简介：%s\n\n编号 | 热度分 | 正文节选\n%s" % (
                acc["user"]["username"], acc["user"]["name"], acc["user"]["description"], "\n".join(lines)), 6000)
            j = S.parse_json_obj(out)
            if j.get("topics"):
                break
        except Exception as e:
            last = e
            j = None
    if not j:
        raise RuntimeError("主题聚类失败：%s" % last)
    topics, used = [], set()
    for t in j["topics"]:
        idx = [i for i in t.get("posts", []) if isinstance(i, int) and 0 <= i < len(posts) and i not in used]
        used.update(idx)
        if not idx:
            continue
        ps = [posts[i] for i in idx]
        sc = [score(p["metrics"]) for p in ps]
        topics.append({"name": str(t.get("name", ""))[:20], "desc": t.get("desc", ""), "hook": t.get("hook", ""),
                       "count": len(ps), "avgScore": round(sum(sc) / len(sc), 1), "totalScore": round(sum(sc)),
                       "avgViews": round(sum(p["metrics"]["views"] for p in ps) / len(ps)),
                       "share": round(100 * len(ps) / len(posts), 1),
                       "postIds": [p["id"] for p in sorted(ps, key=lambda p: -score(p["metrics"]))]})
    topics.sort(key=lambda t: -t["avgScore"])
    return topics


REPORT_SYS = ("你是 Instagram 账号运营顾问，为当前账号的主人分析一个同类参考账号（数据来自导入的 JSON 或示例数据，指标里 likes=赞、replies=评论、reposts=分享、bookmarks=收藏、views=覆盖/播放）。只依据给你的数据下结论，"
              "数据里没有的不要编造（写【待核实】）。用中文 Markdown 输出，结构固定：\n"
              "## 一句话结论\n## 主要方向（为什么这几个方向热）\n## 粉丝与互动\n## 发帖节奏\n## 变现方式\n（逐条列出证据：链接域名/出现次数/示例帖子编号；"
              "区分『已证实』和『推测』）\n## 对你的账号该学什么（5 条具体动作：形式（单图/轮播/Reels/Stories）、视觉、文案钩子、话题标签，结合 myAccount 的定位和现有数据）\n"
              "## 发布时间建议（给当前账号：每周几、几点，北京时间，说明依据是参考账号的最佳时段还是当前账号自己的数据）\n"
              "不要声称已经执行任何操作。")


def my_account_brief():
    p = S.profile() or {}
    me = {k: p.get(k) for k in ("handle", "positioning", "audience", "pillars", "followers") if p.get(k) is not None}
    own = jload(os.path.join(S.acct_dir(), "self.json"))
    if own and own.get("posts"):
        try:
            st = compute_stats(own)
            me["ownStats"] = {"posts": st["posts"], "perDay": st["frequency"]["perDay"],
                              "bestSlots": st["frequency"]["bestSlots"][:3], "avgViews": st["fan"]["avg"]["views"],
                              "erPerView": st["fan"]["erPerView"]}
        except Exception:
            pass
    return me


def advice_for_me(stats):
    """Deterministic posting-time advice for the active account, from the benchmark's best slots."""
    fr = stats["frequency"]
    me = my_account_brief()
    slots = [s["label"] for s in fr.get("bestSlots", [])[:3]]
    hours = [str(h["hour"]) + "点" for h in fr.get("bestHours", [])[:3]]
    per = fr.get("perDay") or 0
    mine = (me.get("ownStats") or {}).get("perDay")
    tip = "按对标博主互动最高的时段发：%s；每天优先 %s（北京时间）。" % ("、".join(slots) or "样本不足", "、".join(hours) or "样本不足")
    if mine is not None:
        tip += " 你现在每天约 %s 条，对标约 %s 条。" % (mine, per)
    return {"account": S.active(), "bestSlots": slots, "bestHours": hours, "benchPerDay": per, "myPerDay": mine, "tip": tip}


def report(acc, stats, topics, money):
    top = sorted(acc["posts"], key=lambda p: -score(p["metrics"]))[:12]
    data = {"user": acc["user"], "stats": {k: stats[k] for k in ("posts", "spanDays", "fan", "format")},
            "frequency": {k: stats["frequency"][k] for k in ("perDay", "perWeek", "bestSlots", "bestHours")},
            "weekdays": stats["frequency"]["weekdays"],
            "topics": [{k: t[k] for k in ("name", "desc", "hook", "count", "avgScore", "avgViews", "share")} for t in topics],
            "monetization": money, "myAccount": my_account_brief(),
            "topPosts": [{"id": p["id"], "text": p["text"][:300], "metrics": p["metrics"], "media": len(p["media"]),
                          "urls": p["urls"][:3], "time": local_dt(p["created_at"]).strftime("%m-%d %a %H:%M")} for p in top]}
    return llm(os.environ.get("BENCH_REPORT_AGENT", "chief"), REPORT_SYS,
               "对标博主数据（时间均为 Asia/Shanghai）：\n" + json.dumps(data, ensure_ascii=False), 3500)


def analyze(username, emit=None):
    acc = jload(os.path.join(adir(username), "account.json"))
    if not acc:
        raise RuntimeError("还没有 @%s 的数据，请先抓取或导入" % username)
    t0 = time.time()
    ev = emit or (lambda e: None)
    ev({"event": "step", "msg": _t("计算粉丝指数 / 互动率 / 发帖热力图…", "Computing follower index / engagement rate / posting heatmap…")})
    stats = compute_stats(acc)
    money = monetization_signals(acc["posts"], acc["user"])
    ev({"event": "step", "msg": _t("Gemini 正在给 %d 条帖子做主题聚类…", "Gemini is clustering %d posts into topics…") % len(acc["posts"])})
    try:
        topics = cluster_topics(acc)
    except Exception as e:
        topics, terr = [], str(e)
    else:
        terr = None
    ev({"event": "step", "msg": _t("Claude 正在写分析报告（含变现判断）…", "Claude is writing the analysis report (incl. monetization)…")})
    try:
        rep = report(acc, stats, topics, money)
        rerr = None
    except Exception as e:
        rep, rerr = "", str(e)
    an = {"username": acc["user"]["username"], "analyzed_at": S.iso(), "stats": stats, "topics": topics,
          "monetization": money, "report": rep, "forMe": advice_for_me(stats), "errors": [x for x in (terr, rerr) if x],
          "ms": int((time.time() - t0) * 1000)}
    jsave(an_file(username), an)
    ev({"event": "step", "msg": _t("下载热度前 20% 的帖子到知识库…", "Saving the top 20% posts to the knowledge base…")})
    kb = build_kb(username, acc, an)
    an["kbCount"] = len(kb["items"])
    return an


# ---------------------------------------------------------------------------
# Knowledge base: top ~20% posts by engagement, text + media files saved locally
# ---------------------------------------------------------------------------
def build_kb(username, acc=None, an=None):
    acc = acc or jload(os.path.join(adir(username), "account.json"))
    an = an or jload(an_file(username)) or {}
    pct = float(os.environ.get("KB_TOP_PERCENT", "20"))
    ranked = sorted(acc["posts"], key=lambda p: -score(p["metrics"]))
    top = ranked[:max(5, int(round(len(ranked) * pct / 100)))]
    topic_of = {pid: t["name"] for t in an.get("topics", []) for pid in t["postIds"]}
    kdir = os.path.join(adir(username), "kb")
    mdir = os.path.join(kdir, "media")
    os.makedirs(mdir, exist_ok=True)
    old = {x["id"]: x for x in (jload(os.path.join(kdir, "kb.json")) or {}).get("items", [])}
    items = []

    def dl(job):
        url, fn = job
        p = os.path.join(mdir, fn)
        if os.path.exists(p) and os.path.getsize(p) > 0:
            return fn
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 LucaXOps/1.4"})
            with urllib.request.urlopen(req, timeout=30, context=S.SSL_CTX) as r:
                data = r.read(15_000_000)
            open(p, "wb").write(data)
            return fn
        except Exception:
            return None
    jobs = []
    for rank, p in enumerate(top, 1):
        files = []
        for k, m in enumerate(p["media"]):
            url = m.get("url") if m.get("type") == "photo" else (m.get("preview_image_url") or m.get("url"))
            if not url:
                continue
            ext = os.path.splitext(urllib.parse.urlparse(url).path)[1][:5] or ".jpg"
            fn = "%s_%d%s" % (p["id"], k + 1, ext)
            jobs.append((url, fn))
            files.append({"type": m.get("type"), "file": fn, "src": url})
        items.append({"id": p["id"], "rank": rank, "text": p["text"], "url": p["url"], "created_at": p["created_at"],
                      "local_time": local_dt(p["created_at"]).strftime("%Y-%m-%d %H:%M"), "metrics": p["metrics"],
                      "score": round(score(p["metrics"]), 1), "topic": topic_of.get(p["id"], ""), "urls": p["urls"],
                      "media": files, "notes": (old.get(p["id"]) or {}).get("notes", "")})
    with S.TLPool(max_workers=6) as ex:
        ok = set(x for x in ex.map(dl, jobs) if x)
    for it in items:
        for f in it["media"]:
            f["saved"] = f["file"] in ok
    kb = {"username": acc["user"]["username"], "built_at": S.iso(), "topPercent": pct, "items": items}
    jsave(os.path.join(kdir, "kb.json"), kb)
    with open(os.path.join(kdir, "知识库.md"), "w", encoding="utf-8") as f:
        f.write("# @%s 爆款知识库（热度前 %g%%，%d 条）\n\n" % (kb["username"], pct, len(items)))
        for it in items:
            m = it["metrics"]
            f.write("## #%d · %s · %s\n\n%s\n\n- 热度 %s ｜ ❤ %d ｜ 🔁 %d ｜ 💬 %d ｜ 👁 %d\n- 原帖：%s\n%s\n" % (
                it["rank"], it["topic"] or "未分类", it["local_time"], it["text"], it["score"], m["likes"], m["reposts"],
                m["replies"], m["views"], it["url"], "".join("- 媒体：media/%s\n" % x["file"] for x in it["media"] if x.get("saved"))))
    return kb


# ---------------------------------------------------------------------------
# Imitation: the 5 roles each write a new post for the current account modelled on a hot post (through 审查狼)
# ---------------------------------------------------------------------------
IMITATE_SYSTEM = ("\n【本次任务覆盖上面的「输出要求」和「不写成稿」】本次是仿写任务：你要亲自写 1 条当前账号的 Instagram 中文帖子草稿（供账号主人审核，不会自动发布），"
                  "用你岗位的视角选角度。禁止输出选题卡、汇总表、表格、周报、SOP 或多条备选；"
                  "整段回复只能是「文案：<caption，末尾带话题标签>」+ 空行 +「视觉概念：<一句话>」+ 空行 +「仿写要点：<一句话>」三部分。")
IMITATE_EN = ("\n[LANGUAGE OVERRIDE] The UI is in English: write the Instagram caption in ENGLISH, not Chinese (≤2200 characters, ≤30 hashtags). "
              "Your whole reply must be exactly: 'Caption: <caption with hashtags>' + blank line + 'Visual concept: <one sentence>' + blank line + 'Notes: <one sentence>'.")
IMITATE_TASK = """下面是同类参考账号 @{u} 的一条爆款 Instagram 帖子（热度排名 #{rank}，主题：{topic}）。
请为**当前账号**（中文 AI 科普）仿写 1 条新的 Instagram 帖子：
- 学它的结构、前 125 字符钩子、分段节奏和信息密度，但**换成当前账号自己的角度和内容**，不能照抄句子，不能冒用对方的经历或数据；
- 中文文案 ≤2200 字符（建议 300–800 字符），末尾 5–15 个话题标签（≤30 个）；
- 具体数字、日期、价格如果原帖没有给出依据，就不要写，或写【待核实】；
- 从你的岗位出发（{role}）。

严格按下面格式输出：
文案：<caption 正文，可以多行，末尾带话题标签>

视觉概念：<封面 / 画面一句话>

仿写要点：<一句话说明借鉴了原帖的什么>

【爆款原帖】
\"\"\"{text}\"\"\"
（❤ 赞 {likes} ｜ ✈️ 分享 {reposts} ｜ 💬 评论 {replies} ｜ 👁 覆盖 {views}）"""


def imitate_agent_id(aid):
    """Roles whose prompt forbids drafting posts (DeepSeek选题: 「你不写成稿」+ 固定选题卡格式) get a
    trimmed alias for imitation: same model/persona, without the output-format section."""
    a = S.AGENTS[aid]
    sp = a.get("systemPrompt", "")
    if "你不写成稿" not in sp and "选题卡" not in sp:
        return aid
    alias = aid + "__imitate"
    if alias not in S.AGENTS:
        persona = sp.split("【输出要求】")[0].replace("- 你不写成稿，也不在 Instagram 上做任何互动。", "- 你不在 Instagram 上做任何互动。")
        tail = sp.split("【本应用中的额外规则】")[-1] if "【本应用中的额外规则】" in sp else ""
        S.AGENTS[alias] = {**a, "id": alias, "systemPrompt": persona + "\n【本应用中的额外规则】" + tail}
    return alias


def extract_post(text):
    m = re.search(r"(?:文案|配文|Caption|中文版|English version)\s*\**\s*[:：]\s*(.+?)(?:\n\s*-{3,}\s*\n|\n\s*\**(?:视觉概念|Visual concept|仿写要点|Notes)|$)", text, re.S | re.I)
    if m and m.group(1).strip():
        return m.group(1).strip().strip("*").strip()
    drafts = S.extract_post_drafts(text)
    if drafts:
        return drafts[0][1]
    return re.split(r"\n\s*\**(?:视觉概念|Visual concept|仿写要点|Notes)", text)[0].strip()


def imitate(username, post_id, roles, review=True, demo=False, emit=None, post=None):
    kb = jload(os.path.join(adir(username), "kb", "kb.json")) or {}
    it = next((x for x in kb.get("items", []) if x["id"] == post_id), None)
    if not it and isinstance(post, dict) and str(post.get("id")) == post_id and isinstance(post.get("text"), str) and isinstance(post.get("metrics"), dict):
        it = {"id": post_id, "rank": post.get("rank", "-"), "topic": str(post.get("topic") or "")[:80], "text": post["text"][:4000],
              "metrics": {k: post["metrics"].get(k, 0) for k in ("likes", "reposts", "replies", "views", "bookmarks", "quotes")}}
    if not it:
        acc = jload(os.path.join(adir(username), "account.json")) or {}
        p = next((x for x in acc.get("posts", []) if x["id"] == post_id), None)
        if not p:
            raise RuntimeError(_t("找不到这条帖子", "Post not found"))
        it = {"id": p["id"], "rank": "-", "topic": "", "text": p["text"], "metrics": p["metrics"]}
    roles = roles or list(S.PIPELINE)
    m = it["metrics"]
    en = S.req_lang() == "en"
    question = "Write one Instagram imitation post for the current account in English (based on a benchmark top post)" if en else "仿写一条当前账号的 Instagram 中文帖子（参考同类爆款）"
    sysx = IMITATE_SYSTEM + (IMITATE_EN if en else "")

    def job(aid):
        a = S.AGENTS[aid]
        user = IMITATE_TASK.format(u=username, rank=it["rank"], topic=it.get("topic") or "未分类", role=a.get("role", ""),
                                   text=it["text"], likes=m["likes"], reposts=m["reposts"], replies=m["replies"], views=m["views"])
        if emit:
            emit({"event": "start", "agent": aid, "name": a["name"], "model": a["model"]})
        rid = imitate_agent_id(aid)
        r = S.reviewed_role(rid, user, "ask", question, max_tokens=max(a.get("max_tokens", 2500), 2000),
                            context="对标原帖（只能借鉴写法）：" + it["text"], lang=S.req_lang() or "zh",
                            extra_system=sysx, review=review, demo=demo, emit=emit)
        r.pop("usage", None)
        r["agent"] = aid
        if r.get("ok"):
            r["post"] = extract_post(r["output"])
            r["weighted"] = ig_len(r["post"])
            r["hashtags"] = ig_tags(r["post"])
            vm = re.search(r"(?:视觉概念|Visual concept)\s*\**\s*[:：]\s*(.+)", r["output"], re.I)
            r["visual"] = vm.group(1).strip().strip("*") if vm else ""
            r["intent"] = intent_url(r["post"])
        if emit:
            emit({"event": "agent", **r})
        return r
    with S.TLPool(max_workers=len(roles)) as ex:
        res = list(ex.map(job, roles))
    out = {"ok": any(r.get("ok") for r in res), "source": {"username": username, "postId": post_id}, "outputs": res}
    if review:
        out["review"] = {"enabled": True, "demo": bool(demo), **(S.review_summary(res) or {})}
    return out


# ---------------------------------------------------------------------------
# Post queue + scheduler
# ---------------------------------------------------------------------------
def load_queue():
    return jload(queue_file(), {"items": []}) or {"items": []}


def save_queue(q):
    jsave(queue_file(), q)


def next_slot(username, taken):
    an = jload(an_file(username)) if username else None
    fr = (an or {}).get("stats", {}).get("frequency", {})
    slots = [(s["weekday"], s["hour"]) for s in fr.get("bestSlots", [])]
    hours = [h["hour"] for h in fr.get("bestHours", [])] or [9, 12, 20]
    now = S.now()
    taken = set(taken)
    for day in range(0, 21):
        base = (now + timedelta(days=day)).replace(minute=0, second=0, microsecond=0)
        cands = [h for (d, h) in slots if d == base.weekday()] + hours
        for h in cands:
            t = base.replace(hour=h, minute=5)
            if t > now + timedelta(minutes=10) and t.strftime("%Y-%m-%dT%H") not in taken:
                return t
    return now + timedelta(hours=1)


def queue_add(text, mode="review", scheduled_at=None, source=None):
    text = (text or "").strip()
    if not text:
        raise ValueError("text is required")
    if mode not in ("review", "auto"):
        raise ValueError("mode must be review or auto")
    with LOCK:
        q = load_queue()
        if scheduled_at:
            t = datetime.fromisoformat(scheduled_at)
            if t.tzinfo is None:
                t = t.replace(tzinfo=S.TZ)
            t = t.astimezone(S.TZ)
        else:
            taken = [x["scheduled_at"][:13] for x in q["items"] if x["status"] in ("pending", "approved")]
            t = next_slot((source or {}).get("username"), taken)
        item = {"id": S.now().strftime("%Y%m%d%H%M%S") + secrets.token_hex(2), "text": text, "mode": mode,
                "status": "pending", "scheduled_at": t.isoformat(timespec="seconds"), "created_at": S.iso(),
                "source": source or {}, "weighted": ig_len(text), "hashtags": ig_tags(text), "tweetId": None, "error": None}
        q["items"].append(item)
        save_queue(q)
    return item


def queue_view():
    q = load_queue()
    items = sorted(q["items"], key=lambda x: x["scheduled_at"])
    for x in items:
        x["intent"] = intent_url(x["text"])
    return {"items": items, "postKeysConfigured": post_allowed(), "account": S.active(), "schedulerIntervalSec": SCHED_INTERVAL}


def queue_action(qid, body):
    act = body.get("action")
    with LOCK:
        q = load_queue()
        it = next((x for x in q["items"] if x["id"] == qid), None)
        if not it:
            return 404, {"error": "queue item not found"}
        if act == "delete":
            q["items"].remove(it)
        elif act == "cancel":
            it["status"] = "canceled"
        elif act == "approve":
            if it["status"] in ("pending", "manual", "failed"):
                it["status"] = "approved"; it["approved_at"] = S.iso(); it["error"] = None
        elif act == "update":
            if it["status"] == "posted":
                return 400, {"error": "already posted"}
            if "text" in body:
                it["text"] = str(body["text"]).strip(); it["weighted"] = ig_len(it["text"]); it["hashtags"] = ig_tags(it["text"])
            if body.get("mode") in ("review", "auto"):
                it["mode"] = body["mode"]
                if it["mode"] == "review" and it["status"] == "approved":
                    it["status"] = "pending"
            if body.get("scheduled_at"):
                t = datetime.fromisoformat(body["scheduled_at"])
                it["scheduled_at"] = (t if t.tzinfo else t.replace(tzinfo=S.TZ)).astimezone(S.TZ).isoformat(timespec="seconds")
            if it["status"] in ("canceled", "failed", "manual"):
                it["status"] = "pending"; it["error"] = None
        elif act == "mark_posted":  # posted by hand via the intent link
            it["status"] = "posted"; it["posted_at"] = S.iso(); it["manual"] = True
        elif act == "post_now":
            it["status"] = "approved"; it["scheduled_at"] = S.iso()
        else:
            return 400, {"error": "unknown action", "valid": ["approve", "cancel", "delete", "update", "post_now", "mark_posted"]}
        save_queue(q)
    if act == "post_now":
        run_due()
    return 200, {"ok": True, "queue": queue_view()}


def run_due():
    now = S.now()
    with LOCK:
        q = load_queue()
        due = [x for x in q["items"] if datetime.fromisoformat(x["scheduled_at"]) <= now and
               (x["status"] == "approved" or (x["status"] == "pending" and x["mode"] == "auto"))]
        for it in due:
            if ig_len(it["text"]) > 2200 or ig_tags(it["text"]) > 30:
                it["status"], it["error"] = "failed", "文案超过 2200 字符或话题标签超过 30 个，请先修改"
                continue
            if not post_allowed():
                it["status"], it["error"] = "manual", "到发布时间了：Instagram 版不自动发布，请在 Instagram App 手动发布后点「我已手动发好」"
                continue
            try:
                it["tweetId"] = post_tweet(it["text"])
                it["status"], it["posted_at"], it["error"] = "posted", S.iso(), None
            except Exception as e:
                it["status"], it["error"] = "failed", str(e)[:300]
        if due:
            save_queue(q)
    return len(due)


def scheduler_loop():
    while True:
        for p in S.list_profiles():
            S._TL.handle = p["handle"]
            try:
                run_due()
            except Exception as e:
                print("[scheduler] %s" % e)
            finally:
                S._TL.handle = None
        time.sleep(SCHED_INTERVAL)


def post_allowed():
    """IG edition: never posts (no Instagram Graph API content-publishing permission wired)."""
    return False and post_keys_ok() and S.active().lower() == (env("X_POST_HANDLE") or "").lstrip("@").lower()


# ---------------------------------------------------------------------------
# HTTP routing (called from server.Handler)
# ---------------------------------------------------------------------------
def handle_get(h, path):
    if path == "/api/bench/config":
        return h.send_json(200, config())
    if path == "/api/bench":
        return h.send_json(200, {"accounts": list_accounts()})
    if path == "/api/queue":
        return h.send_json(200, queue_view())
    m = re.match(r"^/api/bench/([A-Za-z0-9_.]{1,30})$", path)
    if m:
        u = m.group(1)
        acc = jload(os.path.join(adir(u), "account.json"))
        if not acc:
            return h.send_json(404, {"error": "account not found"})
        an = jload(an_file(u))
        kb = jload(os.path.join(adir(u), "kb", "kb.json"))
        return h.send_json(200, {"user": acc["user"], "source": acc.get("source"), "fetched_at": acc.get("fetched_at"),
                                 "postCount": len(acc["posts"]), "analysis": an, "kb": kb})
    m = re.match(r"^/api/bench/avatar/([a-z0-9_.]{1,30})$", path)
    if m:
        p = os.path.join(S.DATA_ROOT, "apify_cache", "img", m.group(1) + ".jpg")
        if not os.path.isfile(p):
            return h.send_json(404, {"error": "avatar not found"})
        data = open(p, "rb").read()
        h.send_response(200); h.cors()
        h.send_header("Content-Type", "image/jpeg"); h.send_header("Content-Length", str(len(data))); h.send_header("Cache-Control", "max-age=86400")
        h.end_headers(); h.wfile.write(data)
        return
    m = re.match(r"^/api/bench/([A-Za-z0-9_.]{1,30})/media/([A-Za-z0-9_.-]+)$", path)
    if m:
        p = os.path.join(adir(m.group(1)), "kb", "media", m.group(2))
        if not os.path.isfile(p):
            return h.send_json(404, {"error": "media not found"})
        ext = os.path.splitext(p)[1].lower()
        ctype = {".png": "image/png", ".gif": "image/gif", ".webp": "image/webp", ".mp4": "video/mp4"}.get(ext, "image/jpeg")
        data = open(p, "rb").read()
        h.send_response(200); h.cors()
        h.send_header("Content-Type", ctype); h.send_header("Content-Length", str(len(data))); h.send_header("Cache-Control", "max-age=86400")
        h.end_headers(); h.wfile.write(data)
        return
    return h.send_json(404, {"error": "not found"})


POS_SYS = ("你是 Instagram 账号定位分析师。根据账号资料和最近帖子，判断这个账号的定位。只输出一个 JSON 对象，不要其他文字：\n"
           '{"positioning":"一句话定位（≤30字）","short":"2–6 字的领域标签，如 AI 科普 / 加密投资","audience":"目标受众（≤40字）",'
           '"pillars":["内容主线1","内容主线2","内容主线3"],"tone":"语气风格（≤20字）","language":"主要语言"}\n只依据给你的数据，没有帖子就根据简介推断并在 positioning 末尾加（推测）。')
POS_SYS += ("\nIMPORTANT: write every JSON value in English (e.g. short = \"AI explainers\" or \"Crypto investing\"; append \" (inferred)\" instead of （推测）). Keep handles and names exactly as they are.")


def infer_positioning(doc):
    u = doc["user"]
    top = sorted(doc["posts"], key=lambda p: -score(p["metrics"]))[:25]
    data = {"username": u["username"], "name": u["name"], "bio": u["description"], "followers": u["followers"],
            "posts": [p["text"][:220] for p in top]}
    try:
        txt = llm(os.environ.get("POSITIONING_AGENT", "chief"), POS_SYS, json.dumps(data, ensure_ascii=False), 1200)
        j = S.parse_json_obj(txt)
        if isinstance(j, dict) and j.get("positioning"):
            return j
    except Exception as e:
        print("[positioning] %s" % e)
    return {"positioning": (u["description"] or "(to be confirmed from bio)")[:60], "short": "Account topic", "audience": "[to be confirmed]",
            "pillars": [], "tone": "", "language": ""}


def handle_add_account(h, body):
    """POST /api/accounts {username, data?, sample?} -> fetch own profile/posts, infer positioning, make active."""
    try:
        if isinstance(body.get("restore"), dict):   # serverless: re-seed a profile the browser cached (no fetch / LLM)
            rp = dict(body["restore"]); ru = S.canon_handle(str(rp.get("handle") or "").lstrip("@"))
            if not S.HANDLE_RE.match(ru):
                return h.send_json(400, {"error": "bad handle"})
            if not S.profile(ru):
                rp["handle"] = ru
                jsave(os.path.join(S.acct_dir(ru), "profile.json"), {k: v for k, v in rp.items() if isinstance(v, (str, int, float, bool, list)) or v is None})
            sf = os.path.join(S.acct_dir(ru), "self.json")
            if isinstance(body.get("self"), dict) and not os.path.isfile(sf):   # the account's own fetched posts (browser cache)
                try:
                    jsave(sf, normalize(body["self"]))
                except Exception:
                    pass
            if body.get("activate"):
                S.set_active(ru)
            return h.send_json(200, {"ok": True, "restored": ru})
        u = (body.get("username") or "").strip().lstrip("@")
        u = re.sub(r"^https?://(www\.)?instagram\.com/", "", u).split("/")[0].split("?")[0]
        doc, warn = None, None
        if body.get("data"):
            doc = normalize(body["data"])
        elif body.get("sample"):
            name = str(body["sample"])
            if not UNAME_RE.match(name):
                return h.send_json(400, {"error": "bad sample"})
            doc = normalize(json.load(open(os.path.join(S.BASE, "samples", name + ".json"), encoding="utf-8")))
        else:
            if not S.HANDLE_RE.match(u):
                return h.send_json(400, {"error": "Instagram 用户名不合法（1–30 位字母、数字、下划线或点）"})
            sample_names = {n[:-5].lower() for n in os.listdir(os.path.join(S.BASE, "samples")) if n.endswith(".json")}
            if u.lower() == str(getattr(S, "IG_DEFAULT", "ig_demo")).lower() or u.lower() in sample_names:
                # the built-in sample account is not a real Instagram user: never "refresh" it via Apify
                # (that would pull a stranger's account that happens to have the same name)
                return h.send_json(400, {"error": "This is the built-in sample account, so there is nothing to refresh. Use \"Add account…\" to add your own Instagram account."})
            doc = None
            if apify_ok():
                try:
                    doc = fetch_x(u, force=bool(body.get("force")))   # 24 h cache unless force
                except Exception as e:
                    warn = S.en_error(str(e)) if re.search(r"[\u4e00-\u9fff]", str(e)) else str(e)
        if doc:
            u = doc["user"]["username"]
        u = S.canon_handle(u)   # v1.6: reuse existing folder regardless of letter case
        old = S.profile(u) or {}
        # v1.6: do NOT switch the active account until profile.json is written (was set before a 20-60 s
        # LLM call, so the header/other requests saw the new handle with a missing/old profile).
        if doc:
            jsave(os.path.join(S.acct_dir(u), "self.json"), doc)
        if old.get("builtin") and not body.get("reinfer"):
            pos = {}
        elif body.get("quick"):   # silent default account on first open: no LLM call, positioning from the bio
            bio = ((doc or {}).get("user", {}).get("description") or "").strip()
            pos = {"positioning": old.get("positioning") or bio[:60], "quick": True} if (old.get("positioning") or bio) else {"quick": True}
        elif doc and S.api_key():
            prev = getattr(S._TL, "handle", None)
            S._TL.handle = u   # personalise the positioning prompt for THIS account, not the active one
            try:
                pos = infer_positioning(doc)
            finally:
                S._TL.handle = prev
        else:
            pos = {"positioning": old.get("positioning") or ((doc or {}).get("user", {}).get("description") or "【待确认】")[:60]}
        prof = dict(old)
        if not body.get("quick"):
            prof.pop("quick", None)   # a real positioning run replaces the quick sample placeholder
        prof.update({k: v for k, v in pos.items() if v})
        prof["platform"] = "instagram"
        prof.update({"handle": u, "createdAt": old.get("createdAt") or S.iso(), "updatedAt": S.iso(),
                     "source": doc.get("source") if doc else (old.get("source") or "manual")})
        if doc and doc.get("_ig"):
            prof.update({"category": doc["_ig"].get("category") or "", "private": doc["_ig"].get("private"),
                         "postsCount": doc["_ig"].get("postsCount"), "fetchedAt": S.iso()})
        if doc:
            prof.update({"name": doc["user"]["name"], "followers": doc["user"]["followers"],
                         "bio": doc["user"]["description"], "avatar": doc["user"]["profile_image_url"],
                         "posts": len(doc["posts"])})
        for k in ("positioning", "audience", "short", "tone"):
            if isinstance(body.get(k), str) and body[k].strip():
                prof[k] = body[k].strip()
        prof.setdefault("name", u)
        jsave(os.path.join(S.acct_dir(u), "profile.json"), prof)
        S.set_active(u)
        return h.send_json(200, {"ok": True, "active": u, "profile": prof, "warning": warn, "self": doc})
    except (ValueError, RuntimeError, OSError) as e:
        return h.send_json(400, {"error": str(e)})


def handle_post(h, path, body):
    try:
        if path == "/api/bench/fetch":
            u = (body.get("username") or "").strip().lstrip("@")
            if not UNAME_RE.match(u):
                return h.send_json(400, {"error": "Instagram 用户名不合法（1–30 位字母、数字、下划线或点）"})
            n = body.get("maxPosts", 300)
            n = n if isinstance(n, int) and 10 <= n <= 1000 else 300
            doc = fetch_x(u, n, force=bool(body.get("force")))
            if not doc["posts"]:
                return h.send_json(400, {"error": "@%s 没有可读取的公开帖子（可能是私密账号）。" % u})
            save_account(doc)
            return h.send_json(200, {"ok": True, "username": doc["user"]["username"], "posts": len(doc["posts"]), "source": doc.get("source"), "doc": doc})
        if path == "/api/bench/import":
            if body.get("sample"):
                name = str(body["sample"])
                if not UNAME_RE.match(name):
                    return h.send_json(400, {"error": "bad sample"})
                doc = json.load(open(os.path.join(S.BASE, "samples", name + ".json"), encoding="utf-8"))
            else:
                doc = body.get("data")
            doc = normalize(doc)
            u = save_account(doc)
            # serverless: the browser may hand back an analysis / knowledge base it cached for this account
            if isinstance(body.get("analysis"), dict):
                if body.get("lang") == "en":   # English-only: never restore a cached Chinese analysis
                    jsave(an_file(u, "en"), body["analysis"])
            if isinstance(body.get("kb"), dict) and body["kb"].get("items") is not None:
                jsave(os.path.join(adir(u), "kb", "kb.json"), body["kb"])
            return h.send_json(200, {"ok": True, "username": u, "posts": len(doc["posts"]), "doc": doc})
        m = re.match(r"^/api/bench/([A-Za-z0-9_.]{1,30})/(analyze|imitate|kb)$", path)
        if m:
            u, act = m.groups()
            if not jload(os.path.join(adir(u), "account.json")) and not (act == "imitate" and isinstance(body.get("post"), dict)):
                return h.send_json(404, {"error": "account not found"})
            if act == "kb":
                return h.send_json(200, {"ok": True, "kb": build_kb(u)})
            if not S.api_key():
                return h.send_json(500, {"error": "ZENMUX_API_KEY not configured on server"})
            if act == "analyze":
                if body.get("stream"):
                    emit = h.start_stream()
                    try:
                        emit({"event": "done", "ok": True, "analysis": analyze(u, emit)})
                    except Exception as e:
                        emit({"event": "done", "ok": False, "error": _err(e)})
                    return
                return h.send_json(200, {"ok": True, "analysis": analyze(u)})
            opts = h.review_opts(body)
            if not opts:
                return
            roles = body.get("roles") or []
            if not isinstance(roles, list) or any(r not in S.AGENTS for r in roles):
                return h.send_json(400, {"error": "roles must be a list of role ids", "valid": list(S.PIPELINE)})
            pid = str(body.get("postId") or "")
            if body.get("stream"):
                emit = h.start_stream()
                lock = threading.Lock()
                def safe(e):
                    with lock:
                        emit(e)
                try:
                    emit({"event": "done", **imitate(u, pid, roles, *opts, emit=safe, post=body.get("post"))})
                except Exception as e:
                    emit({"event": "done", "ok": False, "error": _err(e)})
                return
            return h.send_json(200, imitate(u, pid, roles, *opts, post=body.get("post")))
        if path == "/api/queue":
            it = queue_add(body.get("text"), body.get("mode", "review"), body.get("scheduled_at"), body.get("source"))
            return h.send_json(200, {"ok": True, "item": it, "queue": queue_view()})
        m = re.match(r"^/api/queue/([0-9a-f]{14,24})$", path)
        if m:
            code, r = queue_action(m.group(1), body)
            return h.send_json(code, r)
    except (ValueError, RuntimeError, OSError) as e:
        return h.send_json(400, {"error": str(e)})
    return h.send_json(404, {"error": "not found"})
