# -*- coding: utf-8 -*-
"""领星 ERP 开放平台自动对接：鉴权 + 数据拉取 + 写入本地库。

凭证获取（一次性）：
  领星 ERP → 设置 → 业务配置 → 全局 → 开放接口
  获取 AppId / AppSecret，并到同一页面把本系统部署服务器的【外网 IP】加入白名单。

已核实的官方接口路径（apidoc.lingxing.com，2026-08）：
  鉴权     ： POST {host}/api/auth-server/oauth/access-token
             （multipart: appId, appSecret）→ data.access_token（约 2 小时有效，过期自动重取）
  FBA 库存 ： POST {host}/basicOpen/openapi/storage/fbaWarehouseDetail        （v2，亚马逊 FBA）
  多平台库存： POST {host}/basicOpen/multiplatform/full/stockSearch            （中国仓/沃尔玛/其他，含 storeIdList）
  Temu 库存 ： POST {host}/basicOpen/multiplatform/fbt/stockSearch             （Temu/FBT）
  日销量    ： POST {host}/basicOpen/platformStatisticsV2/saleStat/pageList    （按日、按 SKU 维度；V1 已 04.30 下线，必须用 V2）

返回结构差异（已分别适配）：
  FBA       → data.list[]
  多平台/FBT → data.records[]
  日销量 V2  → data[]（数组直出）
  统一由 _list() 兼容 data / data.list / data.records 三种形态。

字段映射为多候选兜底（领星不同店铺/版本字段名略有差异），并支持 LINGXING_DEBUG=1
打印首条记录真实字段名，便于首次接入时按需校准。

未配置凭证 / 调用失败 → 自动跳过该部分并告警，不破坏系统（仍可走 Excel 兜底）。
"""
import os
import json
import time
import logging
from datetime import date, timedelta

import requests

log = logging.getLogger("lingxing")

DEFAULT_HOST = "https://openapi.lingxing.com"

# ============ 官方接口路径（常量固化，可用环境变量覆盖） ============
FBA_STOCK_PATH = os.environ.get("LINGXING_FBA_STOCK_PATH", "/basicOpen/openapi/storage/fbaWarehouseDetail")
FULL_STOCK_PATH = os.environ.get("LINGXING_FULL_STOCK_PATH", "/basicOpen/multiplatform/full/stockSearch")
FBT_STOCK_PATH = os.environ.get("LINGXING_FBT_STOCK_PATH", "/basicOpen/multiplatform/fbt/stockSearch")
DAILY_SALES_PATH = os.environ.get("LINGXING_DAILY_SALES_PATH", "/basicOpen/platformStatisticsV2/saleStat/pageList")

DEBUG = os.environ.get("LINGXING_DEBUG") == "1"

# 领星仓库名关键词 → 本系统 warehouse 桶。命中即归桶；不命中则忽略。
WAREHOUSE_MAP = {
    "中国仓": "中国仓", "国内": "中国仓", "本地仓": "中国仓", "domestic": "中国仓",
    "沃尔玛": "walmart", "wal-mart": "walmart", "walmart": "walmart",
    "其他": "other", "其它": "other", "other": "other",
}


class LingxingError(Exception):
    pass


def _num(v):
    if v is None or v == "":
        return 0
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0


def _first_keys(sample, label):
    """DEBUG 模式下打印首条记录字段名，便于校准字段映射。"""
    if not DEBUG or not sample:
        return
    keys = list(sample[0].keys()) if isinstance(sample, list) else list(sample.keys())
    log.warning("[LINGXING_DEBUG] %s 首条字段: %s", label, keys)


