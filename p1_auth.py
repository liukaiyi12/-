# -*- coding: utf-8 -*-
"""
P1 鉴权与权限模型（独立模块，服务端和测试共用）。

只解决三个问题：
  1. 你是谁 —— PBKDF2 密码 + HMAC 签名 Token（标准库实现，无第三方）
  2. 你能看多远 —— 八种角色的行级数据范围
  3. 你能看多细 —— 成本/底价这类字段按角色脱敏

角色矩阵刻意做成「配置」而不是「散落在各处的 if」，
因为 B08（业务员多少人、怎么划区域）还没答案，
以后加角色、改范围只动这张表。
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time

ITER = 120_000
TTL_SECONDS = 8 * 3600

# ============================================================
# 角色矩阵
#   scope: all / region / own / route / store
#   see_cost: 能否看到成本与毛利（底价、配方同理）
#   can_operate: 能否推进订单（拣货/出库/签收）
# ============================================================
ROLES = {
    "boss":     {"cn": "老板",     "scope": "all",    "see_cost": True,  "can_operate": True,
                 "desc": "全部数据，全部动作"},
    "director": {"cn": "销售总监", "scope": "region", "see_cost": True,  "can_operate": False,
                 "desc": "本区域及其下级区域的全部门店与订单"},
    "sales":    {"cn": "业务员",   "scope": "own",    "see_cost": False, "can_operate": False,
                 "desc": "只看自己名下门店，看不到成本与底价"},
    "cs":       {"cn": "内勤",     "scope": "all",    "see_cost": False, "can_operate": True,
                 "desc": "全部订单，不含成本与价格权限"},
    "wh":       {"cn": "仓库",     "scope": "all",    "see_cost": False, "can_operate": True,
                 "desc": "库存与出入库，可推进订单"},
    "driver":   {"cn": "司机",     "scope": "route",  "see_cost": False, "can_operate": True,
                 "desc": "只看本线路的门店与配送单"},
    "store":    {"cn": "门店",     "scope": "store",  "see_cost": False, "can_operate": False,
                 "desc": "只看本门店订单与账"},
    "fin":      {"cn": "财务",     "scope": "all",    "see_cost": True,  "can_operate": False,
                 "desc": "资金口径，可见成本，不可改业务数据"},
}


# ============================================================
# 密码
# ============================================================
def hash_password(pw, salt=None):
    salt = salt or secrets.token_bytes(16)
    if isinstance(salt, str):
        salt = base64.b64decode(salt)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, ITER)
    return base64.b64encode(salt).decode(), base64.b64encode(h).decode()


def verify_password(pw, salt_b64, hash_b64):
    salt = base64.b64decode(salt_b64)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, ITER)
    return hmac.compare_digest(base64.b64encode(h).decode(), hash_b64)


# ============================================================
# Token（JWT 的简化版：payload.signature，都 base64url）
# ============================================================
def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def secret(path):
    """密钥落盘一次，重启后旧 Token 依然有效。"""
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(secrets.token_bytes(32))
    with open(path, "rb") as f:
        return f.read()


def sign(payload: dict, sec: bytes, ttl=TTL_SECONDS) -> str:
    body = dict(payload)
    body["exp"] = int(time.time()) + ttl
    raw = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()
    sig = hmac.new(sec, raw, hashlib.sha256).digest()
    return _b64(raw) + "." + _b64(sig)


def unsign(token: str, sec: bytes):
    try:
        raw_b64, sig_b64 = token.split(".")
        raw = _unb64(raw_b64)
        sig = _unb64(sig_b64)
        if not hmac.compare_digest(hmac.new(sec, raw, hashlib.sha256).digest(), sig):
            return None
        payload = json.loads(raw.decode())
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


# ============================================================
# 数据范围
# ============================================================
def can(role, what):
    r = ROLES.get(role)
    return bool(r and r.get(what))


def store_where(user, alias="s"):
    """
    返回 (sql_fragment, params)，用于过滤门店。
    user 是 app_user 的一行（dict）：id / role / region_path / store_id / route_code
    """
    role = user["role"]
    scope = ROLES.get(role, {}).get("scope", "none")
    if scope == "all":
        return "1=1", []
    if scope == "region":
        path = user.get("region_path") or ""
        if not path:
            return "1=0", []
        return "%s.region_id IN (SELECT id FROM region WHERE path LIKE ?)" % alias, [path + "%"]
    if scope == "own":
        return "%s.owner_user = ?" % alias, [user["id"]]
    if scope == "route":
        return "%s.route_code = ?" % alias, [user.get("route_code")]
    if scope == "store":
        return "%s.id = ?" % alias, [user.get("store_id")]
    return "1=0", []


def mask(role, row: dict, fields=("cost_cent",)):
    """按角色脱敏：没有成本权限的人，这些字段直接是 None，而不是前端藏起来。"""
    if can(role, "see_cost"):
        return row
    out = dict(row)
    for f in fields:
        if f in out:
            out[f] = None
    return out
