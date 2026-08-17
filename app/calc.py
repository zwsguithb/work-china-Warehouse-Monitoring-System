# -*- coding: utf-8 -*-
"""计算引擎：减噪算法、全平台销量 Z、中国仓可售天数、生命周期默认淘汰期。

口径说明（与《中国仓库存监控》公式一致，并修正了原 Excel 中 temu 误用库存的瑕疵）：
  - 亚马逊 / 沃尔玛 / 其他平台：5 段加权日均
        V = d3/3*w3 + d7/7*w7 + d14/14*w14 + d30/30*w30 + d60/60*w60
  - temu：仅用近 30 天  ->  V = d30 / 30
  - 全平台销量 Z = 亚马逊减噪 + 沃尔玛减噪 + 其他平台减噪 + temu减噪
  - 中国仓可售天数-各码数 = 中国仓库存 / Z
  - 中国仓可售天数-该款号 = Σ中国仓库存(同款号) / ΣZ(同款号)
"""
from datetime import datetime
from .database import get_conn, PLATFORMS, WEIGHTED_PLATFORMS, LIFECYCLES_MONITORED, DEFAULT_LIFECYCLE

PLATFORM_LABEL = {
    "amazon": "亚马逊",
    "walmart": "沃尔玛",
    "other": "其他平台",
    "temu": "temu",
}


def get_config():
    conn = get_conn()
    rows = conn.execute("SELECT param_key, param_value FROM config").fetchall()
    conn.close()
    return {r["param_key"]: r["param_value"] for r in rows}


def weights():
    cfg = get_config()
    return {
        "w3": float(cfg.get("w3", 0.05)),
        "w7": float(cfg.get("w7", 0.15)),
        "w14": float(cfg.get("w14", 0.27)),
        "w30": float(cfg.get("w30", 0.28)),
        "w60": float(cfg.get("w60", 0.25)),
    }


def threshold_map():
    cfg = get_config()
    return {
        "爆旺期": float(cfg.get("th_baowang", 90)),
        "热销期": float(cfg.get("th_rexiao", 60)),
        "平销期": float(cfg.get("th_pingxiao", 45)),
        "观察期": float(cfg.get("th_guancha", 45)),
        "新品期": float(cfg.get("th_xinpin", 35)),
    }


def topn_map():
    cfg = get_config()
    return {
        "eliminated_inventory": int(cfg.get("top_eliminated_inv", 10)),
        "eliminated_60day": int(cfg.get("top_eliminated_60", 10)),
        "temu": int(cfg.get("top_temu", 5)),
        "amazon": int(cfg.get("top_amazon", 20)),
    }


def denoise_weighted(d3, d7, d14, d30, d60):
    w = weights()
    return (d3 / 3 * w["w3"] + d7 / 7 * w["w7"] + d14 / 14 * w["w14"]
            + d30 / 30 * w["w30"] + d60 / 60 * w["w60"])


def denoise_temu(d30):
    return d30 / 30.0


def _safe_div(num, den):
    if not den or den <= 0:
        return None
    return round(num / den, 1)


def compute_all():
    """读取销售/库存/生命周期，逐 SKU 计算减噪销量、Z、可售天数，并聚合同款号。

    返回:
        skus:  [{sku, style_code, lifecycle, is_default, z, china_inv, sellable_days,
                 amazon_v, walmart_v, other_v, temu_v, temu_d30, amazon_d30, china_inv}]
        styles: [{style_code, lifecycle, is_default, z, china_inv, sellable_days,
                  d60_total, temu_inv, temu_d30, amazon_v}]
    """
    conn = get_conn()
    c = conn.cursor()

    # 生命周期（含默认淘汰期）
    style_rows = c.execute("SELECT style_code, lifecycle, is_default FROM styles").fetchall()
    style_lc = {r["style_code"]: (r["lifecycle"], r["is_default"]) for r in style_rows}

    # sku -> style
    sku_style = {r["sku"]: r["style_code"] for r in c.execute("SELECT sku, style_code FROM skus")}

    # 销量
    sales = {}
    for r in c.execute("SELECT sku, platform, d3,d7,d14,d30,d60 FROM sales_agg"):
        sales.setdefault(r["sku"], {})[r["platform"]] = {
            "d3": r["d3"] or 0, "d7": r["d7"] or 0, "d14": r["d14"] or 0,
            "d30": r["d30"] or 0, "d60": r["d60"] or 0,
        }

    # 库存
    inv = {}
    for r in c.execute("SELECT sku, warehouse, quantity FROM inventory"):
        inv.setdefault(r["sku"], {})[r["warehouse"]] = r["quantity"] or 0

    conn.close()

    skus = []
    style_acc = {}  # style_code -> accumulators

    for sku, style in sku_style.items():
        s = sales.get(sku, {})
        inv_s = inv.get(sku, {})

        amazon_v = denoise_weighted(*[s.get("amazon", {}).get(k, 0) for k in ("d3", "d7", "d14", "d30", "d60")]) \
            if "amazon" in s else 0
        walmart_v = denoise_weighted(*[s.get("walmart", {}).get(k, 0) for k in ("d3", "d7", "d14", "d30", "d60")]) \
            if "walmart" in s else 0
        other_v = denoise_weighted(*[s.get("other", {}).get(k, 0) for k in ("d3", "d7", "d14", "d30", "d60")]) \
            if "other" in s else 0
        temu_d30 = s.get("temu", {}).get("d30", 0)
        temu_v = denoise_temu(temu_d30)

        z = amazon_v + walmart_v + other_v + temu_v
        china_inv = inv_s.get("中国仓", 0)
        sellable = _safe_div(china_inv, z)

        lc, is_def = style_lc.get(style, (DEFAULT_LIFECYCLE, 1))

        # 近60天全平台销量（原始求和，跨平台）
        d60_total = sum(s.get(p, {}).get("d60", 0) for p in PLATFORMS)
        temu_inv = inv_s.get("temu", 0)
        amazon_d30 = s.get("amazon", {}).get("d30", 0)

        skus.append({
            "sku": sku, "style_code": style, "lifecycle": lc, "is_default": is_def,
            "z": round(z, 2), "china_inv": china_inv, "sellable_days": sellable,
            "amazon_v": round(amazon_v, 2), "walmart_v": round(walmart_v, 2),
            "other_v": round(other_v, 2), "temu_v": round(temu_v, 2),
            "temu_d30": temu_d30, "amazon_d30": amazon_d30,
            "temu_inv": temu_inv, "d60_total": d60_total,
        })

        acc = style_acc.setdefault(style, {
            "style_code": style, "lifecycle": lc, "is_default": is_def,
            "z": 0.0, "china_inv": 0, "d60_total": 0,
            "temu_inv": 0, "temu_d30": 0, "amazon_v": 0.0,
        })
        acc["z"] += z
        acc["china_inv"] += china_inv
        acc["d60_total"] += d60_total
        acc["temu_inv"] += temu_inv
        acc["temu_d30"] += temu_d30
        acc["amazon_v"] += amazon_v

    styles = []
    for style, acc in style_acc.items():
        acc["z"] = round(acc["z"], 2)
        acc["amazon_v"] = round(acc["amazon_v"], 2)
        acc["sellable_days"] = _safe_div(acc["china_inv"], acc["z"])
        styles.append(acc)

    return skus, styles


