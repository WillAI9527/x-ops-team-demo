# @LucaAiAi 运营团队 · 5 岗位智能体

一个只用 Python 标准库写的小服务：一个网页，加一套 REST API。两种用法：

1. **问答**：问一个岗位，或全员接力，主编出最终交付。
2. **运营策略（每日跟进）**：输入问题或目标，5 个岗位按各自定位出一份完整的 X 运营策略。之后每天写跟进，主编修订成新版本，每个版本都存成文件。
每个岗位的 system prompt 来自 `LucaAiAi_团队岗位定位.md`，各用一个不同的 ZenMux 模型。

> **只出草稿。** 本服务不连接 X，不会发帖、关注、评论或私信。所有内容都要由账号主人骊元（Luca）审核后亲自决定。

| id | 名字 | 岗位 | 模型 | 备注 |
|---|---|---|---|---|
| researcher | DeepSeek | 选题研究员 | deepseek/deepseek-v3.2 | |
| editor | Qwen | 内容编辑·视觉 | qwen/qwen3.8-flash | max_tokens 4000，关闭 reasoning（否则思考会耗尽 token，正文为空） |
| community | Gemini | 社区运营 | google/gemini-3.1-flash-lite | |
| data | MiniMax | 数据分析·流程 | minimax/minimax-m2.1 | 输出里的 `<think>` 会被自动去掉 |
| chief | Claude | 负责人·主编 | anthropic/claude-haiku-5.5 | **不传 temperature**（会报 400）；策略模式 max_tokens 8000 |

## 最新版本（2026-11）：X 版 + Instagram 版

| 应用 | 代码位置 | 线上地址 | 本地端口 |
|---|---|---|---|
| X 运营团队 | 仓库根目录 | https://x-ops-team-demo.vercel.app/ | 8788 |
| Instagram 运营团队 | `ig-ops-web/` | https://ig-ops-team-demo.vercel.app/ | 8789 |

- 两个版本顶部都有「X ⇄ Instagram」切换按钮，中文 / EN 一键切换：中文模式界面全中文，EN 模式全英文；抓取到的帖子保持原文，不做翻译。
- X 版对标抓取免密钥（fxtwitter + 镜像 + 备用通道，带重试和缓存）；Instagram 版通过 Apify 读取公开主页数据（24 小时缓存）。
- **密钥放在各自目录的 `.env.local`**（复制 `.env.local.example` 后填写）：X 版需要 `ZENMUX_API_KEY`；Instagram 版需要 `ZENMUX_API_KEY` 和 `APIFY_TOKEN`。`.env.local` 已在 `.gitignore` 里，不会被提交。
- 本地运行：`python3 server.py`（Instagram 版在 `ig-ops-web/` 里运行），或双击 `start.command`。
- 线上部署用的 Vercel 包装在 `deploy/`，说明见 `deploy/README.md`。

## 运行

```bash
cp .env.local.example .env.local   # 填入 ZENMUX_API_KEY（也可以直接用环境变量）
python3 server.py                  # 打开 http://127.0.0.1:8787
```

- macOS 上也可以直接**双击 `start.command`**：已经在运行就直接打开网页，没在运行就后台启动再打开（日志写到 `server.log`）。双击 `stop.command` 停止。
- 需要 Python 3.8 以上，不用装任何依赖。
- Key 的读取顺序：先看环境变量 `ZENMUX_API_KEY`，没有再读 `.env.local`。服务不会打印或记录 key。`.env.local` 已经写进 `.gitignore`。
- 可选环境变量：`DATA_DIR`（策略保存目录，默认 `./data`）、`CHIEF_MAX_TOKENS`（默认 8000）、`STRATEGY_TIMEOUT`（单次模型调用超时，默认 300 秒）、`PORT`（默认 8787）、`HOST`（默认 127.0.0.1，给局域网或网关访问时改成 `0.0.0.0`）、`AGENTS_API_TOKEN`、`CORS_ORIGIN`（默认 `*`）、`ZENMUX_TIMEOUT`（默认 180 秒）。

## 网页怎么用

