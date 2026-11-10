# -*- coding: utf-8 -*-
"""
P1 数据内核 demo —— 验证「主数据留空」时系统怎么降级。

核心结论：
  留空 ≠ 全部不可用，也 ≠ 瞎猜默认值。
  每个字段按「对哪个动作必填」分级；缺了就拦住那个动作，
  并直接告诉你去 02_商品主数据表 的第几列补。

运行：python 05_core_demo.py
输出：控制台 + _work/core_run.txt
"""
import io
import os
import sqlite3
from datetime import date

from openpyxl import load_workbook

BASE = r"C:\Users\13780\Desktop\deepseek工作文件\新平食品系统"
XLSX = os.path.join(BASE, "02_商品主数据表_v1.xlsx")
OUTFILE = os.path.join(BASE, "05_运行输出.txt")

TODAY = date(2026, 11, 10)   # 固定，保证输出可复现
buf = []


def p(s=""):
    buf.append(str(s))


# ============================================================
# 能力表：字段对「哪个动作」必填（这是留空策略的全部）
#   key = 动作, value = [(字段, 中文名, 表格列)]
# ============================================================
CAP = {
    "order_by_carton": [("units_per_carton", "装箱规格", "H")],
    "price": [("price_cent", "建议经销价", "P")],
    "inbound": [("shelf_life_month", "保质期(月)", "J"),
                ("storage_cond", "储存条件", "K")],
    "label_print": [("ingredient_text", "配料表", "N"),
                    ("sc_no", "SC证号", "M"),
                    ("std_code", "执行标准", "L")],
}


ACTION_CN = {
    "order_by_carton": "按箱下单",
    "price": "定价",
    "inbound": "入库",
    "label_print": "打印标签",
    "下单": "下单",
}


class MissingMasterData(Exception):
    """缺主数据。带出来源列，错误信息本身就是操作指引。"""

    def __init__(self, action, missing, subject=""):
        self.action = action
        self.missing = missing          # [(field, 中文名, 列)]
        self.subject = subject
        super().__init__(self._msg())

    def _msg(self):
        act = ACTION_CN.get(self.action, self.action)
        head = "缺少主数据（动作：%s%s）" % (act, "，" + self.subject if self.subject else "")
        lines = [head]
        for f, cn, col in self.missing:
            lines.append("    · %s（%s）→ 02_商品主数据表 %s 列" % (cn, f, col))
        return "\n".join(lines)


def require(sku, action):
    """按动作校验；返回缺失清单。"""
    if not sku:
        raise ValueError("SKU 不存在")
    need = CAP[action]
    miss = [(f, cn, col) for f, cn, col in need if sku[f] in (None, "")]
    if miss:
        raise MissingMasterData(action, miss, "%s %s" % (sku["name"], sku["sku_code"]))
    return []


