# 外部定时器（cron-job.org）配置

GitHub Actions 自带的 `schedule` cron **不保证准点**（公开仓库尤其容易延迟数小时）。  
因此改为：**cron-job.org 每 5 分钟 → 调 GitHub API → 触发本仓库 `workflow_dispatch`**。

## 1. 创建 GitHub Personal Access Token

1. 打开：https://github.com/settings/tokens?type=beta  
   （或 Classic：https://github.com/settings/tokens ）
2. 建议用 **Fine-grained token**：
   - Resource owner: `stephenfor2123`
   - Repository access: **Only select repositories** → `quota-monitor`
   - Permissions → Repository permissions:
     - **Actions**: Read and write
     - **Contents**: Read（一般已有）
3. 生成后 **复制 token**（只显示一次），形如 `github_pat_...` 或 `ghp_...`

> 不要把 token 写进仓库代码；只填到 cron-job.org。

## 2. 在 cron-job.org 新建任务

1. 注册/登录：https://cron-job.org/  
2. **Cronjobs → Create cronjob**
3. 填写：

| 字段 | 值 |
|------|-----|
| Title | `quota-monitor every 5 min` |
| URL | `https://api.github.com/repos/stephenfor2123/quota-monitor/actions/workflows/quota-monitor.yml/dispatches` |
| Schedule | Every **5 minutes**（或自定义 cron：`*/5 * * * *`） |
| Request method | **POST** |
| Request timeout | 30s |

4. **Request headers**（各加一行）：

```
Authorization: Bearer <你的_PAT>
Accept: application/vnd.github+json
Content-Type: application/json
X-GitHub-Api-Version: 2022-11-28
User-Agent: cron-job-org-quota-monitor
```

5. **Request body**：

```json
{"ref":"main"}
```

6. 保存并启用（Enable）。

## 3. 验证

- 在 cron-job.org 点一次 **Run now**（或等 5 分钟）
- 到仓库 Actions：https://github.com/stephenfor2123/quota-monitor/actions  
  应出现 `workflow_dispatch` 触发的成功运行

## 4. 用 curl 本地自测（可选）

```bash
curl -X POST \
  -H "Authorization: Bearer YOUR_PAT" \
  -H "Accept: application/vnd.github+json" \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  https://api.github.com/repos/stephenfor2123/quota-monitor/actions/workflows/quota-monitor.yml/dispatches \
  -d '{"ref":"main"}'
```

成功时 HTTP 状态码为 **204**（无响应体）。

## 故障排查

| 现象 | 处理 |
|------|------|
| 401 / 403 | PAT 过期或权限不足，检查 Actions: Write |
| 404 | workflow 文件名/仓库名写错 |
| 422 | `ref` 不是 `main`，或 workflow 未启用 |
| Actions 有记录但失败 | 看 run 日志；账单/私有库问题已通过改 public 规避 |

## 安全提醒

- PAT 泄露后立刻在 GitHub Settings → Tokens 撤销
- 本仓库为 **公开**，飞书 webhook 仍只存在于 Actions Secrets，不会进代码
