# -*- coding: utf-8 -*-
"""SQLite 数据层：表结构初始化与连接管理。"""
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "cwms.db"

PLATFORMS = ["amazon", "walmart", "other", "temu"]
WEIGHTED_PLATFORMS = ["amazon", "walmart", "other"]
LIFECYCLES_MONITORED = ["爆旺期", "热销期", "平销期", "观察期", "新品期"]
DEFAULT_LIFECYCLE = "淘汰期"


def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS styles (
            style_code  TEXT PRIMARY KEY,
            lifecycle   TEXT NOT NULL DEFAULT '淘汰期',
            is_default  INTEGER NOT NULL DEFAULT 0,
            updated_at  TEXT
        );
        CREATE TABLE IF NOT EXISTS skus (
            sku         TEXT PRIMARY KEY,
            style_code  TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sales_agg (
            sku       TEXT NOT NULL,
            platform  TEXT NOT NULL,
            d3  REAL, d7 REAL, d14 REAL, d30 REAL, d60 REAL,
            PRIMARY KEY (sku, platform)
        );
        CREATE TABLE IF NOT EXISTS inventory (
            sku       TEXT NOT NULL,
            warehouse TEXT NOT NULL,
            quantity  REAL,
            PRIMARY KEY (sku, warehouse)
        );
        CREATE TABLE IF NOT EXISTS config (
            param_key   TEXT PRIMARY KEY,
            param_value TEXT
        );
        """
    )
    conn.commit()
    conn.close()
