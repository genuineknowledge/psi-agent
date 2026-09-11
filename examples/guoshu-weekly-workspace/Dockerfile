# 国数周报取数服务(MCP)—— 交付镜像
#
# 这是**取数能力**的服务侧封装:31 个语义工具经 MCP 暴露,正式源为 O2OA PostgreSQL。
# 不含任何前端;由主 Agent 通过 MCP 调用(streamable-http)。
#
# 构建:  docker build -t guoshu-weekly-mcp:latest examples/guoshu-weekly-workspace
# 运行:  docker run --rm -p 18900:18900 --env-file o2oa.env guoshu-weekly-mcp:latest
#
# 两个**可选**构建参数(默认值就是标准值,交付方不用管;内网/受限网络下才需要覆盖):
#   --build-arg BASE_IMAGE=<镜像站前缀>/library/python:3.14-slim
#   --build-arg PIP_INDEX_URL=https://<pip 镜像>/simple
ARG BASE_IMAGE=python:3.14-slim
FROM ${BASE_IMAGE}

ARG PIP_INDEX_URL=""
# 只装运行期依赖。两条都踩过:
#
# * ``mcp`` 必须钉在 1.x:2.x 把 FastMCP 改名成 MCPServer,直接导入失败;
# * ``pymysql`` **在正式源模式下也必须装**:``server.py`` 顶层就 ``import _db`` →
#   ``_db`` 里 ``import pymysql``,而那是**演示源**的驱动。正式源模式下它一辈子用不到,
#   但少了它容器一起来就 ``ModuleNotFoundError: No module named 'pymysql'``(实跑抓到的)。
#   演示路径本身也要它:未迁移的参数组合要靠它去连演示库、失败后才由 ``_fallback`` 报
#   ``not_migrated``。所以它不是"可选依赖",是**导入期硬依赖**。
RUN if [ -n "$PIP_INDEX_URL" ]; then \
        pip install --no-cache-dir --index-url "$PIP_INDEX_URL" \
            "mcp>=1.28.1,<2.0.0" "psycopg[binary]>=3.2" "pymysql>=1.1"; \
    else \
        pip install --no-cache-dir \
            "mcp>=1.28.1,<2.0.0" "psycopg[binary]>=3.2" "pymysql>=1.1"; \
    fi

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
