# -*- coding: utf-8 -*-
"""应用入口：初始化数据库、挂载 API、托管前端静态页。"""
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from . import database
from . import seed
from .api import router

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="中国仓库存监控系统", version="1.0")

# 初始化数据库 + 演示数据（首次运行）
database.init_db()
try:
    import sqlite3
    conn = sqlite3.connect(str(database.DB_PATH))
    cnt = conn.execute("SELECT COUNT(*) FROM skus").fetchone()[0]
    conn.close()
    if cnt == 0:
        seed.seed_demo()
        seed.make_sample_excels()
except Exception as e:  # noqa
    print("seed skipped:", e)

app.include_router(router)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
