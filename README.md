# 钉钉差旅报销 Excel 生成工具

本仓库实现一个公司内部钉钉 H5 工具：员工免登后填写报销项目/预算代码，按需申请出差补助，上传独立票据，确认识别结果，下载基于公司模板生成的费用报销单 `.xlsx`。

详细设计和分阶段验收见 [`docs/V1_IMPLEMENTATION_PLAN.md`](docs/V1_IMPLEMENTATION_PLAN.md)。本文是开发时必须遵守的范围与业务契约；两者冲突时，应先更新并确认文档，不在代码中自行扩大需求。

## V1 交付范围

- Vue 3 + TypeScript H5、FastAPI 单体后端、SQLite 配置库。
- 钉钉免登；姓名和部门只能来自后端确认的钉钉 Session。
- 项目/预算代码搜索、选择和本次手工填写。
- 出差补助为可选项；申请时填写类型和开始/结束时间，由后端重算。员工只选“市外项目”，系统按 30 个自然日边界判定短期/长期。
- JPG、JPEG、PNG 和独立单页 PDF；一个文件只包含一张票据。
- PDF 文本优先、本地 PaddleOCR 回退；同时尽力本地解码旧版发票二维码作为金额和开票日期的交叉校验证据，不调用付费 OCR 或大模型 API。
- V1 自动解析铁路电子客票和旅客运输电子发票；旅客运输发票按票面交通工具分类，通用发票保守兜底。
- OCR 结果可编辑、删除，也可完全手工新增费用明细。
- 管理员可在每日补助设置之前按费用类别维护关键词；所有关键词规则一致，均可直接新增、修改、移动类别或删除。
- 基于脱敏公司模板生成 Excel 并即时下载；生成文件不落盘。
- 上传票据仅临时保存并自动清理；不保存报销历史和 OCR 原文。

## 补助计算契约

不申请出差补助时，无需填写任何出差信息，Excel 中不生成补助行。申请补助时，出差时间必须精确到时分，以公司时区的 12:00 为半天边界。V1 将 12:00 整点归入后半天，正式上线前需由业务负责人确认。

跨日行程：

- 出发日 12:00 前出发计 1 天，12:00 及以后计 0.5 天。
- 返回日 12:00 前返回计 0.5 天，12:00 及以后计 1 天。
- 中间完整自然日每天计 1 天。

同日往返：

- 跨过 12:00 计 1 天。
- 均在同一半天内计 0.5 天。
- 结束时间早于开始时间必须拒绝。

当前配置默认值来自制度截图，正式使用前需确认：

| 出差类型 | 默认标准 | V1 处理方式 |
|---|---:|---|
| 商务出差 | 100 元/天 | 按半天规则自动计算 |
| 市外项目短期 | 100 元/天 | 按半天规则自动计算 |
| 市外项目长期 | 150 元/天 | 超过 30 个自然日；按半天规则自动计算，标准由管理员配置 |
| 同市项目 | 50 元/天 | 用户按实际出勤确认有效天数，标准由管理员配置 |
| 公司内部出差 | 通常 100 元/天 | 用户确认有效天数；无锡—苏州等制度例外可明确选择不补助 |

商务出差和市外项目都按时间自动计算有效天数。员工选择“市外项目”后，系统按日期自动分类：连续 30 个自然日以内（含）使用短期标准，超过 30 个自然日使用长期标准。同市实际出勤和公司内部出差由员工确认有效天数，但不能修改管理员配置的每日标准；确认天数不能超过按半天边界得到的本次行程天数。

## Excel 输出契约

- 保留公司模板的标题、合并单元格、字体、边框、行列尺寸和 A4 打印设置。
- 姓名、部门从 Session 获取；前端不能覆盖。
- 页面字段称“报销项目/预算代码”，输出时保留正式模板的实际表头。
- 明细按发生日期升序稳定排列，同日保持原始录入/上传顺序。
- 币种固定为“人民币”，V1 不处理外币。
- 费用类别代码和中文名称只在后端公共契约中定义一次；模板现有 17 类全部可手工选择。V1 自动识别只覆盖已有样本支持的子集，不能识别的类别直接生成可编辑的“其他”费用行。
- 旅客运输发票优先按票面交通工具分类：铁路为“火车票”、航空为“飞机票”；已支持的公路/出租汽车类型及明确的“客运服务费”税目归为“市内交通费”。客运 PDF 同时从版面列恢复出发地和到达地作为可编辑说明；无法可靠判断时为“其他”并标记需要核对。
- 出差补助行的发生日期为返回日期，说明格式为“开始日期—结束日期，共 N 天出差补助”，票据张数为 0。
- 金额使用 `Decimal`；总额、人民币大写和票据张数由后端重新计算。
- 姓名、部门、项目、日期文本、说明等所有来自用户、钉钉或 OCR 的 Excel 文本统一通过安全写入函数；首字符为 `=`、`+`、`-`、`@` 时按纯文本转义，禁止形成公式。
- Excel 回归测试必须分别向上述字段写入四种危险前缀，重新打开 `.xlsx` 后断言其仍为字符串、没有公式单元格，并检查 OOXML 中未生成外部输入公式。
- Excel 明细不受模板预留行数限制；超过第 54 行时按模板样式动态扩行，并同步下移总计行、扩展类别下拉和打印区域。部署默认保留 200 条费用明细的技术保护上限，可选补助由后端额外生成。生成内容写入内存后直接下载。

