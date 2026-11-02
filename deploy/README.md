# Vercel 部署包装

线上版本用这两个文件夹部署（`api/index.py` 把 `app/server.py` 包成 Vercel Python 函数）。部署前把对应应用的代码复制到 `app/`：

```bash
# X 版（仓库根目录的文件）
rm -rf /tmp/vx && cp -r deploy/x /tmp/vx && mkdir /tmp/vx/app && \
  cp -r *.html *.js *.py *.json *.yaml assets samples /tmp/vx/app/
# Instagram 版
rm -rf /tmp/vig && cp -r deploy/ig /tmp/vig && cp -r ig-ops-web /tmp/vig/app
cd /tmp/vx && vercel deploy --prod   # 或 /tmp/vig
```

密钥只放在 Vercel 的环境变量里（ZENMUX_API_KEY、APIFY_TOKEN、PLATFORM_SWITCH_URL），不要写进代码。