# ---------------- 各看板聚合 ----------------

def dashboard_eliminated():
    _, styles = compute_all()
    topn = topn_map()
    elim = [s for s in styles if s["lifecycle"] == DEFAULT_LIFECYCLE]
    by_inv = sorted(elim, key=lambda x: x["china_inv"], reverse=True)[: topn["eliminated_inventory"]]
    by_60 = sorted(elim, key=lambda x: x["d60_total"], reverse=True)[: topn["eliminated_60day"]]
    return {
        "top_inventory": [
            {"style_code": s["style_code"], "total_inventory": s["china_inv"],
             "sellable_days": s["sellable_days"], "total_sales": s["z"]}
            for s in by_inv
        ],
        "top_60day": [
            {"style_code": s["style_code"], "sales_60d": s["d60_total"],
             "total_inventory": s["china_inv"]}
            for s in by_60
        ],
    }


def dashboard_temu():
    _, styles = compute_all()
    topn = topn_map()
    ranked = sorted(styles, key=lambda x: x["temu_d30"], reverse=True)[: topn["temu"]]
    return {
        "items": [
            {"style_code": s["style_code"], "temu_sales": s["temu_d30"],
             "temu_inventory": s["temu_inv"], "sellable_days": s["sellable_days"]}
            for s in ranked
        ]
    }


def dashboard_amazon():
    _, styles = compute_all()
    topn = topn_map()
    ranked = sorted(styles, key=lambda x: x["amazon_v"], reverse=True)[: topn["amazon"]]
    return {
        "items": [
            {"style_code": s["style_code"], "amazon_recent_sales": s["amazon_v"],
             "china_inventory": s["china_inv"], "sellable_days": s["sellable_days"]}
            for s in ranked
        ]
    }


def low_stock(which):
    """which: 'sku' 按 SKU 维度；'style' 按款号维度。返回触发提醒的列表与按生命周期计数。"""
    skus, styles = compute_all()
    th = threshold_map()
    monitored = set(LIFECYCLES_MONITORED)

    if which == "sku":
        base = [s for s in skus if s["lifecycle"] in monitored and s["sellable_days"] is not None]
        triggered = [s for s in base if s["sellable_days"] < th.get(s["lifecycle"], 999)]
        counts = {}
        for lc in LIFECYCLES_MONITORED:
            counts[lc] = sum(1 for s in triggered if s["lifecycle"] == lc)
        # 按销量（Z）高低排序
        triggered.sort(key=lambda x: x["z"], reverse=True)
        items = [
            {"sku": s["sku"], "style_code": s["style_code"], "lifecycle": s["lifecycle"],
             "sellable_days": s["sellable_days"], "z": s["z"], "threshold": th.get(s["lifecycle"])}
            for s in triggered
        ]
        return {"counts": counts, "items": items, "total": len(items)}
    else:
        base = [s for s in styles if s["lifecycle"] in monitored and s["sellable_days"] is not None]
        triggered = [s for s in base if s["sellable_days"] < th.get(s["lifecycle"], 999)]
        counts = {}
        for lc in LIFECYCLES_MONITORED:
            counts[lc] = sum(1 for s in triggered if s["lifecycle"] == lc)
        triggered.sort(key=lambda x: x["z"], reverse=True)
        items = [
            {"style_code": s["style_code"], "lifecycle": s["lifecycle"],
             "sellable_days": s["sellable_days"], "z": s["z"], "threshold": th.get(s["lifecycle"])}
            for s in triggered
        ]
        return {"counts": counts, "items": items, "total": len(items)}
