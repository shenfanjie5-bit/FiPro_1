# API_SPEC 模板（最小可用接口集）

按资源划分，先做 REST 即可。

## 3.1 策略与配置

- `POST /strategies`
- `POST /strategies/{id}/versions`
- `GET /strategies/{id}/versions/{version}`

## 3.2 数据快照

- `GET /tickers/{ticker}/snapshot?asof=...&strategy_version=...`

## 3.3 生成报告（触发工作流）

- `POST /reports/generate`
- body: `ticker`, `asof`, `strategy_version`, `tier`
- `GET /reports/{report_id}`

## 3.4 观察/持仓（影响检索深度）

- `POST /watchlist`（add/update tier）
- `GET /watchlist`

## 3.5 图谱查询

- `GET /graph/subtree?ticker=...&depth=...`
- `GET /graph/paths?entity=...&ticker=...`

## 3.6 记忆

- `GET /memory/search?ticker=...&q=...`
- `POST /memory/write`
