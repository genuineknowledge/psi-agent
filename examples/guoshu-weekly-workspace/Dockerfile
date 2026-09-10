# 国数周报取数服务(MCP)—— 交付镜像
#
# 这是**取数能力**的服务侧封装:31 个语义工具经 MCP 暴露,正式源为 O2OA PostgreSQL。
# 不含任何前端;由主 Agent 通过 MCP 调用(streamable-http)。
#
# 构建:  docker build -t guoshu-weekly-mcp:latest examples/guoshu-weekly-workspace
# 运行:  docker run --rm -p 18900:18900 --env-file o2oa.env guoshu-weekly-mcp:latest
FROM python:3.14-slim

# 只装运行期依赖。mcp 必须钉在 1.x:2.x 把 FastMCP 改名成 MCPServer,直接导入失败。
RUN pip install --no-cache-dir "mcp>=1.28.1,<2.0.0" "psycopg[binary]>=3.2"

WORKDIR /app
COPY mock-mcp/ /app/mock-mcp/
COPY README.md /app/README.md

# 非 root 运行;代码里已带 default_transaction_read_only=on 与 statement_timeout,
# 容器这一层只是再加一道(最小权限)。
RUN useradd --create-home --uid 10001 appuser
USER appuser

# 正式源模式:演示库(MySQL)不参与,启动时不会去探它
ENV TASK_BOARD_DATA_SOURCE=o2oa \
    PGPORT=5432 \
    PGSCHEMA=public \
    PYTHONUNBUFFERED=1

EXPOSE 18900
# 绑 0.0.0.0 是为了容器外可访问;生产请置于内网/反代之后,并对 MCP 端点做鉴权
CMD ["python", "/app/mock-mcp/server.py", "--host", "0.0.0.0", "--port", "18900"]
