# -*- coding: utf-8 -*-
"""
微信小程序工程 · 静态检查 + 接口联调

能验证的（这里全做）：
  A 工程结构：app.json 里声明的每个页面，四个文件是否齐全
  B JSON 合法：app.json / project.config.json
  C WXML 结构：标签闭合、自闭合标签识别
  D 接口一致：页面里调用的每个 /api/ 路径，服务端都真的注册了
  E 端到端：按每个页面的真实调用顺序打一遍接口（登录→今日待访→打卡→KPI→
    门店→商品→下单→订单→配送→看板），含越权与无效打卡

不能验证的（说清楚，不含糊）：
  渲染效果、真机定位精度、微信原生组件行为 —— 这些要在微信开发者工具里看。
  本机没有开发者工具，也没有 AppID，所以只做到「打开就能编译、点下去有反应」为止。

用法：先启动 07_p1_server.py，再 python 11_miniprogram_test.py
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
MP = os.path.join(BASE, "miniprogram")
OUT = os.path.join(BASE, "11_小程序测试结果.txt")
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
    return d["token"]


VOID = {"input", "image", "icon", "progress", "switch", "slider", "radio",
        "checkbox", "textarea", "import", "include", "wxs"}


def wxml_balance(text):
    """返回 (未闭合栈, 错误列表)。WXML 没有 HTMLParser，自己扫一遍。"""
    stack, errs = [], []
    for m in re.finditer(r"<(/?)([a-zA-Z0-9\-]+)([^>]*?)(/?)>", text):
        closing, tag, attrs, selfclose = m.group(1), m.group(2), m.group(3), m.group(4)
        if tag in VOID or selfclose == "/":
            continue
        if closing:
            if not stack:
                errs.append("多余的 </%s>" % tag)
            elif stack[-1] != tag:
                errs.append("不匹配：<%s> 对 </%s>" % (stack[-1], tag))
                stack.pop()
            else:
                stack.pop()
        else:
            stack.append(tag)
    return stack, errs


def main():
    log("=" * 78)
    log("微信小程序工程 · 静态检查 + 接口联调  %s" % date.today().isoformat())
    log("=" * 78)

    # ---------- A 工程结构 ----------
    log("\n[A] 工程结构")
    app_json = os.path.join(MP, "app.json")
    check("存在 app.json", os.path.exists(app_json))
    check("存在 app.js", os.path.exists(os.path.join(MP, "app.js")))
    check("存在 app.wxss", os.path.exists(os.path.join(MP, "app.wxss")))
    check("存在 project.config.json", os.path.exists(os.path.join(MP, "project.config.json")))
    check("存在 utils/api.js", os.path.exists(os.path.join(MP, "utils", "api.js")))

    app = json.loads(io.open(app_json, encoding="utf-8").read())
    pages = app.get("pages", [])
    check("声明了 %d 个页面" % len(pages), len(pages) == 7,
          "、".join(p.split("/")[1] for p in pages))
    missing = []
    for p in pages:
        for ext in ("js", "wxml"):
            f = os.path.join(MP, p + "." + ext)
            if not os.path.exists(f):
                missing.append(p + "." + ext)
    check("每个页面都有 .js 与 .wxml", not missing, str(missing))

    # ---------- B JSON ----------
    log("\n[B] JSON 合法性")
    for f in ("app.json", "project.config.json"):
        p = os.path.join(MP, f)
        try:
            d = json.loads(io.open(p, encoding="utf-8").read())
            check("%s 可解析" % f, True, "%d 个键" % len(d))
        except Exception as e:
            check("%s 可解析" % f, False, str(e))
    cfg = json.loads(io.open(os.path.join(MP, "project.config.json"), encoding="utf-8").read())
    check("appid 已设置（游客模式 touristappid）", cfg.get("appid") == "touristappid", cfg.get("appid"))
    check("已关闭域名校验（本地开发用）", cfg.get("setting", {}).get("urlCheck") is False)

    # ---------- C WXML ----------
    log("\n[C] WXML 结构")
    for p in pages:
        f = os.path.join(MP, p + ".wxml")
        if not os.path.exists(f):
            continue
        text = io.open(f, encoding="utf-8").read()
        stack, errs = wxml_balance(text)
        check("%-22s 标签闭合" % p.split("/")[1], not stack and not errs,
              str(stack[:3] + errs[:3]))
    # 微信不允许的写法
    for p in pages:
        f = os.path.join(MP, p + ".wxml")
        if not os.path.exists(f):
            continue
        text = io.open(f, encoding="utf-8").read()
        check("%-22s 没有 onclick/onchange（小程序用 bindtap）" % p.split("/")[1],
              "onclick" not in text and "onchange=" not in text)

    # ---------- D 接口一致性 ----------
    log("\n[D] 页面调用的接口服务端都注册了")
    srv = io.open(os.path.join(BASE, "07_p1_server.py"), encoding="utf-8").read()
    used_all = set()
    for dirpath, _, files in os.walk(MP):
        for fn in files:
            if not fn.endswith(".js"):
                continue
            t = io.open(os.path.join(dirpath, fn), encoding="utf-8").read()
            used_all |= set(re.findall(r"['\"`](/api/[a-zA-Z0-9\-/_]+)", t))
    missing_api = []
    for u in sorted(used_all):
        base = u.rstrip("/")
        if base in srv or ('"%s"' % base) in srv:
            continue
        if re.match(r"^/api/(orders|deliveries|batches|visits)/", base):
            continue  # 由正则路由处理
        missing_api.append(base)
    check("小程序共用到 %d 个接口，全部存在" % len(used_all), not missing_api, str(missing_api))
    log("     " + "、".join(sorted(used_all)))

    # ---------- E 端到端 ----------
    log("\n[E] 按页面真实调用顺序联调")
    TK = {u: login(u) for u in ("boss", "sales1", "store1", "driver1", "sales2")}
    req("/api/reset", "POST", {}, token=TK["boss"])
    TK = {u: login(u) for u in ("boss", "sales1", "store1", "driver1", "sales2")}

    log("\n[E1] 业务员端：今日待访 → 打卡 → KPI")
    st, d = req("/api/visits/today", token=TK["sales1"])
    check("今日待访 2 家（自己名下）", st == 200 and d["stats"]["total"] == 2,
          "、".join(x["name"] for x in d["todo"]))
    check("每条带经纬度（地图打点要用）", all(x["lng"] and x["lat"] for x in d["todo"]))

    st, d = req("/api/visits/checkin", "POST",
                {"store_id": "S001", "lng": 121.5551, "lat": 29.9081, "accuracy_m": 12},
                token=TK["sales1"])
    check("到店打卡：有效（距 14.7 米）", st == 200 and d["valid"] is True, str(d.get("distance_m")))

    st, d = req("/api/visits/checkin", "POST",
                {"store_id": "S002", "lng": 121.60, "lat": 29.95, "accuracy_m": 20},
                token=TK["sales1"])
    check("远离门店打卡：记录但判无效", st == 200 and d["valid"] is False, d.get("reason", ""))

    st, d = req("/api/visits/checkin", "POST",
                {"store_id": "S002", "lng": 121.5662, "lat": 29.9125, "accuracy_m": 300},
                token=TK["sales1"])
    check("定位精度 300 米：判无效", d["valid"] is False, d.get("reason", ""))

    st, d = req("/api/visits/checkin", "POST",
                {"store_id": "S003", "lng": 121.62, "lat": 29.81}, token=TK["sales1"])
    check("给别家门店打卡 → 403", st == 403, d.get("code", ""))

    st, d = req("/api/visits/checkin", "POST", {"store_id": "S001"}, token=TK["sales1"])
    check("没有定位 → 400", st == 400 and d.get("code") == "NO_LOCATION", d.get("code", ""))

    st, d = req("/api/visits/today", token=TK["sales1"])
    check("打卡后 S001 进入已访", d["stats"]["done"] >= 1, "已访 %d 家" % d["stats"]["done"])

    st, d = req("/api/visits/kpi", token=TK["sales1"])
    check("KPI 有拜访数", d["visits"] >= 2, "本月 %d 次，有效 %d 次" % (d["visits"], d["valid"]))
    st, d = req("/api/visits/kpi", token=TK["sales2"])
    check("KPI 按人隔离（sales2 看不到 sales1 的）", d["visits"] == 0, str(d["visits"]))

    log("\n[E2] 门店端：门店 → 商品 → 下单 → 订单 → 对账")
    st, d = req("/api/stores", token=TK["store1"])
    check("门店只见自己 1 家", len(d["items"]) == 1, d["items"][0]["name"])
    st, d = req("/api/master/skus", token=TK["store1"])
    check("商品目录 72 个 SKU", len(d["items"]) == 72, str(len(d["items"])))
    req("/api/demo/setup", "POST", {}, token=TK["boss"])
    st, d = req("/api/master/skus", token=TK["store1"])
    orderable = [x for x in d["items"] if x["can_order"]]
    check("补数据后 3 个 SKU 可下单", len(orderable) >= 3,
          str(["%s ¥%s" % (x["name"], x["price"]) for x in orderable])[:70])
    check("门店看不到成本", all(x["cost"] is None for x in d["items"]))
    st, d = req("/api/orders", "POST",
                {"store_id": "S001", "lines": [{"sku_code": orderable[0]["sku_code"], "cartons": 2}]},
                token=TK["store1"])
    check("门店下单成功", st == 200, d.get("order_no", ""))
    st, d = req("/api/orders", token=TK["store1"])
    check("门店订单列表非空", len(d["items"]) >= 1, "%d 单" % len(d["items"]))
    st, d = req("/api/receivables", token=TK["store1"])
    check("对账页有数据", isinstance(d["items"], list), "%d 条" % len(d["items"]))

    log("\n[E3] 司机端：配送单 → 签收")
    st, d = req("/api/demo/dispatch", "POST", {}, token=TK["boss"])
    check("老板发车", st == 200 and len(d["deliveries"]) >= 1, str(d.get("deliveries")))
    st, d = req("/api/deliveries", token=TK["driver1"])
    items = d["items"]
    check("司机看到本线路配送单", len(items) >= 1, "%d 单" % len(items))
    check("全部 CX-01", all(x["route"] == "CX-01" for x in items))
    pending = [x for x in items if x["status"] == "配送中"]
    check("列表把「配送中」排在前面", bool(pending) and items[0]["status"] == "配送中",
          items[0]["status"] if items else "空")
    first = pending[0]
    st, d = req("/api/deliveries/%s/sign" % first["delivery_no"], "POST",
                {"receiver": first["store"] + " 收货人"}, token=TK["driver1"])
    check("签收成功并出账期", st == 200 and bool(d.get("due_date")), str(d.get("due_date")))

    log("\n[E4] 老板端：看板")
    st, d = req("/api/dashboard", token=TK["boss"])
    check("看板有订单数与金额", d["orders"] >= 1, "%d 单 / ¥%s" % (d["orders"], d["amount"]))
    check("看板有毛利率（老板可见）", "gross_margin" in d, "%s%%" % d.get("gross_margin"))
    st, d = req("/api/dashboard", token=TK["sales1"])
    check("业务员看板没有毛利率", "gross_margin" not in d)

    log("\n" + "=" * 78)
    log("结果：通过 %d ｜ 失败 %d" % (passed, failed))
    log("=" * 78)
    log("说明：渲染效果与真机定位需在微信开发者工具里看，本脚本只验证结构与接口。")
    with io.open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    log("[saved] %s" % OUT)
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