模板坐标只在 `backend/app/excel/template_contract.py` 中维护：`C2` 姓名、`E2` 部门、
`G2:I2` 项目；第 4–54 行的 `B`、`C`、`D:F`、`G`、`H`、`I` 依次为类别、日期、说明、
金额、币种、票据张数；第 55 行的 `C:F`、`G`、`I` 依次为人民币大写、总金额、总票据数。标准发生日期以 `M月D日` 紧凑显示；明细金额为普通数值格式，总金额使用原版人民币会计格式。
明细按发生日期升序，同日保持用户顺序，补助排在同日用户明细之后。

17 个稳定费用类别为：`airfare`（飞机票）、`rail_fare`（火车票）、`local_transport`（市内交通费）、`lodging`（住宿费）、`subsidy`（出差补助）、`office`（办公费）、`hospitality`（招待费）、`communications`（通讯费）、`employee_welfare`（福利费）、`consulting`（咨询费）、`advertising`（广告费）、`leasing`（租赁费）、`property_management`（物业费）、`utilities`（水电费）、`labor_service`（劳务费）、`conference`（会议费）、`other`（其他）。前端、Parser 和 Excel 服务都引用该公共契约，不各自维护枚举。

## 明确排除

V1 不实现：

- 打车行程单解析。
- 汇总 PDF、PDF 合并、票据配对、`merge.py`。
- 一个文件多张票据、多页票据拆分。
- 钉钉报销审批、出差申请关联。
- 发票验真、查重、税务查询。
- 预算控制、ERP 对接、财务付款。
- 机票舱位、住宿限额、自驾标准、跨年发票等合规审核。
- 纸质票据粘贴和电子票据打印排版。
- 历史报销、草稿恢复、长期文件存储。
- Redis、消息队列、微服务、Kubernetes、大模型。

## 实现原则

- 先完成免登、手工明细、补助和正确 Excel，再接上传与 OCR。
- OCR 获取文本，Parser 负责票据类型和字段；新增费用类别不要求新增 Parser。
- 内置 Parser 明确分类时不允许管理员关键词覆盖；仅当结果为“其他”时应用管理员关键词，同一票据命中多个不同类别仍保留“其他”并提示核对。
- 无法可靠分类、缺少字段或识别失败时，前端都会保留一条带警告的可编辑费用行；缺日期或金额会阻止最终计算和 Excel，直至用户修正。前端对每个独立文件分别发起请求，因此一张失败或超时不会影响下一张，也不会切换到云端 OCR。
- 所有业务 API 必须鉴权，写接口必须校验 CSRF，管理员接口必须后端鉴权。
- CSRF token 只保存在前端内存；页面刷新后，以现有 HttpOnly Session 调用 `GET /api/me`，后端轮换并返回新的 token，前端覆盖内存值后继续写请求。
- Client Secret 只在后端；上传目录不可公开；原始 OCR 文本不入库、不写普通日志。
- 不提交含真实员工、项目或票据信息的样本；测试使用脱敏或合成 fixtures。

## Phase 2：钉钉免登配置

后端使用固定的组织应用配置完成免登，浏览器只会读取 `DINGTALK_CLIENT_ID` 和
`DINGTALK_CORP_ID`；`DINGTALK_CLIENT_SECRET`、`DINGTALK_AGENT_ID` 与 `SESSION_SECRET`
只存在后端环境变量中。
钉钉应用需要具备免登码换用户、读取用户详情和读取部门详情的权限，并将部署使用的
HTTPS 域名配置为应用可信域名。管理员使用 `ADMIN_USER_IDS` 配置钉钉 userId，多个值用
英文逗号分隔。

本地普通浏览器调试可以显式设置 `APP_ENV=development`、
`AUTH_MOCK_ENABLED=true` 和固定的 `AUTH_MOCK_USER_ID`、
`AUTH_MOCK_USER_NAME`、`AUTH_MOCK_DEPARTMENTS`。Mock 接口不接受请求传入的身份或管理员
标记，且生产配置发现 Mock 开启会直接拒绝启动。`.env.example` 默认关闭 Mock；本地 HTTP
可使用 `SESSION_COOKIE_SECURE=false`，生产环境必须改为 `true` 并配置至少 32 字符的随机
`SESSION_SECRET`。

当前免登 API：

