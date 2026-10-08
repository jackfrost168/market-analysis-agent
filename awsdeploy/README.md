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

## AWS Bedrock Converse 默认模型与费用

当前提供 **Qwen3 32B (AWS Bedrock Converse，默认)** 和 **Qwen3 8B (Mac)**。阿里云外部 API 8B/30B 和其密钥录入脚本已经移除；不添加 Coder。九节点工作流、检索、验证、可选反事实检查与报告主体不变。

### 服务器配置

安装 requirements.txt 的 boto3 依赖；不要在项目中保存 AWS 长期访问密钥。EC2 使用 IAM 实例角色及 SDK 自动获得的临时凭证。服务器 .env 如下（routes JSON 为一整行，systemd 外层单引号保留 JSON 引号）：

```dotenv
OLLAMA_MODEL=qwen.qwen3-32b-v1:0
OLLAMA_MODEL_ROUTES='{"qwen.qwen3-32b-v1:0":{"provider":"bedrock","region":"us-east-1","label":"Qwen3 32B (AWS Bedrock Converse)"},"qwen3:8b":{"base_url":"http://127.0.0.1:11434","label":"Qwen3 8B (Mac)"}}'
BEDROCK_REGION=us-east-1
BEDROCK_PRICING_JSON='{"qwen.qwen3-32b-v1:0":{"input":0.15,"output":0.60}}'
```

调用 SDK 的 bedrock-runtime.converse，使用 Qwen 原生模型 ID qwen.qwen3-32b-v1:0。Schema 放入提示词，回复继续由 Pydantic 校验。使用 /no_think 软指令请求非思考回复，不把它描述成服务端强制开关；服务商返回的全部输出 tokens（包括可能的推理 tokens）均计费。普通调用最多 4096 输出 tokens，反事实调用保留原 512 tokens 与时间预算。SDK 不自动重试，不因 Bedrock 失败改用另一个提供商；原工作流已有的确定性 fallback 仍保留并记录失败。

### 绑定 EC2 IAM 角色

1. AWS 控制台 → IAM → Roles → Create role → AWS service → EC2，创建 FinancialAgentBedrockConverse 角色。
2. 在角色中添加 inline policy，使用同目录 bedrock-invoke-policy.json 的 JSON。它仅允许 bedrock:InvokeModel 到 us-east-1 的 Qwen3-32B；这是 Converse 所需权限，不给 EC2 管理 IAM 的权限。
3. EC2 控制台 → us-east-1 → 找到当前 financial-agent 实例 → Actions → Security → Modify IAM role → 绑定上述角色。
4. 如果首次调用提示模型订阅/访问问题，用有账户管理权限的用户打开 Bedrock Model catalog，确认 Qwen3-32B 的账户访问及计费条件。不要创建 provisioned throughput 或自部署 Marketplace 端点；本项目使用原生按量推理。
5. 在 Mac 终端执行：

```bash
ssh -i "$HOME/.ssh/financial-agent.pem" ec2-user@100.59.221.40
cd /home/ec2-user/financial-agent
.venv/bin/python awsdeploy/check_bedrock.py
```

此脚本会发一次极短的真实模型请求，按量计费；success:true 表示 SDK、IAM、模型访问及结构化回复已验证。不写入分析数据库，不输出凭证。IAM 角色绑定不要求重启 EC2；页面刷新即可重新检查凭证来源。/api/models 的 credentials_configured 仅表示 SDK 找到凭证，不代表模型权限已确认。

缺少角色时 API 选项显示 AWS IAM role required，提交 Auto 在入队前被拒绝。仍可手动选 Mac 8B；Mac Ollama 与 SSH 隧道需在线。真实推理的 AccessDenied、超时或输出校验错误显示在 LLM calls 中。

### 保留与更新费用统计

现有历史报告及费用不删除、不重新计价。新 Bedrock 调用使用 usage.inputTokens 和 usage.outputTokens，保存单次调用用量、当时输入/输出单价、区域和 standard_converse 计价模式。单次费用估算 = 输入 tokens × 输入单价 / 1,000,000 + 输出 tokens × 输出单价 / 1,000,000。

报告汇总原分析、修复与反事实调用；输出验证失败但已返回 usage 的调用也计入；超时等未返回 usage 的调用标为 unknown。已完成与失败的历史任务都进入 saved run statistics。原阿里云调用的历史记录仍按它原本存储的费用汇总。Mac Ollama 的 API token 费用为 0。

网页在 Measure、运行摘要和折叠的 LLM calls 中显示费用；小金额保留最多六位小数。费用是按实际 usage 与保存单价计算的列表价估算，不是 AWS 扣款账单，排除免费额度、折扣、税、embedding、服务器、电费等。

单价为 us-east-1 标准按量推理：每百万输入 $0.15，输出 $0.60（2026-10-09 查询，官方价目发布于 2026-10-06）。更换区域、模型或服务层级时修改 BEDROCK_PRICING_JSON。当前代码仅请求标准推理，不开启 prompt cache 或 Flex；不能套用其折扣单价。

参考：[模型 ID 与区域](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-qwen-qwen3-32b.html)、[Converse 接口](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html)、[官方区域价格数据](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonBedrock/current/us-east-1/index.json)。
