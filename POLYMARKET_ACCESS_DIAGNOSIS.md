# Polymarket 访问诊断

## 2026-09-16 实施与验证

当前项目已新增 **CoinRithm 独立聚合数据源**，默认使用它；高级设置仍可显式选择 **Gamma official direct**。这是数据源能力补全，不是解除 Gamma 的访问限制。每次分析只调用所选提供方，拒绝访问时不自动切换，也不使用代理或地域路由。

实测 BTC 和 TSLA 均从 CoinRithm 取得 HTTP 200，各保留 5 条价格结果。过滤掉非价格事件、其他资产、已结束或缺失结束时间的事件、缺失/过期更新时间的数据，以及提供方质量检查拒绝的数据。概率由百分数正确换算为 0 至 1；0%/100% 报价与无法区分的重复结果不纳入分析。聚合事件 open 不等于逐个结果的可交易状态，报告保留此限制。

完整 BTC 验证运行 `ad9e38a6-0623-4782-96ad-80f8b47ee672`：3 次 `qwen3:8b` 调用全部成功，5 条市场证据（`EV-002` 至 `EV-006`）均进入 thesis LLM 的 `input_manifest`。前端显示 `Polymarket: Data included via CoinRithm`，卡片和 LLM 输入都标注 `Data by CoinRithm`、原始市场和提供方数据时间。

最终 TSLA 验证运行 `7e222c56-bd13-40bf-b946-10158d676d7b`（2026-09-16 16:19:57 Asia/Seoul）同样完成：3 次 Qwen 调用成功、5 条市场证据全部进入 thesis 输入，报告中保留聚合数据限制。页面已实际展开核对 $355、$360、$350 等价格结果与来源链接。

页面独立连接检查：CoinRithm 返回 HTTP 200；Gamma 两个发现接口仍返回 451 / 1026，Ray ID 为 `a3be18dc2d2083f1-ICN`、`a3be18dc4b6d76c7-ICN`。两个结果分别显示，没有把聚合数据成功等同于 Gamma 恢复。

回归验证：`python -m pytest tests -q` 为 58 passed、25 subtests passed；前端 Node 测试 10 passed，`web/app.js` 语法检查通过。直接在根目录运行 pytest 还会包含保留的 `_legacy_memory_agent` 归档，其 3 项旧数据库测试在 Windows 临时文件清理阶段报文件占用；该归档未修改，当前工程测试结果不包含归档测试。

旧报告不会被改写。页面若仍在回放旧 Gamma 报告，会继续显示那一次真实的 451；重新分析才使用当前选择的数据源。

以下为 2026-09-14 的历史排查记录，其中“尚未接入”描述的是当时状态。

检查时间：2026-09-14 02:58 至 02:59（Asia/Seoul）。

## 补充：旧版为什么可能仍有数据

2026-09-14 03:26 至 03:28（Asia/Seoul）进一步对比后，确认之前的诊断漏掉了旧项目 Agent V2 的第三方数据源分支。不能把“旧版直连 Gamma 失败”概括成“旧项目所有数据路径都失败”。

| 版本 | 实际数据路径 | 本次核实 |
| --- | --- | --- |
| `E:\polymarket_trading\server.py` 原始版 | Gamma `/public-search`，随后 `/events?slug=...` | 直接调用原函数，BTC 与 TSLA 均返回 451 |
| `E:\polymarket_trading\agent_v2_core.py` V2 | 先调用原始版；未取得数据后调用 `fetch_coinrithm_crowd_probability` | 已核实代码与旧 README；离线路由测试确认会进入该分支 |
| 当前 `agent/tools/polymarket.py` | 仅官方 Gamma | 在线诊断的两条发现接口均返回 451；没有第三方聚合数据分支 |

旧 V2 的关键代码：`get_crowd_probability` 调用 `maintained_app.fetch_polymarket_snapshot`，之后调用 `fetch_coinrithm_crowd_probability`。第三方结果的 `source` 是 `crowd_probability.coinrithm`，`fallback_used` 为 `true`，卡片注明 `Data by CoinRithm`。这和 `source=crowd_probability.public_search` 的直连结果不是同一来源。

