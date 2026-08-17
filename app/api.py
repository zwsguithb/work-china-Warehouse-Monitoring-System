# -*- coding: utf-8 -*-
"""FastAPI 路由：数据同步、生命周期上传、四大看板接口、配置。"""
import tempfile
import os
from datetime import datetime
from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse
import csv
import io

from . import calc
from .database import get_conn
from .data_import import import_sales, import_inventory, import_lifecycle, ensure_default_lifecycle

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
