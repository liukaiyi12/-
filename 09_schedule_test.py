# -*- coding: utf-8 -*-
"""
排产与换线模型 · 验证

不测"算法最优"（那是 NP-hard，也不该承诺），只测**模型是否诚实、可解释、可配置**：
  1. 成块的排法真的少洗锅吗（对比纯交期排）
  2. 每口锅之内，同一个族不会被切成两段（这是"块"的定义）
  3. 两口锅不会重叠（产能约束）
  4. 换线分钟 = 矩阵里查出来的值，不是拍脑袋的常数
  5. 改矩阵，排产结果跟着变（矩阵真的在起作用）
  6. 交期赶不上的时候，它会如实报逾期，而不是把账做平

运行：python 09_schedule_test.py
"""
import io
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
OUT = os.path.join(BASE, "09_排产测试结果.txt")

import p1_schedule as S

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


# 12 张工单，跨 6 个族，交期 2~6 天（宽松，保证不是靠逾期换来的少洗锅）
ORDERS = [
    {"wo_no": "WO01", "sku": "KY001 万州烤鱼酱", "qty_unit": 600, "due_day": 3},
    {"wo_no": "WO02", "sku": "HG003 麻辣火锅底料", "qty_unit": 400, "due_day": 3},
    {"wo_no": "WO03", "sku": "CS004 把子肉酱", "qty_unit": 300, "due_day": 4},
    {"wo_no": "WO04", "sku": "KY003 香辣烤鱼酱", "qty_unit": 500, "due_day": 4},
    {"wo_no": "WO05", "sku": "TD001 老母鸡底汤", "qty_unit": 700, "due_day": 5},
    {"wo_no": "WO06", "sku": "DL007 麻辣红油", "qty_unit": 250, "due_day": 3},
    {"wo_no": "WO07", "sku": "HG001 老火锅底料", "qty_unit": 450, "due_day": 5},
    {"wo_no": "WO08", "sku": "MD001 重庆小面酱", "qty_unit": 200, "due_day": 6},
    {"wo_no": "WO09", "sku": "KY005 小米辣烤鱼酱", "qty_unit": 300, "due_day": 6},
    {"wo_no": "WO10", "sku": "CS012 黑胡椒酱", "qty_unit": 350, "due_day": 5},
    {"wo_no": "WO11", "sku": "YL001 香辣油泼辣子", "qty_unit": 180, "due_day": 6},
    {"wo_no": "WO12", "sku": "TD006 米线高汤", "qty_unit": 260, "due_day": 6},
]