class LingxingClient:
    def __init__(self, app_id=None, app_secret=None, host=None, timeout=30):
        self.app_id = app_id or os.environ.get("LINGXING_APP_ID")
        self.app_secret = app_secret or os.environ.get("LINGXING_APP_SECRET")
        self.host = (host or os.environ.get("LINGXING_HOST") or DEFAULT_HOST).rstrip("/")
        self.timeout = timeout
        self._token = None
        self._token_exp = 0
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "cwms/1.1"})

    # ---------- 鉴权 ----------
    def is_configured(self):
        return bool(self.app_id and self.app_secret)

    def get_token(self, force=False):
        if not force and self._token and time.time() < self._token_exp - 60:
            return self._token
        if not self.is_configured():
            raise LingxingError("未配置领星 AppId / AppSecret（请在配置页或环境变量中设置）")
        url = f"{self.host}/api/auth-server/oauth/access-token"
        resp = self.session.post(
            url, data={"appId": self.app_id, "appSecret": self.app_secret}, timeout=self.timeout
        )
        try:
            data = resp.json()
        except Exception:
            raise LingxingError(f"领星鉴权返回非 JSON：HTTP {resp.status_code}")
        if str(data.get("code")) not in ("200", "0", ""):
            raise LingxingError(
                f"领星鉴权失败：{data.get('msg')} (code={data.get('code')}, http={resp.status_code})"
            )
        d = data.get("data") or {}
        self._token = d.get("access_token")
        if not self._token:
            raise LingxingError("领星鉴权成功但未返回 access_token")
        self._token_exp = time.time() + int(d.get("expires_in", 7199))
        return self._token

    # ---------- 通用 POST ----------
    def _post(self, path, payload):
        token = self.get_token()
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
        url = f"{self.host}{path}"
        resp = self.session.post(url, json=payload, headers=headers, timeout=self.timeout)
        try:
            data = resp.json()
        except Exception:
            raise LingxingError(f"接口 {path} 返回非 JSON：HTTP {resp.status_code}")
        if str(data.get("code")) not in ("200", "0", ""):
            raise LingxingError(f"接口 {path} 失败：{data.get('msg')} (code={data.get('code')})")
        return data

    @staticmethod
    def _list(data):
        """兼容三种返回形态：data 为数组 / data.list / data.records。"""
        d = data.get("data")
        if isinstance(d, list):
            return d
        if isinstance(d, dict):
            for k in ("list", "records", "items"):
                if isinstance(d.get(k), list):
                    return d[k]
        if isinstance(d, dict) and "list" in d and isinstance(d.get("list"), list):
            return d["list"]
        return []

    # ---------- 各数据接口 ----------
    def fetch_fba_inventory(self, sid_list, page_size=200):
        """FBA 总库存（亚马逊）。返回 {sku: quantity}。
        FBA 用 sid（逗号分隔字符串）；返回 data.list[]；SKU 取 seller_sku/sku/msku/fnsku，
        数量取 afn_fulfillable_quantity（可售，用于可售天数计算）。"""
        sids = ",".join(sid_list) if isinstance(sid_list, list) else sid_list
        if not sids:
            raise LingxingError("未配置亚马逊店铺 sid（lingxing_sids_amazon）")
        out = {}
        offset = 0
        while True:
            payload = {"sid": sids, "offset": offset, "length": page_size, "is_hide_zero_stock": 0}
            data = self._post(FBA_STOCK_PATH, payload)
            lst = self._list(data)
            if not lst:
                break
            _first_keys(lst, "FBA")
            for it in lst:
                sku = (it.get("seller_sku") or it.get("sku") or it.get("msku") or it.get("fnsku") or "").strip()
                qty = (_num(it.get("afn_fulfillable_quantity"))
                       or _num(it.get("total_fulfillable_quantity"))
                       or _num(it.get("quantity")) or _num(it.get("availableQty")))
                if sku:
                    out[sku] = out.get(sku, 0) + qty
            if len(lst) < page_size:
                break
            offset += page_size
        return out

    def fetch_full_inventory(self, store_id_list=None, page_size=200):
        """多平台 FULL 库存（中国仓/沃尔玛/其他等）。返回 {sku: {warehouse_bucket: qty}}。
        不传 storeIdList 时拉取全部店铺库存，再按仓库名映射到本系统桶。
        返回 data.records[]；SKU 取 sku/skc/goodsId，仓库取 warehouseName/whName/wname，
        数量取 stockNum/availableQty/quantity。"""
        out = {}
        offset = 0
        while True:
            payload = {"length": page_size, "offset": offset,
                       "selectTypeEnum": "COUNT_TYPE", "hideZeroStorage": 0}
            if store_id_list:
                payload["storeIdList"] = store_id_list
            data = self._post(FULL_STOCK_PATH, payload)
            lst = self._list(data)
            if not lst:
                break
            _first_keys(lst, "FULL")
            for it in lst:
                sku = (it.get("sku") or it.get("skc") or it.get("goodsId") or it.get("skuCode") or "").strip()
                wh = it.get("warehouseName") or it.get("whName") or it.get("wname") or ""
                qty = (_num(it.get("stockNum")) or _num(it.get("availableQty"))
                       or _num(it.get("quantity")) or _num(it.get("stockQuantity")))
                if sku and wh:
                    bucket = self._map_warehouse(wh)
                    if bucket:
                        out.setdefault(sku, {})
                        out[sku][bucket] = out[sku].get(bucket, 0) + qty
            if len(lst) < page_size:
                break
            offset += page_size
        return out

    def fetch_temu_inventory(self, store_id_list=None, page_size=200):
        """Temu 库存。返回 {sku: quantity}。返回 data.records[]。"""
        out = {}
        offset = 0
        while True:
            payload = {"length": page_size, "offset": offset}
            if store_id_list:
                payload["storeIdList"] = store_id_list
            data = self._post(FBT_STOCK_PATH, payload)
            lst = self._list(data)
            if not lst:
                break
            _first_keys(lst, "FBT(temu)")
            for it in lst:
                sku = (it.get("skc") or it.get("sku") or it.get("skuCode") or "").strip()
                qty = (_num(it.get("stockNum")) or _num(it.get("availableQty"))
                       or _num(it.get("quantity")) or _num(it.get("stockQuantity")))
                if sku:
                    out[sku] = out.get(sku, 0) + qty
            if len(lst) < page_size:
                break
            offset += page_size
        return out

    def _map_warehouse(self, wh_name):
        name = str(wh_name).lower()
        for key, bucket in WAREHOUSE_MAP.items():
            if key.lower() in name:
                return bucket
        return None

    def fetch_daily_sales(self, sid_list, start_date, end_date, page_size=200):
        """日销量（按 SKU、按日期）。返回 {sku: {yyyy-mm-dd: qty}}。
        接口：platformStatisticsV2/saleStat/pageList（按日 date_unit=4、按 SKU data_type=4、销量 result_type=1）。
        sids 接受数组（亚马逊 sid 与多平台 store_id 可混用）。"""
        out = {}
        for sid in sid_list:
            offset = 0
            page = 1
            while True:
                payload = {
                    "sids": [sid],
                    "start_date": start_date,
                    "end_date": end_date,
                    "result_type": "1",   # 销量
                    "date_unit": "4",     # 日
                    "data_type": "4",     # SKU
                    "page": page,
                    "length": page_size,
                }
                try:
                    data = self._post(DAILY_SALES_PATH, payload)
                except LingxingError as e:
                    raise LingxingError(
                        f"日销量接口({DAILY_SALES_PATH})调用失败：{e}。"
                        f"请按 apidoc.lingxing.com 校准路径（环境变量 LINGXING_DAILY_SALES_PATH）与字段映射。"
                    )
                lst = self._list(data)
                if not lst:
                    break
                _first_keys(lst, "日销量")
                for it in lst:
                    sku = (it.get("sku") or it.get("skuCode") or it.get("msku")
                           or it.get("sellerSku") or it.get("seller_sku") or "").strip()
                    d = (it.get("date") or it.get("statDate") or it.get("stat_date")
                         or it.get("daily") or it.get("saleDate") or "")
                    qty = (_num(it.get("saleVolume")) or _num(it.get("saleNum"))
                           or _num(it.get("salesVolume")) or _num(it.get("quantity"))
                           or _num(it.get("saleQty")))
                    if sku and d:
                        out.setdefault(sku, {})[str(d)[:10]] = out.get(sku, {}).get(str(d)[:10], 0) + qty
                if len(lst) < page_size:
                    break
                offset += page_size
                page += 1
        return out


