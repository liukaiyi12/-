# -*- coding: utf-8 -*-
"""
门店端 / 司机端 · 页面与权限验证

为什么单独测：这两端是给**外面的人**用的（门店老板、司机），
主控台那套权限再严，这两页只要能越权看到一个别家门店，就等于没有权限。

覆盖：
  A 页面可用：/store、/driver 能打开、无外部依赖、关键文案在
  B 门店端：只看自己一家店、看不到成本、看不到别家订单、能下单
  C 司机端：只看本线路配送单、签收后订单变已回单并出账期、不能签别线路
  D 静态一致性：两个页面调用的每个接口，服务端都真的注册了

用法：先启动 07_p1_server.py，再 python 10_store_driver_test.py
"""
import io
import json
import os
import re
import sys
import urllib.request
import urllib.error
from datetime import date

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "10_两端测试结果.txt")
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


def html(path):
    with urllib.request.urlopen(HOST + path, timeout=15) as r:
        return r.read().decode("utf-8")


def login(u):
    st, d = req("/api/auth/login", "POST", {"username": u, "password": PW})
    if st != 200:
        log("登录失败 %s: %s" % (u, d))
        sys.exit(2)
    return d["token"]


def main():
    log("=" * 78)
    log("门店端 / 司机端 · 页面与权限验证  %s" % date.today().isoformat())
    log("=" * 78)

    st, d = req("/api/health")
    if st != 200:
        log("服务未启动，请先运行：python 07_p1_server.py")
        sys.exit(2)

    TK = {u: login(u) for u in ("boss", "store1", "driver1", "sales2")}

    # ---------- A 页面 ----------
    log("\n[A] 三个页面都能打开")
    pages = {"/": "index.html", "/store": "store.html", "/driver": "driver.html"}
    for path, f in pages.items():
        try:
            body = html(path)
            check("%-8s → %-12s 可访问" % (path, f), len(body) > 3000, "%d 字节" % len(body))
        except Exception as e:
            check("%-8s 可访问" % path, False, str(e))
    for path, must in (("/store", "门店订货"), ("/driver", "配送"),
                       ("/store", "store1"), ("/driver", "driver1")):
        check("%s 含关键文案「%s」" % (path, must), must in html(path))
    for path in ("/store", "/driver"):
        body = html(path)
        check("%s 无外部 CDN 依赖" % path, "http://" not in body and "https://" not in body)

    # ---------- D 静态一致性 ----------
    log("\n[D] 页面调用的接口服务端都注册了")
    srv = io.open(os.path.join(BASE, "07_p1_server.py"), encoding="utf-8").read()
    for path in ("/store", "/driver"):
        body = html(path)
        used = set(re.findall(r"['\"](/api/[a-zA-Z0-9\-/_]+)", body))
        used |= set(re.findall(r"`(/api/[a-zA-Z0-9\-/_]+)", body))
        missing = []
        for u in sorted(used):
            base = re.sub(r"\$\{[^}]*\}", "", u).rstrip("/")
            base = base.split("${")[0]
            if not base or base in ("/api",):
                continue
            # 服务端要么有字面量，要么有对应的正则路由
            if ('"%s"' % base) in srv:
                continue
            if base.startswith("/api/deliveries/"):
                continue  # 由正则路由处理
            missing.append(base)
        check("%s 用到的 %d 个接口都存在" % (path, len(used)), not missing, str(missing))

    # ---------- B 门店端 ----------
    log("\n[B] 门店端：只能看自己")
    req("/api/reset", "POST", {}, token=TK["boss"])
    TK = {u: login(u) for u in ("boss", "store1", "driver1", "sales2")}
    req("/api/demo/setup", "POST", {}, token=TK["boss"])
    st, d = req("/api/demo/orders", "POST", {}, token=TK["boss"])
    check("铺 4 张跨区域订单", st == 200 and len(d["orders"]) == 4, str(len(d.get("orders", []))))

    st, d = req("/api/stores", token=TK["store1"])
    check("门店只见自己 1 家", len(d["items"]) == 1 and d["items"][0]["id"] == "S001",
          "、".join(x["name"] for x in d["items"]))

    st, d = req("/api/orders", token=TK["store1"])
    check("门店只见自己的 1 张订单", len(d["items"]) == 1, str(len(d["items"])))

    st, d = req("/api/master/skus", token=TK["store1"])
    check("门店看不到成本", all(x["cost"] is None for x in d["items"]))

    st, d = req("/api/orders", token=TK["sales2"])
    other = d["items"][0]["order_no"] if d["items"] else None
    st, d = req("/api/orders/" + other, token=TK["store1"])
    check("门店点开别家订单 → 403", st == 403, "%s ｜ %s" % (other, d.get("code", "")))

    st, d = req("/api/orders", "POST",
                {"store_id": "S001", "lines": [{"sku_code": "KY001-1K-B-1", "cartons": 1}]},
                token=TK["store1"])
    check("门店自己下单成功", st == 200, d.get("order_no", ""))
    st, d = req("/api/orders", "POST",
                {"store_id": "S002", "lines": [{"sku_code": "KY001-1K-B-1", "cartons": 1}]},
                token=TK["store1"])
    check("门店给别家店下单 → 403", st == 403, d.get("code", ""))

    # ---------- C 司机端 ----------
    log("\n[C] 司机端：只看本线路")
    st, d = req("/api/demo/dispatch", "POST", {}, token=TK["boss"])
    check("老板发车（CX-01 线路拣货+出库）", st == 200 and len(d["deliveries"]) >= 1,
          str(d.get("deliveries")))

    st, d = req("/api/deliveries", token=TK["driver1"])
    items = d["items"]
    check("司机能看到配送单", len(items) >= 1, "%d 单" % len(items))
    check("全部属于本线路 CX-01", all(x["route"] == "CX-01" for x in items),
          "、".join(sorted({x["route"] for x in items})))
    check("带门店地址（司机要导航）", all(x["address"] for x in items))

    st, d = req("/api/deliveries", token=TK["boss"])
    boss_items = d["items"]
    check("老板能看到全部配送单", len(boss_items) >= len(items),
          "%d vs 司机 %d" % (len(boss_items), len(items)))

    # 司机签收第一单
    target = items[0]
    st, d = req("/api/deliveries/%s/sign" % target["delivery_no"], "POST",
                {"receiver": "门店收货人"}, token=TK["driver1"])
    check("司机签收成功并出账期", st == 200 and bool(d.get("due_date")), str(d.get("due_date")))
    st, d = req("/api/orders/" + target["order_no"], token=TK["boss"])
    check("订单状态变「已回单」", d["order"]["status"] == "已回单", d["order"]["status"])

    st, d = req("/api/deliveries/%s/sign" % target["delivery_no"], "POST",
                {"receiver": "x"}, token=TK["driver1"])
    check("重复签收 → 409", st == 409, d.get("code", ""))

    # 越权：造一张别线路（CD-01）的配送单，司机点不动
    st, d = req("/api/orders", token=TK["boss"])
    all_orders = [x for x in d["items"] if x["status"] == "已确认"]
    cd_order = None
    for o in all_orders:
        st, od = req("/api/orders/" + o["order_no"], token=TK["boss"])
        if od["order"]["store"] == "川味小厨":
            cd_order = o["order_no"]
            break
    if cd_order:
        req("/api/orders/%s/pick" % cd_order, "POST", {}, token=TK["boss"])
        st, cd = req("/api/orders/%s/ship" % cd_order, "POST", {}, token=TK["boss"])
        check("给别线路（CD-01）生成配送单", st == 200, cd.get("delivery_no", ""))
        st, d = req("/api/deliveries/%s/sign" % cd["delivery_no"], "POST",
                    {"receiver": "x"}, token=TK["driver1"])
        check("司机签别线路的单 → 403", st == 403, d.get("code", ""))
        st, d = req("/api/deliveries", token=TK["driver1"])
        check("别线路的单不出现在司机列表里",
              all(x["delivery_no"] != cd["delivery_no"] for x in d["items"]))
    else:
        check("找到别线路订单", False, "没找到川味小厨的已确认订单")

    # ---------- 汇总 ----------
    st, d = req("/api/deliveries", token=TK["driver1"])
    pending = [x for x in d["items"] if x["status"] == "配送中"]
    log("\n  司机端当前：待送 %d 单、已签 %d 单"
        % (len(pending), len(d["items"]) - len(pending)))

    log("\n" + "=" * 78)
    log("结果：通过 %d ｜ 失败 %d" % (passed, failed))
    log("=" * 78)
    with io.open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    log("[saved] %s" % OUT)
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