- `GET /api/config/public`：仅返回 CorpId、Client ID 和是否开放开发 Mock。
- `POST /api/auth/dingtalk`：接收一次性 `authCode` 并建立服务端 Session。
- `GET /api/me`：读取当前身份并轮换仅保存在前端内存中的 CSRF token。
- `POST /api/me/department`：多部门用户从服务端确认过的部门中选择当前部门。
- `POST /api/auth/logout`：校验 CSRF 后注销服务端 Session。

项目查询、设置读取和全部管理接口都要求 Session；写接口还要求
`X-CSRF-Token`。开发环境用 Vite `/api` 同源代理，生产环境由 Nginx 同源反向代理，不启用
携带凭据的通配 CORS。

### 从钉钉测试真实 dev 免登

普通 `make dev-backend` 始终使用合成身份，不会调用钉钉。需要联调真实免登时，可以创建
本机私密配置并填写真实组织应用信息：

```bash
cp .env.dingtalk-dev.example .env.dingtalk-dev
```

`.env.dingtalk-dev` 已被 Git 忽略，但不是必需文件：文件不存在时，脚本直接读取当前终端
已有的环境变量；显式设置 `DINGTALK_DEV_ENV_FILE` 后，指定文件不存在则拒绝启动。后端始终
要求真实的 `DINGTALK_CLIENT_ID`、`DINGTALK_CLIENT_SECRET`、`DINGTALK_CORP_ID` 和
`DINGTALK_AGENT_ID`。
开发配置中的 `SESSION_SECRET` 可留空，脚本会为本次进程生成随机值；重启后已有开发 Session
失效是预期行为。生产环境仍必须显式提供至少 32 字符的持久随机值。

一个命令同时启动后端和前端：

```bash
make dev-dingtalk
```

需要把日志分开时，仍可在两个终端分别执行 `make dev-dingtalk-backend` 和
`make dev-dingtalk-frontend`。

新模式固定关闭 `AUTH_MOCK_ENABLED`，使用独立的 `backend/data/dev-dingtalk.db` 和
`/tmp/dingtalk-expense-dingtalk-dev`。前端仍通过 Vite 将同源 `/api` 代理到本机后端。
`dev-dingtalk-frontend` 会显式设置 `VITE_DINGTALK_REMOTE_DEBUG=true`，按需动态加载锁定的
`dingtalk-h5-remote-debug@0.1.3`；只有钉钉调试平台生成的调试链接才会继续加载远程调试 SDK。
普通 `dev-frontend`、测试和生产构建默认不初始化该工具。
PC 钉钉本机调试时，不配置 `DINGTALK_DEV_PUBLIC_HOST`，保持
`DINGTALK_DEV_COOKIE_SECURE=false`，在官方四端调试工具中填写
`http://127.0.0.1:5173`。如果改用外部 HTTPS 入口，则把
`DINGTALK_DEV_PUBLIC_HOST` 设置为不含协议、端口和路径的可信域名，将
`DINGTALK_DEV_COOKIE_SECURE=true`，并把该入口反向代理或安全隧道转发到前端端口；HTTPS
入口还必须支持 WebSocket 才能使用 Vite 热更新。若入口运行在本机，保持
`DINGTALK_DEV_BIND_ADDRESS=127.0.0.1`；只有可信代理从局域网连接时才改为 `0.0.0.0`。
最终必须从钉钉调试工具、工作台或钉钉内置浏览器打开配置地址，验证
`dd.requestAuthCode -> /api/auth/dingtalk -> /api/me`，而不是直接点击开发 Mock。

## Phase 3：手工报销与计算

- `GET /api/expense-categories` 返回后端集中维护的 17 类费用契约；`subsidy` 由系统生成，不可手工选择。新增类别只改集中契约，OCR Parser 映射保持独立。
- `POST /api/calculate/subsidy` 和 `POST /api/calculate/totals` 都要求 Session、已选择部门和 CSRF。请求不接受姓名、部门、总额、人民币大写或自动类型的覆盖天数/标准。
- 五类每日标准均由管理员配置，默认依次为 `100/100/150/50/100` 元。出差补助默认不申请；申请时，员工侧只显示“市外项目”，后端按 30 个自然日边界选择短期或长期标准。商务出差和市外项目自动计算；同市项目、公司内部出差必须提交 `policyConfirmed=true` 和本次有效天数。
- 金额在 API 中使用两位十进制字符串，后端使用 `Decimal`，前端即时预览使用整数分；最终费用金额、补助、合计、票据数和人民币大写以后端响应为准。
- 报销项目、出差和明细仅在 Pinia 内存中，不使用 `localStorage`、`sessionStorage`，不写 SQLite。刷新、退出或 Session 失效后清空。

## Phase 4：Excel 生成与下载

