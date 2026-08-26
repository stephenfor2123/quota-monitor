#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""香港入境处智能身份证预约配额（svcId=579）放号监控。

抓取官方配额预览接口 -> 与上一轮快照比对 -> 检测放号事件 -> 写日志 + 飞书通知。
设计为在 GitHub Actions 中每轮独立运行一次：快照和事件日志通过 git commit 持久化，
作为下一轮比对基准。

接口与数据格式说明见 README.md。
"""

import json
import os
import sys
import time
import datetime
import urllib.request
import urllib.error

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
QUOTA_FILE = os.path.join(DATA_DIR, "quota.json")
EVENTS_FILE = os.path.join(DATA_DIR, "events.jsonl")
LAST_NOTIFY_FILE = os.path.join(DATA_DIR, "last_notify.json")

STATUS_LABEL = {"g": "充足", "y": "少量", "r": "已满", "x": "未开放"}
STATUS_ICON = {"g": "🟢", "y": "🟡", "r": "🔴", "x": "⚪"}
WD_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
# 状态优先级：x < r < y < g。数值越大代表越易约到。
ORDER = {"x": 0, "r": 1, "y": 2, "g": 3}


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def now_hk():
    try:
        import zoneinfo
        return datetime.datetime.now(zoneinfo.ZoneInfo("Asia/Hong_Kong"))
    except Exception:
        return datetime.datetime.now(
            datetime.timezone(datetime.timedelta(hours=8))
        ).astimezone()


def hk_timestamp():
    return now_hk().strftime("%m/%d/%Y %H:%M:%S")


def weekday_label(date_iso):
    d = datetime.date.fromisoformat(date_iso)
    label = WD_LABELS[d.weekday()]
    return label + "（周末）" if d.weekday() >= 5 else label


def parse_status(raw):
    """"quota-g/quota-y/quota-r/no-quotaK/None -> g/y/r/x"""
    if not raw:
        return "x"
    raw = str(raw)
    if raw.startswith("quota") and len(raw) > 6:
        return raw[6]
    return "x"


# ---------------------------------------------------------------------------
# 抓取与解析
# ---------------------------------------------------------------------------
def load_config():
    with open(os.path.join(BASE_DIR, "config.json"), encoding="utf-8") as f:
        return json.load(f)


def fetch_quota(cfg):
    url = "{}?svcId={}&t={}".format(
        cfg["endpoint"], cfg.get("svcId", 579), int(time.time() * 1000)
    )
    req = urllib.request.Request(url, headers={
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        ),
        "Referer": cfg.get(
            "referer",
            "https://eservices.es2.immd.gov.hk/es/quota-enquiry-client/",
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8",
    })
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(
                req, timeout=cfg.get("request_timeout_sec", 20)
            ) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError,
                OSError, json.JSONDecodeError) as e:
            last_err = e
            time.sleep(2 ** attempt)
    raise RuntimeError("fetch_quota failed: {}".format(last_err))


def office_map(raw):
    """office[] -> {officeId: 中文 district 名}。"""
    result = {}
    for o in raw.get("office", []):
        oid = o.get("officeId")
        if oid:
            cht = o.get("cht") or {}
            result[oid] = cht.get("district") or oid
    return result


def normalize(raw):
    """原始响应 -> {officeId: {YYYY-MM-DD: {"R": g/y/r/x, "K": g/y/r/x}}}"""
    result = {}
    for row in raw.get("data", []):
        oid = row.get("officeId")
        date = row.get("date")  # MM/DD/YYYY
        if not oid or not date:
            continue
        try:
            iso = datetime.datetime.strptime(date, "%m/%d/%Y").date().isoformat()
        except ValueError:
            continue
        result.setdefault(oid, {})[iso] = {
            "R": parse_status(row.get("quotaR")),
            "K": parse_status(row.get("quotaK")),
        }
    return result


def window_dates(raw, cfg):
    """监控窗口内（今天起 days_window 天）营业的 ISO 日期，升序。"""
    served = set()
    for row in raw.get("data", []):
        try:
            served.add(
                datetime.datetime.strptime(row["date"], "%m/%d/%Y").date().isoformat()
            )
        except (KeyError, ValueError):
            continue
    horizon = cfg.get("days_window", 10)
    today = datetime.date.today()
    return sorted(
        (today + datetime.timedelta(days=i)).isoformat()
        for i in range(horizon)
        if (today + datetime.timedelta(days=i)).isoformat() in served
    )


# ---------------------------------------------------------------------------
# diff / 事件
# ---------------------------------------------------------------------------
def is_release(p, cur):
    """是否“放号”（可约程度变好）。"""
    return ORDER.get(cur, 0) > ORDER.get(p, 0)


def is_shrink(p, cur):
    """是否“收紧”：变满。"""
    return ORDER.get(cur, 0) < ORDER.get(p, 0)


def make_event(etype, slot, date_iso, oid, oname, frm, to):
    return {
        "ts": hk_timestamp(),
        "type": etype,
        "office": oid,
        "officeName": oname,
        "date": date_iso,
        "weekday": weekday_label(date_iso),
        "slot": slot,
        "from": frm,
        "to": to,
    }


