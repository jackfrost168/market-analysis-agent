# FastAPI / AWS 上传准备

这个目录是现有 LangGraph Agent 的 HTTP 入口。`api.py` 提供 FastAPI 路由和原有网页，`bridge.py` 调用 `agent.service.AgentService` 和异步任务管理器。**无需 Docker**，也无需改动原有 Agent 节点。

## 项目结构

```text
market_analysis_agent/
├── requirements.txt
├── agent/
├── web/
├── awsdeploy/
│   ├── __init__.py
│   ├── api.py
│   ├── bridge.py
│   ├── financial-agent.service.example
│   └── README.md
├── server.py                 # 原 HTTP 服务，仍可本地使用
└── data/                    # SQLite 数据，运行时生成
```

`Dockerfile.aws` 和两个 Compose 文件当前用不上，故没有创建。

## 启动与检查

使用 Python 3.12，在项目根目录运行：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn awsdeploy.api:app --host 127.0.0.1 --port 8001 --workers 1
```

从本机打开 `http://127.0.0.1:8001/`，或检查：

```bash
curl http://127.0.0.1:8001/api/health
```

运行回归测试：`.venv/bin/python -m unittest discover -s tests -v`。

`/docs` 是 FastAPI 自动生成的接口文档。网页先调用 `POST /api/runs`，随后通过 `/api/runs/{run_id}/events` 的 SSE 流接收节点级实时状态；连接不支持 SSE 或中途断线时，会自动退回 `/api/runs/{run_id}` 轮询。`POST /api/analyze` 仍提供同步调用。

运行指标可以通过 `GET /api/metrics` 查看。单次报告的 `observability` 包括总耗时、节点耗时、工具和模型调用、Ollama token 数量以及成本口径。本地 Ollama 的 token API 计费为 0；若需要与云 API 对比，可以配置 `LLM_INPUT_USD_PER_MILLION_TOKENS` 和 `LLM_OUTPUT_USD_PER_MILLION_TOKENS`。

完整评测见根目录 `evals/`：

```bash
.venv/bin/python -m evals.run_eval --limit 1
.venv/bin/python -m evals.run_eval --output evals/latest_report.json
```

完整评测会调用实时数据源和 Ollama，不属于快速单元测试。

## 上传 AWS 时的配置

以下按**一台 EC2 Linux 实例**准备；如果后续选择其他 AWS 运行方式，应用入口仍是 `awsdeploy.api:app`。

1. 上传 `agent/`、`awsdeploy/`、`web/`、`requirements.txt` 等项目文件到同一项目目录。`.venv/`、`__pycache__/` 和测试缓存无需上传。
2. 在实例上创建 Python 3.12 环境并安装依赖，启动命令与上面相同。运行进程的工作目录设为项目根目录。
3. 将 `AGENT_DATA_DIR` 设为**绝对路径**，指向进程可写的持久目录，例如挂载的数据卷中的目录。两个 SQLite 文件会写在这里。若要保留本机历史记录，另行复制 `data/*.sqlite3`，复制前停止写入进程。
4. 配置 `OLLAMA_URL`、`OLLAMA_TAGS_URL`、`OLLAMA_MODEL`。完整的本地 LLM 分析需要可访问的 Ollama 服务和已安装的模型。`OLLAMA_EMBED_MODEL` 留空时使用本地 hash embedding。设置真实的 `SEC_USER_AGENT` 联系信息。环境变量模板见根目录 `.env.example`；程序**不会自动读取** `.env` 文件。
5. 使用反向代理或负载均衡器提供 HTTPS 和访问控制，让 Uvicorn 监听 `127.0.0.1:8001`。应用本身没有用户认证；不要直接把分析接口公开到互联网。Ollama 的 11434 端口也应保持私有。

当前异步任务状态和 SSE 事件保存在进程内，已完成报告和失败记录保存在 SQLite。先使用 **一个 Uvicorn worker、一台实例**；多 worker 会让状态请求落到不同进程。进程重启时未完成任务不能恢复，需要重新提交。日后若要水平扩展，需要外部任务队列、共享状态和事件存储。

