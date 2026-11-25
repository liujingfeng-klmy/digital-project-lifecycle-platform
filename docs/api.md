# API 接口

RESTful 风格，JSON 传输，JWT 认证。所有接口前缀 `/api/`。

---

## 一、通用约定

### 认证

```http
Authorization: Bearer <access_token>
```

Access Token 有效期 2 小时，Refresh Token 7 天。Access 过期后用 `/api/auth/refresh` 换新，刷新时轮换（`ROTATE_REFRESH_TOKENS`）并作废旧 token。

### 响应

| 情况 | 状态码 | 响应体 |
|---|---|---|
| 成功 | 200 / 201 / 204 | 资源或操作结果 |
| 参数错误 | 400 | `{"字段名": ["错误说明"]}` 或 `{"detail": "..."}` |
| 未认证 | 401 | `{"detail": "身份认证信息未提供。"}` |
| 越权 | 403 | `{"detail": "..."}` |
| 不存在 | 404 | `{"detail": "未找到。"}` |
| 限流 | 429 | `{"detail": "请求过多..."}` |

### 分页

列表接口支持 `?page=1&page_size=20`。

```json
{
  "count": 128,
  "next": "http://host/api/projects/?page=2",
  "previous": null,
  "results": []
}
```

`page_size` 上限 200。分页类在 `config/pagination.py`——DRF 原生的 `PageNumberPagination` 不读 `PAGE_SIZE_QUERY_PARAM`，必须子类化才生效。

---

## 二、认证（core）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/auth/login` | 登录，返回 access / refresh 与用户信息 |
| POST | `/api/auth/refresh` | 用 refresh 换新 access |
| GET | `/api/auth/me` | 当前用户信息 |
| POST | `/api/auth/logout` | 登出，refresh 加入黑名单 |
| POST | `/api/auth/change-password` | 改密，口令版本 +1（所有旧 token 失效） |

**登录**

```http
POST /api/auth/login
Content-Type: application/json

{"username": "admin", "password": "..."}
```

```json
{
  "access": "eyJ...",
  "refresh": "eyJ...",
  "user": {
    "id": 1, "username": "admin", "name": "系统管理员",
    "role": "admin", "role_label": "管理员", "unit": "数据管理局",
    "must_change_password": false
  }
}
```

登录限流 10 次/分钟，改密 5 次/分钟（按 IP）。

`must_change_password` 为 `true` 时前端强制跳改密页。

---

## 三、用户与角色（core）

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/api/users/` | 管理员 | 用户列表 |
| POST | `/api/users/` | 管理员 | 新建用户 |
| PATCH | `/api/users/{id}/` | 管理员 | 修改（用户名、姓名、角色、单位、手机） |
| POST | `/api/users/{id}/toggle/` | 管理员 | 停用 / 启用 |
| POST | `/api/users/{id}/reset-password/` | 管理员 | 重置为临时密码，对方下次登录强制改 |
| GET | `/api/roles/` | 管理员 | 角色列表 |

不开放自助注册。账号一律由管理员创建。

---

## 四、机构名录（core）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/org-units/` | 按分组返回名录 |

```json
{
  "count": 92,
  "groups": [
    {"key": "party", "label": "市委部门",
     "items": [{"id": 1, "name": "市委办公室", "subgroup": ""}]},
    {"key": "gov", "label": "市政府部门", "items": []}
  ]
}
```

空组不出现。申报表单与账号表单都用这一个接口。

---

## 五、项目（project）

### 基础 CRUD

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/projects/` | 列表。支持 `stage`、`unit`、`year`、`q`、`mine`、`include_draft` |
| POST | `/api/projects/` | 新建申报 |
| GET | `/api/projects/{id}/` | 详情。含 `actions`（当前用户可执行的动作） |
| PATCH | `/api/projects/{id}/` | 修改（仅申报中或本人可改字段） |
| DELETE | `/api/projects/{id}/` | 删除。管理员可彻底删，删除前强制导出留痕 |

列表默认排除 `draft`。排查数据加 `include_draft=1`。

### 详情响应中的 actions

```json
{
  "actions": [
    {"key": "precheck_pass", "label": "初审通过，转评审",
     "confirm": "确认规则预审已完成？", "form": []},
    {"key": "precheck_return", "label": "退回补正",
     "form": [{"key": "items", "label": "补正事项", "type": "textarea", "required": true}]}
  ]
}
```

**前端只渲染这个列表，不自行判断角色**。列表由 `flow.available_actions` 计算，已含角色、阶段、前置条件三重过滤。

### 流转

```http
POST /api/projects/{id}/flow/
Content-Type: application/json

{"action": "precheck_pass", "comment": "材料齐全，同意上会", "payload": {}}
```

返回最新项目对象（含新的 `actions`）。动作不在可用列表内返回 403，前置条件不满足返回 400。

### 承办人与草稿

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/projects/{id}/reassign/` | 指定当前承办人 |
| GET/POST/DELETE | `/api/projects/{id}/draft/` | 动作表单草稿的读写与清除 |

