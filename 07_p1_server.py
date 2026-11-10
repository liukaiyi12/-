# -*- coding: utf-8 -*-
"""
P1 骨架后端 —— 一条订单从下单到回款的完整闭环，带登录与行级权限。

设计目标：
  1) 主数据缺什么，只拦住相关动作，不拦住整个系统（见 06 号文档）；
  2) 拦截信息带「表-列」定位，前端可直接展示给填表的人；
  3) 你是谁 → 你能看多远 → 你能看多细（成本/底价按角色脱敏）。

零依赖：标准库 + openpyxl。鉴权逻辑在 p1_auth.py。
启动：python 07_p1_server.py [--port 8765]
演示账号：见 /api/auth/accounts，密码统一 demo1234
"""
import base64
import json
import math
import mimetypes
import os
import re
import sqlite3
import sys
import uuid
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

try:
    from openpyxl import load_workbook
except ImportError:          # 容器里 pip 装不上时也别让服务起不来
    load_workbook = None

import p1_auth as A
import p1_schedule as SCH

BASE = os.path.dirname(os.path.abspath(__file__))

# 生产/开发由环境变量控制：
#   P1_ENV=prod        关掉演示接口（重置、demo/*、账号列表）
#   P1_DATA=<目录>     数据目录（数据库、上传的图片、密钥都在这）
#   P1_HOST / P1_PORT  监听地址与端口
ENV = os.environ.get("P1_ENV", "dev").lower()
PROD = ENV == "prod"
DATA = os.environ.get("P1_DATA") or os.path.join(BASE, "_work")
os.makedirs(DATA, exist_ok=True)

DB = os.path.join(DATA, "p1.db")
UPLOADS = os.path.join(DATA, "uploads")
os.makedirs(UPLOADS, exist_ok=True)
XLSX = os.environ.get("P1_XLSX") or os.path.join(BASE, "02_商品主数据表_v1.xlsx")
CATALOG_JSON = os.path.join(BASE, "catalog.json")
WEB = os.path.join(BASE, "p1_web")
TODAY = date.today()
SECRET = A.secret(os.path.join(DATA, ".secret"))

ACTION_CN = {"order": "下单", "inbound": "入库", "label_print": "打印标签"}
CAP = {
    "order": [("units_per_carton", "装箱规格", "H"), ("price_cent", "建议经销价", "P")],
    "inbound": [("shelf_life_month", "保质期(月)", "J"), ("storage_cond", "储存条件", "K")],
    "label_print": [("ingredient_text", "配料表", "N"), ("sc_no", "SC证号", "M"),
                    ("std_code", "执行标准", "L")],
}

DEMO_PW = "demo1234"


class ApiError(Exception):
    def __init__(self, status, code, message, **extra):
        self.status = status
        self.payload = {"ok": False, "code": code, "message": message}
        self.payload.update(extra)
        super().__init__(message)


def missing_for(sku, action, skip_price_if_contract=False):
    out = []
    for f, cn, col in CAP[action]:
        if skip_price_if_contract and f == "price_cent":
            continue
        if sku[f] in (None, ""):
            out.append({"field": f, "name": cn, "column": col,
                        "hint": "02_商品主数据表 %s 列" % col})
    return out


# ============================================================
# 数据库
# ============================================================
SCHEMA = """
CREATE TABLE IF NOT EXISTS region(id TEXT PRIMARY KEY, parent_id TEXT, name TEXT NOT NULL,
  path TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS app_user(
  id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, pw_salt TEXT NOT NULL, pw_hash TEXT NOT NULL,
  name TEXT NOT NULL, role TEXT NOT NULL, region_id TEXT, store_id TEXT, route_code TEXT,
  active INTEGER NOT NULL DEFAULT 1, fail_count INTEGER NOT NULL DEFAULT 0, locked_until TEXT);
CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, user_id TEXT, username TEXT, role TEXT,
  action TEXT NOT NULL, target TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS category(code TEXT PRIMARY KEY, name TEXT NOT NULL, typical_client TEXT);
CREATE TABLE IF NOT EXISTS spu(id TEXT PRIMARY KEY, code TEXT NOT NULL UNIQUE,
  category_code TEXT NOT NULL, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sku(
  id TEXT PRIMARY KEY, sku_code TEXT NOT NULL UNIQUE, spu_id TEXT NOT NULL, name TEXT NOT NULL,
  category TEXT NOT NULL, net_weight_g INTEGER NOT NULL, pack_type TEXT NOT NULL,
  units_per_carton INTEGER, shelf_life_month INTEGER, storage_cond TEXT, price_cent INTEGER,
  cost_cent INTEGER, ingredient_text TEXT, sc_no TEXT, std_code TEXT, moq_carton INTEGER DEFAULT 1,
  status TEXT NOT NULL DEFAULT '在产', is_custom INTEGER NOT NULL DEFAULT 0,
  CHECK (units_per_carton IS NULL OR units_per_carton > 0),
  CHECK (price_cent IS NULL OR price_cent >= 0));
CREATE TABLE IF NOT EXISTS store(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, settle_type TEXT NOT NULL DEFAULT 'm30',
  credit_limit_cent INTEGER NOT NULL DEFAULT 0, owner_user TEXT, address TEXT,
  region_id TEXT, route_code TEXT, lng REAL, lat REAL);
CREATE TABLE IF NOT EXISTS visit(
  id TEXT PRIMARY KEY, store_id TEXT NOT NULL, user_id TEXT NOT NULL,
  checkin_at TEXT NOT NULL, lng REAL, lat REAL, accuracy_m REAL, distance_m REAL,
  purpose TEXT, result TEXT, note TEXT, photos TEXT,
  valid_flag INTEGER NOT NULL DEFAULT 0, invalid_reason TEXT);
CREATE TABLE IF NOT EXISTS contract_price(
  store_id TEXT NOT NULL, sku_id TEXT NOT NULL, price_cent INTEGER NOT NULL,
  PRIMARY KEY (store_id, sku_id));
CREATE TABLE IF NOT EXISTS batch(
  id TEXT PRIMARY KEY, sku_id TEXT NOT NULL, batch_no TEXT NOT NULL,
  prod_date TEXT NOT NULL, expire_date TEXT NOT NULL,
  qty_on_hand INTEGER NOT NULL DEFAULT 0, qty_locked INTEGER NOT NULL DEFAULT 0,
  unit_cost_cent INTEGER, qc_status TEXT NOT NULL DEFAULT '待检',
  UNIQUE (sku_id, batch_no), CHECK (expire_date > prod_date));
CREATE TABLE IF NOT EXISTS order_h(
  id TEXT PRIMARY KEY, order_no TEXT NOT NULL UNIQUE, store_id TEXT NOT NULL,
  status TEXT NOT NULL, amount_cent INTEGER NOT NULL, channel TEXT NOT NULL DEFAULT 'store',
  created_by TEXT, created_at TEXT NOT NULL, pick_at TEXT, ship_at TEXT, sign_at TEXT);
CREATE TABLE IF NOT EXISTS order_l(
  id TEXT PRIMARY KEY, order_id TEXT NOT NULL, sku_id TEXT NOT NULL,
  qty_unit INTEGER NOT NULL, qty_carton INTEGER NOT NULL, price_cent INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS order_alloc(
  order_id TEXT NOT NULL, sku_id TEXT NOT NULL, batch_no TEXT NOT NULL, qty INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS delivery(
  id TEXT PRIMARY KEY, delivery_no TEXT NOT NULL UNIQUE, order_no TEXT NOT NULL,
  status TEXT NOT NULL, created_at TEXT NOT NULL, signed_at TEXT, receiver TEXT);
CREATE TABLE IF NOT EXISTS stock_move(
  id TEXT PRIMARY KEY, biz_type TEXT NOT NULL, biz_no TEXT NOT NULL, sku_id TEXT NOT NULL,
  batch_no TEXT NOT NULL, qty INTEGER NOT NULL, occurred_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS receivable(
  id TEXT PRIMARY KEY, store_id TEXT NOT NULL, order_no TEXT NOT NULL,
  amount_cent INTEGER NOT NULL, paid_cent INTEGER NOT NULL DEFAULT 0,
  due_date TEXT, status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS work_order(
  id TEXT PRIMARY KEY, wo_no TEXT NOT NULL UNIQUE, sku_code TEXT NOT NULL, sku_name TEXT,
  qty_unit INTEGER NOT NULL, due_day INTEGER NOT NULL DEFAULT 3, status TEXT NOT NULL DEFAULT '待排',
  source_order TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS changeover(
  from_family TEXT NOT NULL, to_family TEXT NOT NULL, minutes INTEGER NOT NULL,
  PRIMARY KEY (from_family, to_family));
CREATE TABLE IF NOT EXISTS schedule_run(
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, strategy TEXT, metrics TEXT);
CREATE INDEX IF NOT EXISTS ix_batch_sku ON batch(sku_id, expire_date);
CREATE INDEX IF NOT EXISTS ix_order_store ON order_h(store_id, created_at);
CREATE INDEX IF NOT EXISTS ix_store_region ON store(region_id);
"""


