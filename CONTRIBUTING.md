# 贡献指南

感谢参与。本文说明如何报告问题、提交代码，以及这个项目在实现上的取舍标准。

---

## 报告问题

提 issue 时说清三件事：

1. **复现步骤**——从哪个页面、点了什么、期望什么、实际什么
2. **环境**——部署形态（桌面 / 单机生产 / 服务化）、数据库类型、浏览器版本
3. **相关日志**——`data/logs/pm.log` 里的对应片段，或后端终端报错

如果是业务口径问题（「这一步不该允许」），请说明依据：哪条规定、哪次评审结论、哪个环节的实际做法。**这个项目的业务规则大多有明确出处，改动前需要知道出处。**

---

## 提交代码

### 流程

```bash
git checkout -b fix/describe-the-problem
# 改代码
cd backend && ./.venv/bin/python manage.py test    # 必须全绿
git commit -m "fix: ..."
git push origin fix/describe-the-problem
```

### 提交信息

```
<类型>: <简要说明>

<正文：为什么改，改之前是什么行为>
```

类型：`feat` / `fix` / `refactor` / `docs` / `test` / `chore`

正文写「为什么」比写「改了什么」重要——代码自己会说明改了什么。

---

## 实现约定

### 业务判断放后端

前端按钮的可见性由后端下发的 `actions` 列表决定，不在前端写角色与阶段判断。新增动作时：

1. `flow.ACTIONS` 加定义
2. `flow.available_actions` 里会自然生效
3. 前端自然拿到该动作

**不要**在前端加 `v-if="user.role === 'reviewer'"` 这类判断。会漏。

### 注释写为什么

```python
# 好
# 24 而不是 16：review_supplement 有 17 个字符，卡在 16 上迁移直接报错
stage = models.CharField(max_length=24, ...)

# 差
# stage 字段
stage = models.CharField(max_length=24, ...)
```

现有代码里大量注释记录的是踩过的坑与业务口径的来源。**改代码时不要顺手删注释**，那是这个项目最容易丢的资产。如果注释确实过期了，改成新的理由，而不是删掉。

### 迁移不篡改

已合并的迁移文件不改。已上线的库里那条记录已存在，改文件不会重跑，只会在新库上产生不一致。

例外：纯注释与种子文案的修正（不影响 schema）。

### 测试要覆盖权限边界

新增动作或接口，至少三条用例：正确角色可推进、错误角色 403、前置条件不满足时拒绝。

```python
def test_reviewer_can_precheck_pass(self):
    p = self.make_project('precheck')
    self.run_rule_check(p)
    r = self.as_user(self.reviewer).post(
        f'/api/projects/{p.id}/flow/', {'action': 'precheck_pass'}, format='json')
    self.assertEqual(r.status_code, 200, r.data)
```

第三参数 `r.data` 别省——失败时它会告诉你是哪条校验拦下的。

### 不引入重量级依赖

项目刻意不依赖 Redis、Celery、消息队列。新增基础设施依赖前请先在 issue 里讨论必要性，说明现有方案为什么不够。

---

## 文档

改动影响使用方式时同步更新文档：

| 改动 | 更新 |
|---|---|
| 新增环境变量 / 配置项 | `README.md` 配置说明 + `config.example.json` |
| 新增 API | `docs/api.md` |
| 新增阶段或动作 | `docs/workflow.md` |
| 新增数据表或改字段语义 | `docs/data-model.md` |
| 架构层面的取舍 | `docs/architecture.md` |

---

## 数据与隐私

**不要提交任何真实业务数据。**

- 不提交 `data/`、`media/`、`config.json`
- 测试用例用构造数据，人名用「张三」「李四」，单位用示例名
- 截图里如果含真实项目名，不要直接贴到 issue 里
- 提交前跑一遍 `git status --short`，确认待提交列表里没有上述文件

---

## 许可

提交的代码按 [MIT License](LICENSE) 授权。
