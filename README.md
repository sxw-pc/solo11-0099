# 工地巡检证据台

面向本地合成巡检数据的覆盖性核验。照片字节的 SHA-256、声明、轨迹和检查项进入 SQLite；决策引用证据，缺轨迹时不虚构位置。

`python -m sitewalk demo` 初始化、生成 fixture、导入、核验并打印 JSON 报告路径。`python -m sitewalk --help` 查看命令；`python -m pytest -q` 运行测试。`evaluate --contract fixed_order|dependency` 可临时切换核验合同，只影响本次判定，不改写冻结计划。Python 3.11+ 标准库运行，pytest 仅用于测试。

数据流：Plan → Point / Prerequisite；Source → Photo / Track；Point → Item / Problem / Decision；Plan + Run → RouteCheck。位置用相邻轨迹点线性插值，轨迹缺口超过 10 分钟视为未知；核验规划有效期、点位半径、停留时长、检查项证据、跨点位或跨日期复用照片。问题状态为 found → assigned → rechecked → closed，非法状态转移报错。报告含实际到达顺序、违反的前置边、每个点的轨迹区间和证据哈希。

路线合同：计划用 `route_contract` 选择 `fixed_order`（实际到达顺序必须与 `ord` 完全一致）或 `dependency`（所有必检点覆盖且每条 `A → B` 前置边满足，无依赖点可按现场通道先后到达）。前置边写在 `prerequisites: [[A, B], ...]`、`{before, after}` 或点位 `after` 字段；未知点位、自环和循环依赖拒绝整份计划。两种合同都必须覆盖全部必检点，宽松合同不会把漏检、轨迹缺口、照片复用或计划版本不符判成覆盖。计划加载时冻结内容摘要 `digest`，`verify_plan` 可复算校验；`route_check` 同时记录两种合同的实际到达顺序、违反边与证据哈希。

定位距离使用局部平面近似，适用于小型工区。室内楼层和多终端冲突属于未来边界，不在首版实现。

安装检查：在仓库目录运行 `python -m pip install . --no-build-isolation`；本地开发也可直接运行模块命令。报告同时生成 JSON 和 Markdown，`inspect` 子命令可追踪单个对象。

审计台账：Run 由规则版本和来源摘要确定，Evidence 关联原始对象与来源，Decision 引用 Run；重复核验保持同一输入的报告稳定。

原始来源字节保存在 SQLite BLOB 中，JSON 证据包以 Base64 表示字节；修改输入文件不会覆盖已采录证据。