- `POST /api/excel/generate` 要求 Session、已选择部门和 CSRF，请求顶层只允许 `project`、`trip`、`items`。`trip=null` 表示不申请补助，不生成补助行。接口拒绝姓名、部门、天数、补助总额、总金额和人民币大写等客户端权威字段。
- 选择项目提交 `{ "mode": "selected", "id": 1 }`，后端重新读取仍启用的项目；本次手工项目提交 `{ "mode": "manual", "text": "..." }`。费用行只提交类别、发生日期、显示日期、说明、金额和票据张数，不能提交补助行。
- 姓名和部门只取当前 Session；补助设置、有效天数、补助金额、费用合计、总额、票据数和人民币大写均在生成时重新计算。
- 后端只读加载 `backend/app/templates/expense_template.xlsx`，先做 ZIP 安全预检（10 MiB 文件、200 条目、20 MiB 单条目、50 MiB 总解压体积、200 压缩比上限，并拒绝重复或越界路径），再验证唯一可见 `费用报销模板`、标签、合并区域和精确的 17 类 `B4:B54` 字面列表验证。生成内容超过模板预留行时，服务按第 54 行样式扩展明细、类别下拉和合并区域，将总计行及打印区域同步下移；保持样例的 A4 纵向、页边距和打印设置。公式或自定义数据验证、普通公式、超链接、额外定义名、打印标题、条件格式、表格/计算列、透视、图表、图片/绘图、切片器、批注/注释、外部关系或链接、宏/VBA、数据连接、QueryTable、ExternalLink 和嵌入活动内容一律拒绝。写入 `BytesIO` 后再次验证并直接流式返回，不创建临时 Excel。
- 所有外部文本都会清理控制字符并限制长度；去除前导空白后以 `=`、`+`、`-`、`@` 开头的内容加文本转义前缀，确保不会形成公式。
- 文件名为 `差旅费报销单-姓名-项目编号或项目.xlsx`，替换 `/\\:*?"<>|` 和控制字符，并通过 RFC 5987 的 `Content-Disposition` 返回。前端只发送上述最小载荷，以 Blob 下载并及时释放对象 URL。

替换模板时必须先在副本中彻底脱敏，确保只有一个可见 `费用报销模板`，不含公式、超链接、额外定义名、
打印标题、条件格式、表格/计算列、透视、图表、图片/绘图、切片器、批注/注释、外部关系或链接、
宏/VBA、数据连接、QueryTable、ExternalLink 或嵌入活动内容，并通过上述 ZIP 预检和严格部件白名单；
同时保持基础模板的上述坐标、`D4:F54`/`C55:F55` 合并及精确的 17 类 `B4:B54` 字面列表验证。运行时只允许生成服务按受控规则扩展这些区域，并继续保持样例的 A4 纵向、页边距和打印设置。随后运行完整 Excel 回归测试。不得修改桌面业务样例原件，也不要通过放宽模板契约来掩盖模板版本差异。

## Phase 5：临时票据与本地 OCR

