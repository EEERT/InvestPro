# InvestPro – 可转债数据查看器 (MVP)

本地单机版可转债数据查看工具，第一版 MVP。

**数据链路**：AkShare → 数据清洗 → 指标计算 → SQLite → FastAPI → Vue 3 前端展示

---

## 功能

- 使用 AkShare `bond_zh_hs_cov_spot` + `bond_zh_cov_info_ths` 获取可转债数据
- 字段：转债代码（sh/sz）、转债名称、最新价、涨跌幅、实际发行量、正股代码、正股名称、转股价
- 保存到本地 SQLite，断网可查看缓存
- 手动刷新（60 秒冷却防止频繁请求）
- 搜索（代码/名称/正股）、排序、价格/涨跌幅筛选
- 刷新状态实时查看

---

## 技术栈

| 层     | 技术                          |
|--------|-------------------------------|
| 后端   | Python 3.11+, FastAPI, AkShare, pandas, SQLite |
| 前端   | Vue 3, TypeScript, Element Plus, Axios, Vite |

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

API 文档：http://localhost:8000/docs

### 3. 启动前端（开发模式）

```bash
cd frontend
npm install
npm run dev
```

前端运行在 http://localhost:5173

### 4. 使用

1. 打开浏览器访问 http://localhost:5173
2. 点击右上角 **刷新数据** 按钮，等待数据从 AkShare 拉取完成（约 10–30 秒）
3. 数据加载后即可搜索、排序、筛选

### 一键启动脚本（macOS / Linux）

```bash
chmod +x start.sh
./start.sh
```

---

## 项目结构

```
InvestPro/
├── backend/
│   ├── main.py          # FastAPI 应用入口
│   ├── database.py      # SQLite 操作（建表、增删查）
│   ├── fetcher.py       # AkShare 数据抓取与合并
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── App.vue      # 主页面（列表+筛选+刷新）
│   │   ├── api/
│   │   │   ├── bonds.ts # API 调用函数
│   │   │   └── types.ts # TypeScript 类型定义
│   │   ├── main.ts
│   │   └── style.css
│   ├── vite.config.ts   # 开发代理配置
│   └── package.json
├── start.sh             # 一键启动脚本
└── README.md
```

---

## API 接口

| Method | Path                     | 说明           |
|--------|--------------------------|----------------|
| GET    | `/api/bonds`             | 获取可转债列表 |
| POST   | `/api/bonds/refresh`     | 触发数据刷新   |
| GET    | `/api/bonds/refresh/status` | 查询刷新状态 |
| GET    | `/health`                | 健康检查       |

### GET /api/bonds 参数

| 参数       | 类型   | 说明               |
|------------|--------|--------------------|
| search     | string | 模糊搜索           |
| min_price  | float  | 最低价格           |
| max_price  | float  | 最高价格           |
| min_change | float  | 最低涨跌幅(%)      |
| max_change | float  | 最高涨跌幅(%)      |
| sort_by    | string | 排序字段           |
| sort_dir   | string | asc / desc         |

---

## 注意事项

- 首次运行需要网络连接，后续断网可查看本地缓存
- AkShare 接口为第三方数据，仅供参考，不构成投资建议
- 刷新冷却时间默认 60 秒，可通过环境变量 `REFRESH_COOLDOWN` 调整
- 数据库文件路径：`backend/investpro.db`，可通过环境变量 `DB_PATH` 自定义
