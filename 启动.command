#!/bin/bash
# 信息化项目全生命周期管理平台 —— 服务器版启动脚本
cd "$(dirname "$0")" || exit 1

PORT=8000
PY=backend/.venv/bin/python

# 本机桌面模式启动（绑定 127.0.0.1、自动开浏览器、关终端即停）。
# PM_DEBUG=1 仅为本地试运行的自动重载与详细报错；正式部署请用 gunicorn +
# 反向代理，并删除/置 0 这个变量（settings 默认 PM_DEBUG=0），DEBUG 下会
# 无鉴权托管 /media/，一旦改绑 0.0.0.0 全部原件可直接下载。
export PM_DEBUG=1
# 「在本机打开原件」是桌面模式特性，靠显式开关启用；不靠 REMOTE_ADDR 猜测
# （反代后所有请求都像来自本机）。生产/反代部署不要设这个变量。
export PM_LOCAL_OPEN=1

# 配置外置：项目内 config.json 已移除，改为读取项目外的 /etc/pm/config.json。
# 若该文件不存在，会自动回退内置默认配置（AI 关闭，系统其余功能正常）。
export PM_CONFIG=/etc/pm/config.json
# 模型密钥不落盘：在下方填入轮换后的新 Key（等号右边），系统优先用它，
# 覆盖配置文件里的 api_key。若暂不启用 AI，可留空并确认 PM_CONFIG 文件里
# llm.enabled=false；填了 Key 后请把外置配置里的 enabled 设为 true。
# export PM_LLM_API_KEY='在此粘贴新Key'

echo "=========================================="
echo "  信息化项目管理平台 · 服务器版"
echo "=========================================="

if [ ! -x "$PY" ]; then
  echo "缺少 Python 虚拟环境，正在创建…"
  python3 -m venv backend/.venv || { echo "创建虚拟环境失败"; exit 1; }
  backend/.venv/bin/pip install -q --upgrade pip
  backend/.venv/bin/pip install -q -r backend/requirements.txt || { echo "安装依赖失败"; exit 1; }
fi

# 前端产物重建：只判断「有没有」会让改了源码再启动仍是旧页面。
# 这里比较 frontend 源码与 spa/index.html 的修改时间，源码更新就重新构建。
NEED_BUILD=0
if [ ! -f backend/spa/index.html ]; then
  NEED_BUILD=1
else
  NEWER=$(find frontend/src frontend/index.html frontend/vite.config.js frontend/package.json \
    -newer backend/spa/index.html -print -quit 2>/dev/null)
  if [ -n "$NEWER" ]; then NEED_BUILD=1; fi
fi

if [ "$NEED_BUILD" = "1" ]; then
  echo "检测到前端源码有更新，正在构建…"
  ( cd frontend && npm install --no-audit --no-fund && npm run build ) || {
    echo "前端构建失败，请检查 Node.js 是否已安装"; exit 1;
  }
fi

if lsof -nP -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
  echo "端口 $PORT 已被占用，可能已经启动过了。"
  open "http://127.0.0.1:$PORT/"
  echo "如页面异常，请先关闭占用该端口的程序后重试。"
  exit 0
fi

echo "  访问地址：http://127.0.0.1:$PORT/"
echo "  数据存放：data/db.sqlite3 与 media/"
echo "  关闭此终端窗口即停止服务"
echo "=========================================="

( sleep 1.5; open "http://127.0.0.1:$PORT/" ) &

cd backend || exit 1

# 结构升级后必须迁移：曾出现装了 token_blacklist 却没跑 migrate，
# 登录签发 refresh 时直接 500（no such table: token_blacklist_outstandingtoken）。
# 每次启动先同步，迁移失败就别带着坏库起服务。
echo "  正在同步数据库结构（首次或升级后需要）…"
./.venv/bin/python manage.py migrate --noinput || {
  echo "数据库迁移失败，服务未启动。请把上面的报错发给维护人员。"
  exit 1
}
exec ./.venv/bin/python manage.py runserver "127.0.0.1:$PORT"