# ===================== 同步到本地库 + 同步日志 =====================
def _style_of(sku):
    s = str(sku)
    return s.split("-")[0].rstrip("Bb") if "-" in s else s


def _windows(daily, today):
    """daily: {yyyy-mm-dd: qty} → (d3, d7, d14, d30, d60)。近 N 天求和（缺失记 0）。"""
    def s_last(n):
        tot = 0
        for i in range(n):
            d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            tot += daily.get(d, 0)
        return tot
    return s_last(3), s_last(7), s_last(14), s_last(30), s_last(60)


def _write_sync_log(started_at, finished_at, status, detail, error):
    """写入同步日志表（与业务库共用连接）。"""
    try:
        import sqlite3
        from .database import DB_PATH
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            """INSERT INTO sync_log(started_at, finished_at, status, detail, error)
               VALUES(?,?,?,?,?)""",
            (started_at, finished_at, status, json.dumps(detail, ensure_ascii=False), error or ""),
        )
        conn.commit()
        conn.close()
    except Exception as e:  # noqa
        log.warning("写入同步日志失败（不影响主流程）：%s", e)


def build_client_from_config(conn):
    rows = conn.execute(
        "SELECT param_key, param_value FROM config WHERE param_key LIKE 'lingxing_%'"
    ).fetchall()
    cfg = {r["param_key"]: r["param_value"] for r in rows}
    return LingxingClient(
        app_id=cfg.get("lingxing_app_id"),
        app_secret=cfg.get("lingxing_app_secret"),
        host=cfg.get("lingxing_host"),
    ), cfg