因此，缺少旧版 V2 的第二数据源可以解释“旧版有市场数据、新版没有”的代码差异，但未找到用户之前成功那次的完整报告，不能断言那一次具体使用了哪个来源，也不能否认 Gamma 过去曾经可用。

### 同机原函数复测

- Python：`D:\Anaconda3\envs\ogc2026\python.exe`，与当前服务相同。
- 导入旧 `server.py` 并调用未改动的 `fetch_polymarket_snapshot`，没有启动或改写旧项目。
- 内存缓存调用前后均为 0；Python 未配置自动代理。
- BTC：`/public-search?q=Bitcoin`，HTTP 451，`error code: 1026`，Ray ID `a3a9333df93b98ff-ICN`。
- TSLA：`/public-search?q=Tesla`，HTTP 451，`error code: 1026`，Ray ID `a3a9333e4cebea20-ICN`。
- 原函数复测时间：2026-09-13 18:26:25 UTC。
- 当前服务复测时间：2026-09-13 18:28:27 UTC；`/public-search`、`/events` 均为 451。
- V2 分支验证使用离线 mock，只证明路由存在，不冒充 CoinRithm 的实时连接或数据成功验证。

### 后续处理边界

Gamma 的访问限制仍然存在，不应通过代理、镜像或改写请求来绕过。CoinRithm 是另一家数据提供方，其[公开 API 文档](https://www.coinrithm.com/en/prediction-markets/api)提供独立聚合数据接口；将其接入当前项目属于新增第三方来源，不是恢复 Gamma。需先确认来源变更及适用的使用许可，再分别记录提供方、原始市场、数据时间和真实可用状态，不能隐藏 Gamma 失败或把聚合数据称作官方直连数据。本次尚未添加或实时调用该第三方分支。

## 确认的原因

Python 和本机浏览器访问官方 Gamma API 都被阻止。浏览器不是连接失败或证书错误，而是显示 HTTP 451 的韩文网页。
该错误页说明：Cloudflare 按韩国政府的法律命令，对经由韩国境内 Cloudflare 安全和 CDN 服务提供的该网站采取了访问限制。
这是错误页给出的说明，不是根据时区或 IP 推测用户所在地，也不构成法律意见。

浏览器检查地址：<https://gamma-api.polymarket.com/events?active=true&closed=false&limit=1>。
页面时间：2026-09-13 17:59:29 UTC。

## 技术证据

| 检查 | 结果 |
| --- | --- |
| 当前 `/public-search` | HTTP 451 / `legal_block` / 1026 |
| 当前 `/events` | HTTP 451 / `legal_block` / 1026 |
| 官方 `/api/geoblock` 诊断 | HTTP 451，未取得国家或资格 JSON |
| 旧版两种 Gamma 请求写法 | 同一环境下同样返回 451 |
| 浏览器直接访问 | 显示韩国相关法律访问限制页面 |
| Python 自动代理配置 | 未发现已配置的代理 |
| 自定义 Gamma 地址 | 未配置，使用官方地址 |

本次 Python 检查的 Cloudflare Ray ID：`a3a909b17c3aaa7d-ICN`、`a3a909b1cc02ea27-ICN`。
记录不包含账户凭据、公共 IP 或任何交易操作。

## 可以如何处理

1. 如果认为被错误限制，可向 Polymarket 官方支持核实只读 Gamma 数据访问的可用性，并提供上述时间、URL、HTTP 状态与 Ray ID。不要把交易接口的地区说明直接等同于只读数据接口的全部限制。
2. 待服务方确认访问已获允许并恢复后，在应用高级设置中点击 `Polymarket connection diagnostics > Check API`，再启动新分析。历史报告不会自动改写。
3. 当前应用仍可分析其他来源，但会明确标记缺少 Polymarket 证据，不会填入虚假概率或把缓存伪装成实时数据。

目前限制未解除。更换本地 LLM、过滤关键词或重复重试不能消除该服务端响应，本次没有更改代理、地域路由或证书验证。

## 官方参考

- [Cloudflare HTTP 451 说明](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/4xx-client-error/error-451/)
- [Cloudflare 错误分类：1026 属于 legal](https://developers.cloudflare.com/fundamentals/reference/error-responses/)
- [Polymarket 官方可用性说明与支持入口](https://docs.polymarket.com/api-reference/geoblock)
