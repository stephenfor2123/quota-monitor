# 入境处配额放号监控（GitHub Actions 云端）

监控香港入境处智能身份证预约配额（[配额预览页](https://eservices.es2.immd.gov.hk/es/quota-enquiry-client/?appId=579&l=zh-HK)），**每 5 分钟**抓取一次，检测到"放号"（配额从已满/未开放变成可约）时通过 **飞书群机器人** 通知，特别关注周六/周日。

第三方工具，只做监控提醒，不做任何代抢/代约。以 [入境处官网](https://www.gov.hk/tc/residents/immigration/idcard/hkic/bookregidcard.htm) 为准。

## 工作原理

```
cron-job.org（每 5 分钟）──▶ GitHub API workflow_dispatch
                            │  python monitor.py
                            │  ├─ 抓入境处公开配额接口（只读，一次一请求）
                            │  ├─ 与仓库里上一轮快照 diff → 放号事件
                            │  ├─ 命中放号 → 飞书 webhook 通知（带冷却防抖）
                            │  └─ commit data/ 快照到仓库（作为下一轮比对基准）
                            ▼
  data/quota.json       # 当前快照
  data/events.jsonl     # 历史事件日志（放号/收紧/新日期）
```

> GitHub 自带 schedule 不可靠（常延迟数小时），已改为外部定时器触发。配置步骤见 [docs/cron-setup.md](docs/cron-setup.md)。

## 监控的 6 家办事处

| 代号 | 中文名 |
|------|--------|
| RHK | 香港（灣仔） |
| RKO | 九龍（長沙灣） |
| RTK | 新界（將軍澳） |
| FTO | 火炭 |
| TMO | 屯門 |
| YLO | 元朗 |

配额状态：`g` 充足（🟢）· `y` 少量（🟡）· `r` 已满（🔴）· `x` 未开放（⚪）

---

## 一键部署到 GitHub

### 1. 创建一个公开仓库并推送

```bash
# 在本项目目录下
git init
git add .
git commit -m "init: 入境处配额放号监控"
gh repo create YOUR_GITHUB_USERNAME/quota-monitor --public --source=. --push
```

> `gh` 需先登录：`gh auth login`。也可在 GitHub 网页上新建公开仓库，再 `git remote add origin <url>` + `git push`。

### 2. 配置飞书机器人

1. 打开飞书 → 目标群 → 设置 → **群机器人** → **添加机器人** → **自定义机器人**；
2. 复制机器人 **Webhook 地址**（形如 `https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx`）；
3. 在仓库 **Settings → Secrets and variables → Actions → New repository secret** 添加：
   - Name: `FEISHU_WEBHOOK`
   - Value: 粘贴上面的完整 webhook 地址

> Secret 保存后不可查看。放号通知只发这条 webhook。

### 3. 配置外部定时器（每 5 分钟）

按 [docs/cron-setup.md](docs/cron-setup.md)：创建 GitHub PAT → 在 [cron-job.org](https://cron-job.org/) 建 POST 任务，每 5 分钟触发 `workflow_dispatch`。

公开仓库的 Actions 标准 runner 免费。也可在仓库 **Actions** 页点 **Run workflow** 手动验证。

---

## 本地手动运行（可选）

无需第三方依赖，仅 Python 3 标准库：

```bash
python3 monitor.py
```

不设 `FEISHU_WEBHOOK` 时只打印报告、不发通知。

---

## 配置文件 `config.json`

```jsonc
{
  "svcId": 579,                    // 无需改
  "endpoint": "https://eservices.es2.immd.gov.hk/surgecontrolgate/ticket/getSituation",
  "days_window": 10,               // 关注最近多少天（含今天）
  "offices": ["RHK","RKO","RTK","FTO","TMO","YLO"],  // 需监控的办事处
  "notify_cooldown_sec": 300,      // 连续放号通知的最小间隔（秒），防刷屏
  "request_timeout_sec": 20
}
```

---

## 记录文件说明

- `data/quota.json` — 每次运行后更新的快照（`{办事处: {YYYY-MM-DD: {R, K}}}`，R/K 为一般/延长时段状态）
- `data/events.jsonl` — 每次运行追加的事件行（每行一个 JSON）：
  ```json
  {"ts":"08/26/2026 14:34:52","type":"quota_released","office":"RHK","officeName":"香港","date":"2026-08-29","weekday":"周六（周末）","slot":"R","from":"r","to":"g"}
  ```
  - `type`：`quota_released`(放号) · `quota_shrunk`(收紧) · `new_day`(新进窗口)

---

## 常见问题

- **首次运行报出一堆 `new_day`**：是首次建档基线，属正常，非放号，不会误报。
- **cron 不触发**：GitHub Actions 定时有分钟级偏差；确认仓库是"公开"且 workflow 已 commit 到 `main`。
- **收到很多条通知**：同一轮多个办事处的放号会合并成一条；跨轮有 5 分钟冷却，可改 `notify_cooldown_sec`。

## 免责声明

本工具仅用于个人配额监控提醒，为第三方非官方工具，不对预约结果负责。请以入境处官网及其预约系统为准。