# ============================================================
# 建库
# ============================================================
SCHEMA = """
CREATE TABLE category(code TEXT PRIMARY KEY, name TEXT NOT NULL, typical_client TEXT);

CREATE TABLE spu(
  id TEXT PRIMARY KEY, code TEXT NOT NULL UNIQUE,
  category_code TEXT NOT NULL REFERENCES category(code),
  name TEXT NOT NULL);

CREATE TABLE sku(
  id TEXT PRIMARY KEY,
  sku_code TEXT NOT NULL UNIQUE,
  spu_id TEXT NOT NULL REFERENCES spu(id),
  name TEXT NOT NULL,
  net_weight_g INTEGER NOT NULL,
  pack_type TEXT NOT NULL,
  units_per_carton INTEGER,          -- 留空：待工厂填 H 列
  shelf_life_month INTEGER,          -- 留空：待填 J
  storage_cond TEXT,                 -- 留空：待填 K
  price_cent INTEGER,                -- 留空：待填 P
  ingredient_text TEXT,              -- 留空：待填 N
  sc_no TEXT,                        -- 留空：待填 M
  std_code TEXT,                     -- 留空：待填 L
  moq_carton INTEGER DEFAULT 1,
  status TEXT NOT NULL DEFAULT '在产',
  is_custom INTEGER NOT NULL DEFAULT 0,
  CHECK (units_per_carton IS NULL OR units_per_carton > 0),
  CHECK (price_cent IS NULL OR price_cent >= 0));

CREATE TABLE store(
  id TEXT PRIMARY KEY, name TEXT NOT NULL,
  settle_type TEXT NOT NULL DEFAULT 'm30',
  credit_limit_cent INTEGER NOT NULL DEFAULT 0,
  owner_user TEXT);

CREATE TABLE contract_price(
  store_id TEXT NOT NULL REFERENCES store(id),
  sku_id TEXT NOT NULL REFERENCES sku(id),
  price_cent INTEGER NOT NULL,
  PRIMARY KEY (store_id, sku_id));

CREATE TABLE batch(
  id TEXT PRIMARY KEY,
  sku_id TEXT NOT NULL REFERENCES sku(id),
  batch_no TEXT NOT NULL,
  prod_date TEXT NOT NULL,
  expire_date TEXT NOT NULL,
  qty_on_hand INTEGER NOT NULL DEFAULT 0,
  qty_locked INTEGER NOT NULL DEFAULT 0,
  unit_cost_cent INTEGER,
  qc_status TEXT NOT NULL DEFAULT '合格',
  UNIQUE (sku_id, batch_no),
  CHECK (expire_date > prod_date));

CREATE TABLE order_h(
  id TEXT PRIMARY KEY, order_no TEXT NOT NULL UNIQUE,
  store_id TEXT NOT NULL REFERENCES store(id),
  status TEXT NOT NULL,
  amount_cent INTEGER NOT NULL,
  channel TEXT NOT NULL DEFAULT 'store',
  created_at TEXT NOT NULL);

CREATE TABLE order_l(
  id TEXT PRIMARY KEY,
  order_id TEXT NOT NULL REFERENCES order_h(id),
  sku_id TEXT NOT NULL REFERENCES sku(id),
  qty_unit INTEGER NOT NULL,
  qty_carton INTEGER NOT NULL,
  price_cent INTEGER NOT NULL,
  batch_no TEXT);

CREATE TABLE stock_move(
  id TEXT PRIMARY KEY,
  biz_type TEXT NOT NULL,      -- PI 生产入库 / SO 销售出库
  biz_no TEXT NOT NULL,
  sku_id TEXT NOT NULL,
  batch_no TEXT NOT NULL,
  qty INTEGER NOT NULL,
  occurred_at TEXT NOT NULL);

CREATE TABLE receivable(
  id TEXT PRIMARY KEY,
  store_id TEXT NOT NULL,
  order_no TEXT NOT NULL,
  amount_cent INTEGER NOT NULL,
  paid_cent INTEGER NOT NULL DEFAULT 0,
  due_date TEXT NOT NULL,
  status TEXT NOT NULL);
"""


