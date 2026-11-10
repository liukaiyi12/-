# -*- coding: utf-8 -*-
"""
补的四个缺口 · 验证

  A 门店建档：业务员扫街遇到新店，就地建档并归到自己名下（之前只能靠种子数据）
  B 打卡照片：base64 上传 → 拿到 URL → 存进拜访记录（之前打卡没有证据）
  C 回款核销：签收 → 出账期 → 收款（部分/全额），应收闭环（之前只有台账没有核销）
  D 登录锁定：连错 5 次锁 5 分钟，老板可解锁（之前文档写着"上线前必补"）

用法：先启动 07_p1_server.py，再 python 12_gap_test.py
"""
import base64
import io
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import date

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "12_补齐项测试结果.txt")
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


def login(u, pw=None):
    st, d = req("/api/auth/login", "POST", {"username": u, "password": pw or PW})
    return st, d


def main():
    log("=" * 78)
    log("补齐项验证：建档 / 打卡照片 / 回款核销 / 登录锁定   %s" % date.today().isoformat())
    log("=" * 78)

    st, d = req("/api/health")
    if st != 200:
        log("服务未启动，请先运行：python 07_p1_server.py")
        sys.exit(2)

    st, boss = login("boss")
    TK = boss["token"]
    req("/api/reset", "POST", {}, token=TK)
    st, boss = login("boss")
    TK = boss["token"]
    sales1 = login("sales1")[1]["token"]
    sales2 = login("sales2")[1]["token"]
    fin = login("fin")[1]["token"]
    store1 = login("store1")[1]["token"]

    # ============ A 建档 ============
    log("\n[A] 门店建档")
    st, d = req("/api/stores", "POST", {"name": "", "address": "x"}, token=sales1)
    check("门店名空 → 400", st == 400, d.get("code", ""))
    st, d = req("/api/stores", "POST",
                {"name": "巷子口烤鱼", "address": "江北区慈城路 9 号",
                 "lng": 121.5560, "lat": 29.9090}, token=sales1)
    check("业务员建档成功", st == 200 and d["store"]["id"].startswith("S"), d["store"]["id"] if st == 200 else "")
    new_id = d["store"]["id"] if st == 200 else None
    check("归属自动记为本人", d["store"]["owner"] == "sales1", d["store"].get("owner", ""))
    check("区域继承本人区域", d["store"]["region"] == "R-CX", str(d["store"].get("region")))

    st, d = req("/api/stores", token=sales1)
    check("sales1 门店数 2 → 3", len(d["items"]) == 3, "、".join(x["name"] for x in d["items"]))
    st, d = req("/api/stores", token=sales2)
    check("sales2 看不到别人新建的店", all(x["id"] != new_id for x in d["items"]),
          "%d 家" % len(d["items"]))
    st, d = req("/api/stores", "POST", {"name": "x", "address": "y"}, token=store1)
    check("门店角色不能建档 → 403", st == 403, d.get("code", ""))

    # ============ B 打卡照片 ============
    log("\n[B] 打卡照片（上传 + 存证）")
    png = base64.b64encode(
        b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 40 + b"IMG").decode()
    st, d = req("/api/upload", "POST", {"name": "checkin.jpg", "b64": png}, token=sales1)
    check("上传成功并返回 URL", st == 200 and d["url"].startswith("/uploads/"), d.get("url", ""))
    url = d["url"] if st == 200 else None
    if url:
        with urllib.request.urlopen(HOST + url, timeout=10) as r:
            raw = r.read()
        check("URL 能取回图片（%d 字节）" % len(raw), raw.startswith(b"\x89PNG"))
    st, d = req("/api/upload", "POST", {"name": "a.exe", "b64": png}, token=sales1)
    check("非图片扩展名 → 400", st == 400 and d.get("code") == "BAD_TYPE", d.get("code", ""))
    st, d = req("/api/upload", "POST", {"name": "a.jpg", "b64": ""}, token=sales1)
    check("空数据 → 400", st == 400, d.get("code", ""))

    st, d = req("/api/visits/checkin", "POST",
                {"store_id": "S001", "lng": 121.5551, "lat": 29.9081, "accuracy_m": 10,
                 "photos": [url]}, token=sales1)
    check("带照片打卡成功", st == 200 and d["valid"] is True, str(d.get("distance_m")))
    st, d = req("/api/visits/today", token=sales1)
    check("打卡后进入已访", d["stats"]["done"] >= 1, "已访 %d 家" % d["stats"]["done"])

    # 新建的店也能打卡
    if new_id:
        st, d = req("/api/visits/checkin", "POST",
                    {"store_id": new_id, "lng": 121.5560, "lat": 29.9090, "accuracy_m": 8},
                    token=sales1)
        check("刚建的店立刻能打卡（距 0 米）", st == 200 and d["valid"] is True,
              str(d.get("distance_m")))

    # ============ C 回款核销 ============
    log("\n[C] 回款核销")
    req("/api/demo/setup", "POST", {}, token=TK)
    req("/api/demo/orders", "POST", {}, token=TK)
    st, d = req("/api/receivables", token=TK)
    ar = d["items"][0]
    st, d = req("/api/payments", "POST",
                {"order_no": ar["order_no"], "amount_cent": 100}, token=TK)
    check("未签收就收款 → 409", st == 409 and d.get("code") == "NOT_BILLED", d.get("code", ""))

    st, d = req("/api/demo/dispatch", "POST", {}, token=TK)
    dno = d["deliveries"][0]
    st, d = req("/api/deliveries/%s/sign" % dno, "POST", {"receiver": "收货人"}, token=TK)
    check("签收出账期", st == 200, d.get("due_date", ""))

    st, d = req("/api/receivables", token=TK)
    target = [x for x in d["items"] if x["status"] == "未收"][0]
    total = int(round(target["amount"] * 100))
    half = total // 2

    st, d = req("/api/payments", "POST",
                {"order_no": target["order_no"], "amount_cent": half, "method": "转账"},
                token=fin)
    check("登记部分回款 → 状态「部分」", st == 200 and d["status"] == "部分",
          "已收 ¥%s 剩 ¥%s" % (d.get("paid"), d.get("left")))
    st, d = req("/api/payments", "POST",
                {"order_no": target["order_no"], "amount_cent": total, "method": "转账"},
                token=fin)
    check("超额收款 → 400", st == 400 and d.get("code") == "OVER_PAY", d.get("code", ""))
    st, d = req("/api/payments", "POST",
                {"order_no": target["order_no"], "amount_cent": total - half, "method": "转账"},
                token=fin)
    check("补齐尾款 → 状态「已收」", st == 200 and d["status"] == "已收", str(d.get("left")))
    st, d = req("/api/payments", "POST",
                {"order_no": target["order_no"], "amount_cent": 1}, token=boss["token"])
    check("已收清后还能再收 → 400", st == 400, d.get("code", ""))
    st, d = req("/api/payments", "POST",
                {"order_no": target["order_no"], "amount_cent": 1}, token=sales1)
    check("业务员不能登记回款 → 403", st == 403, d.get("code", ""))

    # ============ D 登录锁定 ============
    log("\n[D] 登录锁定")
    for i in range(4):
        st, d = login("sales3", "wrong")
        check("第 %d 次错密码 → 401，提示还剩次数" % (i + 1), st == 401,
              d.get("message", "")[:24])
    st, d = login("sales3", "wrong")
    check("第 5 次 → 423 锁定", st == 423 and d.get("code") == "LOCKED", d.get("code", ""))
    st, d = login("sales3", PW)
    check("锁定期间正确密码也进不去", st == 423, d.get("code", ""))
    st, d = req("/api/auth/unlock", "POST", {"username": "sales3"}, token=TK)
    check("老板解锁", st == 200)
    st, d = login("sales3", PW)
    check("解锁后正常登录", st == 200 and d["user"]["username"] == "sales3")
    st, d = req("/api/auth/unlock", "POST", {"username": "sales3"}, token=sales1)
    check("业务员不能解锁 → 403", st == 403, d.get("code", ""))
    st, d = req("/api/auth/unlock", "POST", {"username": "nobody"}, token=TK)
    check("解锁不存在的账号 → 404", st == 404, d.get("code", ""))

    log("\n" + "=" * 78)
    log("结果：通过 %d ｜ 失败 %d" % (passed, failed))
    log("=" * 78)
    with io.open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    log("[saved] %s" % OUT)
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
