# X 运营工具 v1.6（多账号 + 中英双语）· 前端重做交接文档（给 UI 设计师）

> 目标：你可以完全重做前端（任何框架都行），只要调用同一套本地 API。后端（`server.py` + `bench.py`，纯 Python 标准库）不用改。
> 现在的前端 = `index.html`（问答 / 运营策略 / 辩论 + 审查开关）+ `bench.js`（对标账号分析）。两个文件都只是 API 的客户端。

## 0. 怎么跑起来

```bash
cp .env.example .env.local     # 填 ZENMUX_API_KEY（必需），其余可选
python3 server.py              # 默认 http://127.0.0.1:8787；或双击 start-x-ops.command（8788，直接打开对标页）
```
- 没有云端，所有数据存在本地 `data/`（JSON + Markdown）。时间统一按北京时间（UTC+8）。
- 新前端可以放在任何位置：服务端开了 CORS（默认 `*`）。也可以替换 `index.html` / `bench.js`，由同一个服务托管。
- 可选鉴权：服务端设置了 `AGENTS_API_TOKEN` 后，所有 `/api/*`（`/api/health` 除外）都要带 `Authorization: Bearer <token>`。现在的 UI 在页头有个 Token 输入框，存在 localStorage。
- 5 个 AI 岗位（都经 ZenMux 调用）：`researcher` DeepSeek 选题研究员 · `editor` Qwen 内容编辑·视觉 · `community` Gemini 社区运营 · `data` MiniMax 数据分析·流程 · `chief` Claude 主编。头像缩写和颜色：DS / QW / GE / MM / CL。

## 1. 前端 vs 后端分工

| 前端负责 | 后端负责 |
|---|---|
| 页面、标签页、表单、加载状态、流式渲染（逐行读 NDJSON） | 调用模型（ZenMux）、审查狼审查/重写、计票 |
| Markdown 渲染（报告、策略、裁决书都是 Markdown；`【待核实】` 建议标黄） | 存储（`data/` 下的 JSON / .md） |
| 热力图、KPI 卡片、主题条形图等可视化（后端只给数字） | 统计：粉丝指数、互动率、热力图数据、最佳时段、变现信号 |
| X 加权字数提示（后端已返回 `weighted`，前端编辑时可自算：中日韩字符算 2、链接算 23、其他算 1，上限 280） | 抓取 X 数据、下载知识库图片、发帖调度器（每 30 秒）、OAuth1 发帖 |
| 「用 x.com 发」链接：`https://x.com/intent/post?text=<urlencoded>` | 密钥只在 `.env.local`，**前端永远拿不到也不该显示密钥** |

## 2. 通用约定

> 本文里的 JSON 示例是**节选**（字段名准确，`…` 表示省略）。做 UI 时以本地实际返回为准——跑一次接口 `curl` 一下最直观。

- 请求体都是 JSON：`Content-Type: application/json`。错误统一返回 `{"error": "..."}`，状态码 400 / 401 / 404 / 409 / 500 / 502。
- **流式**：长任务在请求体加 `"stream": true`，返回 `application/x-ndjson`：每行一个 JSON 事件，最后一行 `{"event":"done", ...}`（结构等于非流式的返回）。前端用 `fetch().body.getReader()` 按 `\n` 切行。
- **审查狼（AI 审查）**：所有生成类接口都接受 `"review": true|false`（默认 true）和 `"demo": true|false`（DEMO 出错模式，需 review=true）。开着审查时，每条输出多一个 `review` 对象，流里多三种事件：
  - `{"event":"review_start","agent":"editor","name":"Qwen","kind":"ask","attempt":0}`
  - `{"event":"review","agent":"editor","attempt":0,"passed":false,"checks":[...]}`
  - `{"event":"fix","agent":"editor","attempt":1,"failed":["X 字数"],"failedIds":["post_length"]}`
- `review` 对象（徽章 + 可展开详情用）：
```json
{"enabled": true, "demo": false, "passed": true, "originalPassed": false, "fixedCount": 1, "attempts": 1,
 "original": "原稿", "final": "修正稿",
 "finalChecks": [{"id":"post_length","name":"X 字数","source":"code","pass":true,"reason":"…"}],
 "history": [{"attempt":0,"checks":[...]}], "demoNote": ""}
```
  徽章规则：`originalPassed` → 「审查 ✓通过」；`passed` → 「已修正 N 项」；否则 → 「✗未通过 N 项」。整次请求的汇总 `review`：`{reviewed, passedFirstTime, fixed, stillFailing, issuesFixed}`。