页面顶部有三个标签页：「问答」「运营策略（每日跟进）」和「辩论（5 岗位）」。打开 `http://127.0.0.1:<端口>/#debate` 直接进辩论页。

### 运营策略（每日跟进）

1. **新建策略**：写下问题或目标，点「生成策略」。DeepSeek（选题）、Qwen（内容·视觉）、Gemini（社区）、MiniMax（数据·流程）并行给出建议，Claude（主编）再整合成一份完整策略，章节固定为：0 结论摘要、1 定位、2 内容支柱、3 每周日历、4 形式与模板、5 社区互动、6 指标与 KPI、7 风险与合规、8 未来 7 天行动、9 待核实清单。实测约 3 分钟。
2. **今日跟进**：每天写下进展、真实数据和想改的地方，点「提交跟进并修订策略」。Claude 基于最新版、以往日志和今天的记录，修订出新版本，并写出「本次变更」。可以勾选几个岗位先给调整建议，会慢一点。实测只勾 MiniMax 时约 75 秒。
3. **手动编辑**：点「手动编辑」，直接改 Markdown，再「保存为新版本」。
4. **版本历史**：左侧列出全部版本，点开就能看历史版本；也可以复制或下载 `.md` 文件。

规则：只出草稿，不连接 X；没提供的数字一律写【待核实】或"基线待采集"（页面上【待核实】会标黄）。你在跟进里给的真实数据会注明"（账号主人 日期 提供）"。

**文件保存位置**：`data/<策略ID>/`

- `strategy.json`：全部版本、各岗位原始建议、每日跟进日志。
- `LucaAiAi_策略_<日期>_v<N>.md`：每个版本一个 Markdown 文件，末尾附版本信息、当天跟进记录、本次变更和每日日志。

时间全部按 UTC+8。

### 辩论（5 岗位）

输入一个关于 @LucaAiAi X 运营的议题（例如"起步期日更 3 条短帖，还是每周 2 条深度线程？"），点「开始辩论」。发言实时出现在聊天记录里，每个模型有自己的头像和颜色：DS = DeepSeek（选题研究员）、QW = Qwen（内容编辑·视觉）、GE = Gemini（社区运营）、MM = MiniMax（数据分析·流程）、CL = Claude（主编）。

1. **第 1 轮 · 立论**：5 位按 DeepSeek → Qwen → Gemini → MiniMax → Claude 依次发言，每人约 150 字，从第二位起先点名回应上一位。
2. **第 2 轮 · 反驳**：每人挑出自己最不同意的观点，点名回应，可以明确写"我改变立场"。轮数可选 1–3，默认 2。
3. **主编提案**：Claude 基于全部发言提出一个具体、可投票的提案。
4. **投票**：5 位同时投票，支持 / 反对 / 有条件支持，各附一句理由，系统自动计票。
5. **裁决书**：Claude 写出共识、主要分歧（表格）、投票结果、最终决定（通过 / 修改后通过 / 否决）、对策略的修改建议、7 天行动和待核实清单。
6. **写进策略**：在「把裁决写进运营策略」里选一份已有策略，Claude 会按裁决修订，存成新版本（版本来源显示「辩论裁决」）；也可以选「用裁决新建一份策略」。

实测：2 轮辩论全程约 2.5 分钟，裁决写进策略约 45 秒。规则和策略模式一样：只出草稿，不编造数字，不确定的地方写【待核实】。

**文件保存位置**：`data/debates/`

- `<辩论ID>.json`：完整记录（每条发言、提案、投票、计票、裁决、应用记录），边跑边保存。
- `LucaAiAi_辩论_<日期>_<当天第 N 场>.md`：可读的辩论全文和裁决书。

### AI 审查（Make It Fail，v1.3）

三个页签顶部都有两个开关：**AI 审查**（默认开）和 **DEMO 出错模式**（默认关）。

审查范围：辩论的每条发言和每张票（提案、裁决书不审）、策略的各岗位建议和 Claude 的最终策略（新建和每日跟进都审）、问答（单岗位和全员接力的每一棒）。

每条输出先过两道检查：

