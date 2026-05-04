# InvestPro – 可转债数据查看器

本地单机版可转债数据查看工具。

**数据链路**：新浪财经（可转债实时行情）+ 同花顺（静态信息）+ 新浪财经（正股实时行情）→ 数据清洗与合并 → 指标计算 → SQLite → FastAPI → Vue 3 前端展示

---

## 功能

- 通过三路数据源拉取可转债数据（见[数据来源](#数据来源)）
- 展示字段：转债代码（sh/sz）、转债名称、最新价、涨跌幅、发行量（亿元）、正股代码、正股名称、转股价、转股价值、溢价率
- 自动计算转股价值（`转股价值 = 正股价 / 转股价 × 100`）和转股溢价率（`溢价率 = (转债价 - 转股价值) / 转股价值 × 100`）
- 已退市的转债自动排除（以新浪实时行情为准）
- 数据保存到本地 SQLite，断网后仍可查看缓存
- 手动刷新，60 秒冷却防止频繁请求
- 搜索（转债代码/名称/正股代码/正股名称）、点击表头排序、价格和涨跌幅范围筛选
- 刷新进度实时显示，刷新完成后自动更新列表

---

## 数据来源

| 数据源 | 接口 | 用途 |
|--------|------|------|
| 新浪财经 | `bond_zh_hs_cov_spot`（`hskzz_z` 节点） | 沪深可转债实时行情，定义活跃债券集合 |
| 同花顺 | `bond_zh_cov_info_ths`（`data.10jqka.com.cn/ipo/kzz/`） | 可转债静态信息：转股价、正股代码、发行量等 |
| 新浪财经 | `stock_zh_a_sh_sz_spot_sina`（`sh_a` + `sz_a` 节点） | 正股实时价格，用于计算转股价值和溢价率 |

合并策略：以新浪可转债行情为主表，LEFT JOIN 同花顺静态信息，再 LEFT JOIN 正股行情，所有三方数据均为可选，任一来源失败不影响其他字段展示。

---

## 技术栈

| 层     | 技术                                                        |
|--------|-------------------------------------------------------------|
| 后端   | Python 3.11+, FastAPI, pandas, SQLite, requests, demjson3   |
| 前端   | Vue 3, TypeScript, Element Plus, Axios, Vite                |

---

## 快速启动

### 前置条件

- Python 3.11+
- Node.js 18+

### 1. 安装后端依赖

```bash
cd backend
pip install -r requirements.txt
```

### 2. 启动后端

```bash
cd backend
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

后端运行在 http://localhost:8000

交互式 API 文档：http://localhost:8000/docs

### 3. 启动前端（开发模式）

```bash
cd frontend
npm install
npm run dev
```

前端运行在 http://localhost:5173（Vite 开发服务器自动将 `/api` 和 `/health` 反向代理到后端）

### 4. 使用

1. 打开浏览器访问 http://localhost:5173
2. 点击右上角 **刷新数据** 按钮，等待数据从各数据源拉取完成（约 10–60 秒，取决于网络）
3. 数据加载后即可搜索、排序、筛选

### 一键启动脚本（macOS / Linux）

```bash
chmod +x start.sh
./start.sh
```

脚本会自动检测并安装缺失的后端/前端依赖，然后同时启动两个服务；按 `Ctrl+C` 可一并停止。

---

## 项目结构

```
InvestPro/
├── backend/
│   ├── main.py          # FastAPI 应用入口（路由、刷新调度、响应模型）
│   ├── database.py      # SQLite 操作（建表、迁移、增删查、刷新日志）
│   ├── fetcher.py       # 数据抓取、合并与指标计算
│   ├── bond_data.py     # 可转债数据接口（新浪实时行情 + 同花顺静态信息）
│   ├── stock_data.py    # 沪深A股实时行情接口（新浪，仅 SH+SZ）
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── App.vue          # 主页面（表格、筛选栏、刷新按钮）
│   │   ├── api/
│   │   │   ├── bonds.ts     # API 调用函数（fetchBonds / triggerRefresh / fetchRefreshStatus）
│   │   │   └── types.ts     # TypeScript 类型定义（Bond, RefreshStatus 等）
│   │   ├── components/      # 组件目录
│   │   ├── main.ts
│   │   └── style.css
│   ├── vite.config.ts   # 开发代理配置（/api → :8000）
│   └── package.json
├── start.sh             # 一键启动脚本（macOS / Linux）
└── README.md
```

---

## API 接口

| Method | Path                        | 说明             |
|--------|-----------------------------|------------------|
| GET    | `/api/bonds`                | 获取可转债列表   |
| POST   | `/api/bonds/refresh`        | 触发数据刷新     |
| GET    | `/api/bonds/refresh/status` | 查询刷新状态     |
| GET    | `/health`                   | 健康检查         |

### GET /api/bonds 查询参数

| 参数       | 类型   | 说明                       |
|------------|--------|----------------------------|
| search     | string | 模糊搜索（代码/名称/正股） |
| min_price  | float  | 最低价格                   |
| max_price  | float  | 最高价格                   |
| min_change | float  | 最低涨跌幅（%）            |
| max_change | float  | 最高涨跌幅（%）            |
| sort_by    | string | 排序字段（见下表）         |
| sort_dir   | string | `asc` / `desc`             |

`sort_by` 支持的字段：`code`、`name`、`price`、`change_pct`、`issue_size`、`stock_code`、`stock_name`、`conv_price`、`conv_value`、`premium_rate`、`updated_at`

### GET /api/bonds/refresh/status 返回字段

| 字段               | 类型    | 说明                                        |
|--------------------|---------|---------------------------------------------|
| status             | string  | `running` / `success` / `error` / `never`  |
| started_at         | string  | 本次刷新开始时间（ISO 8601）                 |
| finished_at        | string  | 本次刷新结束时间（ISO 8601）                 |
| message            | string  | 结果描述（成功条数或错误信息）               |
| cooldown_remaining | int     | 距下次可刷新的剩余秒数                       |

---

## 数据库结构

数据存储在 `backend/investpro.db`（SQLite）。

**bonds 表**

| 字段         | 类型 | 说明                     |
|--------------|------|--------------------------|
| code         | TEXT | 转债代码（主键，sh/sz）  |
| name         | TEXT | 转债名称                 |
| price        | REAL | 最新价                   |
| change_pct   | REAL | 涨跌幅（%）              |
| issue_size   | REAL | 实际发行量（亿元）       |
| stock_code   | TEXT | 正股代码（6 位）         |
| stock_name   | TEXT | 正股名称                 |
| conv_price   | REAL | 转股价                   |
| conv_value   | REAL | 转股价值                 |
| premium_rate | REAL | 转股溢价率（%）          |
| updated_at   | TEXT | 最后更新时间（ISO 8601） |

**refresh_log 表**：记录每次刷新的开始时间、结束时间、状态（`running` / `success` / `error`）及结果描述，用于支持刷新状态查询和冷却计算。

---

## 环境变量

| 变量               | 默认值                        | 说明                   |
|--------------------|-------------------------------|------------------------|
| `REFRESH_COOLDOWN` | `60`                          | 刷新冷却时间（秒）     |
| `DB_PATH`          | `backend/investpro.db`（相对于 `backend/` 目录） | SQLite 数据库文件路径  |

---

## 注意事项

- 首次运行需要网络连接，后续断网可查看本地缓存
- 数据来源为新浪财经和同花顺，仅供参考，不构成任何投资建议
- 同花顺静态信息接口或正股行情接口请求失败时，系统会自动降级继续运行，相应字段显示为 `-`
- 老版本数据库缺少 `conv_value` / `premium_rate` 字段时，启动时会自动执行迁移添加这两列
