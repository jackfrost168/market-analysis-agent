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