Nginx 的 SSE 路径必须关闭响应缓冲，参考 `nginx-financial-agent.conf.example` 中的 `proxy_buffering off`。修改后运行 `sudo nginx -t` 并 reload Nginx。

## EC2 使用 Mac 上的 Ollama

如果模型运行在 Mac，而 FastAPI 运行在 EC2，请在 **Mac 终端**运行一个 SSH 连接，同时建立两个本机隧道（将地址替换为你的实例地址）：

```bash
ssh -N -o IdentitiesOnly=yes -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
  -i "$HOME/.ssh/financial-agent.pem" \
  -R 127.0.0.1:11434:127.0.0.1:11434 \
  -L 127.0.0.1:18001:127.0.0.1:8001 \
  ec2-user@EC2_PUBLIC_IP
```

保持这个终端窗口打开，然后在 Mac 浏览器访问 `http://127.0.0.1:18001/`。`-R` 让 EC2 的 Agent 访问 Mac 上的 Ollama；`-L` 让 Mac 浏览器访问 EC2 上仅监听本机的 FastAPI。Mac 的 Ollama 必须运行，且需要安装生成模型，例如 `ollama pull qwen3:8b`。Mac 睡眠、关机或 SSH 连接中断后，需要重新建立隧道。

EC2 上的服务文件示例为 `awsdeploy/financial-agent.service.example`。运行中可用 `systemctl status financial-agent`、`curl http://127.0.0.1:8001/api/health` 和 `sudo journalctl -u financial-agent -f` 检查服务。

