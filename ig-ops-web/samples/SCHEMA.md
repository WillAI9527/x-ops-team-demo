# 对标账号导入 JSON 格式（luca-xops-bench/v1）

「对标账号分析 → 导入 JSON」接受两种格式：

## 1. 推荐格式 `luca-xops-bench/v1`（samples/dotey.json 就是这个格式）

```json
{
  "schema": "luca-xops-bench/v1",
  "source": "x-mcp public read",
  "fetched_at": "2026-11-02T09:00:00+08:00",
  "user": {
    "id": "3178231", "username": "dotey", "name": "宝玉",
    "description": "…", "created_at": "2008-03-31T…", "location": "", "url": "https://…",
    "profile_image_url": "https://pbs.twimg.com/…", "verified": false,
    "public_metrics": { "followers_count": 264591, "following_count": 1770, "tweet_count": 0, "listed_count": 0 }
  },
  "posts": [
    {
      "id": "1984…", "text": "帖子正文", "created_at": "2026-11-01T03:12:00.000Z", "lang": "zh",
      "url": "https://x.com/dotey/status/1984…",
      "metrics": { "likes": 120, "reposts": 30, "replies": 12, "quotes": 3, "bookmarks": 80, "views": 25000 },
      "media": [ { "type": "photo", "url": "https://pbs.twimg.com/media/….jpg", "preview_image_url": "" } ],
      "urls": [ "https://github.com/…" ]
    }
  ]
}
```

必填：`user.username`、`posts[].id`、`posts[].text`、`posts[].created_at`（ISO 8601，带时区）。其余缺省按 0 / 空处理。
`media[].type` 取 `photo` / `video` / `animated_gif`；视频只保存封面 `preview_image_url`。

## 2. X API v2 原始返回

`GET /2/users/:id/tweets?tweet.fields=created_at,public_metrics,lang,entities,attachments&expansions=attachments.media_keys,author_id&media.fields=type,url,preview_image_url&user.fields=public_metrics,description,…`
的返回体（`{ "data": [...], "includes": { "media": [...], "users": [...] } }`）可以直接导入；多页结果把 `data` 拼起来即可。
`public_metrics` 里的 `like_count / retweet_count / reply_count / quote_count / bookmark_count / impression_count` 会自动映射。

## Instagram 版说明

- Instagram 版**不支持实时抓取**：读取账号数据需要官方 Instagram Graph API（Instagram 商业 / 创作者账号 + 关联 Facebook 主页 + Meta 开发者应用审核），目前没有接入。
- 可以把 Graph API 或 Insights 导出的数据整理成上面的格式导入。指标对应：`likes`=赞、`replies`=评论、`reposts`=分享、`bookmarks`=收藏（保存）、`views`=覆盖人数或播放量；可选 `format`：`photo` / `carousel` / `reels`。
- `samples/ig_ai_tips.json`、`samples/ig_prompt_lab.json` 是**虚构的示例数据**，只用于演示。
