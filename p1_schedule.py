# -*- coding: utf-8 -*-
"""
排产与换线模型（纯逻辑，无 IO，可单独测）。

调味品车间的核心矛盾不是"算不出排程"，而是**换线成本**：
换一个香型/颜色就得洗锅清线，30~55 分钟白扔，还冲掉当班产量。
所以排产要解决的是：**在满足交期前提下，让每口锅的连续生产尽量同族**。

关键认知（第一版踩过的坑）：
  「把工单按交期排好，再依次塞给最早空闲的锅」——这样两台锅会互相插花，
  每口锅每单都换族，洗锅次数反而最多。
  正确做法是**先按族成块，再整块分给锅**：一口锅吃下整个族，
  锅的换线次数 = 该锅分到的族数 − 1，与工单数无关。

三个策略（都跑，用数据说话）：
  edd    —— 纯交期排 + 塞给最早空闲锅（多数工厂现状，洗锅最多）
  family —— 按族成块、整块分配给负载最低的锅（洗锅最少，可能略拖工期）
  smart  —— 成块分配，但选锅时把「逾期风险」也算进去（急单不会被块压后）
"""
import math
from datetime import date, datetime, timedelta

# 品类 → 品类族。族相同 = 可以直接连做，不用洗锅。
FAMILY_OF = {
    "HG": "火锅底料",
    "KY": "烤鱼",
    "TD": "汤底",
    "CS": "炒烧酱",
    "LB": "炒烧酱",
    "DL": "红油辣油",
    "YL": "红油辣油",
    "SK": "小吃面点",
    "MD": "小吃面点",
    "BL": "小吃面点",
    "LX": "小吃面点",
}

FAMILIES = ["火锅底料", "烤鱼", "汤底", "炒烧酱", "红油辣油", "小吃面点"]

# 换线矩阵（分钟）。对角为 0；允许不对称（洗锅方向不同耗时不同）。
DEFAULT_CHANGEOVER = {
    ("火锅底料", "烤鱼"): 25, ("火锅底料", "汤底"): 40, ("火锅底料", "炒烧酱"): 35,
    ("火锅底料", "红油辣油"): 20, ("火锅底料", "小吃面点"): 45,
    ("烤鱼", "火锅底料"): 25, ("烤鱼", "汤底"): 40, ("烤鱼", "炒烧酱"): 35,
    ("烤鱼", "红油辣油"): 20, ("烤鱼", "小吃面点"): 40,
    ("汤底", "火锅底料"): 45, ("汤底", "烤鱼"): 45, ("汤底", "炒烧酱"): 55,
    ("汤底", "红油辣油"): 50, ("汤底", "小吃面点"): 45,
    ("炒烧酱", "火锅底料"): 35, ("炒烧酱", "烤鱼"): 35, ("炒烧酱", "汤底"): 55,
    ("炒烧酱", "红油辣油"): 30, ("炒烧酱", "小吃面点"): 30,
    ("红油辣油", "火锅底料"): 20, ("红油辣油", "烤鱼"): 20, ("红油辣油", "汤底"): 50,
    ("红油辣油", "炒烧酱"): 30, ("红油辣油", "小吃面点"): 40,
    ("小吃面点", "火锅底料"): 45, ("小吃面点", "烤鱼"): 40, ("小吃面点", "汤底"): 45,
    ("小吃面点", "炒烧酱"): 30, ("小吃面点", "红油辣油"): 40,
}

CAPACITY = {
    "pots": 2,               # 几口锅
    "pot_capacity_kg": 500,  # 每锅产量（1 袋 ≈ 1kg）
    "pot_minutes": 90,       # 一锅占用时间（投料+炒制+出料）
    "day_minutes": 480,      # 每天有效工时（8 小时单班）
    "start_hour": 8,
}


def family_of(category_code):
    return FAMILY_OF.get(category_code, "小吃面点")


def changeover(matrix, a, b):
    if a is None or b is None or a == b:
        return 0
    return matrix.get((a, b), 45)


def pot_count(qty_unit, pot_capacity_kg=500):
    return max(1, math.ceil(qty_unit / pot_capacity_kg))


def _dt(minutes, base, cap):
    day, rest = divmod(minutes, cap["day_minutes"])
    return base + timedelta(days=day, minutes=rest)


def _normalize(orders, cap):
    out = []
    for o in orders:
        o = dict(o)
        o.setdefault("family", family_of(str(o.get("sku", ""))[:2]))
        o["pot_count"] = o.get("pot_count") or pot_count(o["qty_unit"], cap["pot_capacity_kg"])
        o["due_day"] = int(o.get("due_day", 3))
        out.append(o)
    return out


# ============================================================
# 策略 1：纯交期 + 最早空闲锅（多数工厂的现状）
# ============================================================
def _plan_edd(orders, matrix, cap):
    queues = {i: [] for i in range(cap["pots"])}
    free = {i: 0 for i in range(cap["pots"])}
    prev = {i: None for i in range(cap["pots"])}
    for o in sorted(orders, key=lambda x: (x["due_day"], x["wo_no"])):
        p = min(free, key=lambda k: free[k])
        clean = changeover(matrix, prev[p], o["family"])
        start = free[p] + clean
        dur = o["pot_count"] * cap["pot_minutes"]
        queues[p].append((o, clean, start, start + dur))
        free[p] = start + dur
        prev[p] = o["family"]
    return queues