- 页头两个全局开关「AI 审查」「DEMO 出错模式」，DEMO 开启时显示红色横幅（第一版故意写坏，只用于演示）。

---

## 3. 页面与功能

### 3.1 问答
流程：选岗位（单个 或「全员」）→ 选语言（自动/中/英）→ 输入问题 → 发送 → 每个岗位一张卡片（模型名、状态、复制按钮、审查徽章）；全员模式按 DS→QW→GE→MM→CL 接力，最后的「📦 最终交付」置顶。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | `{"ok":true,"keyConfigured":true,"version":"1.4","authRequired":false}`（不需鉴权） |
| GET | `/api/agents` | `{"account":"@LucaAiAi","pipeline":["researcher","editor","community","data","chief"],"finalAgent":"chief","agents":[{"id":"editor","name":"Qwen","nameEn":"…","role":"内容编辑·视觉","model":"qwen/…","order":2}]}` |
| POST | `/api/ask` | `{"agent":"all"|"editor"|…, "question":"…", "lang":"zh"|"en"|"auto", "stream":true, "review":true, "demo":false}` |

非流式返回：
```json
{"mode":"all","question":"…","ok":true,"finalAgent":"chief","final":"主编最终交付（Markdown）",
 "outputs":[{"agent":"researcher","name":"DeepSeek","model":"deepseek/…","ok":true,"output":"…","ms":33523,"review":{…}}],
 "review":{"enabled":true,"reviewed":5,"passedFirstTime":4,"fixed":1,"stillFailing":0}}
```
流式事件：`start {agent,name,model}` → （审查事件）→ `agent {…同 outputs 的一项}` → … → `done`。

### 3.2 运营策略（每日跟进）
流程：
1. 左栏 = 策略列表；新建：输入目标 → 「生成策略」（4 岗位并行建议 → Claude 整合，约 3 分钟）。
2. 打开一份策略：显示当前版本 Markdown、版本历史（可切换、复制、下载 .md）、「本次变更」。
3. 「今日跟进」：输入当天进展/数据，可勾选几个岗位先给建议 → 生成新版本。
4. 「手动编辑」：直接改 Markdown → 存为新版本。

| 方法 | 路径 | 请求 / 返回 |
|---|---|---|
| POST | `/api/strategy`（别名 `/api/strategies`） | `{"question":"…","stream":true}` → `{"ok":true,"strategy":{…},"outputs":[…]}` |
| GET | `/api/strategies` | `[{"id":"…","title":"…","updatedAt":"…","versions":3}]` 新的在前 |
| GET | `/api/strategies/<id>` | 完整策略（见下方数据结构） |
| POST | `/api/strategies/<id>/followup` | `{"note":"今天发了 2 条…","roles":["data"],"stream":true}` → 新版本 |
| POST | `/api/strategies/<id>/edit` | `{"markdown":"…","changelog":"改了第 3 节"}` → 新版本（不调模型） |
| GET | `/api/strategies/<id>/versions/<n>` | 某版本 JSON；`…/versions/<n>.md` 下载 Markdown |

策略数据（`data/<id>/strategy.json`）：
```json
{"id":"20261101-xxxx","question":"…","createdAt":"…","updatedAt":"…",
 "versions":[{"v":1,"createdAt":"…","source":"new|followup|edit|debate","markdown":"…","changelog":"…","file":"LucaAiAi_策略_2026-11-01_v1.md"}],
 "dailyLog":[{"at":"…","date":"2026-11-01","note":"…","version":2}],
 "roleOutputs":[…], "applied":[{"at":"…","strategyId":"…","version":3}]}
```
流式事件：`phase`（阶段名）、`start`/`agent`（各岗位）、审查事件、`done`。

### 3.3 辩论（5 岗位）
流程：输入议题 + 轮数（1–3，默认 2）→ 「开始辩论」→ 聊天气泡实时出现（每人头像和颜色）→ 主编提案 → 5 票（支持/反对/有条件支持 + 理由）→ 计票 → 裁决书（Markdown）→ 「把裁决写进运营策略」（选已有策略或新建）。左栏 = 历史辩论列表。