- `POST /api/files/upload` 使用 multipart 字段 `files[]`，要求 Session、已选择部门和 CSRF。每个请求必须且只能携带一个文件；前端多选后按选择顺序逐个请求，一张失败仍继续处理下一张。后端先取得全局准入和 Session 上传租约，再流式解析正文；普通字段必须为 0，单文件最多 20 MiB，请求体上限 25 MiB（含 multipart 余量）。`Content-Length` 只用于提前拒绝，流式计数才是权威限制。Session 默认最多保留 200 个文件、合计 100 MiB；数量是可配置的技术保护值，实际更常先受总容量约束。
- Nginx 的 `client_max_body_size` 设为 25 MiB，只为一个 20 MiB 文件的 multipart 边界和请求头预留余量；200 个文件/100 MiB 是跨请求的两个独立 Session 保留额度，不是单次请求额度。
- 文件扩展名、magic bytes 和实际解析结果必须一致。Pillow 图片解码、PDF 预检/文本提取和 OCR 每次都在全新的标准库 `spawn` 工作进程中执行；进程准入容量为 1，仅有有界等待，不建立业务队列。每任务新进程确保 5 GiB OCR 地址空间配置不会被后续 512 MiB 图片/PDF 验证复用；代价是 V1 不跨 OCR 请求缓存模型。文件验证超时或资源终止返回稳定的 `IMAGE_VALIDATION_TIMEOUT` / `PDF_VALIDATION_TIMEOUT` / `PROCESS_RESOURCE_LIMIT`；OCR 对应返回 `OCR_BUSY` / `OCR_TIMEOUT` / `OCR_FAILED`。取消请求时，准入令牌会一直保留到该子进程终止并回收，然后同步关闭 spool 并删除 `.part`/最终文件。PDF 资源检查递归覆盖 Form XObject 及图片遮罩，并分别限制页面内容流数量（默认 512）和 XObject 总数（默认 100），同时限制嵌套深度、MediaBox/渲染像素、全部内嵌图片累计像素和每页解压后内容流字节数。HEIC、汇总 PDF、多页拆分和行程单均不支持。
- 磁盘路径只使用服务端 Session 哈希与随机 UUID；前端只看到不透明 `tempId` 和原始显示名，不返回真实路径。员工页面不暴露服务器文件概念；删除 OCR 费用行时由应用调用 `DELETE /api/files/{tempId}` 同步清理关联文件，登出也会清理 Session 目录。
- 上传文件超过 30 分钟未使用后进入清理范围；服务启动时先清理、运行期每 10 分钟扫描，因此通常会在未使用约 30～40 分钟内删除。实现不跟随符号链接，不建立票据数据库清单，不写 OCR 原文或 sidecar。
- `POST /api/ocr` 每次只接收一个 `fileId`（兼容请求体形式为 `fileIds: [id]`）和可选 `tripYear`。一个独立文件只产生一个结果；前端逐文件串行调用，并直接将结果写入与该文件关联的可编辑 `ExpenseItem`。金额使用两位十进制字符串；用户明确点击“重新识别”时更新同一条明细，不生成重复行。
- 单页数字 PDF 先使用原生文本；文本过短、解析后仍缺金额或日期、火车票缺路线，或者旅客运输票据仍无法分类时，才执行一次 PaddleOCR 回退。客运 PDF 的起终点优先使用 pypdf 版面列提取；纯图片 OCR 若把出发地与到达地合成一个跨列文字框，则根据已识别的表头坐标分别裁切两个单元格并二次识别，不凭扁平文本猜测列边界。不记录姓名或证件号。基础开发/测试不会导入或加载 Paddle，测试用显式 fake engine；生产禁止 fake。
- 普通发票金额依次优先使用票面数字“价税合计”、大写“价税合计”、或明确的“合计 + 税额”；单独的未税“合计”不会被当成最终报销额。旧版增值税发票二维码只作为补充证据。二维码金额与票面总额一致，或“二维码金额 + 税额 = 票面价税合计”时可提高可靠性；冲突时保留票面总额并提示人工核对；票面缺少金额而只从二维码取得候选时也必须提示核对。二维码中的开票日期不覆盖火车票乘车日期或旅客运输发生日期。动态二维码和未知格式会被安全忽略，不上传、不访问二维码链接、不记录原始载荷。
- 管理员在“系统设置 → 票据分类关键词”按费用类别分组维护全部分类词；该区域显示在每日补助设置之前。设置页与报销页共用 `GET /api/expense-categories` 返回的后端类别契约：系统生成的“出差补助”不显示，“其他”显示为自动兜底且不能添加关键词。关键词按 OCR/PDF 文本的字面子串匹配，至少 2 个字符；所有词始终生效，均可新增、修改、移动类别或删除，不区分来源或启停状态，最多 500 条。不同费用类别同时命中时保留为“其他”；旅客运输发票只在票面“交通工具类型”或明确客运税目中匹配类别，不用起终点、酒店或公司名称作分类证据。火车票等专用票据仍结合版式结构识别，不会只因删除分类词而失去基础识别能力。`GET/POST/PUT/DELETE /api/admin/receipt-keywords` 均由后端管理员权限保护，写操作同时校验 CSRF。
- 本地 OCR 依赖精确锁定为 PaddleOCR 3.7.0、PaddlePaddle 3.3.1、OpenCV 4.10.0.84 和 pypdfium2 5.13.0；后两项用于本地二维码识别和单页 PDF 渲染。开发安装和正式镜像均默认包含 OCR。Linux/amd64 是正式部署路径并要求 AVX CPU；macOS/arm64 仅作为本机 CPU 开发测试路径。模型二进制不入库，必须先在受控流程中校验来源、许可证与 SHA-256，再只读挂载 `PP-OCRv6_small_det` 和 `PP-OCRv6_small_rec`。运行时不会下载模型或回退云服务。
- 2026-09-03 已在当前 Apple Silicon Mac 用固定 SHA 清单中的两份官方模型完成本地 CPU 检查，并通过 Docker 的 Linux/amd64 模拟环境完成完整镜像构建、production 冷启动、资源上限和真实票据 OCR smoke。模型二进制由 `.gitignore` 排除，因此其他检出仍须按清单自行预置；原生 Linux/amd64 目标机和断网部署仍需上线前复验。OCR 默认启用，依赖或模型不完整时 readiness 失败。

前端只提供一个“费用明细”区域，右上角统一放置“手动添加”和“选择票据”。用户一次选择多个独立票据后，上传中/识别中的文件先显示在同一区域，OCR 完成或失败后直接成为带来源与警告标记的可编辑费用行。前端从后端公开配置读取技术保护上限，默认可处理 200 条费用明细；Excel 不再受模板行数限制。删除 OCR 费用行会同时移除关联临时文件；文件超过 30 分钟未使用后仍由后台周期清理，退出登录时立即清理。页面不展示 OCR 原文，不使用浏览器持久化，也不提供云 OCR 或付费 API 配置。

