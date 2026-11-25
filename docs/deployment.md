# 部署指南

三种部署形态，按使用规模选择。

| 形态 | 适用 | 数据库 | 进程 |
|---|---|---|---|
| 桌面模式 | 单机试用、演示 | SQLite | Django runserver |
| 单机生产 | 一个办公室十余人 | SQLite | gunicorn + Nginx |
| 服务化 | 二十人以上、需备份策略 | PostgreSQL | gunicorn 多 worker + Nginx |

---

## 一、桌面模式

双击 `启动.command`。脚本自动完成环境准备、前端构建、数据库迁移、起服务、开浏览器。

关闭终端窗口即停止服务。

**注意**：脚本里 `export PM_DEBUG=1`，DEBUG 模式下 `/media/` 由 Django 无鉴权托管。这种模式只绑定 `127.0.0.1`，外网访问不到。**不要改绑 `0.0.0.0`**，否则所有原件可被直接下载。

---

## 二、单机生产（推荐起点）

### 1. 准备

```bash
# 系统依赖
sudo apt install python3.11 python3.11-venv nginx
# Node 仅在需要现场构建前端时安装

# 用户与目录
sudo useradd -r -s /bin/false pm
sudo mkdir -p /opt/pm && sudo chown pm:pm /opt/pm
```

把项目拷到 `/opt/pm`，确保 `backend/`、`frontend/`、`启动.command` 在正确的相对位置。

### 2. 后端环境

```bash
cd /opt/pm/backend
sudo -u pm python3.11 -m venv .venv
sudo -u pm ./.venv/bin/pip install -r requirements.txt
sudo -u pm ./.venv/bin/pip install gunicorn
```

### 3. 前端构建

```bash
cd /opt/pm/frontend
npm ci
npm run build          # 产物输出到 ../backend/spa/
```

服务器上不装 Node 时，在开发机构建好把 `backend/spa/` 整个拷过去。

### 4. 配置

```bash
sudo mkdir -p /etc/pm
sudo cp /opt/pm/config.example.json /etc/pm/config.json
sudo chown pm:pm /etc/pm/config.json
sudo chmod 600 /etc/pm/config.json
```

编辑 `org` 段——**必须配**，否则批复与证明的落款是占位文字：

```json
{
  "org": {
    "prefixes": ["XX市"],
    "site": "XX市",
    "issuer": "XX市数据管理局",
    "policy_name": "XX市数字化项目管理办法"
  },
  "llm": {"enabled": false}
}
```

### 5. 初始化数据库

```bash
cd /opt/pm/backend
sudo -u pm PM_CONFIG=/etc/pm/config.json ./.venv/bin/python manage.py migrate
sudo -u pm PM_CONFIG=/etc/pm/config.json ./.venv/bin/python manage.py load_org_units
sudo -u pm PM_CONFIG=/etc/pm/config.json ./.venv/bin/python manage.py createsuperuser
```

`createsuperuser` 建的是 Django 超级用户，业务角色需在系统内另行指定为管理员。也可改用 `init_accounts` 建演示账号（**生产不建议**，初始密码写在源码里）。

### 6. gunicorn

`/etc/systemd/system/pm.service`：

```ini
[Unit]
Description=信息化项目管理平台
After=network.target

[Service]
User=pm
Group=pm
WorkingDirectory=/opt/pm/backend
Environment="PM_CONFIG=/etc/pm/config.json"
Environment="PM_ALLOWED_HOSTS=pm.example.gov.cn,10.0.0.5"
Environment="PM_SECRET_KEY=<在此填一个 48 位随机串>"
ExecStart=/opt/pm/backend/.venv/bin/gunicorn config.wsgi:application \
    --bind 127.0.0.1:8000 --workers 4 --timeout 300
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now pm
```

`--timeout 300`：解析 200 MB 的 PDF 可能超过 gunicorn 默认的 30 秒。

**不要设 `PM_DEBUG`**。settings 里默认 `0`，设了才是 1。

### 7. Nginx

```nginx
server {
    listen 80;
    server_name pm.example.gov.cn;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name pm.example.gov.cn;

    ssl_certificate     /etc/ssl/pm/fullchain.pem;
    ssl_certificate_key /etc/ssl/pm/privkey.pem;

    client_max_body_size 210M;      # 单份材料 200MB 上限 + 表单开销

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;    # 与 gunicorn 一致
        proxy_send_timeout 300s;
    }
}
```

**`client_max_body_size` 必须改**。Nginx 默认 1 MB，不改的话 10 MB 以上的可研直接 413。

### 8. 验证

```bash
curl -I https://pm.example.gov.cn/          # 200
curl -X POST https://pm.example.gov.cn/api/auth/login \
     -H 'Content-Type: application/json' \
     -d '{"username":"...","password":"..."}'   # 返回 token
```

---

## 三、切 PostgreSQL