AWS 官方文档：[EC2 安全组](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/creating-security-group.html)、[EBS 数据持久性](https://docs.aws.amazon.com/ebs/latest/userguide/EBSFeatures.html)。

## AWS 1.7B / Mac 8B 模型切换

`OLLAMA_MODEL_ROUTES` 可将模型名映射到不同的私有 Ollama 服务。前端 **Model, horizon & advanced options → LLM model** 显示服务标签；`Auto` 使用 `OLLAMA_MODEL`，不会偷偷切换到另一台机器。请求仅能选择配置中的模型。某个服务断开不会影响另一服务的模型列表；不可用模型会标注 unavailable。

AWS 本机 Ollama 使用 `127.0.0.1:11435`，原 Mac SSH 转发保留 `127.0.0.1:11434`。systemd Ollama override 可设置 `OLLAMA_HOST=127.0.0.1:11435`、`OLLAMA_NUM_PARALLEL=1`、`OLLAMA_MAX_LOADED_MODELS=1` 和 `OLLAMA_CONTEXT_LENGTH=4096`，随后 `systemctl daemon-reload`、`systemctl enable --now ollama`，并执行 `OLLAMA_HOST=127.0.0.1:11435 ollama pull qwen3:1.7b`。

服务 `.env` 中使用 `.env.example` 的 routes 示例；内存验证通过后将 `OLLAMA_MODEL` 设为 `qwen3:1.7b` 并重启 `financial-agent`。1.7B 路由禁用 thinking，8B 保留原调用参数。生成模型路由不会更改 embedding 端点、Vector DB 或已有报告。EC2 约 1 GiB 内存不足以正常运行此模型；swap 无法替代足够的物理内存，需要先升级内存容量并测量完整任务的延迟。

## Qwen API 默认模型与费用

生产配置现在提供 **Qwen API 8B（默认）**、**Qwen API 30B-A3B**、**Qwen3 8B (Mac)**。仅替换模型客户端；九节点工作流、证据检索、验证与可选反事实检查复用原接口。不会因 API 失败自动切换到 Mac。API 模式采用非思考 JSON 输出，并继续使用 Pydantic 验证；普通调用输出上限 4096 tokens，反事实调用保留 512 tokens 和原超时预算。

服务器 `.env` 的配置如下（路由 JSON 使用一整行，systemd 中外层单引号保留 JSON 引号）：

```dotenv
OLLAMA_MODEL=qwen3-8b
OLLAMA_MODEL_ROUTES='{"qwen3-8b":{"provider":"qwen_api","base_url":"https://dashscope-us.aliyuncs.com/compatible-mode/v1","label":"Qwen API 8B"},"qwen3-30b-a3b":{"provider":"qwen_api","base_url":"https://dashscope-us.aliyuncs.com/compatible-mode/v1","label":"Qwen API 30B-A3B"},"qwen3:8b":{"base_url":"http://127.0.0.1:11434","label":"Qwen3 8B (Mac)"}}'
QWEN_API_BASE_URL=https://dashscope-us.aliyuncs.com/compatible-mode/v1
QWEN_API_PRICING_JSON='{"qwen3-8b":{"input":0.072,"output":0.287},"qwen3-30b-a3b":{"input":0.108,"output":0.431}}'
```

### 申请与录入密钥

1. 打开 [Model Studio 控制台](https://modelstudio.console.alibabacloud.com/)，注册/登录并开通服务。
2. 选 **US (Virginia)** → **API Key** → **Create API Key**，使用按量付费 key，复制并保存。Coding Plan / Token Plan 专用 key 不能替代此 key。按账户提示完成开通及计费设置。
3. 在 Mac 终端登录现有 EC2，然后执行安全配置脚本：

```bash
ssh -i "$HOME/.ssh/financial-agent.pem" ec2-user@100.59.221.40
cd /home/ec2-user/financial-agent
python3 awsdeploy/configure_qwen_api.py
sudo systemctl restart financial-agent
```

脚本在交互式终端隐藏密钥输入，先向 8B 和 30B 各发一次极短请求验证访问，再写入权限为 0600 的 `.env`。这两次探测按量计费。API key 不进入前端、Git 或应用日志；备份放在仓库外的 `~/.config/financial-agent/backups/`。有分析任务在运行时，脚本会要求稍后重试。配置后可检查 `curl http://127.0.0.1:8001/api/models`；该接口不会返回密钥，API 列表的 configured 仅表示密钥已配置，并非持续健康检查。

4. 刷新网页，展开 **Model, horizon & advanced options → Model**。Auto 使用 API 8B，也可选 API 30B-A3B 或 Mac 8B。缺少 key 时 API 选项禁用，提交 Auto 会立即提示配置缺失，不生成假成功报告。Mac 选项仍可用，但需要 Ollama 与 SSH 隧道。

### 费用统计

每个调用保存提供商返回的输入/输出 tokens、当时的输入/输出单价、非思考模式与计价来源。公式：`费用估算 = 输入 tokens × 输入单价 / 1,000,000 + 输出 tokens × 输出单价 / 1,000,000`。每次任务汇总所有调用，包括修复与反事实调用；输出校验失败但已有 usage 时也计入。超时等无 usage 的调用标记为 unknown，不冒充零费用。已完成与失败任务的费用都进入 saved run statistics。

网页在 **Measure**、运行摘要和折叠的 **LLM calls: models, prompts & fallbacks** 中显示费用。美元数值保留最多六位小数；unknown 调用数量单独显示。该值是按实际 usage 和配置单价计算的列表价估算，不是已扣款账单，不包含免费额度、折扣、税费、embedding 或服务器费用。Mac Ollama 的 metered API cost 为零，硬件与电费另计。切换模型不会改写历史报告或原有 14 条 benchmark；API 模型性能需重新评测。

默认单价来自 [Model Studio 官方价格表](https://www.alibabacloud.com/help/en/model-studio/model-pricing) 的 Virginia / Global / 非思考模式（2026-10-09）。更换区域或官方调价时，应修改 `QWEN_API_PRICING_JSON`，避免估算失真。端点与 key 区域对应关系见 [官方兼容接口说明](https://www.alibabacloud.com/help/en/model-studio/compatibility-of-openai-with-dashscope)。