| 方法 | 路径 | 请求 / 返回 |
|---|---|---|
| POST | `/api/debate`（别名 `/api/debates`） | `{"topic":"…","rounds":2,"stream":true,"review":true}` → `{"ok":true,"debate":{…},"ms":150000}` |
| GET | `/api/debates` | 列表：`[{"id","topic","createdAt","status","tally","decision","applied","demo"}]` |
| GET | `/api/debates/<id>` | 完整记录；`/api/debates/<id>.md` 返回 Markdown |
| POST | `/api/debates/<id>/apply` | `{"strategyId":"…"}`（不传 = 新建策略）→ `{"ok":true,"strategy":{…}}`；还没有裁决时返回 409 |

流式事件顺序：
```
{"event":"debate","id":"…","topic":"…","rounds":2,"file":"…"}
{"event":"phase","phase":"round","round":1,"label":"第 1 轮 · 立论"}
{"event":"start","agent":"researcher",…}  → 审查事件 → {"event":"turn","agent":"researcher","name":"DeepSeek","round":1,"text":"…","review":{…}}
… 第 2 轮 …
{"event":"phase","phase":"proposal","label":"主编提案"}  {"event":"proposal","text":"…"}
{"event":"phase","phase":"vote","label":"投票"}  {"event":"vote","agent":"editor","vote":"支持","reason":"…","review":{…}} ×5
{"event":"tally","tally":{"支持":3,"有条件支持":1,"反对":1,"弃权":0},"text":"支持 3 · 有条件支持 1 · 反对 1"}
{"event":"phase","phase":"verdict","label":"主编裁决"}  {"event":"verdict","text":"裁决书 Markdown","decision":"修改后通过"}
{"event":"done","ok":true,"debate":{…}}
```

### 3.4 对标账号分析（v1.4 新增）
流程：
1. **拿数据**（三选一）：
   - 输入 X 用户名 → 「抓取并分析」（需要服务端配置 `X_BEARER_TOKEN`）；
   - 「导入 JSON」（格式见 `samples/SCHEMA.md`）；
   - 「载入示例数据」。
2. **分析**（约 40 秒，流式显示进度）。输出内容：
   - 粉丝指数 KPI 卡片
   - 主题排行（按平均热度）
   - 7×24 热力图 + 最佳时段
   - 变现信号
   - Claude 写的中文分析报告（Markdown）
3. **爆款知识库**：热度前 20% 的帖子，支持搜索 + 主题筛选，显示本地缩略图。每条有「让 5 个角色仿写」按钮：5 张卡片流式出现，带审查徽章。卡片里可以：
   - 编辑草稿
   - 选「审核后发 / 全自动发」
   - 「加入发帖队列」
   - 「用 x.com 打开发帖框」
4. **发帖队列**（页面底部）：
   - 排期默认排到对标博主互动最高的时段（北京时间）。
   - 每条可切换模式，可审核通过、立即发、编辑、取消、删除。
   - 没配发帖密钥时显示「⚠️ 未配置发帖密钥」：到点后状态变成「需手动发」，提供「手动发」（x.com 链接）和「我已手动发好」两个按钮。
   - 页面每 30 秒刷新一次。

热度公式：`赞 + 2×转发 + 1.5×回复 + 2×引用 + 0.5×收藏`。粉丝指数（0–100）= 粉丝规模（对数）60% + 浏览互动率（对比 2% 基准）40%。