def build(con):
    con.executescript(SCHEMA)

    wb = load_workbook(XLSX, data_only=True)
    ws = wb["01 商品主数据"]
    rows, r = [], 3
    while ws.cell(r, 1).value:
        rows.append({
            "sku_code": ws.cell(r, 1).value,
            "cat": ws.cell(r, 2).value,
            "name": ws.cell(r, 3).value,
            "spu": ws.cell(r, 5).value,
            "net": ws.cell(r, 6).value,
            "pack": ws.cell(r, 7).value,
            # H/I/J/K/L/M/N/P/... 全部留空，按原样 NULL 入库
        })
        r += 1

    # 品类字典
    wsd = wb["02 品类字典"]
    cats = {}
    for i in range(3, wsd.max_row + 1):
        code, name = wsd.cell(i, 1).value, wsd.cell(i, 2).value
        if code and name and len(str(code)) == 2:
            cats[code] = (name, wsd.cell(i, 5).value)
    for code, (name, cli) in cats.items():
        con.execute("INSERT INTO category VALUES (?,?,?)", (code, name, cli))

    for d in rows:
        cat_code = None
        for code, (name, _) in cats.items():
            if name == d["cat"]:
                cat_code = code
                break
        con.execute("INSERT INTO spu VALUES (?,?,?,?)",
                    (d["spu"], d["spu"], cat_code, d["name"]))
        con.execute(
            """INSERT INTO sku(id, sku_code, spu_id, name, net_weight_g, pack_type,
                               units_per_carton, shelf_life_month, storage_cond,
                               price_cent, ingredient_text, sc_no, std_code, moq_carton, status)
               VALUES (?,?,?,?,?,?, NULL,NULL,NULL, NULL,NULL,NULL,NULL,1,'在产')""",
            (d["sku_code"], d["sku_code"], d["spu"], d["name"], d["net"], d["pack"]))

    con.execute("INSERT INTO store VALUES ('S001','姜山老灶火锅','m30',150000,'张伟')")
    return len(rows), con.execute("SELECT COUNT(*) FROM category").fetchone()[0]


# ============================================================
# 业务动作
# ============================================================
def get_sku(con, code):
    con.row_factory = sqlite3.Row
    r = con.execute("SELECT * FROM sku WHERE sku_code=?", (code,)).fetchone()
    con.row_factory = None
    return dict(r) if r else None


def resolve_price(con, store_id, sku):
    row = con.execute("SELECT price_cent FROM contract_price WHERE store_id=? AND sku_id=?",
                      (store_id, sku["id"])).fetchone()
    if row:
        return row[0], "合同价"
    if sku["price_cent"] is not None:
        return sku["price_cent"], "标准价"
    raise MissingMasterData("price", CAP["price"], "%s %s" % (sku["name"], sku["sku_code"]))