上传票据默认按文件修改时间保留 30 分钟。每个 Session 跨多次请求默认最多保留 200 个文件、100 MiB；解析缓存位于 `<TEMP_DIR>/.spool`，单文件只允许 256 KiB 留在内存，超过后转为可计量的磁盘缓存。最终文件按缓存逐个转存并立即关闭删除缓存。全局临时文件默认限制为 768 MiB，包含活动/残留 spool 和 `.part`；再为单文件转存峰值预留 20 MiB、为运行时预留 200 MiB，使最坏配置低于 1 GiB tmpfs。上传、识别和删除在取得与退出相同的 Session 租约后，会用独立 SQLite 查询重新确认 Session、公司和有效期；租约持续覆盖文件操作，因此退出要么先删除并使等待操作返回 401，要么等待已准入操作完成后删除其文件。清理会跳过正在使用的文件和活动 spool。生产容器以固定非 root 用户运行，后端不直接发布端口，只由 Nginx 反向代理访问。

工作进程在 Linux 上保留宿主硬限额，只调整可恢复的软限额：文件/PDF 验证默认 512 MiB，OCR 默认 5 GiB 虚拟地址空间（Paddle/OpenCV 会预留较大虚拟映射，常驻内存显著低于此值），每页 PDF 解压后内容流默认最多 32 MiB，同时限制生成文件大小、打开文件数、CPU 时间并禁止 core dump。Compose 额外使用 3 GiB 容器实际内存和 128 PID 上限。正式 OCR 必须运行在支持这些限额的 Linux x86_64/amd64 环境，不提供绕过生产限额的开关。

## 开工前业务确认

- 公司正式空白 Excel 模板及“报销项目/预算代码”表头。
- 截图制度是否仍为现行版本，以及 12:00 整点归属。
- 五类补助标准、短期/长期 30 天边界、12:00 整点归属和同市/内部出差有效天数确认责任人。
- 项目/预算代码清单、管理员钉钉 userId。
- 钉钉 CorpId、Client ID、Client Secret、权限和 HTTPS 域名。

公司正式模板版本和制度版本是业务验收依赖，不阻止开发闭环；模板版本仍阻止 Phase 4 正式业务验收，制度版本阻止补助功能及生产上线验收。开发期间只使用已脱敏模板和可配置默认值，不能把待确认内容固化成不可修改规则。

## 依赖锁状态

- 前端提交 npm v3 `package-lock.json`，本地安装与容器构建统一使用 `npm ci`，不允许在构建阶段改写锁文件；当前锁已通过全新 `npm ci`、测试、类型检查、Lint 和构建验证。
- 前端使用 TypeScript 官方的 7/6 并行过渡方案：`@typescript/native` 提供 TypeScript 7 原生 `tsc`，`typescript` 别名指向 TypeScript 6 API，供尚未兼容原生编译器 API 的 `vue-tsc` 和 `typescript-eslint` 使用。`npm run typecheck` 会同时运行两条检查链，不能删除其中任一依赖后只验证另一条。
- 容器构建使用 Node 24 LTS，后端运行时使用 Python 3.13.15；uv 固定为 0.12.9，入口使用 Nginx 1.30.4 stable。Python 3.14 尚无当前 PaddlePaddle 版本的 wheel，Node 26 仍为 Current，因此暂不采用。
- 本地 Python 由 `backend/.python-version` 固定为 3.13.15 并交给 uv 管理，不要求通过 Homebrew 安装 Python；macOS 自带 Python 不参与后端运行。
- 后端业务调用钉钉和 PaddlePaddle 依赖继续使用 `httpx` 0.28.1；测试环境另外锁定 `httpx2` 2.12.0，专供新版 Starlette `TestClient` 使用，不混用两套客户端处理业务请求。
- 当前 npm 11 标准生成结果并未为全部依赖条目写入 `resolved`/`integrity`。这里不手工拼接 URL 或哈希，也不宣称具备完整的锁文件校验和覆盖；若上线环境把完整哈希作为供应链硬性要求，应在干净 npm 环境中重新生成并单独复核后再发布。
- 后端已使用 uv 生成并提交 `uv.lock`；本地安装、检查与容器构建统一使用 `--frozen`，确保声明与锁文件不一致时立即失败。

## 本地普通浏览器联调

先安装已锁定依赖：

```bash
make backend-install
make frontend-install
```

然后在仓库根目录运行：

```bash
make dev
```

需要把日志分开时，仍可在两个终端分别执行 `make dev-backend` 和 `make dev-frontend`。

后端脚本先对 `backend/data/dev.db` 执行 Alembic migration，再监听
`http://127.0.0.1:8000`；前端监听 `http://127.0.0.1:5173` 并将 `/api`
同源代理到后端。打开前端地址后点击“使用固定测试身份”。脚本会强制使用合成的
`local-dev-user / 本地开发用户 / 本地开发部门`，该身份同时是本地管理员；请求不能覆盖它。
`make backend-install` 已包含锁定的 OCR 依赖。`make dev-backend` 默认校验本地模型并启用
真实 PaddleOCR；依赖或模型缺失时直接拒绝启动，不会静默关闭 OCR。