| 方法 | 路径 | 请求 → 返回 |
|---|---|---|
| GET | `/api/bench/config` | `{"bearerConfigured":false,"postKeysConfigured":false,"zenmuxConfigured":true,"tz":"Asia/Shanghai (UTC+8)","samples":["dotey"]}` |
| GET | `/api/bench` | `{"accounts":[{"username":"dotey","name":"宝玉","followers":264591,"posts":189,"source":"…","fetched_at":"…","analyzed_at":"…","kb":38}]}` |
| POST | `/api/bench/fetch` | `{"username":"dotey","maxPosts":300}` → `{"ok":true,"username":"dotey","posts":289}`（只抓取，之后再调 analyze） |
| POST | `/api/bench/import` | `{"sample":"dotey"}` 或 `{"data":{…导入 JSON…}}` → `{"ok":true,"username":"dotey","posts":189}`（请求体上限 10MB） |
| POST | `/api/bench/<u>/analyze` | `{"stream":true}` → 事件 `{"event":"step","msg":"Gemini 正在给 189 条帖子做主题聚类…"}` ×4 → `{"event":"done","ok":true,"analysis":{…}}` |
| GET | `/api/bench/<u>` | `{"user":{…},"source","fetched_at","postCount":189,"analysis":{…}|null,"kb":{…}|null}` |
| POST | `/api/bench/<u>/kb` | `{}` → 重新生成知识库并下载图片 → `{"ok":true,"kb":{…}}` |
| GET | `/api/bench/<u>/media/<file>` | 知识库里的本地图片（jpg/png） |
| POST | `/api/bench/<u>/imitate` | `{"postId":"2115667965328977920","roles":["editor","chief"],"stream":true,"review":true}`（不传 roles = 5 个都上） |
| GET | `/api/queue` | `{"items":[…],"postKeysConfigured":false,"schedulerIntervalSec":30}` |
| POST | `/api/queue` | `{"text":"…","mode":"review"|"auto","scheduled_at":"2026-11-03T07:05:00+08:00"(可选，不传=自动排最佳时段),"source":{"username":"dotey","postId":"…","agent":"editor"}}` → `{"ok":true,"item":{…},"queue":{…}}` |
| POST | `/api/queue/<id>` | `{"action":"approve"|"cancel"|"delete"|"post_now"|"mark_posted"}` 或 `{"action":"update","text":"…","mode":"auto","scheduled_at":"…"}` → `{"ok":true,"queue":{…}}` |

仿写流式事件：
```
{"event":"start","agent":"editor","name":"Qwen","model":"qwen/…"}
（审查事件）
{"event":"agent","agent":"editor","name":"Qwen","ok":true,"output":"完整输出","post":"提取出的帖子正文","weighted":218,"intent":"https://x.com/intent/post?text=…","ms":21000,"review":{…}}
{"event":"done","ok":true,"source":{"username":"dotey","postId":"…"},"outputs":[…],"review":{…汇总}}
```

`analysis` 结构（节选）：
```json
{"username":"dotey","analyzed_at":"…",
 "stats":{"posts":189,"spanDays":18.9,
   "fan":{"followers":264591,"following":1770,"ratio":149.5,"fanIndex":56.6,"avgInteractions":218.2,"erPerView":0.51,"erPerFollower":0.082,
          "avg":{"likes":142.4,"reposts":15.2,"replies":53.7,"quotes":6.9,"bookmarks":112.2,"views":38815.7}},
   "frequency":{"perDay":10.01,"perWeek":70.1,
     "heatmap":[[{"n":2,"avg":310.5}, …24 个小时…], …7 天，周一在前…],
     "weekdays":[{"weekday":0,"label":"周一","posts":30,"avgScore":355.9}],
     "bestSlots":[{"weekday":2,"hour":7,"label":"周三 07:00","posts":2,"avgScore":1204.5}],
     "bestHours":[{"hour":7,"posts":10,"avgScore":617.2}]},
   "format":{"withMedia":56,"withMediaAvg":465.2,"textOnlyAvg":263.4},
   "top":[{"id":"…","score":2800.5}]},
 "topics":[{"name":"baoyu-design 系列","desc":"…","hook":"…","count":9,"avgScore":620.1,"avgViews":49525,"share":4.8,"postIds":["…"]}],
 "monetization":{"bioLink":"https://baoyu.io","postsWithLinks":11,"topDomains":[["github.com",7]],
   "signals":[{"type":"付费社群/订阅","count":8,"examples":["…"]}]},
 "report":"## 一句话结论 …（Markdown）","errors":[]}
```

`kb` 结构（`data/bench/<u>/kb/kb.json`；同目录下还有 `知识库.md` 和 `media/`）：
```json
{"username":"dotey","built_at":"…","topPercent":20,
 "items":[{"id":"…","rank":1,"text":"…","url":"https://x.com/…","created_at":"…","local_time":"2026-10-30 07:12",
   "metrics":{"likes":1618,"reposts":0,"replies":0,"quotes":0,"bookmarks":2237,"views":211709},"score":2800,"topic":"baoyu-design 系列",
   "media":[{"type":"photo","file":"2115667965328977920_1.jpg","src":"https://pbs.twimg.com/…","saved":true}]}]}
```