def _sid_list(cfg, key):
    raw = cfg.get(key, "")
    return [s.strip() for s in str(raw).split(",") if s.strip()]


def sync_from_lingxing():
    """从领星 ERP 自动拉取销售/库存写入本地库，并写入同步日志。返回结果摘要。"""
    from .database import get_conn
    from .data_import import ensure_default_lifecycle

    started_at = time.strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn()
    client, cfg = build_client_from_config(conn)
    if not client.is_configured():
        conn.close()
        msg = "未配置领星凭证（lingxing_app_id / lingxing_app_secret）"
        _write_sync_log(started_at, time.strftime("%Y-%m-%d %H:%M:%S"), "failed", {}, msg)
        return {"ok": False, "error": msg,
                "detail": "请在「系统设置 → 领星对接」中填写，或设置环境变量 LINGXING_APP_ID/SECRET。"}

    result = {"ok": True, "steps": [], "warnings": []}
    today = date.today()
    start = (today - timedelta(days=61)).strftime("%Y-%m-%d")
    end = today.strftime("%Y-%m-%d")

    sids_amazon = _sid_list(cfg, "lingxing_sids_amazon")
    sids_walmart = _sid_list(cfg, "lingxing_sids_walmart")
    sids_other = _sid_list(cfg, "lingxing_sids_other")
    sids_temu = _sid_list(cfg, "lingxing_sids_temu")

    c = conn.cursor()
    try:
        # ---------- 销量：按平台拉取日销量 → 5 段窗口 ----------
        platform_sids = {
            "amazon": sids_amazon, "walmart": sids_walmart,
            "other": sids_other, "temu": sids_temu,
        }
        for platform, sids in platform_sids.items():
            if not sids:
                result["steps"].append(f"{platform}: 未配置店铺 sid，跳过销量")
                continue
            try:
                daily = client.fetch_daily_sales(sids, start, end)
            except LingxingError as e:
                result["warnings"].append(f"{platform} 日销量拉取失败：{e}")
                continue
            merged = {}
            for sku, bydate in daily.items():
                m = merged.setdefault(sku, {})
                for d, q in bydate.items():
                    m[d] = m.get(d, 0) + q
            for sku, bydate in merged.items():
                d3, d7, d14, d30, d60 = _windows(bydate, today)
                style = _style_of(sku)
                c.execute("INSERT OR IGNORE INTO skus(sku, style_code) VALUES(?,?)", (sku, style))
                c.execute(
                    """INSERT OR REPLACE INTO sales_agg(sku, platform, d3,d7,d14,d30,d60)
                       VALUES(?,?,?,?,?,?,?)""",
                    (sku, platform, d3, d7, d14, d30, d60),
                )
            result["steps"].append(f"{platform}: 销量已更新（{len(merged)} 个 SKU）")

        # ---------- 库存 ----------
        # FBA（亚马逊）—— 用 sid（逗号串）
        if sids_amazon:
            try:
                fba = client.fetch_fba_inventory(sids_amazon)
                for sku, q in fba.items():
                    c.execute("INSERT OR IGNORE INTO skus(sku, style_code) VALUES(?,?)",
                              (sku, _style_of(sku)))
                    c.execute(
                        "INSERT OR REPLACE INTO inventory(sku, warehouse, quantity) VALUES(?,?,?)",
                        (sku, "amazon_fba", q),
                    )
                result["steps"].append(f"FBA库存已更新（{len(fba)} 个 SKU）")
            except LingxingError as e:
                result["warnings"].append(f"FBA库存拉取失败：{e}")
        else:
            result["steps"].append("FBA库存：未配置亚马逊 sid，跳过")

        # Temu —— 用 storeIdList（数组）
        if sids_temu:
            try:
                temu = client.fetch_temu_inventory(sids_temu)
                for sku, q in temu.items():
                    c.execute("INSERT OR IGNORE INTO skus(sku, style_code) VALUES(?,?)",
                              (sku, _style_of(sku)))
                    c.execute(
                        "INSERT OR REPLACE INTO inventory(sku, warehouse, quantity) VALUES(?,?,?)",
                        (sku, "temu", q),
                    )
                result["steps"].append(f"Temu库存已更新（{len(temu)} 个 SKU）")
            except LingxingError as e:
                result["warnings"].append(f"Temu库存拉取失败：{e}")

        # 多平台 FULL（中国仓 / 沃尔玛 / 其他）—— 全量拉取后按仓库名归桶
        try:
            full = client.fetch_full_inventory()  # 不传 storeIdList → 拉全部
            for sku, whmap in full.items():
                for wh, q in whmap.items():
                    c.execute("INSERT OR IGNORE INTO skus(sku, style_code) VALUES(?,?)",
                              (sku, _style_of(sku)))
                    c.execute(
                        "INSERT OR REPLACE INTO inventory(sku, warehouse, quantity) VALUES(?,?,?)",
                        (sku, wh, q),
                    )
            result["steps"].append(f"多平台库存已更新（{len(full)} 个 SKU）")
        except LingxingError as e:
            result["warnings"].append(f"多平台库存拉取失败：{e}")

        ensure_default_lifecycle()
        c.execute(
            "INSERT OR REPLACE INTO config(param_key,param_value) VALUES('lingxing_last_sync',?)",
            (end + " " + time.strftime("%H:%M:%S"),),
        )
        conn.commit()
    except LingxingError as e:
        conn.rollback()
        conn.close()
        finished_at = time.strftime("%Y-%m-%d %H:%M:%S")
        _write_sync_log(started_at, finished_at, "failed", result, str(e))
        return {"ok": False, "error": str(e), "steps": result["steps"], "warnings": result["warnings"]}
    finally:
        try:
            conn.close()
        except Exception:
            pass

    finished_at = time.strftime("%Y-%m-%d %H:%M:%S")
    status = "partial" if result["warnings"] else "success"
    _write_sync_log(started_at, finished_at, status, result, "")
    return result


def test_connection(app_id=None, app_secret=None, host=None):
    """测试领星凭证是否可用（仅做鉴权）。"""
    client = LingxingClient(app_id=app_id, app_secret=app_secret, host=host)
    if not client.is_configured():
        return {"ok": False, "error": "缺少 AppId / AppSecret"}
    try:
        tok = client.get_token(force=True)
        return {"ok": True, "token_length": len(tok)}
    except LingxingError as e:
        return {"ok": False, "error": str(e)}