### macOS arm64 本地 OCR 联调

当前 Apple Silicon Mac 可以使用官方 macOS arm64 CPU wheel 做开发测试；不启用 GPU 或 HPI，
也不改变“生产 OCR 必须运行在 Linux x86_64/amd64”的安全边界。本地开发默认启用真实 Paddle：

1. 按 [模型目录说明](backend/models/README.md) 从官方地址取得两个推理模型，记录原始归档
   SHA-256，并解压到指定的两个目录。模型文件不提交仓库。
2. 安装锁定的 macOS arm64 OCR 依赖，先核对模型固定 SHA 清单，再做真实 Paddle 初始化和空白图最小推理：

   ```bash
   make backend-install
   make ocr-models-check
   make ocr-runtime-check
   ```

3. 启动默认的本地 OCR 后端和前端：

   ```bash
   make dev
   ```

4. 访问 `http://127.0.0.1:8000/api/ready`；`ocr` 应为 `configured`。随后只用脱敏、独立单票据
   JPG/PNG/单页 PDF 做 smoke test，核对日期、金额、类型和路线，而不是只看是否返回文字。

`dev-backend` 固定使用合成管理员身份、本地 CPU、显式模型目录以及
`PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=1`，不会回退到云服务或在运行时下载模型。它会先拒绝
缺失、被修改或与固定 SHA 清单不一致的模型目录。

当前 Mac 的真实 smoke 结果：`高铁1.pdf` 识别为火车票，日期 `2026-07-07`、金额 `473.50`、
路线“合肥南站-北京南站”；`打车发票1.pdf` 识别为市内交通费，发生日期 `2026-07-06`、金额
`10.13`，并能从票面行程表恢复起点-终点说明。测试没有使用行程单或汇总 PDF，也没有记录或输出完整 OCR 原文。

不要复制这组开发值到生产配置。`APP_ENV=production` 时，Mock、非 Secure Cookie、空或占位
钉钉配置、短 Session Secret、Fake OCR 都会使配置校验直接失败。

## 存活、就绪与部署边界

- `GET /api/health` 只表示 FastAPI 进程存活，不访问依赖。
- `GET /api/ready` 以只读方式检查 SQLite 连接、当前 Alembic revision 与关键表字段、Excel 模板完整契约、临时目录可写性，以及
  已启用 OCR 的模型精确文件集合、固定 SHA 清单和锁定包版本；返回内容只有组件状态，不包含路径、Secret 或异常细节。模型内容校验按文件 stat 指纹缓存，文件变化才重新计算 SHA；就绪检查不会初始化或加载 Paddle 模型。
- 模板严格校验结果按文件 stat 指纹缓存在最多 8 个条目的进程内缓存中；模板文件变化会重新
  执行完整 ZIP 与工作簿契约校验，常规 10 秒 readiness 轮询不会重复解析不变的 `.xlsx`。
- OCR 默认启用并纳入就绪检查；模型、固定 SHA 或 Paddle 版本不完整时返回 503，
  不会把缺少识别能力的实例标记为就绪。仅诊断或聚焦测试可显式设置 `OCR_ENABLED=false`。
- 生产启动会在独立受限子进程中完成一次真实模型初始化和空白图片推理；自检失败时 FastAPI 不接受流量。
- Compose 后端 healthcheck 使用 readiness，Web 容器等到后端可接单才启动。Dockerfile 仍以
  固定非 root 用户运行，后端没有宿主端口，只能从 Compose 网络中的 Nginx 访问。

生产必须使用一个公司管理的 HTTPS 入口，浏览器只访问同一个
`https://报销应用公司域名/`；该入口把流量转发到宿主机私有的应用端口。Compose 默认
`APP_BIND_ADDRESS=127.0.0.1`，不会把 HTTP 直接暴露到局域网。外层 HTTPS 入口负责：

- 公司域名证书、TLS 策略和 HSTS；本仓库不生成或保存私钥、证书。
- 只把可信域名转发到 `127.0.0.1:${APP_PORT:-8080}`，并传递
  `X-Forwarded-Proto: https`；应用 Nginx 继续以同源方式提供 Vue 和 `/api`。
- 如果 TLS 入口与应用不在同一宿主机，使用受控私网/防火墙替代回环绑定，不得直接发布
  容器的 HTTP 端口。

真实 `DINGTALK_CLIENT_SECRET` 和 `SESSION_SECRET` 由部署平台的 Secret 环境注入或受权限保护的
宿主机环境文件提供，不提交仓库、不进入镜像，也不要把会展开环境值的 Compose 配置输出保存或
发送。`.env.example` 只列空键名。