# ============================================================
# 策略 2/3：按族成块 → 整块分给锅
# ============================================================
def _blocks(orders):
    g = {}
    for o in orders:
        g.setdefault(o["family"], []).append(o)
    for f in g:
        g[f].sort(key=lambda o: (o["due_day"], o["wo_no"]))
    return g


def _plan_blocked(orders, matrix, cap, risk_weight=0.0):
    """
    把每个族当成一整块，依次分给"分到这块之后最不亏"的那口锅。
    risk_weight > 0 时，逾期风险会参与选锅（smart 策略）。
    """
    blocks = _blocks(orders)
    fams = sorted(blocks, key=lambda f: (min(o["due_day"] for o in blocks[f]), f))
    queues = {i: [] for i in range(cap["pots"])}
    free = {i: 0 for i in range(cap["pots"])}
    prev = {i: None for i in range(cap["pots"])}
    fam_pot = {}

    for f in fams:
        tasks = blocks[f]
        block_min = sum(o["pot_count"] * cap["pot_minutes"] for o in tasks)
        best, best_score = None, None
        for p in range(cap["pots"]):
            clean = changeover(matrix, prev[p], f)
            end = free[p] + clean + block_min
            late = max(0, end // cap["day_minutes"]
                       - min(o["due_day"] for o in tasks))
            score = (free[p] + clean) + risk_weight * late * cap["day_minutes"]
            if best_score is None or score < best_score:
                best, best_score = p, score
        p = best
        clean = changeover(matrix, prev[p], f)
        cur = free[p] + clean
        for o in tasks:
            dur = o["pot_count"] * cap["pot_minutes"]
            queues[p].append((o, clean if cur == free[p] + clean else 0, cur, cur + dur))
            cur += dur
            clean = 0   # 同族连做不再洗锅
        free[p] = cur
        prev[p] = f
        fam_pot[f] = p
    return queues, fam_pot


# ============================================================
# 汇总
# ============================================================
def run(orders, matrix=None, cap=None, strategy="family", base_day=None):
    matrix = matrix or DEFAULT_CHANGEOVER
    cap = dict(CAPACITY, **(cap or {}))
    base = datetime.combine(base_day or date(2026, 11, 10), datetime.min.time()).replace(
        hour=cap["start_hour"])
    orders = _normalize(orders, cap)

    if strategy == "edd":
        queues = _plan_edd(orders, matrix, cap)
        fam_pot = {}
    elif strategy == "family":
        queues, fam_pot = _plan_blocked(orders, matrix, cap, risk_weight=0.0)
    elif strategy == "smart":
        queues, fam_pot = _plan_blocked(orders, matrix, cap, risk_weight=2.0)
    else:
        raise ValueError("未知策略：%s" % strategy)

    steps, switches = [], []
    for p, items in queues.items():
        last_f = None
        for o, clean, start, end in sorted(items, key=lambda x: x[2]):
            steps.append({
                "wo_no": o["wo_no"],
                "sku": o.get("sku", ""),
                "family": o["family"],
                "qty_unit": o["qty_unit"],
                "pot_count": o["pot_count"],
                "pot": "锅%d" % (p + 1),
                "clean_min": clean,
                "from_family": last_f,
                "start_min": start,
                "end_min": end,
                "start_at": _dt(start, base, cap).strftime("%m-%d %H:%M"),
                "end_at": _dt(end, base, cap).strftime("%m-%d %H:%M"),
                "due_day": o["due_day"],
                "end_day": end // cap["day_minutes"],
                "span_days": end // cap["day_minutes"] - start // cap["day_minutes"],
                "late_day": max(0, end // cap["day_minutes"] - o["due_day"]),
            })
            if clean > 0:
                switches.append({"pot": "锅%d" % (p + 1), "from": last_f,
                                 "to": o["family"], "minutes": clean})
            last_f = o["family"]

    steps.sort(key=lambda s: s["start_min"])
    for i, s in enumerate(steps, 1):
        s["seq"] = i

    late = [s for s in steps if s["late_day"] > 0]
    makespan = max((s["end_min"] for s in steps), default=0)
    return {
        "strategy": strategy,
        "steps": steps,
        "switches": switches,
        "fam_pot": fam_pot,
        "metrics": {
            "orders": len(steps),
            "clean_count": len(switches),
            "clean_total_min": sum(s["minutes"] for s in switches),
            "makespan_min": makespan,
            "makespan_day": round(makespan / cap["day_minutes"], 2),
            "late_count": len(late),
            "late_orders": [s["wo_no"] for s in late],
            "pots": cap["pots"],
        },
    }


def compare(orders, matrix=None, cap=None, base_day=None):
    """三种策略横向对比——用数据说明为什么不能只按交期排。"""
    return {s: run(orders, matrix, cap, s, base_day)["metrics"]
            for s in ("edd", "family", "smart")}
