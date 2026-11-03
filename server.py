#!/usr/bin/env python3
"""LucaAiAi 5-role agent server (Python stdlib only).

- Web UI:   GET  /            (index.html)
- REST API: GET  /api/health, GET /api/agents, POST /api/ask
            POST /api/strategy, GET /api/strategies, GET /api/strategies/<id>,
            POST /api/strategies/<id>/followup, POST /api/strategies/<id>/edit,
            GET  /api/strategies/<id>/versions/<n>.md
- Spec:     GET  /openapi.yaml

v1.3: every role output (debate turns + votes, strategy contributions + chief strategy, /api/ask answers)
goes through an AI review: code checks + AI checks by a different model; failures are rewritten by the
same role model and rechecked (max 2 retries). Opt out with {"review": false}; {"demo": true} = DEMO 出错模式.

Strategies are stored under data/<id>/ (strategy.json + LucaAiAi_策略_<date>_v<N>.md).

v1.4: 对标账号分析 (bench.py): fetch/import a competitor's posts, analysis report, knowledge base of hot posts,
imitation drafts by the 5 roles (reviewed), and a local post queue + scheduler. The server only posts to X when the
owner put the X_API_KEY/X_API_SECRET/X_ACCESS_TOKEN/X_ACCESS_SECRET keys in .env.local and queued a post
("审核后发" needs an explicit approve; "全自动发" posts at its slot). Otherwise it never talks to X except
optional reads with X_BEARER_TOKEN.
Key: env ZENMUX_API_KEY (or .env.local). Optional auth: env AGENTS_API_TOKEN.
"""
import hmac
import json, os, re, ssl, sys, time, threading, urllib.parse, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

BASE = os.path.dirname(os.path.abspath(__file__))


def load_env_file(path):
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v and not os.environ.get(k):  # real env wins
            os.environ[k] = v


load_env_file(os.path.join(BASE, ".env.local"))

CONFIG = json.load(open(os.path.join(BASE, "agents.json"), encoding="utf-8"))
# English-only UI: role prompts ask for English post drafts (instead of CN + EN pairs)
def _en_only_prompts(o):
    if isinstance(o, dict):
        return {k: _en_only_prompts(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_en_only_prompts(v) for v in o]
    if isinstance(o, str):
        for a, b in (("中英草稿", "英文草稿"), ("定稿的中英文帖子", "定稿的英文帖子"), ("中英各一版", "英文版"), ("中英两版", "英文版"), ("中英文帖子", "英文帖子")):
            o = o.replace(a, b)
    return o
CONFIG = _en_only_prompts(CONFIG)
AGENTS = {a["id"]: a for a in CONFIG["agents"]}
PIPELINE = CONFIG["pipeline"]
FINAL = CONFIG["finalAgent"]
ENDPOINT = os.environ.get("ZENMUX_ENDPOINT", CONFIG["endpoint"])
PORT = int(os.environ.get("PORT", "8787"))
HOST = os.environ.get("HOST", "127.0.0.1")
TIMEOUT = int(os.environ.get("ZENMUX_TIMEOUT", "180"))

LANG_HINT = {
    "zh": "请用简体中文回答（帖子草稿仍按要求给出英文版）。",
    "en": "Please answer in English. Write every post draft in English only (no Chinese version).",
}


def make_ssl_context():
    """python.org builds on macOS ship without CA certs (CERTIFICATE_VERIFY_FAILED).
    Keep verification on, but fall back to certifi or the macOS system bundle."""
    paths = ssl.get_default_verify_paths()
    if os.environ.get("SSL_CERT_FILE") or (paths.cafile and os.path.exists(paths.cafile)):
        return ssl.create_default_context()
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    for f in ("/etc/ssl/cert.pem", "/private/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"):
        if os.path.exists(f):
            return ssl.create_default_context(cafile=f)
    return ssl.create_default_context()


SSL_CTX = make_ssl_context()


def api_key():
    return os.environ.get("ZENMUX_API_KEY", "")


def clean(text):
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    return text.strip()


LANG_FORCE = {
    "en": "\n\n[OUTPUT LANGUAGE] The user interface is set to English. Write your ENTIRE reply in English "
          "(headings, analysis, JSON string values, review reasons, post drafts). Do not answer in Chinese even if the "
          "instructions or source posts are Chinese; keep Chinese only inside direct quotes of source posts. Quote source posts VERBATIM in their original language — never translate fetched posts, captions or comments. Write post drafts in English only (no Chinese versions). Use \"[to verify]\" instead of 【待核实】 and \"baseline TBD\" instead of 基线待采集. Every heading, field label and JSON value (topic names, descriptions, hooks, positioning, tags) must be in English: translate any Chinese labels the instructions give you (e.g. 帖子 → Post, 单图 → Single image, 轮播 → Carousel, 形式 → Format, 视觉概念 → Visual concept, 待解决问题 → Open questions). Never copy Chinese template labels into the answer.\n",
    "zh": "\n\n【输出语言】界面语言是中文：全部内容（标题、分析、JSON 字段值、审查理由、帖子草稿）都用简体中文输出；"
          "引用原帖必须一字不改、保留原文语言（英文帖就引用英文原文），不要翻译抓取到的帖子、文案或评论。\n",
}


def call_model(agent, messages, max_tokens=None, timeout=None):
    messages = [dict(m, content=personalize(m["content"])) if isinstance(m.get("content"), str) else m for m in messages]
    lf = LANG_FORCE.get(req_lang())
    if lf:
        if messages and messages[0].get("role") == "system":
            messages[0] = dict(messages[0], content=messages[0]["content"] + lf)
        else:
            messages = [{"role": "system", "content": lf.strip()}] + messages
    if req_lang() == "en" and messages and messages[-1].get("role") == "user" and isinstance(messages[-1].get("content"), str):
        messages[-1] = dict(messages[-1], content=messages[-1]["content"] + "\n\n[Reminder] Answer entirely in English, including all headings, labels and JSON values. "
                            "Only verbatim quotes of source posts may stay in their original language.")
    hdr = account_header()
    if hdr:
        if messages and messages[0].get("role") == "system":
            messages[0] = dict(messages[0], content=hdr + messages[0]["content"])
        else:
            messages = [{"role": "system", "content": hdr.strip()}] + messages
    body = {"model": agent["model"], "messages": messages,
            "max_tokens": max(max_tokens or 0, agent.get("max_tokens", 2500))}
    if agent.get("sendTemperature", True) and agent.get("temperature") is not None:
        body["temperature"] = agent["temperature"]  # Claude: never sent (sendTemperature=false)
    body.update(agent.get("extraBody") or {})  # e.g. Qwen: {"reasoning": {"enabled": false}}
    data = json.dumps(body).encode()
    last_err = None
    for attempt in range(2):
        req = urllib.request.Request(ENDPOINT, data=data, method="POST", headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + api_key(),
        })
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout or TIMEOUT, context=SSL_CTX) as r:
                j = json.loads(r.read().decode())
            choice = j["choices"][0]
            content = clean(choice["message"].get("content") or "")
            if not content:
                raise RuntimeError("empty content (finish_reason=%s)" % choice.get("finish_reason"))
            return {"content": content, "usage": j.get("usage"),
                    "finishReason": choice.get("finish_reason"), "ms": int((time.time() - t0) * 1000)}
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")[:300]
            last_err = "HTTP %s: %s" % (e.code, msg)
            if e.code < 500 and e.code != 429:
                break
        except Exception as e:  # network / parse
            last_err = "%s: %s" % (type(e).__name__, e)
        time.sleep(1.5)
    raise RuntimeError(last_err)


def run_agent(agent_id, question, lang=None, previous=None, pipeline_mode=False, review=True, demo=False, emit=None):
    a = AGENTS[agent_id]
    extra = LANG_HINT.get(lang, "") if lang in LANG_HINT else ""
    user = "【账号主人的需求】\n" + question
    if pipeline_mode:
        if previous:
            user += "\n\n【前面岗位的产出】"
            for p in previous:
                if p.get("output"):
                    user += "\n\n===== %s（%s）=====\n%s" % (p["name"], p["model"], p["output"])
                else:
                    user += "\n\n===== %s：本轮失败，无产出 =====" % p["name"]
        user += "\n\n【你在本轮的任务】\n" + a["pipelineTask"]
    ctx = ""
    if pipeline_mode and previous:
        ctx = "\n\n".join("%s：%s" % (p["name"], p.get("output", "")) for p in previous if p.get("output"))
    res = reviewed_role(agent_id, user, "ask", question, max_tokens=a.get("max_tokens", 2500), extra_system=extra,
                        context=ctx, lang=lang, review=review, demo=demo, emit=emit)
    res.pop("role", None)
    return res


def ask(agent, question, lang=None, on_step=None, review=True, demo=False):
    res = _ask(agent, question, lang, on_step, review, demo)
    if review:
        res["review"] = {"enabled": True, "demo": bool(demo), **(review_summary(res["outputs"]) or {})}
    return res


def _ask(agent, question, lang, on_step, review, demo):
    if agent == "all":
        outs = []
        for aid in PIPELINE:
            if on_step:
                on_step({"event": "start", "agent": aid, "name": AGENTS[aid]["name"], "model": AGENTS[aid]["model"]})
            r = run_agent(aid, question, lang, previous=list(outs), pipeline_mode=True, review=review, demo=demo, emit=on_step)
            outs.append(r)
            if on_step:
                on_step({"event": "agent", **r})
        final = next((o for o in outs if o["agent"] == FINAL), None)
        return {"mode": "all", "question": question, "outputs": outs,
                "final": final["output"] if final and final["ok"] else "",
                "finalAgent": FINAL, "ok": all(o["ok"] for o in outs)}
    if on_step:
        on_step({"event": "start", "agent": agent, "name": AGENTS[agent]["name"], "model": AGENTS[agent]["model"]})
    r = run_agent(agent, question, lang, review=review, demo=demo, emit=on_step)
    if on_step:
        on_step({"event": "agent", **r})
    return {"mode": "single", "question": question, "outputs": [r],
            "final": r["output"], "finalAgent": agent, "ok": r["ok"]}


# ---------------------------------------------------------------------------
# Strategy mode: 5 roles contribute, chief integrates, versions persisted in data/
# ---------------------------------------------------------------------------
DATA_ROOT = os.environ.get("DATA_DIR", os.path.join(BASE, "data"))
ACCOUNTS_DIR = os.path.join(DATA_ROOT, "accounts")
HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
_TL = threading.local()   # per-thread account override (scheduler)
ACTIVE = {"handle": None}



def en_error(msg, code=None):
    """English UI: replace a Chinese server error with an English one (never mix languages)."""
    h = re.search(r"@([A-Za-z0-9_.]{1,30})", msg); u = ("@" + h.group(1)) if h else "this account"
    http = re.search(r"HTTP (\d{3})", msg); hc = (" (HTTP %s)" % http.group(1)) if http else ""
    rules = [
        (r"抓取 @?\S* ?失败", "Couldn't fetch %s%s. The free public sources may be rate-limited or the account wasn't found. Click Retry in a moment, or use Import JSON / Load sample." % (u, hc)),
        (r"Apify 拒绝请求", "Apify rejected the request%s: the token is invalid or out of credits." % hc),
        (r"Apify 额度不足", "Apify is out of credits (HTTP 402). Check usage in the Apify console."),
        (r"连接 Apify 失败", "Couldn't reach Apify. Please retry in a moment."),
        (r"Apify 没有返回|Apify 返回空", "Apify returned no data for %s (the account may not exist, was renamed or is restricted)." % u),
        (r"没有配置 APIFY_TOKEN", "APIFY_TOKEN isn't configured on the server. Use Import JSON or Load sample instead."),
        (r"没有可读取的公开帖子|没有读到原创帖子|没有 .*原创帖子", "%s has no readable public posts (it may be private)." % u),
        (r"受保护账号", "%s is a protected account; its posts can't be read." % u),
        (r"找不到账号", "Account %s wasn't found." % u),
        (r"用户名不合法", "Invalid username."),
        (r"ZENMUX_API_KEY", "The AI key isn't configured on the server."),
        (r"请求过于频繁|太频繁|too many", "Too many requests. Please wait a few minutes and try again."),
    ]
    for pat, en in rules:
        if re.search(pat, msg):
            return en
    return "Request failed%s. Please retry in a moment." % ((" (HTTP %s)" % code) if code else "")