def conn():
    c = sqlite3.connect(DB, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def init_db(force=False):
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    if force and os.path.exists(DB):
        os.remove(DB)
    fresh = not os.path.exists(DB)
    c = conn()
    c.executescript(SCHEMA)
    if fresh:
        seed(c)
    c.commit()
    c.close()
    return fresh


REGIONS = [
    ("R-CITY", None, "宁波", "CITY"),
    ("R-CX", "R-CITY", "城西", "CITY.CX"),
    ("R-CD", "R-CITY", "城东", "CITY.CD"),
    ("R-CN", "R-CITY", "城南", "CITY.CN"),
]

# username, 姓名, 角色, 区域, 门店, 线路
USERS = [
    ("boss",     "洪总",   "boss",     "R-CITY", None,   None),
    ("director", "王总监", "director", "R-CITY", None,   None),
    ("sales1",   "张伟",   "sales",    "R-CX",   None,   None),
    ("sales2",   "陈丽",   "sales",    "R-CD",   None,   None),
    ("sales3",   "王强",   "sales",    "R-CN",   None,   None),
    ("cs",       "小李",   "cs",       "R-CITY", None,   None),
    ("wh",       "老周",   "wh",       "R-CITY", None,   None),
    ("driver1",  "李师傅", "driver",   "R-CX",   None,   "CX-01"),
    ("fin",      "小陈",   "fin",      "R-CITY", None,   None),
    ("store1",   "李老板", "store",    "R-CX",   "S001", None),
]

STORES = [
    # id, 名称, 月结, 额度(分), 归属业务员, 地址, 区域, 线路, 经度, 纬度
    ("S001", "姜山老灶火锅", "m30", 150000, "sales1", "江北区慈城镇普济路18号", "R-CX", "CX-01",
     121.5550, 29.9080),
    ("S002", "周记烤鱼(万达店)", "m30", 100000, "sales1", "江北区万达广场3F", "R-CX", "CX-01",
     121.5662, 29.9125),
    ("S003", "川味小厨", "m30", 80000, "sales2", "鄞州区中山东路88号", "R-CD", "CD-01",
     121.6203, 29.8177),
    ("S004", "老四烧烤", "cash", 60000, "sales3", "海曙区南站西路12号", "R-CN", "CN-01",
     121.5399, 29.8341),
    ("S005", "刘记麻辣烫", "m60", 120000, "sales2", "鄞州区天童南路200号", "R-CD", "CD-01",
     121.6321, 29.8055),
]


def haversine(lat1, lng1, lat2, lng2):
    """两点间距离（米）。用来判断"人是不是真的到了店门口"。"""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def load_catalog():
    """读商品目录。顺序：xlsx → catalog.json → 空。
    返回 (有品名的 SKU 列表, {品类名: (品类码, 典型客户)})。"""
    # 1) xlsx
    if load_workbook is not None and os.path.exists(XLSX):
        try:
            wb = load_workbook(XLSX, data_only=True)
            ws = wb["01 商品主数据"]
            wsd = wb["02 品类字典"]
            cats = {}
            for i in range(3, wsd.max_row + 1):
                code, name, cli = wsd.cell(i, 1).value, wsd.cell(i, 2).value, wsd.cell(i, 5).value
                if code and name and len(str(code)) == 2:
                    cats[name] = (code, cli)
            rows, r = [], 3
            while ws.cell(r, 1).value:
                rows.append({
                    "code": ws.cell(r, 1).value, "cat": ws.cell(r, 2).value,
                    "name": ws.cell(r, 3).value, "spu": ws.cell(r, 5).value,
                    "net": ws.cell(r, 6).value, "pack": ws.cell(r, 7).value})
                r += 1
            print("[p1] 商品目录：从 xlsx 读取 %d 条" % len(rows))
            return rows, cats
        except Exception as e:
            print("[p1] xlsx 读取失败（%s），改用 JSON 快照" % e)

    # 2) JSON 快照
    if os.path.exists(CATALOG_JSON):
        try:
            data = json.load(open(CATALOG_JSON, encoding="utf-8"))
            cats = {c["name"]: (c["code"], c.get("client")) for c in data["categories"]}
            rows = data["skus"]
            print("[p1] 商品目录：从 catalog.json 读取 %d 条" % len(rows))
            return rows, cats
        except Exception as e:
            print("[p1] catalog.json 读取失败：%s" % e)

    print("[p1] 警告：没有可用的商品目录，SKU 表将是空的")
    return [], {}


def seed(c):
    for rid, pid, name, path in REGIONS:
        c.execute("INSERT OR IGNORE INTO region VALUES (?,?,?,?)", (rid, pid, name, path))
    for uid, uname, role, region, store_id, route in USERS:
        salt, hpw = A.hash_password(DEMO_PW)
        c.execute("""INSERT OR IGNORE INTO app_user
                     (id,username,pw_salt,pw_hash,name,role,region_id,store_id,route_code,active)
                     VALUES (?,?,?,?,?,?,?,?,?,1)""",
                  (uid, uid, salt, hpw, uname, role, region, store_id, route))

    # 商品目录：优先读 xlsx，读不到就退回仓库里的 JSON 快照。
    # 这样即使容器里装不上 openpyxl，服务照样能起来（否则 import 失败 → 进程退出 → 端口没人监听）。
    rows, cats = load_catalog()
    for cat_name, (ccode, cli) in cats.items():
        c.execute("INSERT OR IGNORE INTO category VALUES (?,?,?)", (ccode, cat_name, cli))
    for it in rows:
        cat_name = it["cat"]
        ccode, cli = cats.get(cat_name, ("XX", None))
        c.execute("INSERT OR IGNORE INTO category VALUES (?,?,?)", (ccode, cat_name, cli))
        c.execute("INSERT OR IGNORE INTO spu VALUES (?,?,?,?)",
                  (it["spu"], it["spu"], ccode, it["name"]))
        c.execute(
            """INSERT OR IGNORE INTO sku(id, sku_code, spu_id, name, category, net_weight_g,
                 pack_type, units_per_carton, shelf_life_month, storage_cond, price_cent, cost_cent,
                 ingredient_text, sc_no, std_code, moq_carton, status)
               VALUES (?,?,?,?,?,?,?,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,1,'在产')""",
            (it["code"], it["code"], it["spu"], it["name"], cat_name, it["net"], it["pack"]))

    for sid, sname, settle, limit, owner, addr, region, route, lng, lat in STORES:
        c.execute("""INSERT OR IGNORE INTO store
                     (id,name,settle_type,credit_limit_cent,owner_user,address,region_id,route_code,
                      lng,lat) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                  (sid, sname, settle, limit, owner, addr, region, route, lng, lat))

    for f in SCH.FAMILIES:
        for t in SCH.FAMILIES:
            if f != t:
                c.execute("INSERT OR IGNORE INTO changeover VALUES (?,?,?)",
                          (f, t, SCH.DEFAULT_CHANGEOVER.get((f, t), 45)))


# ============================================================
# 用户
# ============================================================
def user_by_id(c, uid):
    r = c.execute("""SELECT u.*, r.path AS region_path, r.name AS region_name
                     FROM app_user u LEFT JOIN region r ON r.id=u.region_id
                     WHERE u.id=? AND u.active=1""", (uid,)).fetchone()
    return dict(r) if r else None


def public_user(u):
    return {"id": u["id"], "username": u["username"], "name": u["name"], "role": u["role"],
            "role_cn": A.ROLES.get(u["role"], {}).get("cn", u["role"]),
            "region": u.get("region_name"), "store_id": u.get("store_id"),
            "route_code": u.get("route_code"),
            "see_cost": A.can(u["role"], "see_cost"),
            "can_operate": A.can(u["role"], "can_operate"),
            "desc": A.ROLES.get(u["role"], {}).get("desc", "")}


MAX_FAIL = 5
LOCK_MINUTES = 5


def _now_s():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def login(c, username, password):
    r = c.execute("SELECT * FROM app_user WHERE username=? AND active=1", (username,)).fetchone()
    if r and r["locked_until"] and r["locked_until"] > _now_s():
        raise ApiError(423, "LOCKED",
                       "连续输错太多次，账号锁定到 %s（每次输错都会重置倒计时）" % r["locked_until"])
    if not r or not A.verify_password(password, r["pw_salt"], r["pw_hash"]):
        if r:
            fc = (r["fail_count"] or 0) + 1
            lu = ((datetime.now() + timedelta(minutes=LOCK_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
                  if fc >= MAX_FAIL else None)
            c.execute("UPDATE app_user SET fail_count=?, locked_until=? WHERE id=?", (fc, lu, r["id"]))
            audit(c, None, "login.failed", username, "第 %d 次" % fc)
            c.commit()
            if fc >= MAX_FAIL:
                raise ApiError(423, "LOCKED", "连续 %d 次密码错误，锁定 %d 分钟"
                               % (MAX_FAIL, LOCK_MINUTES))
            raise ApiError(401, "BAD_CREDENTIALS",
                           "账号或密码不对（还可以再试 %d 次）" % (MAX_FAIL - fc))
        raise ApiError(401, "BAD_CREDENTIALS", "账号或密码不对")
    c.execute("UPDATE app_user SET fail_count=0, locked_until=NULL WHERE id=?", (r["id"],))
    u = user_by_id(c, r["id"])
    token = A.sign({"uid": u["id"]}, SECRET)
    audit(c, u, "login", u["username"], "")
    c.commit()
    return {"token": token, "user": public_user(u)}


def audit(c, user, action, target="", detail=""):
    c.execute("INSERT INTO audit_log(at,user_id,username,role,action,target,detail) "
              "VALUES (?,?,?,?,?,?,?)",
              (TODAY.isoformat() + "  " + _now(), user["id"] if user else None,
               user["username"] if user else None, user["role"] if user else None,
               action, target, detail))


def _now():
    from datetime import datetime
    return datetime.now().strftime("%H:%M:%S")


def need(user, *roles):
    if user["role"] not in roles:
        raise ApiError(403, "FORBIDDEN",
                       "当前角色「%s」没有这个权限，需要：%s"
                       % (A.ROLES[user["role"]]["cn"],
                          "、".join(A.ROLES[r]["cn"] for r in roles)))


def demo_only():
    """演示接口在生产环境必须关掉：重置会清库，demo 会造假数据。"""
    if PROD:
        raise ApiError(403, "DISABLED_IN_PROD",
                       "生产环境已关闭演示接口（P1_ENV=prod）")


def assert_store(c, user, store_id):
    frag, params = A.store_where(user, "s")
    row = c.execute("SELECT s.id FROM store s WHERE s.id=? AND " + frag,
                    [store_id] + params).fetchone()
    if not row:
        raise ApiError(403, "FORBIDDEN_STORE", "无权操作该门店：%s" % store_id)


# ============================================================
# 业务
# ============================================================
def get_sku(c, code):
    row = c.execute("SELECT * FROM sku WHERE sku_code=?", (code,)).fetchone()
    if not row:
        raise ApiError(404, "SKU_NOT_FOUND", "商品不存在：%s" % code)
    return dict(row)


def load_matrix(c):
    mx = {(r["from_family"], r["to_family"]): r["minutes"]
          for r in c.execute("SELECT * FROM changeover").fetchall()}
    for f in SCH.FAMILIES:
        for t in SCH.FAMILIES:
            if f != t:
                mx.setdefault((f, t), SCH.DEFAULT_CHANGEOVER.get((f, t), 45))
    return mx


def load_wos(c):
    return [{"wo_no": r["wo_no"], "sku": r["sku_code"], "qty_unit": r["qty_unit"],
             "due_day": r["due_day"], "family": SCH.family_of(r["sku_code"][:2])}
            for r in c.execute("SELECT * FROM work_order ORDER BY due_day, wo_no").fetchall()]


def resolve_price(c, store_id, sku):
    row = c.execute("SELECT price_cent FROM contract_price WHERE store_id=? AND sku_id=?",
                    (store_id, sku["id"])).fetchone()
    if row:
        return row["price_cent"], "合同价"
    if sku["price_cent"] is None:
        raise ApiError(400, "MISSING_MASTER", "%s 没有可用价格" % sku["name"],
                       missing=[{"field": "price_cent", "name": "建议经销价",
                                 "column": "P", "hint": "02_商品主数据表 P 列"}])
    return sku["price_cent"], "标准价"


def fefo(c, sku_id, need_qty):
    rows = c.execute(
        """SELECT batch_no, expire_date, qty_on_hand - qty_locked AS avail
           FROM batch WHERE sku_id=? AND qc_status='合格' AND qty_on_hand - qty_locked > 0
           ORDER BY expire_date ASC""", (sku_id,)).fetchall()
    got, plan = 0, []
    for b in rows:
        if got >= need_qty:
            break
        take = min(b["avail"], need_qty - got)
        plan.append({"batch_no": b["batch_no"], "expire_date": b["expire_date"], "qty": take})
        got += take
    return plan, got


def credit_used(c, store_id):
    return c.execute(
        "SELECT COALESCE(SUM(amount_cent - paid_cent),0) FROM receivable "
        "WHERE store_id=? AND status IN ('未出账','未收','部分')", (store_id,)).fetchone()[0]


def create_order(c, user, store_id, lines, channel="store"):
    assert_store(c, user, store_id)
    store = c.execute("SELECT * FROM store WHERE id=?", (store_id,)).fetchone()
    if not store:
        raise ApiError(404, "STORE_NOT_FOUND", "门店不存在")

    prepared = []
    for ln in lines:
        sku = get_sku(c, ln["sku_code"])
        cartons = int(ln["cartons"])
        if cartons <= 0:
            raise ApiError(400, "BAD_QTY", "%s 数量必须大于 0" % sku["name"])
        if sku["status"] != "在产":
            raise ApiError(400, "SKU_STOPPED", "%s 已停产，不可下单" % sku["name"])
        if sku["is_custom"]:
            raise ApiError(400, "CUSTOM_SKU", "%s 是定制 SKU，不可混入通用订单" % sku["name"])

        has_contract = c.execute("SELECT 1 FROM contract_price WHERE store_id=? AND sku_id=?",
                                 (store_id, sku["id"])).fetchone()
        miss = missing_for(sku, "order", skip_price_if_contract=bool(has_contract))
        if miss:
            raise ApiError(400, "MISSING_MASTER", "%s 缺少下单必需的主数据" % sku["name"],
                           missing=miss, sku=sku["sku_code"])
        if cartons < (sku["moq_carton"] or 1):
            raise ApiError(400, "BELOW_MOQ", "%s 起订 %d 箱" % (sku["name"], sku["moq_carton"]))

        price, src = resolve_price(c, store_id, sku)
        qty_unit = cartons * sku["units_per_carton"]
        plan, got = fefo(c, sku["id"], qty_unit)
        if got < qty_unit:
            raise ApiError(400, "OUT_OF_STOCK",
                           "%s 库存不足：需 %d 袋，可用 %d 袋" % (sku["name"], qty_unit, got),
                           need=qty_unit, available=got)
        prepared.append({"sku": sku, "cartons": cartons, "qty_unit": qty_unit,
                         "price": price, "price_source": src, "plan": plan})

    total = sum(p["qty_unit"] * p["price"] for p in prepared)
    used = credit_used(c, store_id)
    if used + total > store["credit_limit_cent"]:
        raise ApiError(402, "CREDIT_EXCEEDED",
                       "超出信用额度：已占用 ¥%.2f + 本单 ¥%.2f > 额度 ¥%.2f"
                       % (used / 100, total / 100, store["credit_limit_cent"] / 100),
                       used_cent=used, order_cent=total, limit_cent=store["credit_limit_cent"])

    seq = c.execute("SELECT COUNT(*) FROM order_h").fetchone()[0] + 1
    order_no = "SO%s-%04d" % (TODAY.strftime("%y%m%d"), seq)
    oid = "O%d" % seq
    c.execute("""INSERT INTO order_h(id,order_no,store_id,status,amount_cent,channel,
                 created_by,created_at) VALUES (?,?,?,?,?,?,?,?)""",
              (oid, order_no, store_id, "已确认", total, channel, user["id"], TODAY.isoformat()))
    for i, p in enumerate(prepared):
        c.execute("INSERT INTO order_l VALUES (?,?,?,?,?,?)",
                  ("L%s-%d" % (seq, i), oid, p["sku"]["id"], p["qty_unit"], p["cartons"], p["price"]))
        for a in p["plan"]:
            c.execute("INSERT INTO order_alloc VALUES (?,?,?,?)",
                      (oid, p["sku"]["id"], a["batch_no"], a["qty"]))
            c.execute("UPDATE batch SET qty_locked=qty_locked+? WHERE sku_id=? AND batch_no=?",
                      (a["qty"], p["sku"]["id"], a["batch_no"]))
    c.execute("INSERT INTO receivable VALUES (?,?,?,?,0,?,'未出账')",
              ("R%d" % seq, store_id, order_no, total, None))
    audit(c, user, "order.create", order_no, "¥%.2f %d 行" % (total / 100, len(prepared)))
    c.commit()
    return order_no, total


def order_visible(c, user, order_no):
    frag, params = A.store_where(user, "s")
    row = c.execute("""SELECT o.* FROM order_h o JOIN store s ON s.id=o.store_id
                       WHERE o.order_no=? AND """ + frag, [order_no] + params).fetchone()
    if not row:
        raise ApiError(403, "FORBIDDEN_ORDER", "无权查看该订单（或不存在）")
    return row


def order_detail(c, user, order_no):
    o = order_visible(c, user, order_no)
    items = c.execute(
        """SELECT l.qty_unit, l.qty_carton, l.price_cent, s.sku_code, s.name
           FROM order_l l JOIN sku s ON s.id=l.sku_id WHERE l.order_id=?""", (o["id"],)).fetchall()
    alloc = c.execute(
        """SELECT a.batch_no, a.qty, b.expire_date, s.name
           FROM order_alloc a JOIN batch b ON b.batch_no=a.batch_no AND b.sku_id=a.sku_id
           JOIN sku s ON s.id=a.sku_id WHERE a.order_id=? ORDER BY b.expire_date""",
        (o["id"],)).fetchall()
    d = c.execute("SELECT * FROM delivery WHERE order_no=?", (order_no,)).fetchone()
    return {
        "order_no": o["order_no"], "status": o["status"],
        "store": c.execute("SELECT name FROM store WHERE id=?", (o["store_id"],)).fetchone()["name"],
        "amount": o["amount_cent"] / 100, "created_at": o["created_at"],
        "created_by": o["created_by"],
        "items": [{"sku_code": i["sku_code"], "name": i["name"], "cartons": i["qty_carton"],
                   "qty_unit": i["qty_unit"], "price": i["price_cent"] / 100} for i in items],
        "alloc": [{"sku": a["name"], "batch_no": a["batch_no"], "expire": a["expire_date"],
                   "qty": a["qty"]} for a in alloc],
        "delivery": d["delivery_no"] if d else None,
    }


def pick_order(c, user, order_no):
    o = order_visible(c, user, order_no)
    if o["status"] != "已确认":
        raise ApiError(409, "BAD_STATE", "订单当前状态是「%s」，只有「已确认」才能拣货" % o["status"])
    c.execute("UPDATE order_h SET status='待发货', pick_at=? WHERE id=?",
              (TODAY.isoformat(), o["id"]))
    audit(c, user, "order.pick", order_no)
    c.commit()
    return order_detail(c, user, order_no)


def ship_order(c, user, order_no):
    o = order_visible(c, user, order_no)
    if o["status"] != "待发货":
        raise ApiError(409, "BAD_STATE", "订单当前状态是「%s」，只有「待发货」才能出库配送" % o["status"])
    seq = c.execute("SELECT COUNT(*) FROM delivery").fetchone()[0] + 1
    dno = "DL%s-01%02d" % (TODAY.strftime("%y%m%d"), seq)
    c.execute("INSERT INTO delivery VALUES (?,?,?,?,?,NULL,NULL)",
              ("D%d" % seq, dno, order_no, "配送中", TODAY.isoformat()))
    c.execute("UPDATE order_h SET status='配送中', ship_at=? WHERE id=?",
              (TODAY.isoformat(), o["id"]))
    for a in c.execute("SELECT sku_id, batch_no, qty FROM order_alloc WHERE order_id=?",
                       (o["id"],)).fetchall():
        c.execute("""UPDATE batch SET qty_on_hand=qty_on_hand-?, qty_locked=qty_locked-?
                     WHERE sku_id=? AND batch_no=?""", (a["qty"], a["qty"], a["sku_id"], a["batch_no"]))
        c.execute("INSERT INTO stock_move VALUES (?,?,?,?,?,?,?)",
                  ("M-%s-%s-%s" % (order_no, a["batch_no"], a["qty"]), "SO", order_no,
                   a["sku_id"], a["batch_no"], -a["qty"], TODAY.isoformat()))
    audit(c, user, "order.ship", order_no, dno)
    c.commit()
    return {"delivery_no": dno, "order_no": order_no}


def sign_delivery(c, user, delivery_no, receiver, note=""):
    d = c.execute("SELECT * FROM delivery WHERE delivery_no=?", (delivery_no,)).fetchone()
    if not d:
        raise ApiError(404, "DELIVERY_NOT_FOUND", "配送单不存在")
    o = order_visible(c, user, d["order_no"])
    if d["status"] != "配送中":
        raise ApiError(409, "BAD_STATE", "配送单状态是「%s」，不可重复签收" % d["status"])
    now = TODAY.isoformat()
    c.execute("UPDATE delivery SET status='已签收', signed_at=?, receiver=? WHERE id=?",
              (now, receiver, d["id"]))
    c.execute("UPDATE order_h SET status='已回单', sign_at=? WHERE id=?", (now, o["id"]))
    store = c.execute("SELECT settle_type FROM store WHERE id=?", (o["store_id"],)).fetchone()
    days = {"cash": 0, "m30": 30, "m60": 60}.get(store["settle_type"], 30)
    due = (TODAY + timedelta(days=days)).isoformat()
    c.execute("UPDATE receivable SET status='未收', due_date=? WHERE order_no=?", (due, d["order_no"]))
    audit(c, user, "delivery.sign", delivery_no, "receiver=%s" % receiver)
    c.commit()
    return {"order_no": d["order_no"], "delivery_no": delivery_no,
            "due_date": due, "receiver": receiver}


# ============================================================
# 路由 GET
# ============================================================
def api_get(path, q, user):
    c = conn()
    try:
        if path == "/api/auth/me":
            return {"ok": True, "user": public_user(user)}

        if path == "/api/auth/accounts":
            if PROD:
                raise ApiError(404, "NO_ROUTE", "未知接口：%s" % path)
            return {"ok": True, "password": DEMO_PW, "items": [
                {"username": u[0], "name": u[1], "role": u[2],
                 "role_cn": A.ROLES[u[2]]["cn"], "desc": A.ROLES[u[2]]["desc"]}
                for u in USERS]}

        if path == "/api/health":
            return {"ok": True, "today": TODAY.isoformat(), "db": os.path.basename(DB)}

        if path == "/api/stores":
            frag, params = A.store_where(user, "s")
            rows = c.execute("""SELECT s.*, r.name AS region_name,
                                       (SELECT COUNT(*) FROM order_h o WHERE o.store_id=s.id) AS orders
                                FROM store s LEFT JOIN region r ON r.id=s.region_id
                                WHERE """ + frag + " ORDER BY s.id", params).fetchall()
            return {"ok": True, "items": [{
                "id": r["id"], "name": r["name"], "region": r["region_name"],
                "owner": r["owner_user"], "route": r["route_code"], "settle": r["settle_type"],
                "credit_limit": r["credit_limit_cent"] / 100, "orders": r["orders"],
                "lng": r["lng"], "lat": r["lat"], "address": r["address"]} for r in rows]}

        if path == "/api/master/skus":
            rows = [dict(r) for r in c.execute(
                """SELECT sku_code, name, category, net_weight_g, pack_type, units_per_carton,
                          price_cent, cost_cent, shelf_life_month, storage_cond, status
                   FROM sku ORDER BY sku_code""").fetchall()]
            out = []
            for r in rows:
                r = A.mask(user["role"], r, fields=("cost_cent",))
                r["price"] = None if r["price_cent"] is None else r["price_cent"] / 100
                r["cost"] = None if r["cost_cent"] is None else r["cost_cent"] / 100
                r["can_order"] = r["units_per_carton"] is not None and r["price_cent"] is not None
                r["can_inbound"] = r["shelf_life_month"] is not None and r["storage_cond"] is not None
                out.append(r)
            return {"ok": True, "total": len(out), "items": out}

        if path == "/api/master/status":
            r = c.execute("""SELECT
                  (SELECT COUNT(*) FROM sku) t,
                  (SELECT COUNT(*) FROM sku WHERE units_per_carton IS NOT NULL AND price_cent IS NOT NULL) o,
                  (SELECT COUNT(*) FROM sku WHERE shelf_life_month IS NOT NULL AND storage_cond IS NOT NULL) i,
                  (SELECT COUNT(*) FROM sku WHERE ingredient_text IS NOT NULL AND sc_no IS NOT NULL
                     AND std_code IS NOT NULL) l""").fetchone()
            return {"ok": True, "total": r["t"], "can_order": r["o"],
                    "can_inbound": r["i"], "can_label": r["l"]}

        if path == "/api/stock":
            need(user, "boss", "wh", "cs", "fin", "director")
            rows = c.execute(
                """SELECT b.batch_no, s.sku_code, s.name, b.prod_date, b.expire_date,
                          b.qty_on_hand, b.qty_locked, b.qc_status
                   FROM batch b JOIN sku s ON s.id=b.sku_id ORDER BY b.expire_date""").fetchall()
            return {"ok": True, "items": [dict(r) for r in rows]}

        if path == "/api/orders":
            frag, params = A.store_where(user, "st")
            rows = c.execute("""SELECT o.order_no, o.status, o.amount_cent, o.created_at, o.created_by,
                                       st.name AS store, st.id AS store_id,
                                       (SELECT COUNT(*) FROM order_l WHERE order_id=o.id) AS lines
                                FROM order_h o JOIN store st ON st.id=o.store_id
                                WHERE """ + frag + """ ORDER BY o.created_at DESC, o.order_no DESC""",
                             params).fetchall()
            return {"ok": True, "items": [{
                "order_no": r["order_no"], "status": r["status"], "store": r["store"],
                "store_id": r["store_id"], "amount": r["amount_cent"] / 100,
                "created_at": r["created_at"], "lines": r["lines"],
                "created_by": r["created_by"]} for r in rows]}

        m = re.match(r"^/api/orders/([A-Z0-9\-]+)$", path)
        if m:
            return {"ok": True, "order": order_detail(c, user, m.group(1))}

        if path == "/api/receivables":
            frag, params = A.store_where(user, "s")
            rows = c.execute("""SELECT r.*, s.name AS store FROM receivable r
                                JOIN store s ON s.id=r.store_id WHERE """ + frag +
                             " ORDER BY r.order_no", params).fetchall()
            return {"ok": True, "items": [{
                "order_no": r["order_no"], "store": r["store"], "amount": r["amount_cent"] / 100,
                "paid": r["paid_cent"] / 100, "due_date": r["due_date"],
                "status": r["status"]} for r in rows]}

        if path == "/api/dashboard":
            frag, params = A.store_where(user, "s")
            t = c.execute("""SELECT COUNT(*) n, COALESCE(SUM(o.amount_cent),0) amt
                             FROM order_h o JOIN store s ON s.id=o.store_id WHERE """ + frag,
                          params).fetchone()
            by = {r["status"]: r["n"] for r in c.execute(
                """SELECT o.status, COUNT(*) n FROM order_h o JOIN store s ON s.id=o.store_id
                   WHERE """ + frag + " GROUP BY o.status", params).fetchall()}
            ar = c.execute("""SELECT COALESCE(SUM(r.amount_cent-r.paid_cent),0)
                              FROM receivable r JOIN store s ON s.id=r.store_id
                              WHERE r.status IN ('未出账','未收','部分') AND """ + frag,
                           params).fetchone()[0]
            lim = c.execute("""SELECT COALESCE(SUM(s.credit_limit_cent),0) FROM store s WHERE """ + frag,
                            params).fetchone()[0]
            ms = c.execute("""SELECT
                  (SELECT COUNT(*) FROM sku) t,
                  (SELECT COUNT(*) FROM sku WHERE units_per_carton IS NOT NULL AND price_cent IS NOT NULL) o,
                  (SELECT COUNT(*) FROM sku WHERE shelf_life_month IS NOT NULL AND storage_cond IS NOT NULL) i,
                  (SELECT COUNT(*) FROM sku WHERE ingredient_text IS NOT NULL) l""").fetchone()
            data = {"ok": True, "orders": t["n"], "amount": t["amt"] / 100, "by_status": by,
                    "ar": ar / 100, "credit_limit": lim / 100,
                    "master": {"total": ms["t"], "can_order": ms["o"],
                               "can_inbound": ms["i"], "can_label": ms["l"]}}
            if A.can(user["role"], "see_cost"):
                cost = c.execute("""SELECT COALESCE(SUM(a.qty * b.unit_cost_cent),0)
                                    FROM order_alloc a
                                    JOIN order_h o ON o.id=a.order_id
                                    JOIN store s ON s.id=o.store_id
                                    JOIN batch b ON b.batch_no=a.batch_no AND b.sku_id=a.sku_id
                                    WHERE """ + frag, params).fetchone()[0]
                data["cost"] = cost / 100
                data["gross_margin"] = round((t["amt"] - cost) / t["amt"] * 100, 1) if t["amt"] else 0
            return data

        if path == "/api/audit":
            need(user, "boss", "fin")
            rows = c.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 200").fetchall()
            return {"ok": True, "items": [dict(r) for r in rows]}

        # ---------- 打卡 / 拜访 ----------
        if path == "/api/visits/today":
            need(user, "boss", "sales", "director")
            frag, params = A.store_where(user, "s")
            rows = c.execute(
                """SELECT s.id, s.name, s.address, s.lng, s.lat, s.route_code, s.owner_user,
                          (SELECT COUNT(*) FROM visit v WHERE v.store_id=s.id
                             AND v.user_id=? AND substr(v.checkin_at,1,10)=?) AS today
                   FROM store s WHERE """ + frag + " ORDER BY s.id",
                [user["id"], TODAY.isoformat()] + params).fetchall()
            done = [dict(r) for r in rows if r["today"] > 0]
            todo = [dict(r) for r in rows if r["today"] == 0]
            return {"ok": True, "date": TODAY.isoformat(),
                    "done": done, "todo": todo,
                    "stats": {"total": len(rows), "done": len(done), "left": len(todo)}}

        if path == "/api/visits/kpi":
            need(user, "boss", "sales", "director")
            month = TODAY.strftime("%Y-%m")
            frag, params = A.store_where(user, "s")
            own = "" if user["role"] in ("boss", "director") else " AND v.user_id=?"
            extra = [user["id"]] if own else []
            r = c.execute(
                """SELECT COUNT(*) total, COALESCE(SUM(v.valid_flag),0) valid,
                          COUNT(DISTINCT v.store_id) covered
                   FROM visit v JOIN store s ON s.id=v.store_id
                   WHERE substr(v.checkin_at,1,7)=? """ + own + " AND " + frag,
                [month] + extra + params).fetchone()
            new = c.execute(
                """SELECT COUNT(*) FROM (SELECT MIN(o.created_at) d FROM order_h o
                     JOIN store s ON s.id=o.store_id WHERE """ + frag +
                " GROUP BY o.store_id) WHERE substr(d,1,7)=?", params + [month]).fetchone()[0]
            return {"ok": True, "month": month, "visits": r["total"], "valid": r["valid"],
                    "covered_stores": r["covered"], "new_stores": new,
                    "valid_rate": round(r["valid"] / r["total"] * 100, 1) if r["total"] else 0}

        if path == "/api/deliveries":
            need(user, "boss", "wh", "cs", "fin", "driver", "store", "director")
            frag, params = A.store_where(user, "s")
            rows = c.execute("""SELECT d.delivery_no, d.order_no, d.status, d.created_at,
                                       d.signed_at, d.receiver, s.name AS store, s.address,
                                       s.route_code, o.amount_cent,
                                       (SELECT COUNT(*) FROM order_l WHERE order_id=o.id) AS lines
                                FROM delivery d
                                JOIN order_h o ON o.order_no = d.order_no
                                JOIN store s ON s.id = o.store_id
                                WHERE """ + frag + """
                                ORDER BY CASE d.status WHEN '配送中' THEN 0 ELSE 1 END,
                                         d.delivery_no""", params).fetchall()
            return {"ok": True, "items": [{
                "delivery_no": r["delivery_no"], "order_no": r["order_no"], "status": r["status"],
                "store": r["store"], "address": r["address"], "route": r["route_code"],
                "amount": r["amount_cent"] / 100, "lines": r["lines"],
                "created_at": r["created_at"], "signed_at": r["signed_at"],
                "receiver": r["receiver"]} for r in rows]}

        # ---------- 排产 ----------
        if path == "/api/workorders":
            need(user, "boss", "wh", "cs", "director", "fin")
            return {"ok": True, "items": [
                {"wo_no": r["wo_no"], "sku_code": r["sku_code"], "sku": r["sku_name"],
                 "qty_unit": r["qty_unit"], "due_day": r["due_day"],
                 "family": SCH.family_of(r["sku_code"][:2]),
                 "pot_count": SCH.pot_count(r["qty_unit"]), "status": r["status"]}
                for r in c.execute("SELECT * FROM work_order ORDER BY due_day, wo_no").fetchall()]}

        if path == "/api/schedule/matrix":
            need(user, "boss", "wh", "cs", "director")
            mx = load_matrix(c)
            return {"ok": True, "families": SCH.FAMILIES,
                    "capacity": SCH.CAPACITY,
                    "items": [{"from": a, "to": b, "minutes": mx[(a, b)]}
                              for a in SCH.FAMILIES for b in SCH.FAMILIES if a != b]}

        if path == "/api/schedule/compare":
            need(user, "boss", "wh", "cs", "director")
            wos = load_wos(c)
            if not wos:
                raise ApiError(400, "NO_WORKORDER", "还没有工单，先点「从订单生成工单」")
            return {"ok": True, "compare": SCH.compare(wos, load_matrix(c)),
                    "workorders": len(wos)}

        raise ApiError(404, "NO_ROUTE", "未知接口：%s" % path)
    finally:
        c.close()


# ============================================================
# 路由 POST
# ============================================================
def api_post(path, body, user):
    c = conn()
    try:
        if path == "/api/auth/login":
            return {"ok": True, **login(c, body.get("username", ""), body.get("password", ""))}

        if path == "/api/master/fill":
            need(user, "boss")
            sku = get_sku(c, body["sku_code"])
            sets, args = [], []
            for f in ("units_per_carton", "price_cent", "cost_cent", "shelf_life_month",
                      "storage_cond", "ingredient_text", "sc_no", "std_code"):
                if f in body and body[f] not in (None, ""):
                    v = body[f]
                    if f not in ("storage_cond", "ingredient_text", "sc_no", "std_code"):
                        v = int(v)
                    sets.append("%s=?" % f)
                    args.append(v)
            if not sets:
                raise ApiError(400, "NOTHING_TO_FILL", "没有要更新的字段")
            args.append(sku["id"])
            c.execute("UPDATE sku SET %s WHERE id=?" % ",".join(sets), args)
            audit(c, user, "master.fill", body["sku_code"], ",".join(s.split("=")[0] for s in sets))
            c.commit()
            return {"ok": True, "sku_code": body["sku_code"]}

        if path == "/api/inbound":
            need(user, "boss", "wh")
            sku = get_sku(c, body["sku_code"])
            miss = missing_for(sku, "inbound")
            if miss:
                raise ApiError(400, "MISSING_MASTER",
                               "%s 缺少入库必需的主数据（算不出到期日）" % sku["name"],
                               missing=miss, sku=sku["sku_code"])
            qty = int(body["qty_unit"])
            if qty <= 0:
                raise ApiError(400, "BAD_QTY", "入库数量必须大于 0")
            bno, prod, exp = body["batch_no"], body["prod_date"], body["expire_date"]
            if exp <= prod:
                raise ApiError(400, "BAD_DATE", "到期日必须晚于生产日")
            if c.execute("SELECT 1 FROM batch WHERE sku_id=? AND batch_no=?",
                         (sku["id"], bno)).fetchone():
                raise ApiError(409, "BATCH_EXISTS", "批次已存在：%s" % bno)
            c.execute("INSERT INTO batch VALUES (?,?,?,?,?,?,0,?, '待检')",
                      ("B-" + bno, sku["id"], bno, prod, exp, qty,
                       int(body.get("unit_cost_cent") or 0)))
            c.execute("INSERT INTO stock_move VALUES (?,?,?,?,?,?,?)",
                      ("M-%s" % bno, "PI", bno, sku["id"], bno, qty, TODAY.isoformat()))
            audit(c, user, "stock.inbound", bno, "%s × %d" % (sku["sku_code"], qty))
            c.commit()
            return {"ok": True, "batch_no": bno, "qty": qty, "qc_status": "待检",
                    "note": "待检批次不可出库，需质检放行"}

        m = re.match(r"^/api/batches/([^/]+)/release$", path)
        if m:
            need(user, "boss", "wh")
            bno = m.group(1)
            r = c.execute("UPDATE batch SET qc_status='合格' WHERE batch_no=? AND qc_status='待检'",
                          (bno,))
            if r.rowcount == 0:
                raise ApiError(409, "BAD_STATE", "批次不存在或不是待检状态：%s" % bno)
            audit(c, user, "stock.release", bno)
            c.commit()
            return {"ok": True, "batch_no": bno, "qc_status": "合格"}

        if path == "/api/orders":
            need(user, "boss", "cs", "sales", "store")
            store_id = body["store_id"]
            if user["role"] == "store" and store_id != user.get("store_id"):
                raise ApiError(403, "FORBIDDEN_STORE", "门店账号只能给自己下单")
            order_no, total = create_order(c, user, store_id, body["lines"],
                                           body.get("channel", "store"))
            return {"ok": True, "order_no": order_no, "amount": total / 100,
                    "order": order_detail(c, user, order_no)}

        m = re.match(r"^/api/orders/([A-Z0-9\-]+)/(pick|ship)$", path)
        if m:
            need(user, "boss", "cs", "wh", "driver")
            no, act = m.group(1), m.group(2)
            if act == "pick":
                return {"ok": True, "order": pick_order(c, user, no)}
            return {"ok": True, **ship_order(c, user, no)}

        m = re.match(r"^/api/deliveries/([A-Z0-9\-]+)/sign$", path)
        if m:
            need(user, "boss", "cs", "wh", "driver", "store")
            return {"ok": True, **sign_delivery(c, user, m.group(1),
                                                body.get("receiver", ""), body.get("note", ""))}

        if path == "/api/demo/setup":
            demo_only()
            need(user, "boss")
            c.execute("""UPDATE sku SET units_per_carton=20, price_cent=840, cost_cent=590,
                         shelf_life_month=6, storage_cond='常温'
                         WHERE sku_code='KY001-1K-B-1'""")
            c.execute("""UPDATE sku SET units_per_carton=20, price_cent=900, cost_cent=640,
                         shelf_life_month=12, storage_cond='常温'
                         WHERE sku_code='HG003-1K-B-1'""")
            c.execute("""UPDATE sku SET units_per_carton=12, price_cent=800, cost_cent=560,
                         shelf_life_month=6, storage_cond='常温'
                         WHERE sku_code='DL007-1K-B-1'""")
            made = []
            for code, bno, qty, prod, exp in [
                    ("KY001-1K-B-1", "P2611011-A-001", 100, "2026-11-01", "2027-05-01"),
                    ("KY001-1K-B-1", "P2611051-A-002", 300, "2026-11-05", "2027-05-05"),
                    ("HG003-1K-B-1", "P2611021-B-001", 200, "2026-11-02", "2027-11-02"),
                    ("DL007-1K-B-1", "P2611031-C-001", 120, "2026-11-03", "2027-05-03")]:
                if c.execute("SELECT 1 FROM batch WHERE batch_no=?", (bno,)).fetchone():
                    continue
                sku = get_sku(c, code)
                c.execute("INSERT INTO batch VALUES (?,?,?,?,?,?,0,?, '合格')",
                          ("B-" + bno, sku["id"], bno, prod, exp, qty, 700))
                c.execute("INSERT INTO stock_move VALUES (?,?,?,?,?,?,?)",
                          ("M-" + bno, "PI", bno, sku["id"], bno, qty, TODAY.isoformat()))
                made.append(bno)
            c.commit()
            audit(c, user, "demo.setup", "", "batches=%s" % len(made))
            return {"ok": True, "filled": 3, "batches": made}

        if path == "/api/demo/orders":
            demo_only()
            need(user, "boss")
            created = []
            for store_id, code, cartons in [("S001", "KY001-1K-B-1", 2),
                                            ("S002", "KY001-1K-B-1", 1),
                                            ("S003", "HG003-1K-B-1", 2),
                                            ("S004", "DL007-1K-B-1", 1)]:
                existed = c.execute(
                    "SELECT COUNT(*) FROM order_h WHERE store_id=?", (store_id,)).fetchone()[0]
                if existed:
                    continue
                try:
                    no, amt = create_order(c, user, store_id,
                                           [{"sku_code": code, "cartons": cartons}], "sales")
                    created.append(no)
                except ApiError:
                    pass
            return {"ok": True, "orders": created}

        if path == "/api/workorders/from-orders":
            need(user, "boss", "wh")
            frag, params = A.store_where(user, "s")
            rows = c.execute(
                """SELECT k.sku_code, k.name, SUM(l.qty_unit) AS q
                   FROM order_l l
                   JOIN order_h o ON o.id = l.order_id
                   JOIN store s ON s.id = o.store_id
                   JOIN sku k ON k.id = l.sku_id
                   WHERE o.status IN ('已确认','待发货') AND """ + frag +
                " GROUP BY k.sku_code ORDER BY k.sku_code", params).fetchall()
            if not rows:
                raise ApiError(400, "NO_ORDER", "没有待生产的订单（需要「已确认」或「待发货」）")
            seq0 = c.execute("SELECT COUNT(*) FROM work_order").fetchone()[0]
            made = []
            for i, r in enumerate(rows, 1):
                wo = "WO%s-%03d" % (TODAY.strftime("%y%m%d"), seq0 + i)
                if c.execute("SELECT 1 FROM work_order WHERE wo_no=?", (wo,)).fetchone():
                    continue
                c.execute("""INSERT INTO work_order
                             (id,wo_no,sku_code,sku_name,qty_unit,due_day,status,created_at)
                             VALUES (?,?,?,?,?,?, '待排', ?)""",
                          (wo, wo, r["sku_code"], r["name"], r["q"], 3, TODAY.isoformat()))
                made.append(wo)
            audit(c, user, "workorder.create", "from-orders", "%d 张" % len(made))
            c.commit()
            return {"ok": True, "created": made}

        if path == "/api/schedule/run":
            need(user, "boss", "wh")
            strategy = body.get("strategy", "family")
            wos = load_wos(c)
            if not wos:
                raise ApiError(400, "NO_WORKORDER", "还没有工单，先点「从订单生成工单」")
            res = SCH.run(wos, load_matrix(c), None, strategy)
            m = res["metrics"]
            c.execute("INSERT INTO schedule_run(at,strategy,metrics) VALUES (?,?,?)",
                      (TODAY.isoformat() + " " + _now(), strategy,
                       json.dumps(m, ensure_ascii=False)))
            audit(c, user, "schedule.run", strategy,
                  "换线 %d 次 / %d 分 / 完工 %.2f 天" % (m["clean_count"],
                                                     m["clean_total_min"], m["makespan_day"]))
            c.commit()
            return {"ok": True, **res}

        if path == "/api/schedule/matrix":
            need(user, "boss")
            f, t = body["from"], body["to"]
            if f not in SCH.FAMILIES or t not in SCH.FAMILIES or f == t:
                raise ApiError(400, "BAD_FAMILY", "族名不对")
            minutes = int(body["minutes"])
            if minutes < 0 or minutes > 480:
                raise ApiError(400, "BAD_MINUTES", "换线分钟应在 0~480 之间")
            c.execute("""INSERT OR REPLACE INTO changeover(from_family,to_family,minutes)
                         VALUES (?,?,?)""", (f, t, minutes))
            audit(c, user, "schedule.matrix", "%s→%s" % (f, t), "%d 分" % minutes)
            c.commit()
            return {"ok": True, "from": f, "to": t, "minutes": minutes}

        if path == "/api/demo/workorders":
            demo_only()
            need(user, "boss")
            demo = [
                ("KY001-1K-B-1", "万州烤鱼酱", 600, 3), ("HG003-1K-B-1", "麻辣火锅底料", 400, 3),
                ("CS004-1K-B-1", "把子肉酱", 300, 4), ("KY003-1K-B-1", "香辣烤鱼酱", 500, 4),
                ("TD001-1K-B-1", "老母鸡底汤", 700, 5), ("DL007-1K-B-1", "麻辣红油", 250, 3),
                ("HG001-1K-B-1", "老火锅底料", 450, 5), ("MD001-1K-B-1", "重庆小面酱", 200, 6),
                ("KY005-1K-B-1", "小米辣烤鱼酱", 300, 6), ("CS012-1K-B-1", "黑胡椒酱", 350, 5),
                ("YL001-1K-B-1", "香辣油泼辣子", 180, 6), ("TD006-1K-B-1", "米线高汤", 260, 6)]
            created = []
            for i, (code, name, qty, due) in enumerate(demo, 1):
                wo = "WO%s-%03d" % (TODAY.strftime("%y%m%d"), 100 + i)
                if c.execute("SELECT 1 FROM work_order WHERE wo_no=?", (wo,)).fetchone():
                    continue
                c.execute("""INSERT INTO work_order
                             (id,wo_no,sku_code,sku_name,qty_unit,due_day,status,created_at)
                             VALUES (?,?,?,?,?,?, '待排', ?)""",
                          (wo, wo, code, name, qty, due, TODAY.isoformat()))
                created.append(wo)
            audit(c, user, "demo.workorders", "", "%d 张" % len(created))
            c.commit()
            return {"ok": True, "created": created}

        if path == "/api/visits/checkin":
            need(user, "boss", "sales", "director")
            store_id = body["store_id"]
            assert_store(c, user, store_id)
            s = c.execute("SELECT * FROM store WHERE id=?", (store_id,)).fetchone()
            lng = float(body.get("lng") or 0)
            lat = float(body.get("lat") or 0)
            acc = float(body.get("accuracy_m") or 0)
            if not lng or not lat:
                raise ApiError(400, "NO_LOCATION", "没拿到定位，无法打卡")
            dist = None
            if s["lng"] and s["lat"]:
                dist = haversine(lat, lng, s["lat"], s["lng"])
            reasons = []
            if dist is None:
                reasons.append("该门店缺坐标，无法判断是否到店")
            elif dist > 200:
                reasons.append("距离 %d 米，超出 200 米围栏" % round(dist))
            if acc and acc > 100:
                reasons.append("定位精度 %d 米，超过 100 米" % round(acc))
            valid = 1 if not reasons else 0
            photos = body.get("photos") or []
            seq = c.execute("SELECT COUNT(*) FROM visit").fetchone()[0] + 1
            vid = "V%s-%03d" % (TODAY.strftime("%y%m%d"), seq)
            from datetime import datetime
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            c.execute("""INSERT INTO visit(id,store_id,user_id,checkin_at,lng,lat,accuracy_m,
                         distance_m,purpose,result,note,photos,valid_flag,invalid_reason)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (vid, store_id, user["id"], now, lng, lat, acc, dist,
                       body.get("purpose", "回访"), body.get("result", ""),
                       body.get("note", ""), json.dumps(photos, ensure_ascii=False),
                       valid, "；".join(reasons)))
            audit(c, user, "visit.checkin", store_id,
                  "距离 %s 米 %s" % (round(dist) if dist is not None else "?",
                                    "有效" if valid else "无效"))
            c.commit()
            return {"ok": True, "visit_id": vid,
                    "distance_m": None if dist is None else round(dist, 1),
                    "valid": bool(valid),
                    "reason": "；".join(reasons) or "在围栏内，有效拜访"}

        if path == "/api/demo/dispatch":
            demo_only()
            need(user, "boss")
            made = []
            rows = c.execute("""SELECT o.order_no FROM order_h o JOIN store s ON s.id=o.store_id
                                WHERE o.status='已确认' AND s.route_code='CX-01'
                                ORDER BY o.order_no""").fetchall()
            for r in rows:
                try:
                    pick_order(c, user, r["order_no"])
                    res = ship_order(c, user, r["order_no"])
                    made.append(res["delivery_no"])
                except ApiError:
                    pass
            return {"ok": True, "deliveries": made}

        # ---------- 门店建档 ----------
        if path == "/api/stores":
            need(user, "boss", "director", "sales")
            name = (body.get("name") or "").strip()
            addr = (body.get("address") or "").strip()
            if not name or not addr:
                raise ApiError(400, "BAD_INPUT", "门店名和地址都要填")
            seq = c.execute("SELECT COUNT(*) FROM store").fetchone()[0] + 1
            sid = "S%03d" % seq
            owner = user["id"] if user["role"] == "sales" else (body.get("owner_user") or user["id"])
            region = body.get("region_id") or user.get("region_id")
            route = body.get("route_code") or user.get("route_code")
            lng = body.get("lng")
            lat = body.get("lat")
            c.execute("""INSERT INTO store(id,name,settle_type,credit_limit_cent,owner_user,
                         address,region_id,route_code,lng,lat)
                         VALUES (?,?,?,?,?,?,?,?,?,?)""",
                      (sid, name, body.get("settle_type") or "cash",
                       int(body.get("credit_limit_cent") or 0), owner, addr, region, route,
                       float(lng) if lng else None, float(lat) if lat else None))
            audit(c, user, "store.create", sid, "%s / %s" % (name, addr))
            c.commit()
            return {"ok": True, "store": {"id": sid, "name": name, "address": addr,
                                          "owner": owner, "region": region, "route": route,
                                          "lng": lng, "lat": lat}}

        # ---------- 图片上传（打卡照片用，base64，避免 multipart） ----------
        if path == "/api/upload":
            name = body.get("name") or "photo.jpg"
            b64 = body.get("b64") or ""
            if not b64:
                raise ApiError(400, "BAD_INPUT", "没有图片数据")
            try:
                raw = base64.b64decode(b64)
            except Exception:
                raise ApiError(400, "BAD_BASE64", "图片数据不是合法 base64")
            if len(raw) > 4 * 1024 * 1024:
                raise ApiError(413, "TOO_LARGE", "图片超过 4MB，请压缩后再传")
            ext = os.path.splitext(name)[1].lower()
            if ext not in (".jpg", ".jpeg", ".png", ".webp"):
                raise ApiError(400, "BAD_TYPE", "只接受 jpg/png/webp")
            d = UPLOADS
            os.makedirs(d, exist_ok=True)
            fn = uuid.uuid4().hex + ext
            with open(os.path.join(d, fn), "wb") as f:
                f.write(raw)
            audit(c, user, "upload", fn, "%d 字节" % len(raw))
            c.commit()
            return {"ok": True, "url": "/uploads/" + fn, "size": len(raw)}

        # ---------- 回款核销 ----------
        if path == "/api/payments":
            need(user, "boss", "fin")
            order_no = body.get("order_no") or ""
            r = c.execute("SELECT * FROM receivable WHERE order_no=?", (order_no,)).fetchone()
            if not r:
                raise ApiError(404, "AR_NOT_FOUND", "找不到这张应收：%s" % order_no)
            if r["status"] == "未出账":
                raise ApiError(409, "NOT_BILLED", "这张单还没签收，先签收再收款")
            amt = int(body.get("amount_cent") or 0)
            if amt <= 0:
                raise ApiError(400, "BAD_AMOUNT", "金额必须大于 0")
            left = r["amount_cent"] - r["paid_cent"]
            if amt > left:
                raise ApiError(400, "OVER_PAY", "本次 %d 分超过未收余额 %d 分" % (amt, left))
            paid = r["paid_cent"] + amt
            status = "已收" if paid >= r["amount_cent"] else "部分"
            c.execute("UPDATE receivable SET paid_cent=?, status=? WHERE id=?",
                      (paid, status, r["id"]))
            audit(c, user, "payment.create", order_no,
                  "¥%.2f %s → %s" % (amt / 100, body.get("method") or "转账", status))
            c.commit()
            return {"ok": True, "order_no": order_no, "paid": paid / 100,
                    "left": (r["amount_cent"] - paid) / 100, "status": status}

        if path == "/api/auth/unlock":
            need(user, "boss")
            target = body.get("username") or ""
            r = c.execute("UPDATE app_user SET fail_count=0, locked_until=NULL WHERE username=?",
                          (target,))
            if r.rowcount == 0:
                raise ApiError(404, "NO_USER", "没有这个账号：%s" % target)
            audit(c, user, "user.unlock", target, "")
            c.commit()
            return {"ok": True, "username": target, "unlocked": True}

        if path == "/api/reset":
            demo_only()
            need(user, "boss")
            c.close()
            init_db(force=True)
            return {"ok": True, "reset": True}

        raise ApiError(404, "NO_ROUTE", "未知接口：%s" % path)
    finally:
        try:
            c.close()
        except Exception:
            pass


# ============================================================
# HTTP
# ============================================================
class Handler(BaseHTTPRequestHandler):
    server_version = "P1/1.1"

    def log_message(self, *a):
        pass

    def _json(self, status, obj):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.end_headers()
        self.wfile.write(raw)

    def _file(self, name):
        p = os.path.join(WEB, name)
        if not os.path.exists(p):
            return self._json(404, {"ok": False, "code": "NOT_FOUND", "message": "文件不存在"})
        raw = open(p, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8"
                         if name.endswith(".html") else "application/octet-stream")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _user(self):
        """解析 Authorization: Bearer <token>，失败返回 None。"""
        h = self.headers.get("Authorization") or ""
        if not h.startswith("Bearer "):
            return None
        payload = A.unsign(h[7:].strip(), SECRET)
        if not payload:
            return None
        c = conn()
        try:
            return user_by_id(c, payload["uid"])
        finally:
            c.close()

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            return self._file("index.html")
        if u.path in ("/store", "/store.html"):
            return self._file("store.html")
        if u.path in ("/driver", "/driver.html"):
            return self._file("driver.html")
        if u.path.startswith("/uploads/"):
            fn = os.path.basename(u.path)
            p = os.path.join(UPLOADS, fn)
            if not os.path.exists(p):
                return self._json(404, {"ok": False, "code": "NOT_FOUND", "message": "图片不存在"})
            raw = open(p, "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(p)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(raw)
            return
        if not u.path.startswith("/api/"):
            return self._file(u.path.lstrip("/"))
        if u.path in ("/api/auth/accounts", "/api/health"):
            try:
                return self._json(200, api_get(u.path, parse_qs(u.query), None))
            except ApiError as e:
                return self._json(e.status, e.payload)
            except Exception as e:
                return self._json(500, {"ok": False, "code": "INTERNAL", "message": str(e)})
        user = self._user()
        if not user:
            return self._json(401, {"ok": False, "code": "UNAUTHORIZED", "message": "请先登录"})
        try:
            return self._json(200, api_get(u.path, parse_qs(u.query), user))
        except ApiError as e:
            return self._json(e.status, e.payload)
        except Exception as e:
            return self._json(500, {"ok": False, "code": "INTERNAL", "message": str(e)})

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}") if n else {}
        if u.path == "/api/auth/login":
            c = conn()
            try:
                return self._json(200, api_post(u.path, body, None))
            except ApiError as e:
                return self._json(e.status, e.payload)
            finally:
                c.close()
        user = self._user()
        if not user:
            return self._json(401, {"ok": False, "code": "UNAUTHORIZED", "message": "请先登录"})
        try:
            return self._json(200, api_post(u.path, body, user))
        except ApiError as e:
            return self._json(e.status, e.payload)
        except Exception as e:
            return self._json(500, {"ok": False, "code": "INTERNAL", "message": str(e)})


def demo_pw_users():
    """生产启动时检查：还有谁在用演示密码。"""
    c = conn()
    try:
        rows = c.execute("SELECT username, pw_salt, pw_hash FROM app_user WHERE active=1").fetchall()
        return [r["username"] for r in rows
                if A.verify_password(DEMO_PW, r["pw_salt"], r["pw_hash"])]
    finally:
        c.close()


def main():
    # Codespaces / 容器里必须绑 0.0.0.0，否则端口代理进不来（表现为"已重置连接"）
    in_cloud = bool(os.environ.get("CODESPACE_NAME") or os.environ.get("CONTAINER_HOST"))
    host = os.environ.get("P1_HOST") or ("0.0.0.0" if (PROD or in_cloud) else "127.0.0.1")
    port = int(os.environ.get("P1_PORT") or 8765)
    for i, a in enumerate(sys.argv):
        if a == "--port" and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])
        if a == "--host" and i + 1 < len(sys.argv):
            host = sys.argv[i + 1]

    fresh = init_db()
    print("[p1] env=%s data=%s fresh=%s" % (ENV, DATA, fresh))
    if PROD:
        weak = demo_pw_users()
        if weak:
            print("[p1] !! 警告：以下账号还在用演示密码 %s，上线前必须改：%s"
                  % (DEMO_PW, "、".join(weak)))
        print("[p1] 生产模式：演示接口（reset / demo/*）与账号列表已关闭")
    else:
        print("[p1] 演示账号：boss / director / sales1~3 / cs / wh / driver1 / store1 / fin，密码 %s" % DEMO_PW)
    print("[p1] listening http://%s:%d/" % (host, port))
    srv = ThreadingHTTPServer((host, port), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[p1] bye")


if __name__ == "__main__":
    main()