应用 Nginx 关闭版本标识和含查询参数的访问日志，设置 CSP、`nosniff`、no-referrer 与受限
Permissions Policy；`/api`、认证和下载均为 `no-store`，仅 Vite 哈希资源长期缓存。它不依据
`remote_addr` 或调用方提供的 `X-Forwarded-For` 限流：认证、上传、OCR 和 Excel 的 IP/账号限流
由公司可信 HTTPS 边缘层执行，边缘层必须丢弃并重建外部转发头。上传请求关闭 Nginx request
buffering；无法避免的 client/proxy temp 路径位于 Web 容器受限 tmpfs，不写镜像层或宿主目录。
Web 容器以非 root、只读根文件系统、全部 capability drop、no-new-privileges、内存和 PID 上限运行。
运行 `make nginx-policy-check` 可做仓库内静态策略检查。若本机安装了 Nginx，仍应在实际部署
环境对最终配置执行 `nginx -t`；本检查不冒充完整的 Nginx 解析验证。

Compose 要求显式提供 `APP_ENV`，没有该值会在配置展开阶段失败。开发环境复制
`.env.example` 后得到明确的 `APP_ENV=development`；生产必须显式设置 `APP_ENV=production`，
随后后端启动校验还会拒绝 Mock、非 Secure Cookie、占位凭据或短 Secret，不能把 Compose
开发默认误当成生产配置。

### 最短生产部署路径

目标服务器使用 Linux x86_64/amd64。Apple Silicon Mac 上的 Docker Compose 会通过
`TARGET_PLATFORM=linux/amd64` 将前后端统一构建为目标架构；本机运行时使用模拟架构，适合
构建和联调，不建议承担正式 OCR：

```bash
cp .env.production.example .env
chmod 600 .env
# 填写钉钉应用凭据和 AgentId、管理员 userId，并用 `openssl rand -hex 32` 生成 SESSION_SECRET
make deploy
```

`make deploy` 会先检查 Compose 展开、Nginx 安全策略和本地 OCR 模型，再构建并等待容器
健康检查通过；可用 `make logs` 查看日志。只想预检而不启动时执行 `make deploy-check`。随后验证
`http://127.0.0.1:8080/api/ready`，再由公司 HTTPS 入口反向代理该回环端口。发布前可执行
`make verify`，一次完成后端测试/Lint、前端测试/类型检查/Lint/构建和部署静态检查。

## 运维与隐私

- 后端结构化日志只记录 request ID、方法、URL path、状态、耗时和安全错误类型；不记录查询
  参数、Cookie、票据 OCR 原文、证件号、票号或完整路线。Nginx access log 默认关闭。
- 上传票据超过 30 分钟未使用后进入清理范围；服务启动时先清理，运行中每 10 分钟扫描，
  因此实际删除通常发生在未使用约 30～40 分钟内。退出会立即清理当前
  Session 目录。过期服务端 Session 在启动及请求期按 5 分钟门限清理。
- SQLite 持久卷只承载 `projects`、`settings` 和短期 `sessions`。业务备份仅导出
  `projects` 与 `settings` 两张表；不要恢复 `sessions`。票据和 Excel 从不属于备份范围，
  当前报销内容也不写 SQLite。
- 恢复演练应在隔离环境创建新数据库、执行当前 migration，再导入项目和设置并运行
  `/api/ready`；不要用整卷快照把旧 Session 带回生产。

## 本地 OCR 制品上架步骤

1. 只从 `backend/app/ocr/model_manifest.json` 记录的官方来源取得
   `PP-OCRv6_small_det` 和 `PP-OCRv6_small_rec`；该机器可读文件是来源、归档 SHA、模型文件
   SHA 和目录清单 SHA 的唯一记录。模型二进制不提交仓库。
2. macOS/arm64 可通过上一节的默认命令做 CPU 开发 smoke；正式环境仍在 Linux/amd64、支持
   AVX 的目标机构建默认包含 OCR 的镜像，将两个已核验目录放在
   `OCR_MODEL_HOST_DIR` 下并只读挂载。
3. Compose 默认启用 OCR 并使用容器内标准模型目录，无需另外设置启用开关；不配置任何云 OCR 地址或 Token。运行时
   不下载模型，也不会在失败时回退付费服务。
4. 先运行 `make ocr-models-check` 和 `make ocr-runtime-check`，再确认 `/api/ready` 为 200；最后
   用公司提供的脱敏独立票据样本执行真实 smoke test。模型目录/依赖不完整时部署保持未就绪，
   修复制品后再上线。

## 正式验收仍依赖的外部输入

- 真实钉钉租户的 CorpId、应用凭据、权限、可信 HTTPS 域名、多部门用户和管理员账号联调。
- 公司当前正式空白模板，并在公司 Microsoft Excel 中核对打开、打印和版式；现有脱敏模板
  只证明开发闭环。
- Paddle 模型制品以及公司脱敏 JPG/PNG/独立单页 PDF 样本，用于 Linux 目标机准确率、资源
  上限和断网 smoke test。
- 原生 Linux/amd64 生产机上的断网冷启动、持久卷恢复和公司外层 HTTPS 反代验收。
- 公司确认现行补助制度、12:00 整点归属、项目清单和管理员钉钉 userId。