- **代码检查**：发言长度（目标约 150 字，超过 200 字算不通过）；投票格式（必须有 `投票：` 和 `理由：` 两行）；X 帖子草稿 ≤280 加权字（中日韩字符算 2，链接算 23，只有输出里有帖子草稿时才查）；不能说"已发布 / 已关注 / 已发送"；不能说自己"被训练"；回复语言和问题一致。
- **AI 检查**（另一个模型，严格 JSON）：没有编造数字、价格、日期或 handle；没核实的不标"已核实"；辩论发言要回应上一位；符合岗位定位；只谈 @LucaAiAi（不混 LucaAiBtc / YuanAiDC）；不承诺自动互动。审查模型默认是 Claude；Claude 自己发言时由 Gemini 审；返回的 JSON 无效时换 DeepSeek 再审一次。审查模型调用失败时，这项记为"跳过"，不会卡住流程。

不通过的话，同一个岗位模型按审查意见重写，再复查一遍，最多重写 2 次。原稿、检查结果和修正稿都会保留。

- 每个气泡或卡片上有徽章：**审查 ✓通过** / **已修正 N 项** / **✗未通过**。点徽章可以展开 ① 原稿 ② 检查项（红绿标记，标明"代码"还是"AI"检查，带原因）③ 修正稿和复查结果。
- **DEMO 出错模式**：每个岗位先故意写一版有问题的初稿（编造数字、假装已发布、超字数等），用来演示审查怎么抓错、怎么修。页面会显示红色横幅，辩论 .md 文件和列表里也会标 DEMO。这个模式只用来演示，不要拿它的产出去发帖。
- 审查会让耗时变长：每条输出多一次审查调用，不通过时还要加上重写和复查。
- 接口用法：请求体里加 `"review": false` 可以关掉审查（行为和 v1.2 一样）；加 `"demo": true`（或 `"fail"`）开启 DEMO 模式。返回结构向后兼容，`output` / `text` 始终是最终文本，另外多出 `review` 字段；流式接口多出 `review_start`、`review`、`fix` 三种事件。

### 问答

- 可以选单个岗位，也可以选 **全员**。
- 全员模式按 DeepSeek（选题）→ Qwen（内容·视觉）→ Gemini（社区）→ MiniMax（数据·流程）→ Claude（主编）的顺序接力，每一棒都能看到前面的产出，最后由主编整合出「📦 最终交付」，并置顶显示。实测一轮大约 1 到 2.5 分钟。
- 每个岗位的输出放在单独的卡片里，标着模型名，带「复制」按钮。
- 服务端开了鉴权的话，在页面上填 API Token（只保存在浏览器的 localStorage 里）。

## 改岗位配置

所有岗位配置都集中在 **`agents.json`**，其他系统也可以直接复用：

- `systemPrompt`：取自岗位定位文档里每个岗位的「角色 system prompt」，后面加了本应用的额外规则（无法联网、未知事实标【待核实】、全是草稿）。
- `pipelineTask`：全员模式下这一棒要做什么。
- `model` / `max_tokens` / `temperature` / `sendTemperature` / `extraBody`：调模型时用的参数。
- `pipeline` / `finalAgent`：接力顺序，以及由谁出最终交付。

改完重启 `server.py` 就生效。

---

## 对接说明（给中央网关或其他系统）

网页只是其中一个客户端。其他系统直接调 REST API 就行，完整规范见 `openapi.yaml`（服务运行时也可以从 `GET /openapi.yaml` 获取）。

- Base URL：`http://<host>:8787`
- 已开启 CORS（`Access-Control-Allow-Origin` 默认是 `*`，可以用 `CORS_ORIGIN` 限定）。
- 鉴权（可选）：服务端设置 `AGENTS_API_TOKEN` 后，`/api/agents` 和 `/api/ask` 都要带 `Authorization: Bearer <token>`，否则返回 401。`/api/health` 不需要鉴权。
- 超时：全员模式是同步接力，网关的读超时建议设到 300 秒以上，或者改用 `stream: true` 边跑边收。