def submit_order(con, store_id, cart, channel="store"):
    """cart = [(sku_code, cartons)]  —— 整单校验，不通过则整单不落库。"""
    # 1) 逐行校验主数据
    lines = []
    for code, cartons in cart:
        sku = get_sku(con, code)
        if not sku:
            raise ValueError("SKU 不存在：%s" % code)
        if sku["status"] != "在产":
            raise ValueError("%s 已停产，不可下单" % sku["name"])
        # 下单需要的字段一次说清：箱规 + 价格（有合同价时价格豁免）
        miss = [(f, cn, col) for f, cn, col in CAP["order_by_carton"]
                if sku[f] in (None, "")]
        has_contract = con.execute(
            "SELECT 1 FROM contract_price WHERE store_id=? AND sku_id=?",
            (store_id, sku["id"])).fetchone()
        if not has_contract:
            miss += [(f, cn, col) for f, cn, col in CAP["price"] if sku[f] in (None, "")]
        if miss:
            raise MissingMasterData("下单", miss, "%s %s" % (sku["name"], sku["sku_code"]))
        if sku["is_custom"]:
            raise ValueError("%s 是定制 SKU，不可混入通用订单" % sku["name"])
        if cartons < sku["moq_carton"]:
            raise ValueError("%s 起订 %d 箱" % (sku["name"], sku["moq_carton"]))
        price, src = resolve_price(con, store_id, sku)
        qty_unit = cartons * sku["units_per_carton"]
        lines.append({"sku": sku, "cartons": cartons, "qty_unit": qty_unit,
                      "price": price, "src": src})

    # 2) 库存 FEFO 预占
    alloc = []
    for ln in lines:
        need = ln["qty_unit"]
        rows = con.execute(
            """SELECT batch_no, expire_date, qty_on_hand - qty_locked AS avail
               FROM batch WHERE sku_id=? AND qc_status='合格' AND qty_on_hand - qty_locked > 0
               ORDER BY expire_date ASC""", (ln["sku"]["id"],)).fetchall()
        got, plan = 0, []
        for bno, exp, avail in rows:
            if got >= need:
                break
            take = min(avail, need - got)
            plan.append((bno, exp, take))
            got += take
        if got < need:
            raise ValueError("%s 库存不足：需 %d 袋，可用 %d 袋（缺 %d 袋）"
                             % (ln["sku"]["name"], need, got, need - got))
        alloc.append(plan)

    # 3) 信用额度
    total = sum(l["cartons"] * l["sku"]["units_per_carton"] * l["price"] for l in lines)
    used = con.execute("SELECT COALESCE(SUM(amount_cent - paid_cent),0) FROM receivable "
                       "WHERE store_id=? AND status<>'已收'", (store_id,)).fetchone()[0]
    limit = con.execute("SELECT credit_limit_cent FROM store WHERE id=?", (store_id,)).fetchone()[0]
    if used + total > limit:
        raise ValueError("超出信用额度：已占用 ¥%.2f + 本单 ¥%.2f > 额度 ¥%.2f"
                         % (used / 100, total / 100, limit / 100))

    # 4) 落库：订单 + 行 + 锁库存 + 流水
    seq = con.execute("SELECT COUNT(*) FROM order_h").fetchone()[0] + 1
    order_no = "SO%s-%04d" % (TODAY.strftime("%y%m%d"), seq)
    oid = "O%d" % seq
    con.execute("INSERT INTO order_h VALUES (?,?,?,?,?,?,?)",
                (oid, order_no, store_id, "已确认", total, channel, TODAY.isoformat()))
    for i, (ln, plan) in enumerate(zip(lines, alloc)):
        first_batch = plan[0][0]
        con.execute("INSERT INTO order_l VALUES (?,?,?,?,?,?,?)",
                    ("L%d-%d" % (seq, i), oid, ln["sku"]["id"], ln["qty_unit"],
                     ln["cartons"], ln["price"], first_batch))
        for bno, exp, take in plan:
            con.execute("UPDATE batch SET qty_locked = qty_locked + ? WHERE sku_id=? AND batch_no=?",
                        (take, ln["sku"]["id"], bno))
            con.execute("INSERT INTO stock_move VALUES (?,?,?,?,?,?,?)",
                        ("M%s-%d-%s" % (seq, i, bno), "SO", order_no, ln["sku"]["id"],
                         bno, -take, TODAY.isoformat()))
    con.execute("INSERT INTO receivable VALUES (?,?,?,?,0,?,'未收')",
                ("R%d" % seq, store_id, order_no, total, "2026-12-10"))
    con.commit()
    return {"order_no": order_no, "total": total, "alloc": alloc}


def stock_in(con, sku_code, batch_no, qty, prod, expire, cost_cent):
    """入库：必须先有保质期与储存条件，否则算不出到期日。"""
    sku = get_sku(con, sku_code)
    require(sku, "inbound")
    con.execute("INSERT INTO batch VALUES (?,?,?,?,?,?,0,?, '合格')",
                ("B-" + batch_no, sku["id"], batch_no, prod, expire, qty, cost_cent))
    con.execute("INSERT INTO stock_move VALUES (?,?,?,?,?,?,?)",
                ("M-" + batch_no, "PI", batch_no, sku["id"], batch_no, qty, TODAY.isoformat()))
    con.commit()


def print_label(con, sku_code):
    """贴标：缺配料表/SC/执行标准就不许打。"""
    sku = get_sku(con, sku_code)
    require(sku, "label_print")
    return "%s 标签可打印" % sku["name"]


def completeness(con):
    q = """SELECT
      (SELECT COUNT(*) FROM sku WHERE status='在产') AS total,
      (SELECT COUNT(*) FROM sku WHERE status='在产' AND units_per_carton IS NOT NULL) AS cart,
      (SELECT COUNT(*) FROM sku WHERE status='在产' AND units_per_carton IS NOT NULL
          AND price_cent IS NOT NULL) AS orderable,
      (SELECT COUNT(*) FROM sku WHERE status='在产' AND shelf_life_month IS NOT NULL
          AND storage_cond IS NOT NULL) AS inbound,
      (SELECT COUNT(*) FROM sku WHERE status='在产' AND ingredient_text IS NOT NULL
          AND sc_no IS NOT NULL AND std_code IS NOT NULL) AS label"""
    return con.execute(q).fetchone()


