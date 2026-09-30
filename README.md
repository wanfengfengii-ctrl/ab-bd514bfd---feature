# 同步辐射束线 · 探测器曝光排程系统

值班工程师在页面录入 5–10 项探测器曝光（持续时间、最早/最晚开始时刻、设备、结束后冷却时间）
与曝光间的最小/最大衔接间隔，服务端用 CP-SAT 联合求出全部**整数**开始时刻，并按以下顺序
**字典序**优化：

1. 最终结束时刻（所有曝光结束时刻的最大值）最小；
2. 开始时刻总和最小；
3. 按录入顺序展开的开始时刻序列字典序最小。

约束：

- 每项曝光满足其可开始时间窗；
- 同一设备同一时刻只能执行一项曝光，且后续曝光不得早于前序“曝光 + 冷却”结束；
- 每条衔接约束同时满足 `min_gap ≤ s_b − (s_a + d_a) ≤ max_gap`（最大间隔可留空）。

可选启用 **共享冷量**（部分探测器在曝光启动瞬间共用同一套低温机组的冷量）：

- 草稿中勾选启用，填写冷量容量 `capacity`、时刻零初始量 `initial_amount`、
  每整数时刻恢复量 `recovery_per_time`，并为每项曝光填写启动耗量 `startup_demand`；
- 全部开始时刻与**全局启动事件顺序**联合进 CP-SAT 求解，而非事后筛选：
  - 时刻零 → 首次启动、以及相邻的不同启动时刻之间，按经过的整数时刻恢复冷量，
    但冷量不得超过容量（`before = min(prev + rate·Δt, capacity)`）；
  - 同一时刻启动的多项曝光按**录入顺序**连续扣减，中间不恢复；
  - 每次扣减后冷量均须 ≥ 0；任何一步会透支的时序都不可行。
- 该约束与原有约束一起参与三级（最终结束时刻 → 开始时刻总和 → 开始时刻序列字典序）
  优化，因此“为等待冷量恢复而延后某些启动”会与其它目标一起取全局最优；
- 可行时响应逐项返回每次启动的启动时刻、本次恢复量、扣减前/后冷量（`cooling_events`，
  按全局启动顺序排列，同刻按录入顺序）；初始量超出容量报 400 输入错误；
  冷量不足属于无可执行时序（200 + `feasible=false`），不返回任何部分排程。

未启用时请求与响应字段、最优时刻与错误口径与旧版完全一致（`cooling_events` 为 `null`，
`startup_demand` 可留空）。

输入合法但无解时返回 `feasible=false`，**绝不返回部分曝光方案，也不沿用旧方案**；
草稿一经修改，前端立即作废旧结果并提示重新求解。

## 目录结构

```
.
├── docker-compose.yml        # api + web + verify（一次性）
├── .env.example              # 宿主机端口 API_PORT / WEB_PORT
├── backend/
│   ├── Dockerfile            # FastAPI + uvicorn，含 HEALTHCHECK
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py           # /health、/api/schedule
│   │   ├── scheduler.py      # CP-SAT 三阶段字典序优化
│   │   └── schemas.py        # 请求/响应模型
│   ├── scripts/verify.py     # 一次性校验脚本（退出码位掩码）
│   └── tests/                # pytest：调度器/冷量 + API 共 36 项
└── web/
    ├── Dockerfile            # nginx 静态站 + 反代 /api，含 HEALTHCHECK
    ├── nginx.conf            # /health 与 /api 反代
    └── src/                  # index.html / app.js / styles.css（无构建步骤）
```

## 启动

```bash
cp .env.example .env          # 可选：修改 API_PORT / WEB_PORT
docker compose build
docker compose up -d
```

- 页面：`http://localhost:${WEB_PORT:-8080}`
- API 健康检查：`http://localhost:${API_PORT:-8000}/health`
- Web 健康检查：`http://localhost:${WEB_PORT:-8080}/health`

宿主机端口通过环境变量配置，例如 `API_PORT=9000 WEB_PORT=9090 docker compose up -d`。

## 一次性校验服务

```bash
docker compose run --rm verify
```

`verify` 服务**自行退出**，退出码为位掩码（0 表示全部通过）：

| 位 | 值 | 检查内容 |
|----|----|----------|
| 0 | 1  | 代码测试（pytest，36 项） |
| 1 | 2  | 构建/完整性（字节码编译、应用导入、静态资源非空） |
| 2 | 4  | 可行排程（独立复核时间窗、设备占用含冷却、衔接间隔） |
| 3 | 8  | 无解/冷量不足 API 冒烟（200 + feasible=false 且无部分解；400 输入错误含初始量越界；Web/API 健康） |
| 4 | 16 | 共享冷量迫使延后（联合约束，独立回放恢复/扣减账目，容量封顶） |

## HTTP 约定

| 场景 | 状态码 | 响应 |
|------|--------|------|
| 可行 | 200 | `feasible=true` + starts/finishes/makespan/sum_starts/slacks/equipment_orders；启用共享冷量时另含 `cooling_events[]`（顺序/曝光/启动时刻/本次恢复量/扣减前后冷量/耗量） |
| 输入合法但无可行时序（含冷量不足） | 200 | `feasible=false, reason="no_schedule"`，所有方案字段（含 `cooling_events`）为 `null` |
| 输入错误（编号重复、时间窗倒置、未知引用、初始量超出容量、启用冷量却缺启动耗量等） | 400 | `reason="input_error"` + `field_errors[]` |
| 字段级 schema 错误 | 422 | FastAPI 校验明细 |
| 求解器超时未能判定 | 503 | `reason="solver_timeout"`（不谎称为无解） |

页面区分三类结果面板：**输入错误**（红）、**无可执行时序**（黄）、**最优排程**，
可行时展示 SVG 时间轴（曝光段/冷却段/时间窗，启用冷量时在各启动点标注 ❄耗量）、各设备执行顺序、
每条约束的实际间隔与对最大间隔的余量；启用共享冷量时另展示**冷量账户**——冷量水平折线图
（绿色段为按经过时间恢复、蓝色箭头为启动扣减）与逐次明细表，逐条说明“为何仍有足够冷量”
（自时刻零/距上次启动恢复多少、容量封顶）或同刻连续扣减的过程；若不可行，则提示冷量可能
在哪类启动处耗尽，且不渲染任何部分排程。

## 本地开发（无 Docker）

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
cd ../web/src && python3 -m http.server 8080   # 仅静态预览；API 需同源反代
python3 -m pytest backend/tests -q
python3 backend/scripts/verify.py              # 默认访问 http://api:8000，用 API_URL 覆盖
```