### 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | `{ok, keyConfigured, authRequired}` |
| GET | `/api/agents` | 岗位列表 `{account, pipeline, finalAgent, agents:[{id,name,nameEn,model,order}]}` |
| POST | `/api/ask` | 请求体 `{agent: "<id>"｜"all", question, lang?: "zh"｜"en"｜"auto", stream?: bool}` |
| POST | `/api/strategy` | 新建策略 `{question, stream?}`，返回 `{ok, strategy, outputs}`（别名：`POST /api/strategies`） |
| GET | `/api/strategies` | 策略列表（按更新时间倒序） |
| GET | `/api/strategies/<id>` | 一份策略的全部版本和每日日志 |
| POST | `/api/strategies/<id>/followup` | 每日跟进 `{note, roles?: ["researcher","editor","community","data"], stream?}`，生成新版本 |
| POST | `/api/strategies/<id>/edit` | 手动编辑 `{markdown, changelog?}`，存成新版本（不调用模型） |
| GET | `/api/strategies/<id>/versions/<n>` | 某个版本的 JSON；加 `.md` 后缀则下载 Markdown |
| POST | `/api/debate` | 发起辩论 `{topic, rounds?: 1–3（默认 2）, stream?}`，返回 `{ok, debate, ms}`（别名：`POST /api/debates`） |
| GET | `/api/debates` | 辩论列表（新的在前，含计票、决定、是否已应用） |
| GET | `/api/debates/<id>` | 一场辩论的完整记录；加 `.md` 后缀则返回 Markdown |
| POST | `/api/debates/<id>/apply` | 把裁决写进策略 `{strategyId?}`：有 ID 就修订该策略并存为新版本，没有就新建一份策略 |

`/api/health`、`/api/agents`、`/api/ask` 的请求和返回与之前一致；`/api/agents` 里每个岗位只是多了一个 `role` 字段。新建策略和跟进耗时较长，建议用 `stream: true`，或者把网关的读超时设到 400 秒以上。

`/api/ask` 的返回（非流式）：

```json
{
  "mode": "all",
  "question": "...",
  "ok": true,
  "finalAgent": "chief",
  "final": "主编整合后的最终交付（Markdown）",
  "outputs": [
    {"agent": "researcher", "name": "选题研究员", "nameEn": "Topic Researcher",
     "model": "deepseek/deepseek-v3.2", "ok": true, "output": "...", "finishReason": "stop",
     "usage": {"prompt_tokens": 0, "completion_tokens": 0}, "ms": 33523}
  ]
}
```

单个岗位失败时，那一项会是 `ok:false` 并带 `error`，全员流程会继续往下跑，整体 `ok` 为 false。

### curl 示例

```bash
# 健康检查
curl http://127.0.0.1:8787/api/health

# 岗位列表（开启鉴权时带上 token）
curl -H "Authorization: Bearer $AGENTS_API_TOKEN" http://127.0.0.1:8787/api/agents

# 全员接力，主编出最终交付
curl -X POST http://127.0.0.1:8787/api/ask \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $AGENTS_API_TOKEN" \
  -d '{"agent":"all","question":"帮我写一条介绍 GPT-6.1 Terra 降价的中文帖","lang":"zh"}'

# 只问内容编辑
curl -X POST http://127.0.0.1:8787/api/ask \
  -H "Content-Type: application/json" \
  -d '{"agent":"editor","question":"写一条支线B的帖子：3个让 AI 写周报更好用的提示词技巧"}'

# 新建运营策略（流式）
curl -N -X POST http://127.0.0.1:8787/api/strategy \
  -H "Content-Type: application/json" \
  -d '{"question":"@LucaAiAi 刚起步，想在 30 天内把 AI 科普账号做起来，每天 1 小时，帮我出一份 X 运营策略","stream":true}'

# 每日跟进（主编修订 + MiniMax 给数据建议）
curl -X POST http://127.0.0.1:8787/api/strategies/<id>/followup \
  -H "Content-Type: application/json" \
  -d '{"note":"今天发了 2 条，第一条浏览 320、赞 6；想少做图，多做短线程","roles":["data"]}'

# 发起辩论（流式：每条发言、提案、每张票、裁决各推一行，最后一行 event=done）
curl -N -X POST http://127.0.0.1:8787/api/debate \
  -H "Content-Type: application/json" \
  -d '{"topic":"@LucaAiAi 起步期应该日更 3 条短帖，还是每周 2 条深度线程？","rounds":2,"stream":true}'

# 把裁决写进某份策略（不带 strategyId 则新建策略）
curl -X POST http://127.0.0.1:8787/api/debates/<辩论ID>/apply \
  -H "Content-Type: application/json" -d '{"strategyId":"<策略ID>"}'

# 流式（NDJSON，每完成一个岗位推一行，最后一行 event=done）
curl -N -X POST http://127.0.0.1:8787/api/ask \
  -H "Content-Type: application/json" \
  -d '{"agent":"all","question":"本周支线A该做哪3个选题？","stream":true}'
```

