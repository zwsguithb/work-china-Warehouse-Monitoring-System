# -*- coding: utf-8 -*-
"""FastAPI 路由：数据同步、生命周期上传、四大看板接口、配置。"""
import tempfile
import os
import json
from datetime import datetime
from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse
import csv
import io

from . import calc
from .database import get_conn
from .data_import import import_sales, import_inventory, import_lifecycle, ensure_default_lifecycle
from . import lingxing

router = APIRouter()


@router.post("/api/data/sync")
async def sync_data(file: UploadFile = File(...), kind: str = "sales"):
    """上传领星 ERP 导出：kind=sales(销售) 或 inventory(库存)。"""
    suffix = ".xlsx"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name
    try:
        if kind == "sales":
            n = import_sales(tmp_path)
        elif kind == "inventory":
            n = import_inventory(tmp_path)
        else:
            raise HTTPException(400, "kind must be 'sales' or 'inventory'")
        ensure_default_lifecycle()
    finally:
        os.unlink(tmp_path)
    return {"ok": True, "imported_rows": n}


# ---------------- 领星 ERP 自动对接 ----------------
@router.post("/api/data/sync/lingxing")
def sync_lingxing():
    """立即从领星 ERP 自动拉取销售/库存数据。"""
    return lingxing.sync_from_lingxing()


@router.get("/api/lingxing/status")
def lingxing_status():
    """查看领星对接状态（不返回密钥明文）。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT param_key, param_value FROM config WHERE param_key LIKE 'lingxing_%'"
    ).fetchall()
    cfg = {r["param_key"]: r["param_value"] for r in rows}
    conn.close()
    has_secret = bool(cfg.get("lingxing_app_secret"))
    return {
        "configured": has_secret and bool(cfg.get("lingxing_app_id")),
        "app_id_set": bool(cfg.get("lingxing_app_id")),
        "app_secret_set": has_secret,
        "host": cfg.get("lingxing_host", "https://openapi.lingxing.com"),
        "sids_amazon": cfg.get("lingxing_sids_amazon", ""),
        "sids_walmart": cfg.get("lingxing_sids_walmart", ""),
        "sids_other": cfg.get("lingxing_sids_other", ""),
        "sids_temu": cfg.get("lingxing_sids_temu", ""),
        "auto_sync": cfg.get("lingxing_auto_sync", "0"),
        "auto_sync_hour": cfg.get("lingxing_auto_sync_hour", "8"),
        "last_sync": cfg.get("lingxing_last_sync", "从未同步"),
    }


@router.put("/api/lingxing/config")
def lingxing_config(payload: dict):
    """配置领星对接参数。可包含：app_id, app_secret, host,
    sids_amazon, sids_walmart, sids_other, sids_temu（逗号分隔的店铺 sid）,
    auto_sync(0/1), auto_sync_hour(0-23)。"""
    allowed = {"lingxing_app_id", "lingxing_app_secret", "lingxing_host",
               "lingxing_sids_amazon", "lingxing_sids_walmart",
               "lingxing_sids_other", "lingxing_sids_temu",
               "lingxing_auto_sync", "lingxing_auto_sync_hour"}
    conn = get_conn()
    c = conn.cursor()
    saved = {}
    for k, v in payload.items():
        key = str(k).strip()
        if key not in allowed:
            continue
        if key == "lingxing_app_secret" and (v is None or str(v) == ""):
            continue  # 不允许用空串覆盖已存密钥
        c.execute("INSERT OR REPLACE INTO config(param_key,param_value) VALUES(?,?)",
                  (key, str(v)))
        saved[key] = "******" if key == "lingxing_app_secret" else str(v)
    conn.commit()
    conn.close()
    return {"ok": True, "saved": saved}


@router.post("/api/lingxing/test")
def lingxing_test(payload: dict = None):
    """测试领星凭证是否可用。可传 app_id/app_secret/host 临时测试，否则用已存配置。"""
    payload = payload or {}
    conn = get_conn()
    rows = conn.execute(
        "SELECT param_key, param_value FROM config WHERE param_key LIKE 'lingxing_%'"
    ).fetchall()
    cfg = {r["param_key"]: r["param_value"] for r in rows}
    conn.close()
    return lingxing.test_connection(
        app_id=payload.get("app_id") or cfg.get("lingxing_app_id"),
        app_secret=payload.get("app_secret") or cfg.get("lingxing_app_secret"),
        host=payload.get("host") or cfg.get("lingxing_host"),
    )


@router.get("/api/lingxing/sync-log")
def lingxing_sync_log(limit: int = 20):
    """返回最近 N 条领星同步日志。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, started_at, finished_at, status, detail, error FROM sync_log "
        "ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    items = []
    for r in rows:
        try:
            detail = json.loads(r["detail"]) if r["detail"] else {}
        except Exception:
            detail = {}
        items.append({
            "id": r["id"],
            "started_at": r["started_at"],
            "finished_at": r["finished_at"],
            "status": r["status"],
            "steps": detail.get("steps", []),
            "warnings": detail.get("warnings", []),
            "error": r["error"],
        })
    return {"items": items}


@router.post("/api/styles/lifecycle/upload")
async def upload_lifecycle(file: UploadFile = File(...)):
    """上传款号-生命周期文件；未上传的款号保持默认淘汰期。"""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    try:
        n = import_lifecycle(tmp_path)
        ensure_default_lifecycle()
    finally:
        os.unlink(tmp_path)
    return {"ok": True, "imported_rows": n}


@router.get("/api/dashboard/eliminated")
def eliminated():
    return calc.dashboard_eliminated()


@router.get("/api/dashboard/temu")
def temu():
    return calc.dashboard_temu()


@router.get("/api/dashboard/amazon")
def amazon():
    return calc.dashboard_amazon()


@router.get("/api/dashboard/low-stock/sku")
def low_stock_sku(export: bool = False):
    data = calc.low_stock("sku")
    if export:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["sku", "style_code", "lifecycle", "sellable_days", "total_sales_z", "threshold"])
        for it in data["items"]:
            w.writerow([it["sku"], it["style_code"], it["lifecycle"],
                        it["sellable_days"], it["z"], it["threshold"]])
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=low_stock_sku.csv"},
        )
    return data


@router.get("/api/dashboard/low-stock/style")
def low_stock_style():
    return calc.low_stock("style")


@router.get("/api/config")
def get_config():
    return calc.get_config()


@router.put("/api/config")
def put_config(payload: dict):
    conn = get_conn()
    c = conn.cursor()
    for k, v in payload.items():
        c.execute("INSERT OR REPLACE INTO config(param_key,param_value) VALUES(?,?)", (k, str(v)))
    conn.commit()
    conn.close()
    return {"ok": True}
