# 中国仓库存监控系统 (China Warehouse Inventory Monitoring System)

基于《中国仓库存监控系统-开发需求说明书》实现的 MVP：自动从领星 ERP 拉取（或上传）销售/库存数据，按减噪算法计算全平台销量与中国仓可售天数，并在首页可视化四大看板。

> 数据来源：销售数据、库存数据在领星 ERP 可获取（本 MVP 提供 Excel 上传通道，对接 API 时替换为领星接口即可）；生命周期由各款号上传维护，**未上传的款号默认淘汰期**。

## 功能看板
1. **淘汰款板块**：淘汰期款总库存数量 Top10（含款号可售天数/全平台销量）；淘汰期款近60天全平台销量 Top10（含总库存数量）。
2. **temu 平台板块**：temu 销量前 5 款号（temu销量 / temu库存 / 总库存可售天数）。
3. **亚马逊板块**：亚马逊平台销量 Top20（亚马逊近期销量 / 中国仓库存 / 该款号可售天数）。
4. **低库存提醒模块**：按 SKU 统计低可售天数个数并支持导出文件；按款号展示在界面。阈值：
   - 爆旺期 < 90 天 ｜ 热销期 < 60 天 ｜ 平销/观察期 < 45 天 ｜ 新品期 < 35 天

## 核心算法
```
减噪后销量(亚马逊/沃尔玛/其他平台) = 近3天/3×5% + 近7天/7×15% + 近14天/14×27% + 近30天/30×28% + 近60天/60×25%
temu 减噪后销量 = 近30天销量 / 30
全平台销量 Z = 亚马逊减噪 + 沃尔玛减噪 + 其他平台减噪 + temu减噪
中国仓可售天数-各码数 = 中国仓库存 ÷ Z
中国仓可售天数-该款号 = Σ中国仓库存(同款号) ÷ ΣZ(同款号)
```
> 已修正原手工表中 temu 误用「库存」而非「减噪销量」混入 Z 的瑕疵，统一以减噪后日销计入。

## 目录结构
```
china-warehouse-monitor/
├── app/
│   ├── main.py          # 应用入口（初始化+挂载路由+托管前端）
│   ├── database.py      # SQLite 表结构与连接
│   ├── calc.py          # 计算引擎 + 四大看板聚合
│   ├── data_import.py   # Excel 导入（销售/库存/生命周期）
│   ├── api.py           # FastAPI 路由（8 个接口）
│   └── seed.py          # 演示数据 + 示例 Excel
├── static/              # 前端看板（index.html / app.js / style.css）
├── sample_data/         # 示例上传 Excel
├── data/                # 运行时 SQLite 数据库（自动生成）
├── requirements.txt
└── README.md
```

## 本地运行
```bash
pip install -r requirements.txt
# 方式一：直接运行（自动建库 + 灌入演示数据）
python -m app.main
# 方式二：用 uvicorn
uvicorn app.main:app --reload --port 8000
```
浏览器打开 http://localhost:8000 即可看到看板（首次启动会自动写入演示数据）。

## Excel 上传模板
通过 `/api/data/sync` 与 `/api/styles/lifecycle/upload` 上传，字段（首行表头，不区分大小写）：

**销售表** `sku, platform, d3, d7, d14, d30, d60`
- platform ∈ `amazon` / `walmart` / `other` / `temu`

**库存表** `sku, warehouse, quantity`
- warehouse ∈ `中国仓` / `temu` / `amazon_fba` / `walmart` / `other`

**生命周期表** `style_code, lifecycle`
- lifecycle ∈ `爆旺期` / `热销期` / `平销期` / `观察期` / `新品期` / `淘汰期`（及退市/已淘汰/暂停）
- 未上传的款号自动默认 `淘汰期`

示例文件见 `sample_data/`（可用其直接测试上传接口）。

## API 一览
| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/data/sync?kind=sales\|inventory` | POST | 上传领星销售/库存 Excel |
| `/api/styles/lifecycle/upload` | POST | 上传款号-生命周期 |
| `/api/dashboard/eliminated` | GET | 淘汰款板块 |
| `/api/dashboard/temu` | GET | temu 板块 |
| `/api/dashboard/amazon` | GET | 亚马逊板块 |
| `/api/dashboard/low-stock/sku?export=true` | GET | 低库存按 SKU（可导出 CSV） |
| `/api/dashboard/low-stock/style` | GET | 低库存按款号 |
| `/api/config` | GET/PUT | 权重/阈值/TopN 配置 |

## 技术说明
- 后端 FastAPI + SQLite（MVP 用 SQLite，生产可平滑切换 PostgreSQL）。
- 前端纯静态（HTML+JS），通过 fetch 调用 API，依赖 Chart.js CDN。
- 生产对接领星 ERP：将 `data_import.import_sales/import_inventory` 替换为领星 OpenAPI 拉取并写入 `sales_agg`/`inventory` 表即可，计算与看板逻辑无需改动。
