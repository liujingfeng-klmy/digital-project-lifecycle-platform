# 开发指南

---

## 一、环境要求

| 项 | 版本 |
|---|---|
| Python | 3.11+（生产验证于 3.11 / 3.13） |
| Node.js | 18+ |
| 数据库 | SQLite 3（内置）/ PostgreSQL 15+（可选） |

---

## 二、搭建

```bash
git clone <repo-url>
cd <repo>

# 后端
cd backend
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python manage.py migrate
./.venv/bin/python manage.py init_accounts
./.venv/bin/python manage.py load_org_units

# 前端
cd ../frontend
npm install
```

### 启动开发环境

```bash
# 终端 1：后端（8000）
cd backend && PM_DEBUG=1 ./.venv/bin/python manage.py runserver

# 终端 2：前端（5173，热更新，/api 代理到 8000）
cd frontend && npm run dev
```

开发时访问 `http://127.0.0.1:5173`。代理配置在 `frontend/vite.config.js`。

**`PM_DEBUG=1` 只在开发时用**。它会让 `/media/` 无鉴权托管，且异常页暴露堆栈。

### 出成品

```bash
cd frontend && npm run build     # 产物输出到 backend/spa/
cd ../backend && ./.venv/bin/python manage.py runserver
```

此时访问 `http://127.0.0.1:8000` 即为生产形态（后端同源托管 SPA）。

---

## 三、项目结构要点

```
backend/
  config/           settings / urls / 分页 / 测试运行器
  apps/
    core/           身份、角色、机构名录
    project/        主业务域（模型 636 行 / 流程 1421 行 / 视图 1530 行）
      docs/templates/   四份文书的打包模板
    archive/        历史档案与查重
    aicore/         LLM 接入
  spa/              前端构建产物（git 忽略？否——保留以便直接部署）
frontend/
  src/
    api/            接口封装，按领域拆文件
    components/     通用组件
    composables/    组合式函数
    router/         路由 + 角色守卫
    stores/         Pinia
    views/          页面
      project-detail/   详情页按环节拆的办理卡
```

### 后端分层约定

| 层 | 位置 | 职责 |
|---|---|---|
| 模型 | `models.py` | 字段、约束、少量只读属性 |
| 流程 | `flow.py` | 状态机、动作定义、前置条件、留痕 |
| 业务 | `precheck.py` / `prefill.py` / `docgen.py` / `diff.py` / `archiving.py` | 领域逻辑 |
| 序列化 | `serializers.py` | 出入参整形 |
| 视图 | `views.py` | 权限、参数收集、调用业务层 |

**视图里不写业务判断**。判断放 `flow.py` 或对应的业务模块，视图只负责取参数、调用、返回。理由：同一判断可能被多个入口调用（列表、详情、动作），写在视图里必然重复。

### 前端约定

- 页面组件在 `views/`，可复用组件在 `components/`
- 详情页按环节拆成独立的办理卡（`views/project-detail/act/`），由父组件按当前阶段挂载
- **按钮渲染依据后端下发的 `actions` 列表**，不在前端写 `if (role === 'reviewer' && stage === 'precheck')`
- API 调用统一走 `src/api/` 封装，不直接在组件里 `axios.get`

---

## 四、测试

### 运行

```bash
cd backend
./.venv/bin/python manage.py test              # 全量
./.venv/bin/python manage.py test apps.project # 单 app
./.venv/bin/python manage.py test apps.project.test_docs_flow  # 单文件
```

当前 **209 项**，全部通过。其中 `apps.project` 170 项。

### 测试运行器的特殊处理

`config/test_runner.py` 里 `MediaIsolatedRunner` 把测试产生的媒体文件写到临时目录。不这么做的话，每跑一次测试真实 `media/` 里就多一批以测试库编号命名的孤儿文件。

### 写测试的约定

| 场景 | 必须覆盖 |
|---|---|
| 新增动作 | 正确角色可推进 / 错误角色 403 / 前置条件不满足时拒绝 |
| 新增接口 | 成功路径 / 越权 403 / 参数错误 400 |
| 权限相关 | 每个角色的可见范围 |

现有用例已经建立了这个模式，新增时照抄结构即可：

```python
def test_reviewer_can_precheck_pass(self):
    """初审岗在规则预审跑过后可以推进。"""
    p = self.make_project('precheck')
    self.run_rule_check(p)
    r = self.as_user(self.reviewer).post(
        f'/api/projects/{p.id}/flow/', {'action': 'precheck_pass'}, format='json')
    self.assertEqual(r.status_code, 200, r.data)
    p.refresh_from_db()
    self.assertEqual(p.stage, Project.Stage.EXPERT_REVIEW)
```

