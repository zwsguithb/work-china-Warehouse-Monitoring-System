# -*- coding: utf-8 -*-
"""应用入口：初始化数据库、挂载 API、托管前端静态页、定时自动同步领星数据。"""
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from . import database
from . import seed
from .api import router
from . import lingxing

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="中国仓库存监控系统", version="1.1")

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


# ---------------- 定时自动同步领星 ERP ----------------
def _auto_sync_job():
    """凭证齐备且开启自动同步时，自动从领星拉取数据。失败仅记录，不影响服务。"""
    try:
        conn = database.get_conn()
        row = conn.execute(
            "SELECT param_value FROM config WHERE param_key='lingxing_auto_sync'"
        ).fetchone()
        conn.close()
        if not row or str(row["param_value"]) != "1":
            return
        print("[scheduler] 开始自动同步领星 ERP 数据 ...")
        res = lingxing.sync_from_lingxing()
        print("[scheduler] 同步结果:", res.get("ok"), res.get("warnings"))
    except Exception as e:  # noqa
        print("[scheduler] 自动同步异常:", e)


def _start_scheduler():
    try:
        from apscheduler.schedulers.background import BackgroundScheduler  # noqa
    except Exception as e:  # noqa
        print("APScheduler 未安装，跳过定时同步（可手动点击「立即同步」）：", e)
        return
    conn = database.get_conn()
    row = conn.execute(
        "SELECT param_value FROM config WHERE param_key='lingxing_auto_sync_hour'"
    ).fetchone()
    conn.close()
    hour = int(row["param_value"]) if row and str(row["param_value"]).isdigit() else 8

    sched = BackgroundScheduler()
    sched.add_job(_auto_sync_job, "cron", hour=hour, minute=0)
    sched.start()
    print(f"[scheduler] 已注册每日 {hour}:00 自动同步领星 ERP（需在设置中开启）")


_start_scheduler()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