def show(label, fn, fmt=None):
    """跑一步，把异常变成可读结论。fmt 用于把返回值变成一句人话。"""
    p()
    p(label)
    try:
        res = fn()
        p("  [通过] " + (fmt(res) if fmt else str(res)))
        return True, res
    except MissingMasterData as e:
        p("  [拦截] " + str(e).replace("\n", "\n  "))
        return False, None
    except ValueError as e:
        p("  [拦截] " + str(e))
        return False, None


def main():
    p("=" * 78)
    p("P1 数据内核 · 留空字段的降级验证")
    p("=" * 78)

    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys=ON")
    n_sku, n_cat = build(con)

    p()
    p("[步骤 0] 建库 + 从 02_商品主数据表 导入")
    p("  品类 %d 个，SKU %d 个；箱规/保质期/储存/价格/配料表/SC/执行标准 全部按原表 NULL 入库" % (n_cat, n_sku))
    c = completeness(con)
    p("  完备度：在产 %d ｜ 有箱规 %d ｜ 可下单 %d ｜ 可入库 %d ｜ 可贴标 %d"
      % (c[0], c[1], c[2], c[3], c[4]))
    p("  也就是说：现在这 72 个 SKU 一单也下不了 —— 这是对的，缺箱规就该拦住。")

    KY = "KY001-1K-B-1"   # 万州烤鱼酱

    show("[步骤 1] 门店下单：万州烤鱼酱 × 2 箱",
         lambda: submit_order(con, "S001", [(KY, 2)]))

    show("[步骤 2] 不补数据，改打标签（同一张表，另一个动作）",
         lambda: print_label(con, KY))

    p()
    p("[步骤 3] 工厂回填第一项：箱规 = 20 袋/箱（H 列）")
    con.execute("UPDATE sku SET units_per_carton=20 WHERE sku_code=?", (KY,))
    con.commit()
    show("  再下单：万州烤鱼酱 × 2 箱", lambda: submit_order(con, "S001", [(KY, 2)]))

    p()
    p("[步骤 4] 工厂回填第二项：建议经销价 = ¥8.40/袋（P 列；× 20 袋/箱 = ¥168/箱）")
    con.execute("UPDATE sku SET price_cent=840 WHERE sku_code=?", (KY,))
    con.commit()
    show("  再下单：万州烤鱼酱 × 2 箱", lambda: submit_order(con, "S001", [(KY, 2)]))

    p()
    p("[步骤 5] 现在缺的是货，不是数据：补库存（但入库还要 J、K 两列）")
    show("  先试入库 100 袋（不填保质期/储存条件）",
         lambda: stock_in(con, KY, "P2611011-A-001", 100, "2026-11-01", "2027-05-01", 9800))

    p()
    p("[步骤 6] 回填第三、四项：保质期 6 个月、常温（J、K 列）→ 入库两批（不同到期日）")
    con.execute("UPDATE sku SET shelf_life_month=6, storage_cond='常温' WHERE sku_code=?", (KY,))
    con.commit()
    # 先入的先过期：混两批，验证 FEFO
    stock_in(con, KY, "P2611051-A-002", 100, "2026-11-05", "2027-05-05", 9800)
    stock_in(con, KY, "P2611011-A-001", 40, "2026-11-01", "2027-05-01", 9700)
    p("  入库 100 袋（到期 2027-05-05）+ 40 袋（到期 2027-05-01）—— 故意让后生产的先到期，看系统认不认账")
    c = completeness(con)
    p("  完备度：在产 %d ｜ 有箱规 %d ｜ 可下单 %d ｜ 可入库 %d ｜ 可贴标 %d"
      % (c[0], c[1], c[2], c[3], c[4]))

    ok, res = show("  再下单：万州烤鱼酱 × 4 箱（= 80 袋）",
                   lambda: submit_order(con, "S001", [(KY, 4)]),
                   fmt=lambda r: "订单号 %s ｜ 金额 ¥%.2f" % (r["order_no"], r["total"] / 100))
    if ok:
        alloc = res["alloc"]
        p("  FEFO 分配：")
        for bno, exp, take in alloc[0]:
            p("     %s（到期 %s）取 %d 袋" % (bno, exp, take))
        p("  → 先到期的先出，这是效期管理唯一靠谱的写法。")

    show("  再下 10 箱（试信用额度）", lambda: submit_order(con, "S001", [(KY, 10)]))

    p()
    p("[步骤 7] 补足库存，再看看信用额度的闸门")
    stock_in(con, KY, "P2611091-A-003", 300, "2026-11-09", "2027-05-09", 9750)
    p("  再入库 300 袋（到期 2027-05-09）")
    show("  下 10 箱（= 200 袋，¥1,680）", lambda: submit_order(con, "S001", [(KY, 10)]))
    show("  只下 2 箱（= 40 袋，¥336）", lambda: submit_order(con, "S001", [(KY, 2)]),
         fmt=lambda r: "订单号 %s ｜ 金额 ¥%.2f" % (r["order_no"], r["total"] / 100))

    p()
    p("[步骤 8] 卖得出去 ≠ 贴得了标")
    show("  打标签（缺配料表/SC/执行标准）", lambda: print_label(con, KY))
    con.execute("""UPDATE sku SET ingredient_text='辣椒、食用油、豆瓣酱…',
                   sc_no='SC10333020500123', std_code='GB 31644' WHERE sku_code=?""", (KY,))
    con.commit()
    show("  回填 N、M、L 三列后再打", lambda: print_label(con, KY))

    p()
    p("[步骤 9] 盘点一下现在库里是什么样")
    p("  %-22s %-8s %-8s %s" % ("批次", "到期日", "在手", "已锁定(被订单占用)"))
    for bno, exp, on, lk in con.execute(
            "SELECT batch_no, expire_date, qty_on_hand, qty_locked FROM batch ORDER BY expire_date"):
        p("  %-22s %-8s %-8d %d" % (bno, exp, on, lk))
    row = con.execute("SELECT order_no, amount_cent FROM order_h").fetchone()
    p()
    p("  订单 %s ｜ 应收 ¥%.2f ｜ 状态 未收（账期到 2026-12-10）"
      % (row[0], row[1] / 100))
    used = con.execute("SELECT SUM(amount_cent - paid_cent) FROM receivable WHERE status<>'已收'").fetchone()[0]
    p("  信用额度占用 ¥%.2f / ¥1,500.00" % (used / 100))

    p()
    p("=" * 78)
    p("结论")
    p("=" * 78)
    p("1. 33 项待确认里，没有任何一项是「不填系统就废」的。")
    p("2. 每缺一项，只锁死一个动作：缺箱规→不能按箱下单；缺保质期→不能入库；")
    p("   缺配料表/SC→不能贴标；缺价格→不能成交。卖货、入库、贴标、开票 是四条独立解锁线。")
    p("3. 系统对缺数据的态度是「拦住 + 指到列」，不是猜个默认值蒙过去。")
    p("   猜出来的箱规、猜出来的保质期，最后都会变成发货单上的错。")
    p("4. 所以工厂可以分批填：先 H+P 两列 → 立刻能接单；再 J+K → 能入库；")
    p("   最后 N+M+L → 能贴标合规。每一批都有可见的收益。")

    text = "\n".join(buf)
    print(text)
    with io.open(OUTFILE, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print("\n[saved] %s" % OUTFILE)


if __name__ == "__main__":
    main()