def diff_events(old, new, window, office):
    events = []
    for oid, days in new.items():
        oname = office.get(oid, oid)
        prev = old.get(oid, {})
        for date_iso, cell in days.items():
            if date_iso not in window:
                continue
            pc = prev.get(date_iso)
            if pc is None:
                # 新进入窗口的日期
                events.append(make_event("new_day", "R", date_iso, oid, oname,
                                         "", cell.get("R")))
                continue
            for slot in ("R", "K"):
                p = pc.get(slot)
                cur = cell.get(slot)
                if p is None or cur is None or p == cur:
                    continue
                if is_release(p, cur):
                    events.append(make_event("quota_released", slot, date_iso,
                                             oid, oname, p, cur))
                elif is_shrink(p, cur):
                    events.append(make_event("quota_shrunk", slot, date_iso,
                                             oid, oname, p, cur))
    return events


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
def build_report(snapshot, window):
    header = ["办事处"] + [
        d[5:] + ("周末" if datetime.date.fromisoformat(d).weekday() >= 5 else "")
        for d in window
    ]
    lines = ["| " + " | ".join(header) + " |"]
    lines.append("|" + "---|" * len(header))
    for oid in sorted(snapshot.keys()):
        cells = []
        for d in window:
            cell = snapshot[oid].get(d)
            if not cell:
                cells.append("⚪")
                continue
            s = cell.get("R")
            cells.append("{}{}".format(STATUS_ICON.get(s, "⚪"), STATUS_LABEL.get(s, s)))
        lines.append("| {} | ".format(oid) + " | ".join(cells) + " |")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Webhook（飞书）
# ---------------------------------------------------------------------------
def feishu_send(url, text):
    payload = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "🎉 入境处配额放号提醒"},
                "template": "red",
            },
            "elements": [{"tag": "markdown", "content": text}],
        },
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main():
    cfg = load_config()
    print("=== 入境处配额放号监控 ===")

    raw = fetch_quota(cfg)
    print("接口 data 行数:", len(raw.get("data", [])))
    print("官方数据更新时间:", raw.get("lastUpdateTime"))

    office = office_map(raw)
    snapshot = normalize(raw)
    window = window_dates(raw, cfg)
    print("监控窗口（今天起最近 {} 天）:".format(cfg.get("days_window", 10)))
    print("  ", "   ".join(window))

    os.makedirs(DATA_DIR, exist_ok=True)

    if os.path.exists(QUOTA_FILE):
        with open(QUOTA_FILE, encoding="utf-8") as f:
            old = json.load(f)
        print("已找到上一轮快照，进行放号比对。")
    else:
        old = {}
        print("无上一轮快照，本轮仅建档基线（不误报放号）。")

    events = diff_events(old, snapshot, window, office)
    releases = [e for e in events if e["type"] == "quota_released"]

    with open(EVENTS_FILE, "a", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    if events:
        print("检测到 {} 个变化，其中放号 {} 个。".format(len(events), len(releases)))
        for e in releases:
            print("  !! 放号:", e["officeName"], e["date"], e["weekday"],
                  "{}时段".format("延长" if e["slot"] == "K" else "一般"),
                  "{} -> {}".format(STATUS_LABEL.get(e["from"], "?"),
                                    STATUS_LABEL.get(e["to"], e["to"])))
    else:
        print("与上一轮相比无变化。")

    # 飞书通知（仅放号 + 冷却）
    webhook_url = os.environ.get("FEISHU_WEBHOOK", "").strip()
    if not webhook_url:
        print("未配置 FEISHU_WEBHOOK，跳过通知。")
    elif not releases:
        print("本轮无放号，不通知。")
    else:
        now = time.time()
        cooled = True
        if os.path.exists(LAST_NOTIFY_FILE):
            try:
                last = json.load(open(LAST_NOTIFY_FILE)).get("ts", 0)
                if now - last < cfg.get("notify_cooldown_sec", 300):
                    cooled = False
            except Exception:
                pass
        if cooled:
            text = "🎉 入境处配额放号！"
            for e in releases:
                text += "\n• {} {}  {}时段 {} -> {}（{}）".format(
                    e["officeName"], e["date"],
                    "延长" if e["slot"] == "K" else "一般",
                    STATUS_LABEL.get(e["from"], e["from"]),
                    STATUS_LABEL.get(e["to"], e["to"]),
                    e["weekday"],
                )
            try:
                feishu_send(webhook_url, text)
                with open(LAST_NOTIFY_FILE, "w", encoding="utf-8") as f:
                    json.dump({"ts": now}, f)
                print("已发送飞书通知。")
            except Exception as e:
                print("飞书通知失败:", e)
        else:
            print("存在放号但处于冷却期，跳过通知。")

    # 报告
    print("\n===== 最近 {} 天配额（一般时段） =====".format(cfg.get("days_window", 10)))
    print(build_report(snapshot, window))
    wk_release = [e for e in releases if "（周末）" in e["weekday"]]
    print("\n===== 周末放号关注 =====")
    if wk_release:
        for e in wk_release:
            print("   ", e["officeName"], e["date"], e["weekday"],
                  "{}->{}".format(e["slot"], STATUS_LABEL.get(e["to"], e["to"])))
    else:
        print("   本轮无周末放号事件。")

    with open(QUOTA_FILE, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=1)
    print("\n已更新快照: data/quota.json")
    print("事件日志: data/events.jsonl")
    return 0


if __name__ == "__main__":
    sys.exit(main())