队列项（`data/bench/queue.json`）：
```json
{"id":"20261102180501a1b2","text":"…","mode":"review","status":"pending","scheduled_at":"2026-11-03T07:05:00+08:00",
 "created_at":"…","source":{"username":"dotey","postId":"…","agent":"editor"},"weighted":218,
 "tweetId":null,"error":null,"intent":"https://x.com/intent/post?text=…"}
```
状态：
- `pending`：待发。「审核后发」的条目要先点 approve。
- `approved`：已审核，等到点。
- `posted`：已发布（有 `tweetId`，可链到 `https://x.com/i/status/<tweetId>`）。
- `failed`：失败，原因见 `error`。
- `manual`：需手动发（没配密钥，或超过 280 字）。
- `canceled`：已取消。

调度规则：到点后，`auto` 模式的条目和已审核（approved）的 `review` 条目会被自动发出；但只有在服务端配齐 `X_API_KEY / X_API_SECRET / X_ACCESS_TOKEN / X_ACCESS_SECRET` 时才真的发，否则状态变成 `manual`。

---

## 3.5 多账号 + 免密抓取（v1.5 新增）

**开始页**：`GET /api/accounts` 返回 `active` 为空（null）时，前端必须先显示「你的 X 账号」输入页，提交 `POST /api/accounts {"username":"xxx"}`。后端会免密读取资料+最近帖子，并用主编模型推断定位（20–60 秒，要有 loading）。

**右上角账号切换**：下拉列出所有 `accounts`，外加「＋ 添加账号」和「↻ 重新识别定位」。切换后整页刷新，因为策略、辩论、对标、队列都按账号分开存。

| 接口 | 说明 |
|---|---|
| `GET /api/accounts` | `{active, profile, accounts:[{handle,name,positioning,short,followers,avatar,createdAt,source}]}` |
| `POST /api/accounts` | body `{username}`，或 `{data: <bench JSON>}` / `{sample:"名字"}`；可选 `reinfer:true` 重新识别定位；可选覆盖 `positioning/audience/short/tone`。返回 `{ok, active, profile, warning}`。抓不到帖子时仍会建账号，`warning` 给出中文原因 |
| `POST /api/accounts/switch` | body `{handle}`，切换当前账号 |
| `GET /api/health` | `version:"1.6"`、`account` |
| `GET /api/bench/config` | 多了 `keylessFetch`（免密来源）、`account`（当前账号）、`postHandle`（允许自动发帖的账号） |

**profile 字段**：`handle, name, bio, positioning（一句话定位）, short（2–6 字标签）, audience, pillars[], tone, language, followers, avatar, posts`。

**全局替换**：所有原来写死 @LucaAiAi 的文案（标题、输入框提示、辩论题目、仿写按钮等）都要换成当前账号。5 个角色和审查狼的提示词由后端按当前账号定位自动改写；LucaAiAi 本身仍用原始提示词。

**抓取顺序**（`POST /api/bench/fetch`、`POST /api/accounts`）：先用 X API（配置了 `X_BEARER_TOKEN` 时），失败或没配置就用免密公开接口 `api.fxtwitter.com`（只取原创帖，约 100–200 条）。都失败时返回中文 `error`，前端直接显示，并引导用户用「导入 JSON」或「载入示例」。

**对标报告新增**：`analysis.forMe = {account, bestSlots[], bestHours[], benchPerDay, myPerDay, tip}` 放在报告顶部，作为「发帖时间建议」卡片。报告 markdown 最后两节固定是「对你的账号该学什么」和「发帖时间建议」。

**自动发帖保护**：只有当前账号等于 `.env.local` 里的 `X_POST_HANDLE`（默认 LucaAiAi）且配置了发帖密钥时才会真发；其他账号的队列项会标记成「需手动发」。

**示例数据**（`samples/`，真实公开数据）：dotey、op7418（歸藏）、BTCdayu、wolfyxbt、LucaAiBtc、YuanAiDC。@LucaAiAi 目前没有原创帖，所以没有示例。