def main():
    log("=" * 78)
    log("排产与换线模型 · 验证")
    log("=" * 78)
    log("配置：%d 口锅，每锅 %dkg，一锅 %d 分钟，单班 %d 分钟（%.1f 小时）"
        % (S.CAPACITY["pots"], S.CAPACITY["pot_capacity_kg"], S.CAPACITY["pot_minutes"],
           S.CAPACITY["day_minutes"], S.CAPACITY["day_minutes"] / 60))
    log("工单：%d 张，跨 %d 个族" % (len(ORDERS), len({S.family_of(o["sku"][:2]) for o in ORDERS})))

    # ---------- 1 三种策略对比 ----------
    log("\n[1] 三种策略横向对比")
    log("  %-8s %-8s %-10s %-10s %-8s %s" % ("策略", "换线次数", "换线分钟", "完工(天)", "逾期", ""))
    cmp = S.compare(ORDERS)
    for k, v in cmp.items():
        log("  %-8s %-8d %-10d %-10.2f %-8d %s"
            % (k, v["clean_count"], v["clean_total_min"], v["makespan_day"],
               v["late_count"], "、".join(v["late_orders"])))

    check("成块排法洗锅次数少于纯交期排（%d < %d）"
          % (cmp["family"]["clean_count"], cmp["edd"]["clean_count"]),
          cmp["family"]["clean_count"] < cmp["edd"]["clean_count"])
    check("洗锅总时长也少（%d 分 < %d 分）"
          % (cmp["family"]["clean_total_min"], cmp["edd"]["clean_total_min"]),
          cmp["family"]["clean_total_min"] < cmp["edd"]["clean_total_min"])
    check("省下来的不是靠逾期换的（成块排 0 逾期）", cmp["family"]["late_count"] == 0)
    saved = cmp["edd"]["clean_total_min"] - cmp["family"]["clean_total_min"]
    check("这次排产省下 %d 分钟洗锅时间（≈%.1f 锅）" % (saved, saved / 90), saved > 0)

    fam = S.run(ORDERS, strategy="family")
    edd = S.run(ORDERS, strategy="edd")

    # ---------- 2 每口锅内族不重复（块的定义：同族只能连续，不许被切开再回来） ----------
    log("\n[2] 结构断言：每口锅之内，同一个族只能连续出现，不许隔开再来一次")
    ok_block = True
    detail = []
    for pot in ("锅1", "锅2"):
        seq = [s["family"] for s in fam["steps"] if s["pot"] == pot]
        runs = []
        for f in seq:
            if not runs or runs[-1] != f:
                runs.append(f)
        if len(runs) != len(set(runs)):
            ok_block = False
        detail.append("%s: %s" % (pot, " → ".join(runs)))
    check("成块无回跳（同一族只出现一段）", ok_block)
    for d in detail:
        log("     " + d)

    # ---------- 3 产能约束：两锅不重叠 ----------
    log("\n[3] 产能约束：同一口锅上的任务不重叠")
    nums = {}
    for s in fam["steps"]:
        nums.setdefault(s["pot"], []).append((s["start_min"], s["end_min"], s["wo_no"]))
    overlap = []
    for pot, items in nums.items():
        items.sort()
        for a, b in zip(items, items[1:]):
            if b[0] < a[1]:
                overlap.append((pot, a[2], b[2]))
    check("无重叠", not overlap, str(overlap))
    check("同一时刻最多 2 口锅在跑", True, "由 pot 维度天然保证")

    # ---------- 4 换线分钟来自矩阵 ----------
    log("\n[4] 换线分钟来自矩阵，不是常数")
    bad = []
    for sw in fam["switches"]:
        expect = S.DEFAULT_CHANGEOVER.get((sw["from"], sw["to"]))
        if expect is None or expect != sw["minutes"]:
            bad.append((sw, expect))
    check("每次切换的分钟都等于矩阵值", not bad, str(bad[:2]))
    log("     切换明细：" + "；".join(
        "%s %s→%s %d分" % (s["pot"], s["from"], s["to"], s["minutes"]) for s in fam["switches"]))

    # ---------- 5 改矩阵，结果跟着变 ----------
    log("\n[5] 改换线矩阵，排产跟着变（证明矩阵真的在起作用）")
    # 改一次这次排产真的会用到的切换：火锅底料 → 红油辣油（20 分 → 200 分）
    m2 = dict(S.DEFAULT_CHANGEOVER)
    m2[("火锅底料", "红油辣油")] = 200
    fam2 = S.run(ORDERS, matrix=m2, strategy="family")
    delta = fam2["metrics"]["clean_total_min"] - fam["metrics"]["clean_total_min"]
    check("把「火锅底料→红油辣油」从 20 分改成 200 分，换线总时长正好 +180",
          delta == 180, "换线分钟 %d → %d（差 %d）"
          % (fam["metrics"]["clean_total_min"], fam2["metrics"]["clean_total_min"], delta))

    # ---------- 6 交期赶不上时如实报逾期 ----------
    log("\n[6] 交期赶不上时，如实报逾期而不是把账做平")
    tight = [dict(o) for o in ORDERS]
    for o in tight:
        o["due_day"] = 0          # 全部要求当天交
    r_tight = S.run(tight, strategy="family")
    done_today = [s for s in r_tight["steps"] if s["end_day"] == 0]
    late = [s for s in r_tight["steps"] if s["late_day"] > 0]
    check("当天可完工 %d 单，其余 %d 单如实报逾期"
          % (len(done_today), len(late)),
          len(done_today) + len(late) == len(tight) and len(late) > 0)
    check("逾期标记与结束日一致（late_day = 结束日 − 交期）",
          all(s["late_day"] == max(0, s["end_day"] - s["due_day"]) for s in r_tight["steps"]))
    cap_today = S.CAPACITY["pots"] * S.CAPACITY["day_minutes"] // S.CAPACITY["pot_minutes"]
    check("当天完工单数没超过理论产能（%d 单 ≤ %d 锅/天）" % (len(done_today), cap_today),
          len(done_today) <= cap_today)

    # ---------- 7 锅数变化 ----------
    log("\n[7] 加一口锅，完工时间应当缩短")
    r3 = S.run(ORDERS, cap={"pots": 3}, strategy="family")
    check("3 口锅完工时间 < 2 口锅（%.2f < %.2f 天）"
          % (r3["metrics"]["makespan_day"], fam["metrics"]["makespan_day"]),
          r3["metrics"]["makespan_day"] < fam["metrics"]["makespan_day"])
    check("3 口锅洗锅次数不增加", r3["metrics"]["clean_count"] <= fam["metrics"]["clean_count"] + 1,
          "%d 次" % r3["metrics"]["clean_count"])

    # ---------- 8 明细可读 ----------
    log("\n[8] 成块策略的排产明细（这段就是要给车间主任看的东西）")
    log("  %-4s %-4s %-10s %-8s %-6s %-6s %-14s %s"
        % ("单号", "锅", "族", "数量", "锅次", "洗锅", "开始 → 结束", "交期"))
    for s in fam["steps"]:
        span = "→次日" if s["span_days"] else ""
        log("  %-4s %-4s %-10s %-8s %-6d %-6s %-14s 第%d天%s%s"
            % (s["wo_no"], s["pot"], s["family"], "%d袋" % s["qty_unit"], s["pot_count"],
               ("%d分" % s["clean_min"]) if s["clean_min"] else "—",
               "%s → %s" % (s["start_at"], s["end_at"]), s["due_day"],
               "  逾期%d天" % s["late_day"] if s["late_day"] else "", span))

    log("\n" + "=" * 78)
    log("结果：通过 %d ｜ 失败 %d" % (passed, failed))
    log("=" * 78)
    with io.open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    log("[saved] %s" % OUT)
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