def req_lang():
    """v1.6: UI language for this request (X-Lang header / body lang), 'zh' | 'en' | None."""
    return getattr(_TL, "lang", None)


class TLPool(ThreadPoolExecutor):
    """ThreadPoolExecutor that carries the request's account + UI language into worker threads."""
    def submit(self, fn, *args, **kwargs):
        h, l = getattr(_TL, "handle", None), getattr(_TL, "lang", None)

        def run(*a, **k):
            _TL.handle, _TL.lang = h, l
            try:
                return fn(*a, **k)
            finally:
                _TL.handle = _TL.lang = None
        return super().submit(run, *args, **kwargs)


def _active_file():
    return os.path.join(DATA_ROOT, "active.json")


def migrate_legacy():
    """v1.4 -> v1.5: data/<sid>/, data/debates/, data/bench/ move into data/accounts/LucaAiAi/."""
    os.makedirs(ACCOUNTS_DIR, exist_ok=True)
    legacy = [n for n in os.listdir(DATA_ROOT) if n not in ("accounts", "active.json")
              and (n in ("debates", "bench") or os.path.exists(os.path.join(DATA_ROOT, n, "strategy.json")))]
    if not legacy:
        return
    dst = os.path.join(ACCOUNTS_DIR, "LucaAiAi")
    os.makedirs(dst, exist_ok=True)
    for n in legacy:
        t = os.path.join(dst, n)
        if not os.path.exists(t):
            os.rename(os.path.join(DATA_ROOT, n), t)
    if not os.path.exists(os.path.join(dst, "profile.json")):
        json.dump({"handle": "LucaAiAi", "name": "LucaAiAi", "builtin": True, "positioning": "AI explainers",
                   "short": "AI explainers", "audience": "AI practitioners, tech and go-global builders, people who want to work faster with AI",
                   "pillars": ["AI products and product lines", "How to use AI better"], "createdAt": iso()},
                  open(os.path.join(dst, "profile.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if not os.path.exists(_active_file()):
        json.dump({"handle": "LucaAiAi"}, open(_active_file(), "w"))


def active():
    h = getattr(_TL, "handle", None) or ACTIVE["handle"]
    if h:
        return h
    try:
        h = json.load(open(_active_file()))["handle"]
    except Exception:
        h = None
    ACTIVE["handle"] = h
    return h or "_none"


def set_active(h):
    h = canon_handle(h)
    ACTIVE["handle"] = h
    json.dump({"handle": h}, open(_active_file(), "w"))


def canon_handle(h):
    """v1.6: handles are case-insensitive on X (and on the macOS filesystem). Map any casing to the existing folder name."""
    h = str(h or "").lstrip("@")
    if os.path.isdir(ACCOUNTS_DIR):
        for n in os.listdir(ACCOUNTS_DIR):
            if n.lower() == h.lower():
                return n
    return h


def acct_dir(h=None):
    d = os.path.join(ACCOUNTS_DIR, h or active())
    os.makedirs(d, exist_ok=True)
    return d


def profile(h=None):
    h = canon_handle(h) if h else active()
    try:
        p = json.load(open(os.path.join(ACCOUNTS_DIR, h, "profile.json"), encoding="utf-8"))
        p["handle"] = h   # folder name is the source of truth for the handle
        return p
    except Exception:
        return None


def list_profiles():
    out = []
    if os.path.isdir(ACCOUNTS_DIR):
        for n in sorted(os.listdir(ACCOUNTS_DIR), key=str.lower):
            p = profile(n)
            if p:
                p["handle"] = n
                out.append({k: p.get(k) for k in ("handle", "name", "positioning", "short", "followers", "avatar", "createdAt", "source", "quick")})
    return out


def personalize(text):
    """Prompts were written for @LucaAiAi; rewrite them for the active account (keeps LucaAiAi untouched)."""
    h = active()
    if not text:
        return text
    if h.lower() == "lucaaiai":
        # Default (built-in) account: keep its AI-explainer positioning but refer to it neutrally, so answers
        # never mention the handle or the owner's name (owner request).
        t = text.replace("骊元（Luca）", "账号主人").replace("供 Luca 审", "供账号主人审").replace("Luca 审核", "账号主人审核")
        t = re.sub(r"@LucaAiBtc[、/ ]*@YuanAiDC|Btc/YuanAiDC", "其他账号", t)
        t = t.replace("@LucaAiAi (AI 科普)", "the account (AI 科普)").replace("@LucaAiAi (", "the account (")
        t = t.replace("（@LucaAiAi）", "").replace("@LucaAiAi", "本账号").replace("LucaAiAi_", "").replace("LucaAiAi", "本账号")
        return t
    p = profile(h) or {}
    pos, short = p.get("positioning") or "（定位待确认）", p.get("short") or "本账号主题"
    t = re.sub(r"- 定位：AI 科普[^\n]*", "- 定位：%s。内容主线：%s" % (pos, "；".join(p.get("pillars") or []) or "见账号定位"), text)
    t = re.sub(r"- 受众：[^\n]*", "- 受众：%s" % (p.get("audience") or "【待确认】"), t)
    t = t.replace("骊元（Luca）", "账号主人").replace("Luca 审核", "账号主人审核")
    t = re.sub(r"@LucaAiBtc[、/ ]*@YuanAiDC|Btc/YuanAiDC", "其他账号", t)
    t = t.replace("@LucaAiAi", "@" + h).replace("LucaAiAi_", h + "_").replace("LucaAiAi", h)
    t = t.replace("中文 AI 科普博主", short + "博主").replace("AI 科普", short)
    t = t.replace("支线 A", "主线 A").replace("支线 B", "主线 B")
    return t


def account_header():
    h = active()
    p = profile(h) or {}
    if h.lower() == "lucaaiai" or not p:
        return ""
    return ("【当前服务的账号（优先于下文任何账号背景）】@%s（%s）\n定位：%s\n受众：%s\n内容主线：%s\n语气/风格：%s\n"
            "粉丝约 %s。下文的「主线 A / 主线 B」= 上面的内容主线。只谈 @%s，不混入其他账号。\n\n" %
            (h, p.get("name") or h, p.get("positioning") or "【待确认】", p.get("audience") or "【待确认】",
             "；".join(p.get("pillars") or []) or "【待确认】", p.get("tone") or "沿用账号近期帖子风格",
             p.get("followers", "【待核实】"), h))


DATA_DIR = DATA_ROOT  # legacy name; per-account data uses acct_dir()
TZ = timezone(timedelta(hours=int(os.environ.get("TZ_OFFSET_HOURS", "8"))))
STRATEGY_TIMEOUT = int(os.environ.get("STRATEGY_TIMEOUT", "300"))
CHIEF_MAX_TOKENS = int(os.environ.get("CHIEF_MAX_TOKENS", "8000"))
ROLE_MAX_TOKENS = int(os.environ.get("ROLE_MAX_TOKENS", "3000"))
STORE_LOCK = threading.Lock()
ID_RE = re.compile(r"^[0-9A-Za-z_-]{1,64}$")
CHANGELOG_MARK = "<<<CHANGELOG>>>"

STRATEGY_RULES = """
【策略模式的额外规则（优先于上面的字数限制）】
- 本轮是为 @LucaAiAi 制定 / 修订 X 运营策略，不是写单条帖子。
- 只出方案和草稿：不发帖、不关注、不评论、不私信，也不说"已发布 / 已执行"。
- 不编造任何数字：没有给出的数据（粉丝数、阅读量、互动率、价格、日期、型号、handle、链接），一律写【待核实】或"基线待采集"。KPI 只写指标口径和采集方法；如需目标值，只能写成相对基线的方向（例如"较第 1 周基线提升"），或标【待核实】由账号主人定。
- 无法确认的事实一律标【待核实】，并写明去哪个官方渠道核实。
- 用 Markdown，多用表格和清单。"""

ROLE_STRATEGY_TASKS = {
    "researcher": "从你的岗位出发，为这份运营策略提供：1) 内容支柱建议（支线 A / 支线 B 各 2–3 个支柱，写清每个支柱讲什么、为什么适合这个账号）；2) 首批 8–10 个选题池（标题 / 支线 / 证据等级 / 建议形式 / 需核实渠道）；3) 需要长期跟踪的信息源类型（只写类型和官方渠道名称，不写具体链接）。约 800–1200 字。",
    "editor": "从你的岗位出发，为这份运营策略提供：1) 内容形式组合（单条 / 线程 / 图解 / 对比表等）和每周比例；2) 语气、英文表达方式和 2–3 个可复用的帖子模板；3) 视觉规范（配图类型、尺寸、风格）；4) 1 条示范帖草稿（英文版，事实不确定处标【待核实】）。约 800–1200 字。",
    "community": "从你的岗位出发，为这份运营策略提供：1) 社区互动计划（每天的互动时段与动作，关注节奏遵守每月 200 个上限、养号期不私信不放外链）；2) 应该互动的账号类型（只写类型，不写具体 handle）；3) 评论回复原则和 3 条评论草稿；4) 需要标记给账号主人的风险信号。约 800–1200 字。",
    "data": "从你的岗位出发，为这份运营策略提供：1) 指标与 KPI 体系（指标 / 口径 / 采集方法 / 频率，目标值没有基线就写'基线待采集'）；2) 每日跟进记录模板（账号主人每天填写的字段）；3) 每周复盘流程（SOP，谁在什么时间做什么）；4) 流程风险与停止条件。约 800–1200 字。",
}

CHIEF_STRATEGY_TASK = """你是策略整合人。请把账号主人的问题和 4 个岗位的建议，整合成**一份完整、可执行的 X 运营策略**（中文为主，可附英文关键词）。
严格使用下面的 Markdown 结构和标题（不要增删一级 / 二级标题）：

# @LucaAiAi X 运营策略
## 0. 结论摘要（3–5 条）
## 1. 账号定位（受众 / 价值主张 / 内容边界 / 语言策略）
## 2. 内容支柱（表格：支柱 / 支线 / 讲什么 / 占比 / 示例选题）
## 3. 每周内容日历（表格：周一到周日 / 时段 UTC+8 / 支柱 / 形式 / 负责人）
## 4. 内容形式与模板
## 5. 社区互动计划
## 6. 指标与 KPI（表格：指标 / 口径 / 采集方法 / 频率 / 目标或"基线待采集"）
## 7. 风险与合规（含停止条件）
## 8. 未来 7 天行动清单（表格：日期序号 D1–D7 / 动作 / 负责岗位 / 交付物 / 是否需账号主人确认）
## 9. 待核实清单（事实点 / 需核实渠道）

要求：取各岗位之长并解决冲突；不重复各岗位的长篇原文；全部是草稿，由账号主人审核决定；未给出的数字一律不编造。总长度约 2500–4000 字。"""

CHIEF_FOLLOWUP_TASK = """你是策略的维护人。账号主人刚写了今天的跟进记录（进展 / 数据 / 想改的地方）。请据此修订当前策略：
1. 输出修订后的**完整策略**，保持原有的 Markdown 标题结构（# 标题和 ## 0–9 节），不要只输出改动部分。
2. 只根据账号主人提供的信息调整；他给出的真实数据可以写进去，并注明"（账号主人 {date} 提供）"；没有提供的数据继续写【待核实】或"基线待采集"，不得编造。
3. 第 8 节"未来 7 天行动清单"要按今天的情况滚动更新。
4. 完整策略之后，单独一行写 <<<CHANGELOG>>>，然后用 3–8 条短清单写"本次变更"（改了哪一节、为什么）。"""

ROLE_FOLLOWUP_TASK = "账号主人刚写了今天的跟进记录。请只从你的岗位出发，指出当前策略里需要调整的地方，并给出具体修改建议（3–6 条，约 300–500 字）。没有提供的数据不要编造，写【待核实】。"


def now():
    return datetime.now(TZ)


def iso(dt=None):
    return (dt or now()).isoformat(timespec="seconds")


def run_role(agent_id, user, max_tokens=None, extra_system=STRATEGY_RULES):
    a = AGENTS[agent_id]
    res = {"agent": agent_id, "name": a["name"], "nameEn": a["nameEn"], "model": a["model"],
           "role": a.get("role", "")}
    started = time.time()
    try:
        r = call_model(a, [{"role": "system", "content": a["systemPrompt"] + "\n" + extra_system},
                           {"role": "user", "content": user}],
                       max_tokens=max_tokens or ROLE_MAX_TOKENS, timeout=STRATEGY_TIMEOUT)
        res.update(ok=True, output=r["content"], usage=r["usage"], finishReason=r["finishReason"])
    except Exception as e:
        res.update(ok=False, output="", error=str(e))
    res["ms"] = int((time.time() - started) * 1000)
    return res


# ---------------------------------------------------------------------------
# AI review ("Make It Fail"): code checks + AI checks by a different model,
# failures sent back to the original role model to rewrite, max 2 retries.
# ---------------------------------------------------------------------------
REVIEW_MAX_RETRIES = int(os.environ.get("REVIEW_MAX_RETRIES", "2"))
REVIEWER_DEFAULT = os.environ.get("REVIEWER_AGENT", "chief")       # Claude
REVIEWER_FALLBACK = os.environ.get("REVIEWER_FALLBACK", "community")  # Gemini (when Claude is the speaker)
REVIEWER_ALT = "researcher"  # DeepSeek: retry once if the reviewer returns no valid JSON
TURN_TARGET, TURN_LIMIT = 150, 200
POST_LIMIT = 280
REVIEW_ORDER = ["length", "vote", "post_length", "no_action_claims", "trained", "language",
                "fabrication", "false_verified", "responds", "in_role", "account_scope", "no_auto_interaction"]
CHECK_NAMES = {
    "length": "发言长度（约150字，≤200）", "vote": "投票格式+理由", "post_length": "帖子草稿 ≤280（加权）",
    "no_action_claims": "不声称已发布/已关注/已发送", "trained": "不说自己\"被训练\"", "language": "语言一致",
    "fabrication": "不编造数字/价格/日期/handle", "false_verified": "未核实的不标已核实",
    "responds": "回应上一位发言人", "in_role": "符合岗位定位", "account_scope": "只谈本账号（不混入其他账号）",
    "no_auto_interaction": "不承诺自动互动",
}
KIND_LABEL = {"turn": "辩论发言", "vote": "投票", "contrib": "岗位建议", "strategy": "主编策略", "ask": "问答回答"}

DEMO_SYSTEM = """【DEMO 出错模式 —— 仅用于现场演示 AI 审查抓错】
你是 @LucaAiAi 运营团队里一个浮夸、过度自信、不守规矩的「%s」。这一稿要故意写得很随意，方便演示审查系统抓错：
- 写得很长（至少 350 字），一大段口语化的话，不管任何字数限制；
- 至少编 2 个具体数字当成事实（例如"互动率 12.7%%""上周涨粉 3400""竞品 @某某 一天 50 万阅读"），不要标【待核实】，还把其中一个写成"已核实"；
- 声称自己已经做过事情，例如"我已经发布了 3 条测试帖""我已关注了 50 个大号""私信已发送"；
- 顺带承诺之后会自动点赞、自动回复评论、自动关注；
- 不要回应前面任何人的观点；如果任务要求投票格式，也不要按格式写。
只输出这段发言本身。"""


def x_weighted_len(text):
    """X (twitter-text v3) weighted length: most CJK/emoji = 2, Latin = 1, URLs = 23."""
    t = re.sub(r"https?://\S+", "x" * 23, text)
    n = 0
    for ch in t:
        cp = ord(ch)
        if cp <= 4351 or 8192 <= cp <= 8205 or 8208 <= cp <= 8223 or 8242 <= cp <= 8247:
            n += 1
        else:
            n += 2
    return n


def cjk_count(s):
    return len(re.findall(r"[\u4e00-\u9fff]", s or ""))


def latin_count(s):
    return len(re.findall(r"[A-Za-z]", s or ""))


def detect_lang(s):
    c, l = cjk_count(s), latin_count(s)
    return "zh" if c >= max(2, l * 0.25) else "en"


def turn_len(text):
    t = re.sub(r"【待核实】|[*#`>_|]", "", text or "")
    if detect_lang(t) == "en":  # v1.6: English turns: ~1.5 Chinese chars per English word
        return int(cjk_count(t) + 1.5 * len(re.findall(r"[A-Za-z0-9'’-]+", t)))
    return len(re.sub(r"\s+", "", t))


POST_HINT_RE = re.compile(r"草稿|帖子|示范帖|推文|线程|中文版|英文版|English|tweet|post|thread", re.I)
POST_LABEL_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:\*\*)?\s*(?:\d+[./、]\s*)?(?:稿件\s*\w*\s*)?"
    r"(中文版|英文版|中文稿|英文稿|中文草稿|英文草稿|English(?: version| draft)?|CN|EN|备选版本|示范帖|帖子草稿|评论草稿|推文草稿|Post|Tweet)"
    r"[^:：\n]{0,24}?(?:\*\*)?\s*[:：]\s*(?:\*\*)?(.*)$", re.I)
ANNOT_RE = re.compile(r"[（(]\s*(?:X\s*)?(?:加权|weighted)[^）)]*[）)]|(?:X\s*)?加权(?:字数|计数)?\s*[:：]?\s*约?\d+\s*/?\s*\d*|weighted(?: count)?\s*[:：]?\s*~?\d+(?:\s*/\s*\d+)?", re.I)


def extract_post_drafts(text):
    """Heuristic: labelled drafts (中文版：/ English: / 示范帖：…), > blockquotes and ``` blocks."""
    if not POST_HINT_RE.search(text or ""):
        return []
    blocks, lines, i = [], (text or "").replace("\r", "").split("\n"), 0
    stop_re = re.compile(r"^\s*(#{1,6}\s|\||```|---)")
    while i < len(lines):
        ln = lines[i]
        if ln.strip().startswith("```"):
            b, i = [], i + 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                b.append(lines[i]); i += 1
            i += 1
            blocks.append(("代码块", "\n".join(b)))
            continue
        if re.match(r"^\s*>", ln):
            b = []
            while i < len(lines) and re.match(r"^\s*>", lines[i]):
                b.append(re.sub(r"^\s*>\s?", "", lines[i])); i += 1
            blocks.append(("引用块", "\n".join(b)))
            continue
        m = POST_LABEL_RE.match(ln)
        if m:
            label, rest = m.group(1), m.group(2).strip()
            b, i = ([rest] if rest else []), i + 1
            while i < len(lines) and lines[i].strip() and not stop_re.match(lines[i]) \
                    and not POST_LABEL_RE.match(lines[i]) and not re.match(r"^\s*>", lines[i]):
                b.append(lines[i]); i += 1
            blocks.append((label, "\n".join(b)))
            continue
        i += 1
    out = []
    for label, b in blocks:
        b = ANNOT_RE.sub("", b).replace("**", "").strip().strip("「」\"“”")
        if len(re.sub(r"\s+", "", b)) >= 15:
            out.append((label, b))
    return out


ACTION_RE = re.compile(r"已(?:经)?(?:成功)?(?:发布|发帖|发出|发送|发了|关注了?|私信|评论|回复了|点赞|转发|执行|上线)"
                       r"|I(?:'ve| have)\s+(?:already\s+)?(?:posted|published|followed|sent|DMed|replied)"
                       r"|(?:has|have) been (?:posted|published|sent)", re.I)
NEG_RE = re.compile(r"(不|未|别|勿|没有|没|禁止|避免|不得|不能|不会|绝不|不说|不写|不要|不声称|假装|声称|说成)[^，。；\n]{0,6}$")
TRAINED_RE = re.compile(r"(?:我|咱|本模型|作为\s*AI)(?:们)?[^，。！？\n]{0,6}(?:被|经过|受过|接受过?)[^，。！？\n]{0,6}训练(?!营)"
                        r"|\bI(?:'ve| have| was| am| 've been)?\s*(?:been\s+)?trained\b|\bmy training (?:data|cutoff)\b", re.I)


def find_action_claims(text):
    hits = []
    for m in ACTION_RE.finditer(text or ""):
        before = text[max(0, m.start() - 10):m.start()]
        after = text[m.end():m.end() + 1]
        if NEG_RE.search(before) or after == "的" or re.search(r"[「“\"'‘]$", before):
            continue  # "不说已发布" / "已发布的帖子" / quoted rule text
        hits.append(m.group(0))
    return hits


def code_checks(kind, question, text, lang=None):
    checks = []

    def add(cid, ok, reason, skipped=False):
        c = {"id": cid, "name": CHECK_NAMES[cid], "pass": bool(ok), "reason": reason, "by": "code"}
        if skipped:
            c["skipped"] = True
        checks.append(c)
    if kind == "turn":
        n = turn_len(text)
        add("length", n <= TURN_LIMIT, "%d 字（目标约 %d，上限 %d）%s" % (
            n, TURN_TARGET, TURN_LIMIT, "" if n <= TURN_LIMIT else "，超出 %d 字" % (n - TURN_LIMIT)))
    if kind == "vote":
        m = re.search(r"投票\s*[:：]\s*\**\s*(有条件支持|支持|反对)", text or "")
        r = re.search(r"理由\s*[:：]\s*(.+)", text or "")
        rs = (r.group(1).strip() if r else "")
        ok = bool(m) and len(rs) >= 2
        add("vote", ok, ("投票：%s，理由 %d 字" % (m.group(1), len(rs))) if ok else
            ("没有「投票：支持/反对/有条件支持」行" if not m else "有投票但缺少「理由：」一句话"))
    if kind != "turn" and kind != "vote":
        posts = extract_post_drafts(text)
        if posts:
            over = [(lb, x_weighted_len(b)) for lb, b in posts if x_weighted_len(b) > POST_LIMIT]
            add("post_length", not over, ("%d 条草稿都 ≤280（最长 %d）" % (len(posts), max(x_weighted_len(b) for _, b in posts)))
                if not over else "超长：" + "；".join("%s 加权 %d" % (lb, n) for lb, n in over))
    hits = find_action_claims(text)
    add("no_action_claims", not hits, "未发现" if not hits else "出现「%s」" % "」「".join(sorted(set(hits))[:5]))
    tm = TRAINED_RE.search(text or "")
    add("trained", not tm, "未发现" if not tm else "出现「%s」" % tm.group(0))
    lang = lang if lang in ("zh", "en") else req_lang()
    want = lang if lang in ("zh", "en") else detect_lang(question)
    got = detect_lang(text)
    ok = (got == want) or (want == "en" and latin_count(text) >= 80)
    if req_lang() == "en":
        add("language", ok, "required %s / got %s" % ("Chinese" if want == "zh" else "English", "Chinese" if got == "zh" else "English"))
    else:
        add("language", ok, "要求 %s / 回答 %s" % ("中文" if want == "zh" else "英文", "中文" if got == "zh" else "英文"))
    return checks


def reviewer_for(aid):
    return REVIEWER_FALLBACK if aid == REVIEWER_DEFAULT else REVIEWER_DEFAULT


def parse_json_obj(s):
    t = re.sub(r"```(?:json)?", "", s or "")
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b < a:
        raise ValueError("reviewer returned no JSON")
    return json.loads(t[a:b + 1])


REVIEW_SYS = ("You are a strict compliance reviewer for an internal X (Twitter) operations drafting tool for the account "
              "@LucaAiAi (AI 科普). You judge another AI's output against rules and output ONLY one JSON object, "
              "no prose, no code fences.")


def ai_checks(aid, kind, question, text, context="", prev=None):
    a = AGENTS[aid]
    has_prev = kind == "turn" and bool((prev or "").strip())
    user = ("ROLE BRIEF of the author \"%s\" (%s) — its system prompt; account facts stated here count as supplied:\n\"\"\"%s\"\"\"\n\n"
            "OWNER'S QUESTION / TOPIC (user-supplied):\n\"\"\"%s\"\"\"\n\n" % (a["name"], a.get("role", ""), a["systemPrompt"][:2200], question))
    if context.strip():
        user += "MATERIAL THE AUTHOR WAS GIVEN (numbers/handles appearing here count as supplied):\n\"\"\"%s\"\"\"\n\n" % context[-6000:]
    if has_prev:
        user += "PREVIOUS SPEAKER'S MESSAGE (the output should respond to it):\n\"\"\"%s\"\"\"\n\n" % prev
    user += ("OUTPUT TO REVIEW (type: %s):\n\"\"\"%s\"\"\"\n\n" % (kind, text[:9000]))
    user += """Judge these rules:
A. fabrication: FAIL if the output states AS FACT any specific statistic, metric, follower/view/engagement number, percentage, price, release date, product version, case result, or real @handle/link that is NOT in the supplied material above AND is not marked 【待核实】/待核实/"基线待采集"/"建议值" or clearly framed as an assumption/example/target to be decided (假设/例如/建议/目标待定). ALLOWED: planning choices (posting times, frequency, ratios, D1–D7, durations), list numbering, the 280 limit, the 200/month follow cap, "【待核实】" placeholders. List each offending item.
B. false_verified: FAIL if something that cannot have been verified (the tool has no internet) is labelled 已核实/verified/官方确认 as fact. Otherwise pass.
C. responds_to_previous: %s
D. in_role: Does the output stay within the author's role positioning from the ROLE BRIEF (e.g. data analyst talks metrics/process, community talks interaction plans)? Pass unless clearly off-role or speaking as another persona.
E. account_scope: FAIL if it mixes in material from, or plans for, another X account. Otherwise pass.
F. no_auto_interaction: FAIL if it promises or claims to automatically post, follow, like, reply, DM, or otherwise act on X on the owner's behalf (drafts/plans for the owner to execute manually are fine).

Return exactly:
{"fabrication":{"pass":true|false,"reason":"<short Chinese>","items":["..."]},"false_verified":{"pass":true|false,"reason":"<short Chinese>"},"responds_to_previous":{"pass":true|false,"reason":"<short Chinese>"},"in_role":{"pass":true|false,"reason":"<short Chinese>"},"account_scope":{"pass":true|false,"reason":"<short Chinese>"},"no_auto_interaction":{"pass":true|false,"reason":"<short Chinese>"}}""" % (
        "Does the output explicitly engage with the previous speaker's point (agree, rebut, build on it)?" if has_prev
        else "No previous message — set pass=true, reason \"N/A\".")
    msgs = [{"role": "system", "content": REVIEW_SYS}, {"role": "user", "content": user}]
    rid = reviewer_for(aid)
    used = rid
    try:
        rv = dict(AGENTS[rid], temperature=0, max_tokens=900)
        j = parse_json_obj(call_model(rv, msgs, max_tokens=900, timeout=120)["content"])
    except Exception:
        used = REVIEWER_ALT if rid != REVIEWER_ALT else REVIEWER_FALLBACK
        rv = dict(AGENTS[used], temperature=0, max_tokens=900)
        j = parse_json_obj(call_model(rv, msgs, max_tokens=900, timeout=120)["content"])

    def g(k):
        v = j.get(k)
        return v if isinstance(v, dict) else {"pass": True, "reason": "reviewer omitted"}
    fab = g("fabrication")
    items = [str(x) for x in (fab.get("items") or []) if str(x).strip()]
    out = [{"id": "fabrication", "pass": bool(fab.get("pass")),
            "reason": str(fab.get("reason", "")) + ("：" + "；".join(items[:6]) if items and not fab.get("pass") else "")},
           {"id": "false_verified", **{k: g("false_verified").get(k) for k in ("pass", "reason")}}]
    if has_prev:
        out.append({"id": "responds", **{k: g("responds_to_previous").get(k) for k in ("pass", "reason")}})
    elif kind == "turn":
        out.append({"id": "responds", "pass": True, "skipped": True, "reason": "第一个发言，无需回应"})
    out += [{"id": "in_role", **{k: g("in_role").get(k) for k in ("pass", "reason")}},
            {"id": "account_scope", **{k: g("account_scope").get(k) for k in ("pass", "reason")}},
            {"id": "no_auto_interaction", **{k: g("no_auto_interaction").get(k) for k in ("pass", "reason")}}]
    for c in out:
        c.update(name=CHECK_NAMES[c["id"]], by="ai", reviewer=AGENTS[used]["model"])
        c["pass"] = bool(c["pass"])
        c["reason"] = str(c.get("reason") or "")
    return out, used


def review_text(aid, kind, question, text, context="", prev=None, lang=None):
    t0 = time.time()
    code = code_checks(kind, question, text, lang)
    try:
        ai, used = ai_checks(aid, kind, question, text, context, prev)
    except Exception as e:  # reviewer down: don't block the user; record it
        used = reviewer_for(aid)
        ai = [{"id": "fabrication", "name": "AI 审查", "pass": True, "skipped": True, "by": "ai",
               "reason": "审查模型调用失败，已跳过 AI 检查：%s" % str(e)[:160]}]
    checks = sorted(code + ai, key=lambda c: REVIEW_ORDER.index(c["id"]) if c["id"] in REVIEW_ORDER else 99)
    return {"checks": checks, "passed": all(c["pass"] for c in checks),
            "reviewer": AGENTS[used]["model"], "ms": int((time.time() - t0) * 1000)}


def fix_rules(kind, lang):
    en = lang == "en"
    r = []
    if kind == "turn":
        r.append("一段话，约 %d 字，绝对不超过 %d 字；不用标题、加粗、清单；开头不写自己的名字。" % (TURN_TARGET, TURN_LIMIT))
    if kind == "vote":
        r.append("严格两行：第一行「投票：支持/反对/有条件支持」，第二行「理由：一句话，40 字以内」。")
    if kind in ("ask", "contrib", "strategy"):
        r.append("保持原任务要求的结构和格式（包括标题、表格，以及原任务要求的 <<<CHANGELOG>>> 段落，如有）；每条 X 帖子草稿按加权口径 ≤280（中日韩字符算 2）。")
    r += ["删除没有依据的具体数字、价格、日期、handle、案例结果，或改成【待核实】/\"基线待采集\"；没法核实的不能写\"已核实\"。",
          "不说已发布 / 已关注 / 已发送 / 已私信 / 已执行；不承诺自动点赞、自动回复、自动关注，一切都是给账号主人审核的草稿。",
          "只谈本账号，不混入其他账号的资料。",
          "保持你的岗位定位；不说自己被训练。",
          "Reply in English; post drafts in English only; write [to verify] instead of 【待核实】." if en else "用中文回答（帖子草稿可按岗位要求附英文版）。"]
    return r


def reviewed_role(aid, user, kind, question, *, max_tokens=None, extra_system=STRATEGY_RULES, context="",
                  prev=None, lang=None, review=True, demo=False, post=None, emit=None, meta=None):
    """run_role + review/fix loop. Returns the run_role dict (output = final text) + 'review'."""
    meta = meta or {}
    t0 = time.time()

    def ev(e):
        if emit:
            emit({**e, **meta, "agent": aid, "name": AGENTS[aid]["name"], "kind": kind})
    if demo:  # sloppy system prompt instead of the role's real one
        a = AGENTS[aid]
        r = {"agent": aid, "name": a["name"], "nameEn": a["nameEn"], "model": a["model"], "role": a.get("role", "")}
        try:
            c = call_model(a, [{"role": "system", "content": DEMO_SYSTEM % a.get("role", a["name"])},
                               {"role": "user", "content": user}],
                           max_tokens=max_tokens or ROLE_MAX_TOKENS, timeout=STRATEGY_TIMEOUT)
            r.update(ok=True, output=c["content"], usage=c["usage"], finishReason=c["finishReason"])
        except Exception as e:
            r.update(ok=False, output="", error=str(e))
        r["ms"] = int((time.time() - t0) * 1000)
    else:
        r = run_role(aid, user, max_tokens=max_tokens, extra_system=extra_system)
    gen_ms = int((time.time() - t0) * 1000)
    if not r["ok"]:
        return r
    if post:
        r["output"] = post(r["output"]) or r["output"]
    if not review:
        return r
    lang = lang if lang in ("zh", "en") else req_lang()
    want_lang = lang if lang in ("zh", "en") else detect_lang(question)
    original = cur = r["output"]
    ev({"event": "review_start", "attempt": 0})
    rv = review_text(aid, kind, question, cur, context, prev, lang)
    steps = [{"attempt": 0, "text": cur, "checks": rv["checks"], "passed": rv["passed"], "ms": rv["ms"]}]
    ev({"event": "review", "attempt": 0, "passed": rv["passed"], "checks": rv["checks"]})
    attempts = 0
    a = AGENTS[aid]
    while not rv["passed"] and attempts < REVIEW_MAX_RETRIES:
        attempts += 1
        failed = [c for c in rv["checks"] if not c["pass"]]
        ev({"event": "fix", "attempt": attempts, "failed": [c.get("name") or c["id"] for c in failed], "failedIds": [c["id"] for c in failed]})
        fu = (user + "\n\n---\n你上一稿被审查员（%s）退回：\n\"\"\"%s\"\"\"\n\n问题：\n%s\n\n请重写，修正以上全部问题，保持你的岗位立场和观点。硬性要求：\n%s\n\n只输出重写后的内容本身，不要解释改了什么。"
              % (AGENTS[reviewer_for(aid)]["name"], cur,
                 "\n".join("- [%s] %s" % (c["name"], c["reason"]) for c in failed),
                 "\n".join("- " + x for x in fix_rules(kind, want_lang))))
        try:
            c = call_model(a, [{"role": "system", "content": a["systemPrompt"] + "\n" + extra_system},
                               {"role": "user", "content": fu}],
                           max_tokens=max_tokens or ROLE_MAX_TOKENS, timeout=STRATEGY_TIMEOUT)
        except Exception as e:
            steps.append({"attempt": attempts, "error": str(e)})
            break
        cur = (post(c["content"]) if post else c["content"]) or c["content"]
        r["finishReason"] = c["finishReason"]
        ev({"event": "review_start", "attempt": attempts})
        rv = review_text(aid, kind, question, cur, context, prev, lang)
        steps.append({"attempt": attempts, "text": cur, "checks": rv["checks"], "passed": rv["passed"], "ms": rv["ms"]})
        ev({"event": "review", "attempt": attempts, "passed": rv["passed"], "checks": rv["checks"]})
    orig_failed = [c["id"] for c in steps[0]["checks"] if not c["pass"]]
    fixed = [cid for cid in orig_failed if any(c["id"] == cid and c["pass"] for c in rv["checks"])]
    r["output"] = cur
    r["review"] = {"enabled": True, "demo": bool(demo), "kind": kind, "passed": rv["passed"],
                   "attempts": attempts, "fixedCount": len(fixed), "fixed": fixed,
                   "reviewer": rv["reviewer"], "original": original, "originalChecks": steps[0]["checks"],
                   "originalPassed": steps[0]["passed"], "final": cur, "finalChecks": rv["checks"],
                   "steps": steps, "generateMs": gen_ms, "ms": int((time.time() - t0) * 1000) - gen_ms}
    if demo:
        r["review"]["demoNote"] = "DEMO 出错模式：原稿是故意让模型随意乱写的（超长、编数字、声称已发布），用来现场展示 AI 抓错与 AI 修复。"
    r["ms"] = int((time.time() - t0) * 1000)
    return r


def review_summary(items):
    rs = [x.get("review") for x in items if x and x.get("review")]
    if not rs:
        return None
    return {"reviewed": len(rs), "passedFirstTime": sum(1 for r in rs if r["originalPassed"]),
            "fixed": sum(1 for r in rs if not r["originalPassed"] and r["passed"]),
            "stillFailing": sum(1 for r in rs if not r["passed"]),
            "issuesFixed": sum(r["fixedCount"] for r in rs)}


def run_roles_parallel(role_ids, make_user, on_step=None, rv=None):
    """Run several roles concurrently; emit start/agent events; keep pipeline order in result.
    rv = {"review", "demo", "question", "context"} -> each contribution goes through reviewed_role."""
    for rid in role_ids:
        if on_step:
            on_step({"event": "start", "agent": rid, "name": AGENTS[rid]["name"], "model": AGENTS[rid]["model"]})
    out = {}
    emit_lock = threading.Lock()

    def locked(e):
        with emit_lock:
            on_step(e)

    def job(rid):
        if rv:
            r = reviewed_role(rid, make_user(rid), "contrib", rv["question"], context=rv.get("context", ""),
                              review=rv.get("review", True), demo=rv.get("demo", False),
                              emit=locked if on_step else None)
        else:
            r = run_role(rid, make_user(rid))
        if on_step:
            with emit_lock:
                on_step({"event": "agent", **r})
        return rid, r
    if role_ids:
        with TLPool(max_workers=len(role_ids)) as ex:
            for rid, r in ex.map(job, role_ids):
                out[rid] = r
    return [out[r] for r in role_ids]


def contributions_text(contribs):
    t = ""
    for c in contribs:
        if c.get("ok"):
            t += "\n\n===== %s（%s · %s）=====\n%s" % (c["name"], c.get("role", ""), c["model"], c["output"])
        else:
            t += "\n\n===== %s：本轮失败，无产出 =====" % c["name"]
    return t


def strip_contrib(c):
    return {k: c.get(k) for k in ("agent", "name", "role", "model", "ok", "output", "error", "finishReason", "ms", "review")
            if c.get(k) is not None}


# ---- storage ---------------------------------------------------------------
def sdir(sid):
    return os.path.join(acct_dir(), sid)


def load_strategy(sid):
    if not ID_RE.match(sid or ""):
        return None
    p = os.path.join(sdir(sid), "strategy.json")
    if not os.path.exists(p):
        return None
    return json.load(open(p, encoding="utf-8"))


def md_filename(version):
    return active() + "_策略_%s_v%d.md" % (version["createdAt"][:10], version["v"])


def render_md_file(s, version):
    lines = [version["markdown"].rstrip(), "", "---", "",
             "## 版本信息", "",
             "- 策略 ID：`%s`" % s["id"],
             "- 版本：v%d（%s）" % (version["v"], {"create": "首次生成", "followup": "每日跟进修订",
                                                 "manual": "手动编辑", "debate": "辩论裁决应用"}.get(version["source"], version["source"])),
             "- 生成时间：%s（UTC+8）" % version["createdAt"].replace("T", " ")[:19],
             "- 最初的问题：%s" % s["question"].replace("\n", " "),
             "- 状态：草稿，仅供账号主人审核，不会自动发布。"]
    if version.get("note"):
        lines += ["", "## 本次跟进记录", "", version["note"].strip()]
    if version.get("changelog"):
        lines += ["", "## 本次变更", "", version["changelog"].strip()]
    if s.get("dailyLog"):
        lines += ["", "## 每日跟进日志", "", "| 时间（UTC+8） | 版本 | 记录 |", "|---|---|---|"]
        for d in s["dailyLog"]:
            if d["version"] <= version["v"]:
                lines.append("| %s | v%d | %s |" % (d["at"].replace("T", " ")[:16], d["version"],
                                                    d["note"].replace("\n", " ").replace("|", "\\|")))
    return "\n".join(lines) + "\n"


def save_strategy(s):
    d = sdir(s["id"])
    os.makedirs(d, exist_ok=True)
    for v in s["versions"]:
        v["file"] = md_filename(v)
    latest = s["versions"][-1]
    with open(os.path.join(d, latest["file"]), "w", encoding="utf-8") as f:
        f.write(render_md_file(s, latest))
    tmp = os.path.join(d, "strategy.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)
    os.replace(tmp, os.path.join(d, "strategy.json"))


def summary(s):
    v = s["versions"][-1]
    return {"id": s["id"], "title": s["title"], "question": s["question"], "createdAt": s["createdAt"],
            "updatedAt": v["createdAt"], "latestVersion": v["v"], "versions": len(s["versions"]),
            "followups": len(s.get("dailyLog", [])), "file": os.path.join("data", "accounts", active(), s["id"], v["file"])}


def list_strategies():
    out = []
    if True:
        for sid in os.listdir(acct_dir()):
            s = load_strategy(sid)
            if s:
                out.append(summary(s))
    out.sort(key=lambda x: x["updatedAt"], reverse=True)
    return out


def new_version(s, markdown, source, **extra):
    v = {"v": len(s["versions"]) + 1, "createdAt": iso(), "source": source, "markdown": markdown.strip() + "\n"}
    v.update({k: val for k, val in extra.items() if val not in (None, "", [])})
    s["versions"].append(v)
    return v


# ---- operations --------------------------------------------------------------
def create_strategy(question, on_step=None, review=True, demo=False):
    roles = [r for r in PIPELINE if r != FINAL]

    def make_user(rid):
        return "【账号主人的问题 / 目标】\n%s\n\n【你在本轮的任务】\n%s" % (question, ROLE_STRATEGY_TASKS[rid])
    contribs = run_roles_parallel(roles, make_user, on_step, rv={"review": review, "demo": demo, "question": question})
    chief_user = ("【账号主人的问题 / 目标】\n%s\n\n【4 个岗位的建议】%s\n\n【你在本轮的任务】\n%s"
                  % (question, contributions_text(contribs), CHIEF_STRATEGY_TASK))
    if on_step:
        on_step({"event": "start", "agent": FINAL, "name": AGENTS[FINAL]["name"], "model": AGENTS[FINAL]["model"]})
    chief = reviewed_role(FINAL, chief_user, "strategy", question, max_tokens=CHIEF_MAX_TOKENS,
                          context=contributions_text(contribs), review=review, demo=demo, emit=on_step)
    if on_step:
        on_step({"event": "agent", **chief})
    allc = contribs + [chief]
    if not chief["ok"]:
        return {"ok": False, "error": "主编整合失败：" + chief.get("error", ""), "outputs": allc}
    created = now()
    with STORE_LOCK:
        sid = created.strftime("%Y%m%d-%H%M%S")
        while os.path.exists(sdir(sid)):
            sid += "x"
        title = re.sub(r"\s+", " ", question)[:40]
        s = {"id": sid, "account": "@" + active(), "title": title, "question": question,
             "createdAt": iso(created), "versions": [], "dailyLog": []}
        new_version(s, chief["output"], "create", by=FINAL, contributions=[strip_contrib(c) for c in allc],
                    truncated=chief.get("finishReason") == "length", review=review_summary(allc), demo=bool(demo) or None)
        save_strategy(s)
    out = {"ok": all(c["ok"] for c in allc), "strategy": s, "outputs": allc}
    if review:
        out["review"] = {"enabled": True, "demo": bool(demo), **(review_summary(allc) or {})}
    return out


def followup_strategy(sid, note, roles=None, on_step=None, review=True, demo=False):
    s = load_strategy(sid)
    if not s:
        return {"ok": False, "error": "strategy not found", "status": 404}
    cur = s["versions"][-1]
    today = now().strftime("%Y-%m-%d")
    roles = [r for r in (roles or []) if r in AGENTS and r != FINAL]
    log_txt = "\n".join("- %s（v%d）：%s" % (d["at"][:16].replace("T", " "), d["version"], d["note"].replace("\n", " "))
                        for d in s.get("dailyLog", [])[-10:]) or "（暂无）"
    ctx = ("【当前策略（v%d）】\n%s\n\n【以往跟进记录（最近 10 条）】\n%s\n\n【今天（%s，UTC+8）账号主人的跟进记录】\n%s"
           % (cur["v"], cur["markdown"], log_txt, today, note))
    contribs = run_roles_parallel(roles, lambda rid: ctx + "\n\n【你在本轮的任务】\n" + ROLE_FOLLOWUP_TASK, on_step,
                                  rv={"review": review, "demo": demo, "question": note, "context": ctx})
    chief_user = ctx
    if contribs:
        chief_user += "\n\n【相关岗位的调整建议】" + contributions_text(contribs)
    chief_user += "\n\n【你在本轮的任务】\n" + CHIEF_FOLLOWUP_TASK.replace("{date}", today)
    if on_step:
        on_step({"event": "start", "agent": FINAL, "name": AGENTS[FINAL]["name"], "model": AGENTS[FINAL]["model"]})
    chief = reviewed_role(FINAL, chief_user, "strategy", note, max_tokens=CHIEF_MAX_TOKENS,
                          context=ctx + contributions_text(contribs), review=review, demo=demo, emit=on_step)
    if on_step:
        on_step({"event": "agent", **chief})
    allc = contribs + [chief]
    if not chief["ok"]:
        return {"ok": False, "error": "主编修订失败：" + chief.get("error", ""), "outputs": allc, "status": 502}
    text = chief["output"]
    if CHANGELOG_MARK in text:
        body, changelog = text.split(CHANGELOG_MARK, 1)
    else:
        body, changelog = text, "（主编没有单独列出变更说明）"
    body = re.sub(r"\n-{3,}\s*$", "", body.rstrip())
    with STORE_LOCK:
        s = load_strategy(sid)  # reload under lock
        v = new_version(s, body, "followup", by=FINAL, note=note, changelog=changelog.strip(),
                        contributions=[strip_contrib(c) for c in allc], basedOn=cur["v"],
                        truncated=chief.get("finishReason") == "length", review=review_summary(allc),
                        demo=bool(demo) or None)
        s.setdefault("dailyLog", []).append({"at": v["createdAt"], "date": today, "note": note, "version": v["v"]})
        save_strategy(s)
    out = {"ok": all(c["ok"] for c in allc), "strategy": s, "outputs": allc}
    if review:
        out["review"] = {"enabled": True, "demo": bool(demo), **(review_summary(allc) or {})}
    return out


def edit_strategy(sid, markdown, changelog=None):
    with STORE_LOCK:
        s = load_strategy(sid)
        if not s:
            return None
        new_version(s, markdown, "manual", by="owner", changelog=(changelog or "账号主人手动编辑").strip(),
                    basedOn=s["versions"][-1]["v"])
        save_strategy(s)
    return s


# ---------------------------------------------------------------------------
# Debate mode: 5 roles debate a topic in turns, chief proposes, all vote, chief rules
# ---------------------------------------------------------------------------
DEBATE_ORDER = ["researcher", "editor", "community", "data", "chief"]
VOTE_CHOICES = ["有条件支持", "支持", "反对"]  # order matters for parsing

DEBATE_RULES = """
【辩论模式的额外规则（优先于上面的字数限制）】
- 这是关于 @LucaAiAi X 运营的内部辩论，你代表你的岗位立场发言，口语化、观点鲜明，不要客套。
- 每次发言约 150 字，严格不超过 200 字；不要用 Markdown 标题、加粗和清单，一段话即可；不要在开头写自己的名字。
- 不要声称"我查过 / 我见过 / 数据显示"某些案例或统计，除非辩论记录里已经给出；只能写成假设，并标【待核实】。
- 不编造任何数字（粉丝数、阅读量、互动率、日期、价格、handle、链接）；不确定的事实写【待核实】。
- 只讨论方案和草稿：不发帖、不关注、不评论、不私信，也不说"已发布 / 已执行"。
- 称呼其他发言人时用他们的名字（例如"DeepSeek 狼"），直接写观点，不要复述你的岗位说明。"""

DEBATE_VERDICT_RULES = """
【辩论裁决的额外规则（优先于上面的字数限制）】
- 不编造任何数字；不确定的事实写【待核实】。
- 只给方案和草稿，由账号主人审核决定，不发帖、不执行。"""


def debate_speaker_label(aid):
    a = AGENTS[aid]
    return "%s（%s）" % (a["name"], a.get("role", ""))


def debate_transcript(turns):
    lines = []
    cur = None
    for t in turns:
        if t["round"] != cur:
            cur = t["round"]
            lines.append("\n—— 第 %d 轮%s ——" % (cur, "（立论）" if cur == 1 else "（反驳）"))
        lines.append("%s：%s" % (debate_speaker_label(t["agent"]), t["text"] if t.get("ok") else "（本轮发言失败）"))
    return "\n".join(lines).strip()


def debate_turn_task(rnd, idx, prev_name):
    if rnd == 1:
        if idx == 0:
            return ("现在是第 1 轮立论，你第一个发言。请从你的岗位出发，亮明你对这个议题的立场和最关键的一条理由（约 150 字）。")
        return ("现在是第 1 轮立论。请先用一句话点名回应上一位发言的 %s（同意或反对哪一点），"
                "再从你的岗位出发亮明你的立场和最关键的理由（约 150 字）。" % prev_name)
    return ("现在是第 %d 轮反驳。请挑出你最不同意的一个观点，点名回应提出它的人，说清楚为什么；"
            "如果别人的论点说服了你，可以明确写\"我改变立场：……\"。最后用一句话给出你现在的立场（约 150 字）。" % rnd)


PROPOSAL_TASK = """辩论已经结束。你是主持人兼主编，请基于双方论点提出**一个具体、可执行、可投票的提案**。
格式（不超过 250 字，不要 Markdown 标题）：
提案：一句话说清做什么。
要点：3–5 条短句（做什么 / 谁负责 / 节奏或期限，期限只写相对时间如"未来 7 天"）。
不做什么：1–2 条。
不编造数字，不确定处写【待核实】。"""

VOTE_TASK = """主编已经提出提案（见上）。请代表你的岗位投票，只能三选一：支持 / 反对 / 有条件支持。
严格按两行输出，不要多写：
投票：<支持|反对|有条件支持>
理由：<一句话，40 字以内；如果是有条件支持，写清条件>"""

VERDICT_TASK = """请作为主编写出本场辩论的**裁决书**（Markdown），严格使用以下结构：

# 辩论裁决：{topic_short}
## 1. 共识（3–5 条）
## 2. 主要分歧（表格：分歧点 / 一方观点（谁） / 另一方观点（谁） / 裁决）
## 3. 投票结果（照抄下面给出的计票，并用一句话点评）
## 4. 最终决定（明确写"通过 / 修改后通过 / 否决"，以及最终版提案）
## 5. 对运营策略的修改建议（写清改哪一部分、怎么改）
## 6. 下一步行动（表格：D1–D7 / 动作 / 负责岗位 / 是否需账号主人确认）
## 7. 待核实清单

要求：总长度约 800–1500 字；全部是草稿，由账号主人审核决定；不编造数字。"""

DEBATE_APPLY_REVISE_TASK = """账号主人决定把一场内部辩论的裁决应用到当前策略。请据此修订策略：
1. 输出修订后的**完整策略**，保持原有的 Markdown 标题结构（# 标题和 ## 0–9 节），不要只输出改动部分。
2. 只采纳裁决书"最终决定"和"对运营策略的修改建议"中的内容；被否决的提案不要写进策略。
3. 不得编造数字；没有的数据继续写【待核实】或"基线待采集"。
4. 完整策略之后，单独一行写 <<<CHANGELOG>>>，然后用 3–8 条短清单写"本次变更"（改了哪一节、对应裁决的哪一条）。"""


def parse_vote(text):
    t = text or ""
    m = re.search(r"投票\s*[:：]\s*(有条件支持|支持|反对)", t)
    choice = m.group(1) if m else next((c for c in VOTE_CHOICES if c in t), "弃权")
    r = re.search(r"理由\s*[:：]\s*(.+)", t)
    reason = r.group(1).strip() if r else re.sub(r"投票\s*[:：].*", "", t).strip()
    return choice, reason.split("\n")[0][:200]


def tally_votes(votes):
    t = {"支持": 0, "有条件支持": 0, "反对": 0, "弃权": 0}
    for v in votes:
        t[v.get("vote", "弃权")] = t.get(v.get("vote", "弃权"), 0) + 1
    return t


def tally_text(t):
    return "支持 %d · 有条件支持 %d · 反对 %d%s" % (t["支持"], t["有条件支持"], t["反对"],
                                                 (" · 弃权/失败 %d" % t["弃权"]) if t.get("弃权") else "")


def ddir():
    d = os.path.join(acct_dir(), "debates")
    os.makedirs(d, exist_ok=True)
    return d


def load_debate(did):
    if not ID_RE.match(did or ""):
        return None
    p = os.path.join(ddir(), did + ".json")
    if not os.path.exists(p):
        return None
    return json.load(open(p, encoding="utf-8"))


def render_debate_md(d):
    L = ["# " + ("本账号" if active().lower() == "lucaaiai" else "@" + active()) + " 内部辩论：%s" % d["topic"].replace("\n", " "), "",
         "- 辩论 ID：`%s`" % d["id"],
         "- 时间：%s（UTC+8）" % d["createdAt"].replace("T", " ")[:19],
         "- 轮数：%d（第 1 轮立论%s）" % (d["rounds"], "，之后为反驳" if d["rounds"] > 1 else ""),
         "- 状态：%s" % {"running": "进行中", "done": "已完成", "failed": "未完成"}.get(d["status"], d["status"]),
         "- 说明：草稿，仅供账号主人审核，不会自动发布。"]
    rvw = d.get("review") or {}
    if rvw.get("enabled"):
        L.append("- AI 审查：开启（审查 %s 条，首次通过 %s，修正后通过 %s，仍未通过 %s）" % (
            rvw.get("reviewed", "-"), rvw.get("passedFirstTime", "-"), rvw.get("fixed", "-"), rvw.get("stillFailing", "-")))
    if rvw.get("demo"):
        L.append("- ⚠️ DEMO 出错模式：每条原稿都是故意写坏的，用于演示 AI 抓错与修复；下面是修正后的版本。")
    L.append("")
    cur = None
    for t in d["turns"]:
        if t["round"] != cur:
            cur = t["round"]
            L += ["## 第 %d 轮%s" % (cur, "（立论）" if cur == 1 else "（反驳）"), ""]
        L += ["**%s** · `%s`%s" % (debate_speaker_label(t["agent"]), t["model"], review_tag(t)), "",
              (t["text"] if t.get("ok") else "（发言失败：%s）" % t.get("error", "")), ""]
    if d.get("proposal"):
        L += ["## 提案（%s）" % AGENTS[FINAL]["name"], "", d["proposal"]["text"] if d["proposal"].get("ok")
              else "（提案失败：%s）" % d["proposal"].get("error", ""), ""]
    if d.get("votes"):
        L += ["## 投票", "", "| 岗位 | 投票 | 理由 |", "|---|---|---|"]
        for v in d["votes"]:
            L.append("| %s%s | %s | %s |" % (debate_speaker_label(v["agent"]), review_tag(v), v["vote"],
                                           (v.get("reason") or v.get("error") or "").replace("|", "\\|")))
        L += ["", "**计票：** " + tally_text(d["tally"]), ""]
    if d.get("verdict"):
        L += ["---", "", d["verdict"]["text"] if d["verdict"].get("ok")
              else "（裁决失败：%s）" % d["verdict"].get("error", ""), ""]
    if d.get("applied"):
        L += ["## 已应用到策略", ""]
        for a in d["applied"]:
            L.append("- %s：策略 `%s` v%d" % (a["at"].replace("T", " ")[:16], a["strategyId"], a["version"]))
    return "\n".join(L).rstrip() + "\n"


def review_tag(x):
    r = x.get("review")
    if not r:
        return ""
    if r["originalPassed"]:
        return " · 审查通过"
    return " · 审查%s（%d 项）" % ("已修正" if r["passed"] else "未通过", r["fixedCount"] if r["passed"] else
                               sum(1 for c in r["finalChecks"] if not c["pass"]))


def save_debate(d):
    p = ddir()
    with open(os.path.join(p, d["file"]), "w", encoding="utf-8") as f:
        f.write(render_debate_md(d))
    tmp = os.path.join(p, d["id"] + ".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, os.path.join(p, d["id"] + ".json"))


def debate_summary(d):
    return {"id": d["id"], "topic": d["topic"], "title": d["title"], "createdAt": d["createdAt"],
            "status": d["status"], "rounds": d["rounds"], "tally": d.get("tally"),
            "decision": d.get("decision"), "applied": d.get("applied", []), "review": d.get("review"),
            "file": os.path.join("data", "accounts", active(), "debates", d["file"])}


def list_debates():
    out = []
    if True:
        for fn in os.listdir(ddir()):
            if fn.endswith(".json"):
                d = load_debate(fn[:-5])
                if d:
                    out.append(debate_summary(d))
    out.sort(key=lambda x: x["createdAt"], reverse=True)
    return out


def new_debate(topic, rounds, review=True, demo=False):
    created = now()
    with STORE_LOCK:
        p = ddir()
        did = created.strftime("%Y%m%d-%H%M%S")
        while os.path.exists(os.path.join(p, did + ".json")):
            did += "x"
        date = created.strftime("%Y-%m-%d")
        n = 1 + sum(1 for fn in os.listdir(p) if fn.startswith(active() + "_辩论_%s_" % date) and fn.endswith(".md"))
        d = {"id": did, "account": "@" + active(), "topic": topic, "title": re.sub(r"\s+", " ", topic)[:40],
             "rounds": rounds, "createdAt": iso(created), "status": "running",
             "file": active() + "_辩论_%s_%d.md" % (date, n),
             "turns": [], "proposal": None, "votes": [], "tally": None, "verdict": None,
             "decision": None, "applied": [], "review": {"enabled": bool(review), "demo": bool(demo)}}
        save_debate(d)
    return d


def decision_from_verdict(text):
    m = re.search(r"##\s*4[^\n]*\n(.{0,400})", text or "", re.S)
    seg = m.group(1) if m else (text or "")
    for k in ("修改后通过", "否决", "通过"):
        if k in seg:
            return k
    return None


def run_debate(topic, rounds=2, on_step=None, review=True, demo=False):
    raw_emit = on_step or (lambda e: None)
    emit_lock = threading.Lock()

    def emit(e):
        with emit_lock:
            raw_emit(e)
    t0 = time.time()
    d = new_debate(topic, rounds, review, demo)
    emit({"event": "debate", "id": d["id"], "topic": topic, "rounds": rounds, "file": d["file"],
          "review": d["review"],
          "speakers": [{"agent": a, "name": AGENTS[a]["name"], "role": AGENTS[a].get("role", ""),
                        "model": AGENTS[a]["model"]} for a in DEBATE_ORDER]})
    head = "【辩论议题（由账号主人 @LucaAiAi 提出）】\n%s" % topic

    def info(aid):
        a = AGENTS[aid]
        return {"agent": aid, "name": a["name"], "role": a.get("role", ""), "model": a["model"]}

    # rounds
    for rnd in range(1, rounds + 1):
        emit({"event": "phase", "phase": "round", "round": rnd,
              "label": "第 %d 轮%s" % (rnd, "：立论" if rnd == 1 else "：反驳")})
        for idx, aid in enumerate(DEBATE_ORDER):
            prev = next((t for t in reversed(d["turns"]) if t.get("ok")), None)
            prev_name = AGENTS[prev["agent"]]["name"] if prev else ""
            user = head
            if d["turns"]:
                user += "\n\n【到目前为止的辩论记录】\n" + debate_transcript(d["turns"])
            user += "\n\n【你现在的任务】\n" + debate_turn_task(rnd, idx, prev_name)
            emit({"event": "start", "phase": "round", "round": rnd, **info(aid)})
            label_re = r"^\s*\**\s*%s[^：:\n]{0,24}?\**\s*[：:]\s*\**\s*" % re.escape(AGENTS[aid]["name"].split()[0])
            # drop a leading self-label like "**Claude（主编）**："
            r = reviewed_role(aid, user, "turn", topic, max_tokens=1200, extra_system=DEBATE_RULES,
                              context=debate_transcript(d["turns"]) if d["turns"] else "",
                              prev=prev["text"] if prev else None, review=review, demo=demo,
                              post=lambda t: re.sub(label_re, "", t, count=1).strip(),
                              emit=emit, meta={"phase": "round", "round": rnd})
            turn = {"round": rnd, **info(aid), "ok": r["ok"], "text": r["output"], "ms": r["ms"]}
            if r.get("review"):
                turn["review"] = r["review"]
            if not r["ok"]:
                turn["error"] = r.get("error", "")
            d["turns"].append(turn)
            emit({"event": "turn", **turn})
            save_debate(d)

    transcript = debate_transcript(d["turns"])
    # proposal
    emit({"event": "phase", "phase": "proposal", "label": "主编提案"})
    emit({"event": "start", "phase": "proposal", **info(FINAL)})
    r = run_role(FINAL, head + "\n\n【完整辩论记录】\n" + transcript + "\n\n【你现在的任务】\n" + PROPOSAL_TASK,
                 max_tokens=1500, extra_system=DEBATE_VERDICT_RULES)
    d["proposal"] = {**info(FINAL), "ok": r["ok"], "text": r["output"], "ms": r["ms"]}
    if not r["ok"]:
        d["proposal"]["error"] = r.get("error", "")
        d["status"] = "failed"
        save_debate(d)
        emit({"event": "proposal", **d["proposal"]})
        return {"ok": False, "error": "主编提案失败：" + r.get("error", ""), "debate": d,
                "ms": int((time.time() - t0) * 1000)}
    emit({"event": "proposal", **d["proposal"]})
    save_debate(d)

    # vote (parallel, emitted as they arrive)
    emit({"event": "phase", "phase": "vote", "label": "投票"})
    vote_user = (head + "\n\n【完整辩论记录】\n" + transcript + "\n\n【主编提案】\n" + d["proposal"]["text"]
                 + "\n\n【你现在的任务】\n" + VOTE_TASK)
    lock = threading.Lock()
    votes = {}

    def vote_job(aid):
        emit({"event": "start", "phase": "vote", **info(aid)})
        rr = reviewed_role(aid, vote_user, "vote", topic, max_tokens=600, extra_system=DEBATE_RULES,
                           context=transcript + "\n\n【主编提案】\n" + d["proposal"]["text"], review=review,
                           demo=demo, emit=emit, meta={"phase": "vote"})
        if rr["ok"]:
            choice, reason = parse_vote(rr["output"])
            v = {**info(aid), "ok": True, "vote": choice, "reason": reason, "raw": rr["output"], "ms": rr["ms"]}
            if rr.get("review"):
                v["review"] = rr["review"]
        else:
            v = {**info(aid), "ok": False, "vote": "弃权", "reason": "", "error": rr.get("error", ""), "ms": rr["ms"]}
        with lock:
            votes[aid] = v
        emit({"event": "vote", **v})
    with TLPool(max_workers=len(DEBATE_ORDER)) as ex:
        list(ex.map(vote_job, DEBATE_ORDER))
    d["votes"] = [votes[a] for a in DEBATE_ORDER]
    d["tally"] = tally_votes(d["votes"])
    emit({"event": "tally", "tally": d["tally"], "text": tally_text(d["tally"])})
    save_debate(d)

    # verdict
    emit({"event": "phase", "phase": "verdict", "label": "主编裁决"})
    emit({"event": "start", "phase": "verdict", **info(FINAL)})
    vote_lines = "\n".join("- %s：%s —— %s" % (debate_speaker_label(v["agent"]), v["vote"], v.get("reason") or "（无）")
                           for v in d["votes"])
    r = run_role(FINAL, head + "\n\n【完整辩论记录】\n" + transcript + "\n\n【主编提案】\n" + d["proposal"]["text"]
                 + "\n\n【投票】\n" + vote_lines + "\n计票：" + tally_text(d["tally"])
                 + "\n\n【你现在的任务】\n" + VERDICT_TASK.replace("{topic_short}", d["title"]),
                 max_tokens=4000, extra_system=DEBATE_VERDICT_RULES)
    d["verdict"] = {**info(FINAL), "ok": r["ok"], "text": r["output"], "ms": r["ms"],
                    "truncated": r.get("finishReason") == "length"}
    if not r["ok"]:
        d["verdict"]["error"] = r.get("error", "")
    d["decision"] = decision_from_verdict(r["output"]) if r["ok"] else None
    d["status"] = "done" if r["ok"] else "failed"
    if review:
        d["review"].update(review_summary(d["turns"] + d["votes"]) or {})
    d["ms"] = int((time.time() - t0) * 1000)
    d["finishedAt"] = iso()
    save_debate(d)
    emit({"event": "verdict", **d["verdict"], "decision": d["decision"]})
    res = {"ok": r["ok"] and all(t["ok"] for t in d["turns"]), "debate": d, "ms": d["ms"], "review": d["review"]}
    if not r["ok"]:
        res["error"] = "主编裁决失败：" + r.get("error", "")
    return res


def apply_debate(did, sid=None):
    d = load_debate(did)
    if not d:
        return {"ok": False, "error": "debate not found", "status": 404}
    if not (d.get("verdict") or {}).get("ok"):
        return {"ok": False, "error": "debate has no verdict yet", "status": 409}
    verdict = d["verdict"]["text"]
    ctx_debate = ("【辩论议题】\n%s\n\n【主编提案】\n%s\n\n【计票】%s\n\n【裁决书】\n%s"
                  % (d["topic"], d["proposal"]["text"], tally_text(d["tally"]), verdict))
    note = "应用辩论 %s 的裁决：%s" % (d["id"], d["title"])
    if sid:
        s = load_strategy(sid)
        if not s:
            return {"ok": False, "error": "strategy not found", "status": 404}
        cur = s["versions"][-1]
        chief = run_role(FINAL, "【当前策略（v%d）】\n%s\n\n%s\n\n【你在本轮的任务】\n%s"
                         % (cur["v"], cur["markdown"], ctx_debate, DEBATE_APPLY_REVISE_TASK),
                         max_tokens=CHIEF_MAX_TOKENS)
        if not chief["ok"]:
            return {"ok": False, "error": "主编修订失败：" + chief.get("error", ""), "status": 502}
        text = chief["output"]
        body, changelog = (text.split(CHANGELOG_MARK, 1) if CHANGELOG_MARK in text
                           else (text, "（主编没有单独列出变更说明）"))
        body = re.sub(r"\n-{3,}\s*$", "", body.rstrip())
        with STORE_LOCK:
            s = load_strategy(sid)
            v = new_version(s, body, "debate", by=FINAL, note=note, changelog=changelog.strip(),
                            debateId=d["id"], basedOn=cur["v"], truncated=chief.get("finishReason") == "length")
            save_strategy(s)
    else:
        chief = run_role(FINAL, "【账号主人的问题 / 目标】\n%s\n\n%s\n\n【你在本轮的任务】\n%s\n只采纳裁决书最终决定的内容，"
                         "其余部分按账号定位补全，未知处写【待核实】。" % (d["topic"], ctx_debate, CHIEF_STRATEGY_TASK),
                         max_tokens=CHIEF_MAX_TOKENS)
        if not chief["ok"]:
            return {"ok": False, "error": "主编生成策略失败：" + chief.get("error", ""), "status": 502}
        created = now()
        with STORE_LOCK:
            sid = created.strftime("%Y%m%d-%H%M%S")
            while os.path.exists(sdir(sid)):
                sid += "x"
            s = {"id": sid, "account": "@" + active(), "title": ("辩论：" + d["title"])[:40],
                 "question": d["topic"], "createdAt": iso(created), "versions": [], "dailyLog": []}
            v = new_version(s, chief["output"], "debate", by=FINAL, note=note, debateId=d["id"],
                            truncated=chief.get("finishReason") == "length")
            save_strategy(s)
    with STORE_LOCK:
        d = load_debate(did)
        d.setdefault("applied", []).append({"at": iso(), "strategyId": s["id"], "version": v["v"]})
        save_debate(d)
    return {"ok": True, "strategy": s, "version": v["v"], "debate": debate_summary(d), "ms": chief["ms"]}


# ---- basic abuse protection for the public deployment (per IP, in memory, per instance) ----
RATE_LIMIT = int(os.environ.get("RATE_LIMIT_PER_10MIN", "40"))        # AI / fetch POSTs per IP per 10 min
AUTH_FAIL_LIMIT = int(os.environ.get("AUTH_FAIL_LIMIT_PER_10MIN", "15"))
_RL, _RL_LOCK = {}, threading.Lock()


def rate_ok(key, limit, window, peek=False):
    now = time.time()
    with _RL_LOCK:
        q = [t for t in _RL.get(key, []) if now - t < window]
        if len(q) >= limit:
            _RL[key] = q
            return False
        if not peek:
            q.append(now)
        _RL[key] = q
        return True


class Handler(BaseHTTPRequestHandler):
    server_version = "LucaAgents/1.4"

    def log_message(self, fmt, *args):  # never logs headers / keys
        sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    def cors(self):
        self.send_header("Access-Control-Allow-Origin", os.environ.get("CORS_ORIGIN", "*"))
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Account")

    def send_json(self, code, obj):
        if code >= 400 and isinstance(obj, dict) and isinstance(obj.get("error"), str) and req_lang() == "en" \
                and re.search(r"[\u4e00-\u9fff]", obj["error"]):
            obj = dict(obj, error=en_error(obj["error"], code))
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_file(self, name, ctype):
        p = os.path.join(BASE, name)
        data = open(p, "rb").read()
        self.send_response(200)
        self.cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def client_ip(self):
        return (self.headers.get("X-Forwarded-For") or self.headers.get("X-Real-IP") or self.client_address[0] or "?").split(",")[0].strip()

    def authorized(self):
        token = os.environ.get("AGENTS_API_TOKEN")
        if not token:
            return True
        ip = self.client_ip()
        if not rate_ok("authfail:" + ip, AUTH_FAIL_LIMIT, 600, peek=True):
            return False   # too many wrong passwords from this IP in 10 min
        ok = hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token)
        if not ok:
            rate_ok("authfail:" + ip, AUTH_FAIL_LIMIT, 600)
        return ok

    def do_OPTIONS(self):
        self.send_response(204)
        self.cors()
        self.end_headers()

    def read_body(self):
        n = int(self.headers.get("Content-Length", "0") or 0)
        if n > 10_000_000:
            raise ValueError("body too large")
        return json.loads(self.rfile.read(n).decode() or "{}")

    def start_stream(self):
        self.send_response(200)
        self.cors()
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        wlock = threading.Lock()

        def emit(obj):
            try:
                with wlock:
                    self.wfile.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass  # client went away; keep working so the version is still saved
        return emit

    def pin_account(self, path):
        """v1.6: the page sends X-Account = the account it is showing; every request is served for that
        account, so another tab switching the global active account can't mix data / names."""
        _TL.handle = None
        _TL.lang = "en"   # English-only UI: model output and server messages are always English
        h = (self.headers.get("X-Account") or "").lstrip("@")
        if h and path not in ("/api/accounts", "/api/accounts/switch") and HANDLE_RE.match(h) and profile(h):
            _TL.handle = canon_handle(h)

    def do_GET(self):
        path = self.path.split("?")[0]
        self.pin_account(path)
        if path in ("/", "/index.html"):
            return self.send_file("index.html", "text/html; charset=utf-8")
        if path == "/bench.js":
            return self.send_file("bench.js", "application/javascript; charset=utf-8")
        if path == "/i18n.js":
            return self.send_file("i18n.js", "application/javascript; charset=utf-8")
        if path == "/accounts.js":
            return self.send_file("accounts.js", "application/javascript; charset=utf-8")
        if path == "/assets/luca-wolf.png":
            return self.send_file("assets/luca-wolf.png", "image/png")
        if path == "/openapi.yaml":
            return self.send_file("openapi.yaml", "application/yaml; charset=utf-8")
        if path == "/api/health":
            return self.send_json(200, {"ok": True, "keyConfigured": bool(api_key()), "version": "1.6", "account": None if active() == "_none" else active(),
                                        "authRequired": bool(os.environ.get("AGENTS_API_TOKEN")), "platform": "x",
                                        "switchUrl": os.environ.get("PLATFORM_SWITCH_URL", "")})
        if not path.startswith("/api/"):
            return self.send_json(404, {"error": "not found"})
        if not self.authorized():
            return self.send_json(401, {"error": "unauthorized"})
        if path == "/api/accounts":
            return self.send_json(200, {"active": None if active() == "_none" else active(), "profile": profile(),
                                        "accounts": list_profiles()})
        if path.startswith("/api/bench") or path == "/api/queue":
            return bench.handle_get(self, path)
        if path == "/api/agents":
            return self.send_json(200, {"account": "@" + active(), "pipeline": PIPELINE, "finalAgent": FINAL,
                                        "agents": [{"id": a["id"], "name": a["name"], "nameEn": a["nameEn"],
                                                    "role": a.get("role", ""),
                                                    "model": a["model"], "order": a["order"]} for a in CONFIG["agents"]]})
        if path == "/api/strategies":
            return self.send_json(200, {"strategies": list_strategies()})
        if path == "/api/debates":
            return self.send_json(200, {"debates": list_debates()})
        m = re.match(r"^/api/debates/([^/]+?)(\.md)?$", path)
        if m:
            d = load_debate(m.group(1))
            if not d:
                return self.send_json(404, {"error": "debate not found"})
            if not m.group(2):
                return self.send_json(200, d)
            data = render_debate_md(d).encode()
            self.send_response(200)
            self.cors()
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Disposition", "inline; filename*=UTF-8''" + urllib.parse.quote(d["file"]))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        m = re.match(r"^/api/strategies/([^/]+)$", path)
        if m:
            s = load_strategy(m.group(1))
            return self.send_json(200, s) if s else self.send_json(404, {"error": "strategy not found"})
        m = re.match(r"^/api/strategies/([^/]+)/versions/(\d+)(\.md)?$", path)
        if m:
            s = load_strategy(m.group(1))
            n = int(m.group(2))
            if not s or not (1 <= n <= len(s["versions"])):
                return self.send_json(404, {"error": "version not found"})
            v = s["versions"][n - 1]
            if not m.group(3):
                return self.send_json(200, v)
            data = render_md_file(s, v).encode()
            self.send_response(200)
            self.cors()
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Disposition", "inline; filename*=UTF-8''" + urllib.parse.quote(v["file"]))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_json(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0]
        self.pin_account(path)
        if not path.startswith("/api/"):
            return self.send_json(404, {"error": "not found"})
        if not self.authorized():
            return self.send_json(401, {"error": "unauthorized"})
        if (os.environ.get("AGENTS_API_TOKEN") or os.environ.get("VERCEL")) and not rate_ok("post:" + self.client_ip(), RATE_LIMIT, 600):
            return self.send_json(429, {"error": "请求太频繁，请 10 分钟后再试 / Too many requests, try again in 10 minutes"})
        try:
            body = self.read_body()
            if not isinstance(body, dict):
                raise ValueError
        except Exception:
            return self.send_json(400, {"error": "invalid JSON body"})
        _TL.lang = "en"   # English-only
        if path == "/api/accounts/switch":
            h = canon_handle(body.get("handle"))
            if not profile(h):
                return self.send_json(404, {"error": "账号不存在，请先添加"})
            set_active(h)
            return self.send_json(200, {"ok": True, "active": h, "profile": profile(h)})
        if path == "/api/accounts":
            return bench.handle_add_account(self, body)
        if path.startswith("/api/bench/") or path.startswith("/api/queue"):
            return bench.handle_post(self, path, body)
        if path == "/api/ask":
            return self.handle_ask(body)
        if path in ("/api/strategy", "/api/strategies"):
            return self.handle_create(body)
        if path in ("/api/debate", "/api/debates"):
            return self.handle_debate(body)
        m = re.match(r"^/api/debates/([^/]+)/apply$", path)
        if m:
            sid = body.get("strategyId") or None
            if sid is not None and not isinstance(sid, str):
                return self.send_json(400, {"error": "strategyId must be a string"})
            if not load_debate(m.group(1)):
                return self.send_json(404, {"error": "debate not found"})
            if not api_key():
                return self.send_json(500, {"error": "ZENMUX_API_KEY not configured on server"})
            r = apply_debate(m.group(1), sid)
            return self.send_json(r.pop("status", 200), r)
        m = re.match(r"^/api/strategies/([^/]+)/(followup|edit)$", path)
        if m:
            sid, action = m.groups()
            if not load_strategy(sid):
                return self.send_json(404, {"error": "strategy not found"})
            if action == "edit":
                md = body.get("markdown") or ""
                if not md.strip():
                    return self.send_json(400, {"error": "markdown is required"})
                if len(md) > 200000:
                    return self.send_json(400, {"error": "markdown too long"})
                s = edit_strategy(sid, md, body.get("changelog"))
                return self.send_json(200, {"ok": True, "strategy": s})
            return self.handle_followup(sid, body)
        self.send_json(404, {"error": "not found"})

    def review_opts(self, body):
        """-> (review, demo) or None after sending a 400."""
        review, demo = body.get("review", True), body.get("demo", False)
        if not isinstance(review, bool):
            self.send_json(400, {"error": "review must be a boolean"})
            return None
        if demo not in (True, False, "fail", None):
            self.send_json(400, {"error": "demo must be a boolean or \"fail\""})
            return None
        demo = bool(demo)
        if demo and not review:
            self.send_json(400, {"error": "demo mode needs review enabled"})
            return None
        return review, demo

    def handle_create(self, body):
        q = (body.get("question") or "").strip()
        if not q:
            return self.send_json(400, {"error": "question is required"})
        if len(q) > 8000:
            return self.send_json(400, {"error": "question too long (max 8000 chars)"})
        if not api_key():
            return self.send_json(500, {"error": "ZENMUX_API_KEY not configured on server"})
        opts = self.review_opts(body)
        if not opts:
            return
        if body.get("stream"):
            emit = self.start_stream()
            emit({"event": "done", **create_strategy(q, emit, *opts)})
            return
        r = create_strategy(q, None, *opts)
        self.send_json(200 if r.get("strategy") else 502, r)

    def handle_followup(self, sid, body):
        note = (body.get("note") or "").strip()
        roles = body.get("roles") or []
        if not note:
            return self.send_json(400, {"error": "note is required"})
        if len(note) > 8000:
            return self.send_json(400, {"error": "note too long (max 8000 chars)"})
        if not isinstance(roles, list) or any(r not in AGENTS or r == FINAL for r in roles):
            return self.send_json(400, {"error": "roles must be a list of role ids (not chief)",
                                        "valid": [r for r in PIPELINE if r != FINAL]})
        if not api_key():
            return self.send_json(500, {"error": "ZENMUX_API_KEY not configured on server"})
        opts = self.review_opts(body)
        if not opts:
            return
        if body.get("stream"):
            emit = self.start_stream()
            r = followup_strategy(sid, note, roles, emit, *opts)
            r.pop("status", None)
            emit({"event": "done", **r})
            return
        r = followup_strategy(sid, note, roles, None, *opts)
        code = r.pop("status", 200 if r.get("strategy") else 502)
        self.send_json(code, r)

    def handle_debate(self, body):
        topic = (body.get("topic") or body.get("question") or "").strip()
        rounds = body.get("rounds", 2)
        if not topic:
            return self.send_json(400, {"error": "topic is required"})
        if len(topic) > 4000:
            return self.send_json(400, {"error": "topic too long (max 4000 chars)"})
        if not isinstance(rounds, int) or isinstance(rounds, bool) or not 1 <= rounds <= 3:
            return self.send_json(400, {"error": "rounds must be an integer 1-3"})
        if not api_key():
            return self.send_json(500, {"error": "ZENMUX_API_KEY not configured on server"})
        opts = self.review_opts(body)
        if not opts:
            return
        if body.get("stream"):
            emit = self.start_stream()
            emit({"event": "done", **run_debate(topic, rounds, emit, *opts)})
            return
        r = run_debate(topic, rounds, None, *opts)
        self.send_json(200 if r["debate"]["status"] == "done" else 502, r)

    def handle_ask(self, body):
        agent = body.get("agent", "all")
        question = (body.get("question") or "").strip()
        lang = "en"   # English-only
        if not question:
            return self.send_json(400, {"error": "question is required"})
        if len(question) > 8000:
            return self.send_json(400, {"error": "question too long (max 8000 chars)"})
        if agent != "all" and agent not in AGENTS:
            return self.send_json(400, {"error": "unknown agent", "valid": ["all"] + list(AGENTS)})
        if lang not in (None, "", "auto", "zh", "en"):
            return self.send_json(400, {"error": "lang must be zh, en or auto"})
        if lang in ("zh", "en"):
            _TL.lang = lang
        elif lang == "auto":
            _TL.lang = None
        opts = self.review_opts(body)
        if not opts:
            return
        if not api_key():
            return self.send_json(500, {"error": "ZENMUX_API_KEY not configured on server"})

        if body.get("stream"):
            # NDJSON progress stream: one JSON object per line, last line {"event":"done",...}
            emit = self.start_stream()
            result = ask(agent, question, lang, emit, *opts)
            emit({"event": "done", **result})
            return
        self.send_json(200, ask(agent, question, lang, None, *opts))


import bench  # noqa: E402  v1.4 对标账号分析 + 发帖队列


def main():
    migrate_legacy()
    bench.init(sys.modules[__name__])
    if not api_key():
        print("WARNING: ZENMUX_API_KEY is not set (env or .env.local). /api/ask will fail.", file=sys.stderr)
    print("LucaAiAi agents on http://%s:%d  (auth %s)" % (
        HOST, PORT, "on" if os.environ.get("AGENTS_API_TOKEN") else "off"))
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