## 3.6 中英双语 + 账号请求头（v1.6 新增）

**右上角「中文 / EN」切换**：选择存在 `localStorage.luca_ui_lang`（`zh` | `en`，默认 zh），切换后整页刷新。也支持 `?lang=en` / `?lang=zh` 直接打开。
当前版本的界面翻译在 `i18n.js`：一个「中文原文 → 英文」字典，加载时和 DOM 变化时自动替换文字、placeholder、title、alert/confirm。**重做 UI 时建议改成正规的 i18n 文案表（key → zh/en）**，字典可以直接拿来当英文文案初稿。模型输出（`.md`、辩论气泡等）不会被前端翻译，它们由后端直接按所选语言生成。

**每个 API 请求都要带两个请求头**：

| 请求头 | 值 | 作用 |
|---|---|---|
| `X-Lang` | `zh` 或 `en` | 所有模型输出（问答、策略、辩论发言/投票/裁决、对标报告、仿写草稿、定位识别）和审查狼的审查理由都用这个语言；审查狼的「语言一致」检查也按这个语言判断。英文辩论发言的字数按「英文词数 × 1.5」折算 |
| `X-Account` | 页面当前显示的账号 handle | 这次请求按这个账号读写数据（策略、辩论、对标、队列都按账号分开存）。不带时用服务器的当前账号（`data/active.json`）。`/api/accounts`、`/api/accounts/switch` 忽略这个头 |

任何 POST body 也可以带 `"lang":"zh"|"en"`，优先级高于 `X-Lang`；`/api/ask` 的 `"lang":"auto"` 表示按问题语言回答。

**账号显示规则（v1.6 修过的 bug）**：
- 页面加载时 `GET /api/accounts` 拿 `active`，之后所有请求都带 `X-Account: <active>`；右上角名字、标题、仿写按钮都只用这个值。
- 窗口重新获得焦点时再查一次 `/api/accounts`，如果当前账号被别的标签页改了，就整页刷新。
- 用户名不区分大小写：后端会把 `lucaaiai` 映射到已存在的 `LucaAiAi`，返回的 `handle` 一律以后端为准，前端比较时也要忽略大小写。
- 添加账号时，后端要等资料和定位都写好（20–60 秒）后才切换当前账号，前端拿到响应后再刷新页面。
- 不要用「全页文本替换 @LucaAiAi」的方式改名字，至少要排除账号下拉框，否则下拉里的 @LucaAiAi 会被改成当前账号名（v1.5 的 bug）。

## 4. 本地文件一览

> v1.5 起数据按账号存：`data/active.json`（当前账号），`data/accounts/<handle>/`（profile.json、self.json、策略目录、debates/、bench/<对标账号>/、bench/queue.json）。v1.4 的旧数据启动时会自动迁移到 `data/accounts/LucaAiAi/`。下面的旧路径都要加上这个前缀。


```
server.py        后端主程序（问答/策略/辩论/审查狼、静态文件、路由）
bench.py         v1.4 对标分析 + 知识库 + 仿写 + 发帖队列/调度器
agents.json      5 个岗位的模型和系统提示词
index.html       现在的前端（三个老页面 + 对标页外壳）
bench.js         对标页的前端逻辑
openapi.yaml     旧接口的 OpenAPI 规范（v1.3 及以前）
samples/         dotey.json（真实公开数据示例）+ SCHEMA.md（导入格式）
data/            运行后生成（不在压缩包里）：策略、辩论、bench/<用户名>/{account,analysis}.json、kb/、queue.json
.env.example     配置模板 → 复制为 .env.local 后填写
```

## 5. 设计建议 / 注意事项
- 长任务：
  - 策略 3 分钟
  - 辩论 2.5 分钟
  - 对标分析约 40 秒
  - 仿写 1–2 分钟

  这些都要有流式进度和可取消的感觉，不要让页面看起来卡死。
- 所有文本都是草稿，UI 上避免出现"已发布"这类误导字样。只有队列里 `status=posted` 才是真的发出去了。
- `【待核实】` 高亮；X 加权字数超过 280 要标红。
- 不要在前端存储或显示任何密钥；配置状态只看 `/api/bench/config` 和 `/api/health` 返回的布尔值。