### 材料

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/projects/{id}/files/` | 上传材料（multipart，字段 `file`、`doc_type`） |
| PATCH | `/api/projects/{id}/files/{fid}/type/` | 改材料类型 |
| DELETE | `/api/projects/{id}/files/{fid}/` | 删除 |
| GET | `/api/projects/{id}/files/{fid}/raw` | 下载原件 |
| GET | `/api/projects/{id}/files/{fid}/text` | 取解析正文 |
| GET | `/api/projects/{id}/files/{fid}/diff` | 与上一版对比（段落级） |
| POST | `/api/projects/{id}/files/{fid}/open` | 在本机打开（仅桌面模式） |

单份上限 200 MB。同内容（SHA-256 相同）在同一项目内会被拦下。

### 申报前抽字段

```http
POST /api/projects/prefill/
Content-Type: multipart/form-data

file=<可研报告>
```

返回抽取结果与未命中字段的定位提示。

```json
{
  "fields": {"name": "...", "unit": "...", "investment": 860},
  "missing": [{"field": "basis", "hint": "写在模板第二节「政策依据」"}],
  "template_url": "/api/projects/feasibility-template"
}
```

### 评审会材料

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/projects/{id}/review-files/` | 上传签字意见表 / 汇总表 |
| DELETE | `/api/projects/{id}/review-files/{fid}/` | 删除 |
| GET | `/api/projects/{id}/review-files/{fid}/download` | 下载 |
| GET | `/api/projects/{id}/review-scan/file` | 取整场扫描件 |

### 文书生成

| 方法 | 路径 | 产出 |
|---|---|---|
| POST | `/api/projects/{id}/docs/precheck-notice` | 《初审修改意见函》 |
| POST | `/api/projects/{id}/docs/cost-review-task` | 《费用评审任务书》 |
| POST | `/api/projects/{id}/docs/approval-reply` | 《立项批复》 |
| POST | `/api/projects/{id}/docs/data-collect` | 《数据归集证明》 |
| GET | `/api/projects/{id}/docs/{doc_id}/download` | 下载已生成文书 |

生成即返回文件记录，管理员可下载也可切换用模板库版本。

### 归档包

```http
GET /api/projects/{id}/archive
```

**仅 `stage=accepted` 可导出**，其余返回 403。管理员也不例外——办到一半的件是半套材料，既不能当档案也不能当凭证。

### 其他

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/projects/{id}/todo-ack/` | 登记「已点开」。`draft` 直接返回不记 |
| POST | `/api/projects/{id}/dup-refresh/` | 重新跑重复建设比对（初审及以上） |
| GET | `/api/projects/stats/` | 统计：阶段分布、耗时、通过率 |
| GET | `/api/projects/todos/` | 待办列表 |
| GET | `/api/projects/inbox/` | 在办箱 |
| GET | `/api/projects/pending/` | 侧栏角标计数 |

### 环节时限

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/flow-sla/` | 时限配置列表 |
| POST | `/api/flow-sla/` | 设置某环节时限与负责岗位 |

### 模板库

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/templates/` | 模板列表（管理员） |
| POST | `/api/templates/` | 上传新版模板，同 kind 自动生效 |
| DELETE | `/api/templates/{id}/` | 删除，回落内置模板 |

---

## 六、历史档案（archive）

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/api/archive/projects/` | 管理员 | 档案项目列表 |
| POST | `/api/archive/projects/` | 管理员 / 初审岗 | 导入档案 |
| GET | `/api/archive/files/` | 管理员 | 档案文件列表 |
| GET | `/api/archive/files/{id}/raw` | 管理员 | 下载原件 |
| GET | `/api/archive/stats/` | 管理员 | 档案统计 |
| POST | `/api/archive/compare/` | 初审及以上 | 发起重复建设比对 |
| GET | `/api/archive/reviews/` | 初审及以上 | 比对结论列表 |

档案的列表、检索、原文、统计只给管理员。初审岗可导入归档，但导完看不到别家档案——避免申报口径从档案页泄漏。

### 比对请求

```http
POST /api/archive/compare/
Content-Type: application/json

{"project_id": 12, "top_n": 8}
```

返回功能级命中、章节级相似度、段落级对照三层结果，不合成单一综合分。

---

## 七、模型接入（aicore）

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/api/llm/presets` | 管理员 | 厂商预设列表 |
| GET | `/api/llm/config` | 管理员 | 当前配置（密钥掩码） |
| POST | `/api/llm/config` | 管理员 | 保存配置，立即生效 |
| POST | `/api/llm/test` | 管理员 | 测试连接 |

密钥只存服务器文件（权限 600），接口不下发明文，页面只显示掩码。

---

## 八、错误码速查

| 场景 | 状态码 | 提示 |
|---|---|---|
| 动作不在当前可用列表 | 403 | `当前阶段或你的角色不能执行该操作` |
| 前置条件不满足 | 400 | 具体条件说明，如 `请先上传第三方评估报告` |
| 归档包导出非已验收 | 403 | `只有已验收的项目可以导出归档包` |
| 超出上传配额 | 400 | `单份材料不能超过 200MB` |
| 重复文件 | 400 | `该材料已上传过` |
| 登录失败次数超限 | 429 | `请求过于频繁，请稍后再试` |
