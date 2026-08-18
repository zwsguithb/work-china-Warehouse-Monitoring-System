# 中国仓库存监控系统 (China Warehouse Inventory Monitoring System)

基于《中国仓库存监控系统-开发需求说明书》实现的 MVP：自动从**领星 ERP** 拉取销售/库存数据（同时也保留 Excel 上传作为兜底），按减噪算法计算全平台销量与中国仓可售天数，并在首页可视化四大看板。

> 数据来源：销售数据、库存数据**自动对接领星 ERP 开放接口**拉取（设置页填写 AppId/AppSecret 并配置店铺 sid 即可）；生命周期由各款号上传维护，**未上传的款号默认淘汰期**。当未配置领星凭证或接口异常时，自动降级为 Excel 上传通道，系统不中断。

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
│   ├── lingxing.py      # 领星 ERP 开放接口客户端 + 自动同步
│   ├── api.py           # FastAPI 路由（含领星对接 4 个接口）
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

## 容器化部署 (Docker)

```bash
# 构建并后台启动
docker compose up -d --build
# 访问 http://localhost:8000
```

- `./data` 通过 volume 持久化 SQLite 数据库，容器重建不丢数据；
- 可在 `docker-compose.yml` 的 `environment` 中预置 `LINGXING_APP_ID` / `LINGXING_APP_SECRET`，使容器启动即对接领星 ERP；
- `.dockerignore` 已排除 `*.db` 与 `.git`，本地数据库不会被打进镜像；
- CI 流水线会 `docker build` 并启动容器做接口冒烟测试，保证镜像可正常出图运行。

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

## 领星 ERP 自动接入

系统支持**自动链接领星 ERP** 拉取数据，无需手工上传 Excel。配置路径：系统首页「⑤ 领星 ERP 数据接入」面板 → 「领星对接设置」，或在部署环境变量中设置。

### 1. 获取凭证
- 登录领星 ERP → **设置 → 业务配置 → 全局 → 开放接口**，获取 **AppId / AppSecret**；
- 同一页面把**本系统部署服务器的外网 IP** 加入白名单（否则接口不可达）。

### 2. 配置项
| 配置项 | 说明 |
|--------|------|
| `lingxing_app_id` / `lingxing_app_secret` | 领星开放接口凭证 |
| `lingxing_host` | 默认 `https://openapi.lingxing.com` |
| `lingxing_sids_amazon` | 亚马逊店铺 sid（逗号分隔，对应 FBA 库存 + 亚马逊销量） |
| `lingxing_sids_walmart` | 沃尔玛店铺 sid |
| `lingxing_sids_other` | 其他平台店铺 sid |
| `lingxing_sids_temu` | temu 店铺 sid |
| `lingxing_auto_sync` | `1` 开启每日定时自动同步（默认 `0`） |
| `lingxing_auto_sync_hour` | 每日同步时间（0-23，默认 `8`） |

> 也可通过环境变量设置（优先级更高）：`LINGXING_APP_ID`、`LINGXING_APP_SECRET`、`LINGXING_HOST`、`LINGXING_SIDS`。

### 3. 同步逻辑
- 前端点「立即从领星同步」或开启自动同步后每日定时调用；
- 拉取以下**已按领星官方 apidoc.lingxing.com 固化的接口**：
  | 数据 | 接口路径 | 关键参数 / 返回 |
  |------|----------|----------------|
  | FBA 库存（亚马逊） | `/basicOpen/openapi/storage/fbaWarehouseDetail` (v2) | `sid`(逗号串)；返回 `data.list` |
  | 多平台库存（中国仓/沃尔玛/其他） | `/basicOpen/multiplatform/full/stockSearch` | `selectTypeEnum=COUNT_TYPE`；返回 `data.records` |
  | Temu 库存 | `/basicOpen/multiplatform/fbt/stockSearch` | `storeIdList`；返回 `data.records` |
  | 日销量（按日/按SKU） | `/basicOpen/platformStatisticsV2/saleStat/pageList` | `date_unit=4,data_type=4,result_type=1`；返回 `data[]` |
- 日销量按近 3/7/14/30/60 天聚合为 `sales_agg` 的 d3/d7/d14/d30/d60，库存写入 `inventory` 表；
- 每次同步（无论成败）写入 **同步日志表** `sync_log`，可在首页「⑥ 领星同步日志」查看；
- **容错**：未配置凭证 / 某接口失败 → 跳过该部分并写 warning，不中断系统（仍可走 Excel 兜底）。

### 4. 字段映射校准（如需）
接口路径已全部按官方文档固化。少数**返回字段名**可能因店铺/版本略有差异（如 FULL 库存的仓库名字段、数量字段），已做多候选兜底。若某平台数据为空，可在部署环境设 `LINGXING_DEBUG=1`，系统会在日志中打印首条记录的真实字段名，据此在 `app/lingxing.py` 对应 `fetch_*` 方法补充字段别名即可。所有路径也可通过环境变量覆盖（如 `LINGXING_DAILY_SALES_PATH`）。

## API 一览
| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/data/sync?kind=sales\|inventory` | POST | 上传领星销售/库存 Excel（兜底通道） |
| `/api/data/sync/lingxing` | POST | 立即从领星 ERP 自动拉取数据 |
| `/api/lingxing/status` | GET | 查看领星对接状态（不返回密钥明文） |
| `/api/lingxing/config` | PUT | 配置领星凭证 / sid / 自动同步 |
| `/api/lingxing/test` | POST | 测试领星凭证是否可用 |
| `/api/lingxing/sync-log?limit=20` | GET | 查看领星同步日志（首页「⑥ 同步日志」用） |
| `/api/styles/lifecycle/upload` | POST | 上传款号-生命周期 |
| `/api/dashboard/eliminated` | GET | 淘汰款板块 |
| `/api/dashboard/temu` | GET | temu 板块 |
| `/api/dashboard/amazon` | GET | 亚马逊板块 |
| `/api/dashboard/low-stock/sku?export=true` | GET | 低库存按 SKU（可导出 CSV） |
| `/api/dashboard/low-stock/style` | GET | 低库存按款号 |
| `/api/config` | GET/PUT | 权重/阈值/TopN 配置 |

## 技术说明
- 后端 FastAPI + SQLite（MVP 用 SQLite，生产可平滑切换 PostgreSQL）；定时同步用 APScheduler。
- 前端纯静态（HTML+JS），通过 fetch 调用 API，依赖 Chart.js CDN。
- **领星对接**：`app/lingxing.py` 封装鉴权（access-token，约 2 小时有效期自动刷新）与四类数据接口，并写入 `sales_agg`/`inventory` 表；计算与看板逻辑（`app/calc.py`）无需改动，数据源切换对上层透明。
- **CI/CD**：仓库已接入 GitHub Actions（`.github/workflows/ci.yml`），push 到 `main` 自动执行：Python 依赖安装 + 应用导入/建库校验 → **`docker build` 构建镜像** → 启动容器做接口冒烟测试（首页/四大看板/领星状态/同步日志接口）→ 清理容器。

### 环境变量（可选，优先级高于设置页）
| 变量 | 说明 |
|------|------|
| `LINGXING_APP_ID` / `LINGXING_APP_SECRET` / `LINGXING_HOST` | 领星凭证 |
| `LINGXING_DEBUG=1` | 打印各接口首条记录真实字段名，便于校准字段映射 |
| `LINGXING_FBA_STOCK_PATH` / `LINGXING_FULL_STOCK_PATH` / `LINGXING_FBT_STOCK_PATH` / `LINGXING_DAILY_SALES_PATH` | 覆盖各接口路径 |