单机 SQLite 在并发写入（>20 人同时办件）时会因库级写锁出现等待。切换只需环境变量，不改代码。

```bash
sudo apt install postgresql
sudo -u postgres psql <<'SQL'
CREATE USER pm WITH PASSWORD '强密码';
CREATE DATABASE pm OWNER pm;
SQL
```

在 systemd unit 里加：

```ini
Environment="PM_DB_ENGINE=django.db.backends.postgresql"
Environment="PM_DB_NAME=pm"
Environment="PM_DB_USER=pm"
Environment="PM_DB_PASSWORD=强密码"
Environment="PM_DB_HOST=127.0.0.1"
Environment="PM_DB_PORT=5432"
```

然后迁移：

```bash
sudo -u pm PM_DB_ENGINE=... ./.venv/bin/python manage.py migrate
```

从 SQLite 迁数据用 Django 的 `dumpdata` / `loaddata`：

```bash
# 旧库导出（SQLite）
./.venv/bin/python manage.py dumpdata --natural-foreign --natural-primary \
    --exclude contenttypes --exclude auth.permission \
    --indent 2 > /tmp/pm_dump.json

# 新库导入（PostgreSQL）
./.venv/bin/python manage.py loaddata /tmp/pm_dump.json
```

`media/` 目录不经过数据库，直接拷。

---

## 四、备份与恢复

### 备份

**两个目录必须一起拿**：`data/` 与 `media/`。只拷数据库等于只剩目录，材料原件全在 `media/` 里。

```bash
#!/bin/bash
# /opt/pm/backup.sh
DATE=$(date +%Y%m%d_%H%M)
DEST=/backup/pm/$DATE
mkdir -p "$DEST"

# SQLite 用 .backup 保证一致性（直接 cp 在有写入时可能拿到半个事务）
sqlite3 /opt/pm/data/db.sqlite3 ".backup '$DEST/db.sqlite3'"

# PostgreSQL 用 pg_dump
# sudo -u postgres pg_dump pm | gzip > "$DEST/pm.sql.gz"

rsync -a /opt/pm/media/ "$DEST/media/"
cp /opt/pm/data/.secret_key "$DEST/" 2>/dev/null

# 留最近 30 份
find /backup/pm -maxdepth 1 -type d -mtime +30 -exec rm -rf {} +
```

**`.secret_key` 必须带上**。丢了它，恢复后所有已签发的 token 全部失效，所有人重新登录（不影响数据，但会打一圈电话）。

### 恢复

```bash
sudo systemctl stop pm
cp /backup/pm/<日期>/db.sqlite3 /opt/pm/data/db.sqlite3
cp /backup/pm/<日期>/.secret_key /opt/pm/data/.secret_key
chown pm:pm /opt/pm/data/*
rsync -a /backup/pm/<日期>/media/ /opt/pm/media/
sudo systemctl start pm
```

---

## 五、升级

```bash
sudo systemctl stop pm
cd /opt/pm

# 备份（见上）
/opt/pm/backup.sh

# 更新代码
git pull        # 或解压新版本覆盖

# 依赖与结构
cd backend
sudo -u pm ./.venv/bin/pip install -r requirements.txt
sudo -u pm PM_CONFIG=/etc/pm/config.json ./.venv/bin/python manage.py migrate

# 前端（源码有改动时）
cd ../frontend && npm ci && npm run build

sudo systemctl start pm
```

迁移失败时服务不会起来（`启动.command` 里做了显式判断）。看日志：

```bash
sudo journalctl -u pm -n 100 --no-pager
tail -100 /opt/pm/data/logs/pm.log
```

---

## 六、常见故障

| 现象 | 原因 | 处理 |
|---|---|---|
| `/media/` 下的原件能直接下载 | `PM_DEBUG=1` | 去掉该环境变量，重启 |
| 上传大文件 413 | Nginx `client_max_body_size` 未改 | 改成 `210M` |
| 上传后解析超时 | gunicorn `--timeout` 太短 | 改 300 |
| 登录返回 500，日志报 `no such table: token_blacklist_...` | 装了 app 没跑 migrate | 跑 `migrate` |
| 所有用户突然掉线 | `.secret_key` 变了 | 恢复备份里的 key |
| 生成的批复落款是「示例市数据管理局」 | `config.json` 的 `org` 段没配 | 配 `org.issuer` / `org.site` |
| 前端打开白屏，控制台 404 | `backend/spa/` 没构建或构建失败 | 重新 `npm run build` |
| 多人同时保存偶发失败 | SQLite 库级写锁 | 切 PostgreSQL |
| 登录限流在多 worker 下不生效 | 默认 LocMemCache 只在单进程内计数 | 把 `CACHES` 换成 Redis |
| 中文文件名下载后乱码 | 反向代理未透传 `Content-Disposition` | 在 Nginx 里放开该响应头 |