`assertEqual(..., 200, r.data)` 里的 `r.data` 是失败时的诊断信息——不加的话失败只剩一行状态码，得重跑去查原因。

---

## 五、调试

### 后端

```bash
# 带 pdb 断点
PM_DEBUG=1 ./.venv/bin/python -m pdb manage.py runserver

# Django shell
./.venv/bin/python manage.py shell

# 查 SQL
./.venv/bin/python manage.py shell -c "
from django.db import connection
from apps.project.models import Project
print(Project.objects.filter(stage='precheck').count())
print(connection.queries[-3:])
"
```

日志在 `data/logs/pm.log`，按天轮转，保留 30 天。

### 前端

Vue Devtools 可用。接口报错在网络面板看完整响应。

前端不吞异常——`src/api/` 的拦截器只处理 401 自动续期，其余错误抛给调用方，由页面决定怎么提示。

### 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| 启动报 `no such table: token_blacklist_...` | 装了 app 没跑 migrate | `manage.py migrate` |
| 前端 5173 打开后接口 404 | 后端没起，或代理配置错 | 检查 `vite.config.js` 的 proxy |
| 改了前端不生效 | 访问的是 8000（读 `backend/spa/` 旧产物） | 用 5173，或重新 build |
| 测试后 `media/` 里多出文件 | 用了默认 runner | 确认 `TEST_RUNNER` 指向 `MediaIsolatedRunner` |
| 生成文书时模板报 KeyError | 模板里用了未定义的占位符 | 在 `_base_ctx` 或对应生成函数里补 |

---

## 六、数据库变更

```bash
./.venv/bin/python manage.py makemigrations <app>
./.venv/bin/python manage.py migrate
./.venv/bin/python manage.py sqlmigrate <app> <编号>   # 看实际 SQL
```

### 约定

1. **迁移文件进版本库**，不忽略
2. **不修改已合并的迁移**。已上线的库里那条记录已存在，改文件不会重跑，只会在新库上产生不一致
3. **种子数据放迁移里**（如 `0005_seed_org_units`），但不内联拷贝，从 `org_data.py` 引用
4. 数据迁移与结构迁移分开成两个文件，便于回滚

唯一例外：脱敏或改注释时可以改历史迁移文件（如把机构分组标签从「国家和自治区垂直管理部门」改成「垂直管理部门」），因为不影响 schema，也不会让已建库与新库产生结构差异。

---

## 七、代码风格

没有引入格式化工具的强制检查，但约定如下：

| 项 | 约定 |
|---|---|
| 行宽 | 100 字符 |
| 引号 | Python 单引号，JS 单引号，JSON 双引号 |
| 命名 | Python `snake_case`，Vue 组件 `PascalCase` |
| 注释 | 中文。**注释写「为什么」，不写「是什么」** |

注释的标准是这一条：读代码能知道做了什么，读注释要知道为什么这么做。例如：

```python
# 好：解释原因
# 24 而不是 16：review_supplement 有 17 个字符，卡在 16 上迁移直接报错
stage = models.CharField(max_length=24, ...)

# 差：复述代码
# stage 字段，最大长度 24
stage = models.CharField(max_length=24, ...)
```

现有代码里有大量这类注释，记录的是踩过的坑与业务口径的来源。修改时**不要删掉它们**——那是这个项目最容易丢的资产。

---

## 八、提交规范

```
<类型>: <简要说明>

<可选正文：为什么改，改之前是什么行为>
```

类型：`feat` / `fix` / `refactor` / `docs` / `test` / `chore`

示例：

```
fix: 管理员降级后仍可登录 /admin/

User.save() 只在建号时设过 is_staff/is_superuser，改角色时没同步，
被改成申报单位的管理员仍是 superuser，可用原密码进 admin 改库。
改为每次 save 都按当前角色域同步，update_fields 也要带上这两个字段。
```

---

## 九、发布检查

```bash
# 1. 全量测试
cd backend && ./.venv/bin/python manage.py test

# 2. 前端构建
cd ../frontend && npm run build

# 3. 迁移无遗漏
cd ../backend && ./.venv/bin/python manage.py makemigrations --check --dry-run

# 4. 确认没有敏感文件被带进版本库
git status --short
git diff --cached --stat
```

第 4 步重点看：`config.json`、`data/`、`media/` 不应出现在待提交列表里。
