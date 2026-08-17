# -*- coding: utf-8 -*-
"""领星 ERP 开放平台自动对接：鉴权 + 数据拉取 + 写入本地库。

凭证获取（一次性）：
  领星 ERP → 设置 → 业务配置 → 全局 → 开放接口
  获取 AppId / AppSecret，并到同一页面把本系统部署服务器的【外网 IP】加入白名单。

已确认的接口事实（2026-08，apidoc.lingxing.com）：
  鉴权 ： POST {host}/api/auth-server/oauth/access-token   (multipart: appId, appSecret)
         返回 data.access_token（有效期内约 2 小时，过期自动重新获取）
  FBA库存： POST {host}/basicOpen/openapi/storage/fbaWarehouseDetail
  Temu库存：POST {host}/basicOpen/multiplatform/fbt/stockSearch
  多平台库存(含中国仓/沃尔玛/其他)：POST {host}/basicOpen/multiplatform/full/stockSearch
  日销量  ：见 DAILY_SALES_PATH（领星有 370+ 接口，日销量端点需按贵司实际 apidoc 校准，
            候选路径见下方常量注释；字段映射亦在该方法内集中说明，便于一处调整）

本模块与 Excel 上传通道并存：
  - 已配置领星凭证 → 优先用自动拉取；
  - 未配置 / 调用失败 → 自动跳过该部分并在结果中给出 warning，不破坏系统（仍可走 Excel 兜底）。
"""
import os
import time
import logging
from datetime import date, timedelta

import requests

log = logging.getLogger("lingxing")

DEFAULT_HOST = "https://openapi.lingxing.com"

# 日销量接口：领星日销量候选路径（请按 apidoc.lingxing.com 实际接口校准其一）。
# 常见候选：
#   /basicOpen/statistics/product/dailySaleList
#   /erp/sc/routing/product/product/dailySaleList
#   /basicOpen/statistics/asin/dailySaleList
# 该路径可通过环境变量 LINGXING_DAILY_SALES_PATH 覆盖，无需改代码。
DAILY_SALES_PATH = os.environ.get(
    "LINGXING_DAILY_SALES_PATH", "/basicOpen/statistics/product/dailySaleList"
)

# 领星仓库名关键词 → 本系统 warehouse 桶。命中即归桶；不命中则忽略（如海外 FBA 仓由专门接口处理）。
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


