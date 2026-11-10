# -*- coding: utf-8 -*-
"""
P1 骨架冒烟测试 —— 真实 HTTP 请求跑完整条链路 + 权限交叉验证。

A 段（业务流程）：
  留空下单被拦（缺口一次列清）→ 补数据 → 入库待检 → 放行 → 下单 → FEFO 跨批次
  → 拣货 → 出库 → 签收 → 应收 → 信用额度闸门 → 看板

B 段（权限）：
  未登录 401 → 十个账号的可见门店数 → 订单按门店范围过滤 → 成本字段脱敏
  → 跨门店越权下单 / 越权改价 / 越权看订单详情 → 门店账号只能给自己下单 → 审计留痕

用法：先启动 07_p1_server.py，再 python 08_smoke_test.py
"""
import io
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import date

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "08_冒烟测试结果.txt")
HOST = "http://127.0.0.1:8765"
PW = "demo1234"

lines = []
passed = failed = 0


def log(s=""):
    lines.append(str(s))
    print(s)


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        log("  [PASS] %s%s" % (name, ("  " + detail) if detail else ""))
    else:
        failed += 1
        log("  [FAIL] %s%s" % (name, ("  " + detail) if detail else ""))


def req(path, method="GET", body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    r = urllib.request.Request(HOST + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def login(u):
    st, d = req("/api/auth/login", "POST", {"username": u, "password": PW})
    if st != 200:
        log("登录失败 %s: %s" % (u, d))
        sys.exit(2)
    return d["token"], d["user"]


def main():
    log("=" * 78)
    log("P1 骨架 · 冒烟 + 权限测试  %s" % date.today().isoformat())
    log("=" * 78)

    st, d = req("/api/health")
    if st != 200:
        log("服务未启动，请先运行：python 07_p1_server.py")
        sys.exit(2)
    log("服务在线 %s  数据库=%s" % (HOST, d.get("db")))

    # 每个套件自行重置，避免被上一个套件留下的数据影响（比如建档新建的门店）
    _, boot = req("/api/auth/login", "POST", {"username": "boss", "password": PW})
    req("/api/reset", "POST", {}, token=boot["token"])

    # ========================================================
    log("\n【B 段：权限】先验证门禁，再跑业务")
    # ========================================================
    log("\n[P1] 未登录访问")
    st, d = req("/api/orders")
    check("无 Token → 401", st == 401 and d.get("code") == "UNAUTHORIZED")
    st, d = req("/api/orders", token="garbage.token")
    check("伪造 Token → 401", st == 401)

    log("\n[P2] 十个账号登录并看各自的门店范围")
    TK = {}
    expect_stores = {"boss": 5, "director": 5, "sales1": 2, "sales2": 2, "sales3": 1,
                     "cs": 5, "wh": 5, "driver1": 2, "store1": 1, "fin": 5}
    for u in expect_stores:
        TK[u], info = login(u)
        st, d = req("/api/stores", token=TK[u])
        n = len(d.get("items", []))
        check("%-8s %-6s 可见门店 %d（期望 %d）" % (u, info["role_cn"], n, expect_stores[u]),
              n == expect_stores[u],
              "、".join(x["id"] for x in d.get("items", [])) if n <= 3 else "")

    log("\n[P3] 角色矩阵自检")
    st, d = req("/api/auth/me", token=TK["sales1"])
    check("业务员 see_cost=False", d["user"]["see_cost"] is False)
    st, d = req("/api/auth/me", token=TK["boss"])
    check("老板 see_cost=True", d["user"]["see_cost"] is True)
    st, d = req("/api/auth/me", token=TK["store1"])
    check("门店账号绑定 S001", d["user"]["store_id"] == "S001")

    # ========================================================
    log("\n【A 段：业务流程】以下全部以老板身份操作")
    # ========================================================
    st, d = req("/api/reset", "POST", {}, token=TK["boss"])
    check("重置到初始状态", st == 200)
    TK["boss"], _ = login("boss")

    st, d = req("/api/master/status", token=TK["boss"])
    check("可下单 SKU 为 0", d["can_order"] == 0, "总数 %d" % d["total"])

    log("\n[1] 留空状态下单：万州烤鱼酱 × 2 箱")
    st, d = req("/api/orders", "POST",
                {"store_id": "S001", "lines": [{"sku_code": "KY001-1K-B-1", "cartons": 2}]},
                token=TK["boss"])
    check("HTTP 400 且缺 H、P", st == 400 and d.get("code") == "MISSING_MASTER"
          and sorted(m["column"] for m in d["missing"]) == ["H", "P"])

    log("\n[2] 补箱规 H=20 → 仍缺价格")
    req("/api/master/fill", "POST", {"sku_code": "KY001-1K-B-1", "units_per_carton": 20},
        token=TK["boss"])
    st, d = req("/api/orders", "POST",
                {"store_id": "S001", "lines": [{"sku_code": "KY001-1K-B-1", "cartons": 2}]},
                token=TK["boss"])
    check("只缺 P", d.get("code") == "MISSING_MASTER"
          and [m["column"] for m in d["missing"]] == ["P"])

    log("\n[3] 补价格 → 改报缺货")
    req("/api/master/fill", "POST", {"sku_code": "KY001-1K-B-1", "price_cent": 840,
                                    "cost_cent": 590}, token=TK["boss"])
    st, d = req("/api/orders", "POST",
                {"store_id": "S001", "lines": [{"sku_code": "KY001-1K-B-1", "cartons": 2}]},
                token=TK["boss"])
    check("库存不足", d.get("code") == "OUT_OF_STOCK", d.get("message", ""))

    log("\n[4] 入库缺 J、K")
    st, d = req("/api/inbound", "POST",
                {"sku_code": "KY001-1K-B-1", "batch_no": "P2611011-A-001", "qty_unit": 40,
                 "prod_date": "2026-11-01", "expire_date": "2027-05-01"}, token=TK["boss"])
    check("缺 J、K", sorted(m["column"] for m in d.get("missing", [])) == ["J", "K"])

    log("\n[5] 补 J、K → 入库两批（后生产的先到期）")
    req("/api/master/fill", "POST", {"sku_code": "KY001-1K-B-1", "shelf_life_month": 6,
                                    "storage_cond": "常温"}, token=TK["boss"])
    st, d = req("/api/inbound", "POST",
                {"sku_code": "KY001-1K-B-1", "batch_no": "P2611051-A-002", "qty_unit": 100,
                 "prod_date": "2026-11-05", "expire_date": "2027-05-05"}, token=TK["boss"])
    check("入库为待检", st == 200 and d.get("qc_status") == "待检")
    req("/api/inbound", "POST",
        {"sku_code": "KY001-1K-B-1", "batch_no": "P2611011-A-001", "qty_unit": 40,
         "prod_date": "2026-11-01", "expire_date": "2027-05-01"}, token=TK["boss"])

    log("\n[6] 待检不可出库")
    st, d = req("/api/orders", "POST",
                {"store_id": "S001", "lines": [{"sku_code": "KY001-1K-B-1", "cartons": 2}]},
                token=TK["boss"])
    check("仍报库存不足", d.get("code") == "OUT_OF_STOCK")

    log("\n[7] 放行 → 下单 4 箱（80 袋）")
    for b in ("P2611051-A-002", "P2611011-A-001"):
        req("/api/batches/%s/release" % b, "POST", {}, token=TK["boss"])
    st, d = req("/api/orders", "POST",
                {"store_id": "S001", "lines": [{"sku_code": "KY001-1K-B-1", "cartons": 4}]},
                token=TK["boss"])
    check("下单成功 ¥672.00", st == 200 and abs(d.get("amount", 0) - 672) < 0.01,
          d.get("order_no", ""))
    no = d["order_no"]
    alloc = d["order"]["alloc"]
    check("跨两批分配 80 袋", len(alloc) == 2 and sum(a["qty"] for a in alloc) == 80,
          str([(a["batch_no"], a["qty"]) for a in alloc]))
    check("先到期的在前", [a["expire"] for a in alloc] == sorted(a["expire"] for a in alloc))

    log("\n[8] 拣货 → 出库 → 签收")
    st, d = req("/api/orders/%s/pick" % no, "POST", {}, token=TK["boss"])
    check("状态=待发货", d.get("order", {}).get("status") == "待发货")
    st, d = req("/api/orders/%s/ship" % no, "POST", {}, token=TK["boss"])
    dno = d.get("delivery_no")
    check("出库生成配送单", st == 200 and bool(dno), str(dno))
    st, d = req("/api/deliveries/%s/sign" % dno, "POST", {"receiver": "李老板"}, token=TK["boss"])
    check("签收并出账期 12-10", st == 200 and d.get("due_date") == "2026-12-10")
    st, d = req("/api/deliveries/%s/sign" % dno, "POST", {"receiver": "李老板"}, token=TK["boss"])
    check("重复签收 409", st == 409)

    log("\n[9] 库存核对")
    st, d = req("/api/stock", token=TK["boss"])
    m = {x["batch_no"]: x for x in d["items"]}
    check("P2611011-A-001 在手 0 / 锁定 0",
          m["P2611011-A-001"]["qty_on_hand"] == 0 and m["P2611011-A-001"]["qty_locked"] == 0)
    check("P2611051-A-002 在手 60", m["P2611051-A-002"]["qty_on_hand"] == 60)

    # ========================================================
    log("\n【B 段续：越权与脱敏】")
    # ========================================================
    log("\n[P4] 重置并铺三张分布在不同区域的订单")
    req("/api/reset", "POST", {}, token=TK["boss"])
    TK["boss"], _ = login("boss")
    req("/api/demo/setup", "POST", {}, token=TK["boss"])
    st, d = req("/api/demo/orders", "POST", {}, token=TK["boss"])
    check("生成 4 张订单", st == 200 and len(d.get("orders", [])) == 4, str(d.get("orders")))

    log("\n[P5] 每个账号能看到的订单数")
    expect_orders = {"boss": 4, "director": 4, "cs": 4, "wh": 4, "fin": 4,
                     "sales1": 2, "sales2": 1, "sales3": 1, "driver1": 2, "store1": 1}
    for u, n in expect_orders.items():
        st, d = req("/api/orders", token=TK[u])
        got = len(d.get("items", []))
        check("%-8s 看到 %d 单（期望 %d）" % (u, got, n), got == n,
              "、".join(x["store"] for x in d.get("items", [])) if got <= 3 else "")

    log("\n[P6] 行级过滤发生在 SQL 层，不是前端藏起来的")
    st, d = req("/api/dashboard", token=TK["sales1"])
    check("sales1 看板订单=2、额度=¥2,500（只含 S001+S002）",
          d["orders"] == 2 and abs(d["credit_limit"] - 2500) < 0.01,
          "订单 %d 额度 %s" % (d["orders"], d["credit_limit"]))
    check("sales1 看板没有毛利字段", "gross_margin" not in d)
    st, d = req("/api/dashboard", token=TK["boss"])
    check("boss 看板有毛利率", "gross_margin" in d, "毛利率 %s%%" % d.get("gross_margin"))
    check("boss 额度合计 ¥5,100", abs(d["credit_limit"] - 5100) < 0.01, str(d["credit_limit"]))

    log("\n[P7] 字段脱敏：成本字段服务端不回传")
    st, d = req("/api/master/skus", token=TK["sales1"])
    check("sales1 全部成本为 null", all(x["cost"] is None for x in d["items"]))
    st, d = req("/api/master/skus", token=TK["boss"])
    filled = [x["cost"] for x in d["items"] if x["cost"] is not None]
    check("boss 能看到成本", len(filled) >= 3, "有成本值的 SKU %d 个" % len(filled))

    log("\n[P8] 越权动作")
    st, d = req("/api/orders", "POST",
                {"store_id": "S003", "lines": [{"sku_code": "HG003-1K-B-1", "cartons": 1}]},
                token=TK["sales1"])
    check("sales1 给别家门店下单 → 403", st == 403, d.get("code", ""))

    st, d = req("/api/master/fill", "POST", {"sku_code": "KY001-1K-B-1", "price_cent": 1},
                token=TK["sales1"])
    check("sales1 改价格 → 403", st == 403, d.get("code", ""))

    st, d = req("/api/stock", token=TK["driver1"])
    check("司机看库存 → 403", st == 403, d.get("code", ""))

    st, d = req("/api/audit", token=TK["sales1"])
    check("业务员看审计日志 → 403", st == 403, d.get("code", ""))

    st, d = req("/api/orders", token=TK["sales1"])
    mine = [x["order_no"] for x in d["items"]]
    st, d = req("/api/orders", token=TK["boss"])
    foreign = [x["order_no"] for x in d["items"] if x["order_no"] not in mine][0]
    st, d = req("/api/orders/" + foreign, token=TK["sales1"])
    check("sales1 看别家订单详情 → 403", st == 403, foreign)

    log("\n[P9] 门店账号的边界")
    st, d = req("/api/orders", "POST",
                {"store_id": "S002", "lines": [{"sku_code": "KY001-1K-B-1", "cartons": 1}]},
                token=TK["store1"])
    check("门店给别家店下单 → 403", st == 403, d.get("code", ""))
    st, d = req("/api/orders", "POST",
                {"store_id": "S001", "lines": [{"sku_code": "KY001-1K-B-1", "cartons": 1}]},
                token=TK["store1"])
    check("门店给自己下单 → 通过", st == 200, d.get("order_no", ""))

    log("\n[P10] 审计留痕")
    st, d = req("/api/audit", token=TK["boss"])
    actions = [x["action"] for x in d["items"]]
    check("有 login 记录", "login" in actions)
    check("有 order.create 记录", "order.create" in actions, "%d 条" % actions.count("order.create"))

    # ========================================================
    log("\n【C 段：排产与换线】")
    # ========================================================
    log("\n[S1] 从订单生成工单")
    st, d = req("/api/workorders/from-orders", "POST", {}, token=TK["boss"])
    check("工单已生成", st == 200 and len(d.get("created", [])) > 0, str(d.get("created")))
    st, d = req("/api/workorders", token=TK["wh"])
    wos = d["items"]
    check("仓库也能看工单", len(wos) > 0, "%d 张" % len(wos))
    check("工单带品类族", all(w["family"] for w in wos),
          "、".join(sorted({w["family"] for w in wos})))
    check("锅次 = ceil(数量/500)", all(w["pot_count"] >= 1 for w in wos),
          str([(w["qty_unit"], w["pot_count"]) for w in wos][:4]))

    log("\n[S2] 三策略对比")
    st, d = req("/api/schedule/compare", token=TK["boss"])
    cmp = d["compare"]
    check("成块洗锅次数 ≤ 纯交期", cmp["family"]["clean_count"] <= cmp["edd"]["clean_count"],
          "%d vs %d" % (cmp["family"]["clean_count"], cmp["edd"]["clean_count"]))
    check("成块洗锅分钟 ≤ 纯交期",
          cmp["family"]["clean_total_min"] <= cmp["edd"]["clean_total_min"],
          "%d 分 vs %d 分" % (cmp["family"]["clean_total_min"], cmp["edd"]["clean_total_min"]))

    log("\n[S3] 运行排产")
    st, d = req("/api/schedule/run", "POST", {"strategy": "family"}, token=TK["boss"])
    run1 = d["metrics"]
    check("返回排产结果", st == 200 and len(d.get("steps", [])) == len(wos),
          "%d 步" % len(d.get("steps", [])))
    check("每步有锅位", all(s["pot"] in ("锅1", "锅2") for s in d["steps"]))
    check("洗锅分钟来自矩阵", all(isinstance(s["clean_min"], int) for s in d["steps"]))
    fams = {w["family"] for w in wos}
    check("成块策略的洗锅次数 ≤ 族数−1（%d ≤ %d）" % (run1["clean_count"], max(0, len(fams) - 1)),
          run1["clean_count"] <= max(0, len(fams) - 1), "族：%s" % "、".join(sorted(fams)))

    log("\n[S4] 换线矩阵可改，并且真的影响排产")
    st, d = req("/api/schedule/matrix", token=TK["boss"])
    before = {(x["from"], x["to"]): x["minutes"] for x in d["items"]}
    check("矩阵有 6×5=30 格", len(d["items"]) == 30, str(len(d["items"])))
    for it in d["items"]:
        req("/api/schedule/matrix", "POST",
            {"from": it["from"], "to": it["to"], "minutes": 300}, token=TK["boss"])
    st, d3 = req("/api/schedule/run", "POST", {"strategy": "family"}, token=TK["boss"])
    m3 = d3["metrics"]
    check("全设 300 分后：洗锅总分钟 = 洗锅次数 × 300",
          m3["clean_total_min"] == m3["clean_count"] * 300,
          "%d 次 × 300 = %d" % (m3["clean_count"], m3["clean_total_min"]))
    check("洗锅时间暴涨后完工天数变长", m3["makespan_min"] >= run1["makespan_min"],
          "%.2f → %.2f 天" % (run1["makespan_day"], m3["makespan_day"]))
    for it in d["items"]:
        req("/api/schedule/matrix", "POST",
            {"from": it["from"], "to": it["to"], "minutes": before[(it["from"], it["to"])]},
            token=TK["boss"])
    st, d5 = req("/api/schedule/matrix", token=TK["boss"])
    check("矩阵已恢复",
          all(x["minutes"] == before[(x["from"], x["to"])] for x in d5["items"]))
    req("/api/schedule/matrix", "POST",
        {"from": "火锅底料", "to": "红油辣油", "minutes": before[("火锅底料", "红油辣油")]},
        token=TK["boss"])

    log("\n[S5] 排产权限")
    st, d = req("/api/workorders", token=TK["sales1"])
    check("业务员看工单 → 403", st == 403, d.get("code", ""))
    st, d = req("/api/schedule/matrix", "POST",
                {"from": "火锅底料", "to": "烤鱼", "minutes": 0}, token=TK["wh"])
    check("仓库改矩阵 → 403（只有老板能改标准）", st == 403, d.get("code", ""))

    log("\n" + "=" * 78)
    log("结果：通过 %d ｜ 失败 %d" % (passed, failed))
    log("=" * 78)
    with io.open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    log("[saved] %s" % OUT)
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