### 错误码

| 状态码 | 含义 |
|---|---|
| 400 | 缺 `question` / `topic`、`rounds` 不在 1–3、未知 `agent`（返回里有 `valid` 列表）、`lang` 不合法、问题超过 8000 字、JSON 格式错误 |
| 401 | 开启了鉴权，但 token 缺失或错误 |
| 404 | 路径、策略、版本或辩论不存在 |
| 409 | 辩论还没有裁决，不能写进策略 |
| 500 | 服务端没有配置 `ZENMUX_API_KEY` |
| 502 | 新建策略或跟进时主编调用失败，这次不会保存；`outputs` 里有各岗位的结果。辩论的提案或裁决失败时，记录会以 `status: failed` 保存；应用裁决失败时不保存 |

## v1.4 对标账号分析（新页面）

双击 `start-x-ops.command`（默认端口 8788，直接打开「对标账号分析」）。全部数据存在本地 `data/bench/`，没有云端。

1. **拿数据**：输入任意 X 用户名点「抓取并分析」（需要 `.env.local` 里的 `X_BEARER_TOKEN`，最多约 300 条原创帖，不含转推/回复）；或者「导入 JSON」（格式见 `samples/SCHEMA.md`）；或者「载入示例数据」（`samples/dotey.json`，宝玉的真实公开数据）。
2. **分析报告**：粉丝指数（0–100）、平均浏览/互动、互动率；Gemini 主题聚类按平均热度排序；发帖频率 + 北京时间 7×24 热力图 + 最佳时段；变现信号（代码正则 + 外链域名）；Claude 主编写中文分析报告（含变现模式判断和给 @LucaAiAi 的建议）。
3. **爆款知识库**：热度前 20% 的帖子连同图片（视频只存封面）存到 `data/bench/<用户名>/kb/`（`kb.json` + `知识库.md` + `media/`），网页里可搜索、按主题筛选。每条可点「让 5 个角色仿写」，结果经审查狼审查/修正。
4. **发帖队列**：仿写稿可「加入发帖队列」，自动排到对标博主互动最高的时段。每条可切换「审核后发 / 全自动发」。配置了 `X_API_KEY / X_API_SECRET / X_ACCESS_TOKEN / X_ACCESS_SECRET` 才会通过 `POST /2/tweets` 真发；没配就显示「未配置发帖密钥」，到点标成「需手动发」，给 x.com 发帖框链接。调度器在 server 进程里每 30 秒检查一次——工具关掉就不会发。

热度 = 赞 + 2×转发 + 1.5×回复 + 2×引用 + 0.5×收藏。

## 已知限制

- 辩论发言有字数要求（约 150 字），个别模型偶尔会写长一些；模型也可能声称"查过某些案例"，这类说法 Claude 会在裁决里标【待核实】，不要当事实用。

- 模型无法联网。用户问题里没给出的事实（价格、日期、链接、handle），岗位应该标【待核实】，但个别模型偶尔还是会自己补数字，或把 handle 标成"已核实"。全员模式下主编会把这些挑出来，发布前仍然需要人工核实。
- 策略里的占比、时段、停止条件都是建议值，不是数据，要按你自己的实际情况调整。
- 模型自报的「X 加权字数」不准，发布前请用工具复核（中日韩字符算 2，上限 280）。