class LingxingClient:
    def __init__(self, app_id=None, app_secret=None, host=None, timeout=30):
        self.app_id = app_id or os.environ.get("LINGXING_APP_ID")
        self.app_secret = app_secret or os.environ.get("LINGXING_APP_SECRET")
        self.host = (host or os.environ.get("LINGXING_HOST") or DEFAULT_HOST).rstrip("/")
        self.timeout = timeout
        self._token = None
        self._token_exp = 0
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "cwms/1.0"})

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
        """领星返回结构不统一：有时是 data.list，有时直接 data 是数组。统一成 list。"""
        if isinstance(data.get("data"), list):
            return data["data"]
        return (data.get("data") or {}).get("list") or []

    # ---------- 各数据接口 ----------
    def fetch_fba_inventory(self, sid_list, page_size=200):
        """FBA 总库存（按 SKU 汇总跨 FBA 仓）。返回 {sku: quantity}。"""
        sids = ",".join(sid_list) if isinstance(sid_list, list) else sid_list
        if not sids:
            raise LingxingError("未配置亚马逊店铺 sid（lingxing_sids_amazon）")
        out = {}
        offset = 0
        while True:
            payload = {"sid": sids, "offset": offset, "length": page_size, "is_hide_zero_stock": 0}
            data = self._post("/basicOpen/openapi/storage/fbaWarehouseDetail", payload)
            lst = self._list(data)
            if not lst:
                break
            for it in lst:
                sku = it.get("seller_sku") or it.get("sku") or it.get("msku") or it.get("fnsku")
                qty = _num(it.get("quantity")) or _num(it.get("afn_total")) or 0
                if sku:
                    out[sku] = out.get(sku, 0) + qty
            if len(lst) < page_size:
                break
            offset += page_size
        return out

    def fetch_temu_inventory(self, store_id_list=None, page_size=200):
        """Temu 库存。返回 {sku: quantity}。"""
        out = {}
        offset = 0
        while True:
            payload = {"length": page_size, "offset": offset, "storeIdList": store_id_list or []}
            data = self._post("/basicOpen/multiplatform/fbt/stockSearch", payload)
            lst = self._list(data)
            if not lst:
                break
            for it in lst:
                sku = it.get("skc") or it.get("sku") or (it.get("mskuList") or [{}])[0].get("msku")
                qty = _num(it.get("quantity")) or 0
                if sku:
                    out[sku] = out.get(sku, 0) + qty
            if len(lst) < page_size:
                break
            offset += page_size
        return out

    def fetch_full_inventory(self, store_id_list=None, page_size=200):
        """多平台 FULL 库存（中国仓/沃尔玛/其他等）。返回 {sku: {warehouse_bucket: qty}}。"""
        out = {}
        offset = 0
        while True:
            payload = {"length": page_size, "offset": offset,
                       "selectTypeEnum": "COUNT_TYPE", "storeIdList": store_id_list or []}
            data = self._post("/basicOpen/multiplatform/full/stockSearch", payload)
            lst = self._list(data)
            if not lst:
                break
            for it in lst:
                sku = it.get("sku") or it.get("skc") or it.get("goodsId")
                wh = it.get("warehouseName") or it.get("wname") or ""
                qty = _num(it.get("quantity")) or 0
                if sku and wh:
                    bucket = self._map_warehouse(wh)
                    if bucket:
                        out.setdefault(sku, {})
                        out[sku][bucket] = out[sku].get(bucket, 0) + qty
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

        字段映射（领星日销量接口返回字段不统一，集中在此说明，便于按实际校准）：
          SKU     ：it.get('sku') / 'sellerSku' / 'msku'
          日期    ：it.get('date') / 'statDate' / 'saleDate' / 'daily'
          销量    ：it.get('saleVolume') / 'salesVolume' / 'quantity' / 'units'
        若贵司接口字段不同，改这里即可。
        """
        out = {}
        for sid in sid_list:
            offset = 0
            while True:
                payload = {"sid": sid, "startDate": start_date, "endDate": end_date,
                           "offset": offset, "length": page_size}
                try:
                    data = self._post(DAILY_SALES_PATH, payload)
                except LingxingError as e:
                    raise LingxingError(
                        f"日销量接口({DAILY_SALES_PATH})调用失败：{e}。"
                        f"请按 apidoc.lingxing.com 校准该路径（环境变量 LINGXING_DAILY_SALES_PATH）与字段映射。"
                    )
                lst = self._list(data)
                if not lst:
                    break
                for it in lst:
                    sku = it.get("sku") or it.get("sellerSku") or it.get("msku")
                    d = it.get("date") or it.get("statDate") or it.get("saleDate") or it.get("daily")
                    qty = (_num(it.get("saleVolume")) or _num(it.get("salesVolume"))
                           or _num(it.get("quantity")) or _num(it.get("units")))
                    if sku and d:
                        out.setdefault(sku, {})[str(d)[:10]] = out.get(sku, {}).get(str(d)[:10], 0) + qty
                if len(lst) < page_size:
                    break
                offset += page_size
        return out


# ===================== 同步到本地库 =====================
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
    """从领星 ERP 自动拉取销售/库存写入本地库。返回结果摘要。"""
    from .database import get_conn
    from .data_import import ensure_default_lifecycle

    conn = get_conn()
    client, cfg = build_client_from_config(conn)
    if not client.is_configured():
        conn.close()
        return {"ok": False, "error": "未配置领星凭证（lingxing_app_id / lingxing_app_secret）",
                "detail": "请在「系统设置 → 领星对接」中填写，或设置环境变量 LINGXING_APP_ID/SECRET。"}

    result = {"ok": True, "steps": [], "warnings": []}
    today = date.today()
    start = (today - timedelta(days=61)).strftime("%Y-%m-%d")
    end = today.strftime("%Y-%m-%d")

    sids_amazon = _sid_list(cfg, "lingxing_sids_amazon")
    sids_walmart = _sid_list(cfg, "lingxing_sids_walmart")
    sids_other = _sid_list(cfg, "lingxing_sids_other")
    sids_temu = _sid_list(cfg, "lingxing_sids_temu")
    all_sids = sids_amazon + sids_walmart + sids_other + sids_temu

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
            # 汇总跨店铺到 platform 桶
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
        # FBA（亚马逊）
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

        # Temu
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

        # 多平台 FULL（中国仓 / 沃尔玛 / 其他）
        if all_sids:
            try:
                full = client.fetch_full_inventory(all_sids)
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
        return {"ok": False, "error": str(e), "steps": result["steps"], "warnings": result["warnings"]}
    finally:
        conn.close()
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
