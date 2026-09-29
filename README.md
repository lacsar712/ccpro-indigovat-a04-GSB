# IndigoVat-01 · 染缸还原台

FastAPI + PostgreSQL + Jinja2：主界面是横向**缸位条**（Alpine 反应式），不是工坊/染缸/批次三表导航。Session Cookie 登录；规则在 `app/services/vat_rules.py`。

## 技术栈

- FastAPI、SQLAlchemy 2、PostgreSQL
- 启动时 `create_all` + 幂等种子（蓝靛湾一号坊 / 清水江二号坊）
- Session Cookie 认证（Starlette SessionMiddleware）
- Jinja2 + Alpine.js + Pico（叠靛蓝水墨自定义样式）
- Docker Compose：`web` + `db`

## 端口与数据库

| 服务 | 端口 |
|------|------|
| Web  | **4720** |
| Postgres | **6120**（容器内 5432） |

数据库账号：`indigovat` / `indigovat` / 库名 `indigovat`

## 快速启动

```bash
cd IndigoVat/IndigoVat-01
docker compose up --build -d
```

浏览器打开：http://localhost:4720

演示账号（登录页已预填）：

- `admin` / `123456`
- `worker` / `123456`

## 交互（信息架构）

1. **染缸还原台**：横滑缸位条，每缸显示状态、最近电位与 redox sparkline；有未销号留底的缸挂「待销」角标
2. **工坊 chip**：仅作缸位筛选，无独立工坊 CRUD 页
3. **点缸展开**：同页内登记浸染批次、改状态、看近几笔；无平行「染缸表 / 批次表」
4. **布样留底专页**（顶栏入口，带未销号角标）：未销号筛选、新建/更新留底、主管销号

**业务规则**：

- 状态改为 `ready`（可染色）时，最新批次 `redoxMv` 须已填且 ≤ -500（见 `vat_rules.py`）。
- **可染色缸登记新浸染前，须先在布样留底专页留一条未销号记录。** 浸染保存入口（`POST /bay/vats/{id}/lots`）与留底有效性检查共用 `validate_dip_recording`（`app/services/vat_rules.py`），缸位展开区不另写放行。
- 留底字段：染缸、留样米数、留样时刻、柜格代号、是否已销号、登记人。**留样米数须为正，上限固定 2 米**；同一染缸同时只能有一条未销号留底。新建与更新共用 `validate_retain`（米数 + 未销号唯一）。
- **12 小时有效期**：浸染保存时，未销号留底的留样时刻距现在须不超过 12 小时；无未销号留底或已逾期，一律中文拒绝。
- **并发拒单**：同一染缸的未销号唯一性由数据库部分唯一索引兜底；两人几乎同时给同一缸建未销号时至多一笔入库，另一笔收到中文拒绝且本笔不落库。被拒后还原台与留底专页照常可打开。
- **销号仅主管（admin）可做**；染缸工（worker）点销号返回中文拒绝且不清除会话（不退出登录）。
- 缸位条「待销」标记与留底专页未销号数同源对账。种子数据恰有一口可染色缸（V-12），且无任何留底。

## 本地开发（可选）

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
set POSTGRES_HOST=localhost
set POSTGRES_PORT=6120
uvicorn app.main:app --host 0.0.0.0 --port 4720 --reload
```

## 业务模型

1. **Workshop**：`name`、`region`、`notes`（UI 上仅为筛选片）
2. **Vat**：归属工坊、`code`、`dyeType`、`volumeL`、状态 `idle|reducing|ready`
3. **DipLot**：归属染缸、`dippedAt`、`clothMeters`、`redoxMv`（可空）
4. **ClothRetain（布样留底）**：归属染缸、`sampleMeters`（>0 且 ≤2）、`retainedAt`、`binCode`、`reconciled`/`reconciledAt`、`registeredBy`；对 `vat_id` 建未销号部分唯一索引

## 目录结构

```
IndigoVat-01/
  Dockerfile
  entrypoint.sh
  docker-compose.yml
  requirements.txt
  app/
    main.py
    db.py
    models.py
    schemas.py
    auth.py
    seed.py
    routers/
    services/vat_rules.py
    templates/   # base / bay / login / retains
```
