# V1 免费、本地 OCR 与 PDF 处理调研

状态：技术决策记录，核心结论已同步到主实施方案  
日期：2026-08-31  
范围：OCR、PDF 文本提取/渲染、核心开源组件与钉钉 OpenAPI 调用成本

## 1. 结论先行

V1 的票据识别、PDF 处理和 Excel 生成可以做到**零 OCR/PDF/Excel 外部 API 调用费**：所有票据先在本机用 `pypdf` 提取 PDF 文本，字段不足时再调用同一后端进程内的 PaddleOCR 3.x 本地推理。不得使用托管 OCR、`PaddleOCRClient` 或任何需要把票据上传到第三方服务的实现。

建议对当前方案做四处最小调整：

1. 保留 PaddleOCR 作为 V1 唯一 OCR 引擎，不同时引入 RapidOCR 或 Tesseract。
2. 删除应用对 PyMuPDF 的显式依赖。PaddleOCR 3.x 通用 OCR 管线的 `predict()` 支持本地 PDF 路径；V1 已限定单页 PDF，因此文本层不足时可把该 PDF 直接交给本地 `predict()`。当前 PaddleX 的 PDF 读取后端使用 `pypdfium2`，无需另写一套 PDF 渲染代码。
3. 将 PaddleOCR、PaddlePaddle、模型名称和模型文件全部固定版本；模型在镜像构建阶段下载并以本地目录显式传入，部署运行期禁止联网下载。
4. 钉钉免登仍然不可避免地调用钉钉 OpenAPI。它不是 OCR 成本，但受组织套餐、月调用量和 QPS 配额约束；不能承诺“钉钉调用永远免费”，只能通过 Session、Token 和用户/部门缓存把调用量降到最低。

“零 API 成本”不等于“零总成本”：仍可能有现有服务器、域名、网络、运维和公司钉钉套餐成本。如果复用公司服务器与现有钉钉权益，应用自身无需新增按次 OCR/模型费用。

## 2. 推荐的 V1 本地处理链路

```text
单页 PDF
  -> pypdf 提取原生文本
  -> 文本质量足够：直接 Parser
  -> 文本质量不足：本地 PaddleOCR.predict(pdf_path)
  -> Parser
  -> 用户确认/修正

JPG / JPEG / PNG
  -> 本地 PaddleOCR.predict(image_path)
  -> Parser
  -> 用户确认/修正
```

这条链路只有钉钉身份认证需要访问外部服务；票据正文、OCR 结果和生成的 Excel 不离开自有服务器。

## 3. PaddleOCR 3.x

### 3.1 是否免费、开源、可本地运行

- PaddleOCR 仓库采用 Apache-2.0；PaddlePaddle 框架也采用 Apache-2.0。该许可证本身不收取按次调用费。[PaddleOCR 仓库与许可证](https://github.com/PaddlePaddle/PaddleOCR)、[PaddlePaddle LICENSE](https://github.com/PaddlePaddle/Paddle/blob/develop/LICENSE)
- PaddleOCR 仓库维护者明确回复 PaddleOCR 项目可商用；但公司仍应在发布时保留许可证与 NOTICE，并对最终锁定的模型文件做一次 SBOM/许可证归档。[PaddleOCR 模型商用讨论](https://github.com/PaddlePaddle/PaddleOCR/discussions/15986)
- 官方通用 OCR 文档展示的是本地 `PaddleOCR(...).predict()`；输入支持本地图片或 PDF 文件，设备可指定为 CPU。本地推理不需要购买或调用 OCR 云 API。[PaddleOCR 3.x 通用 OCR 管线](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/OCR.html)

因此：**PaddleOCR 3.x 本地推理可以作为 V1 的免费默认实现**。代码中不应出现云 OCR Endpoint、API Key、`PaddleOCRClient` 或将票据 Base64 发往第三方的逻辑。

### 3.1.1 当前 Mac 开发机可行性

PaddlePaddle 官方 macOS 安装页已明确支持 Apple Silicon `arm64`，macOS 只提供 CPU 路径；
PaddlePaddle 3.3.1 在官方包索引/PyPI 提供 Python 3.9–3.13 的
`macosx_11_0_arm64` wheels，PaddleOCR 3.7.0 本身是平台无关 wheel。因此当前
`macOS arm64 + Python 3.13` 可以直接验证标准 Paddle CPU 推理，不需要用 amd64 容器模拟，
也不需要增加第二套 OCR 引擎。[PaddlePaddle macOS PIP 安装](https://www.paddlepaddle.org.cn/documentation/docs/zh/install/pip/macos-pip.html)、[PaddlePaddle 3.3.1 wheels](https://pypi.org/project/paddlepaddle/)、[PaddleOCR 3.7.0](https://pypi.org/project/paddleocr/)

此结论只用于开发 smoke：macOS 不启用 GPU/HPI，且官方系统版本枚举尚未覆盖当前 macOS 26，
所以仍需实际安装和脱敏票据验证。生产环境继续使用 Linux/amd64 及现有进程资源限额；开发
可行性不能替代生产验收。

### 3.2 模型自动下载与真正离线部署

官方模型默认可能从 Hugging Face 下载；网络受限时可用 `PADDLE_PDX_MODEL_SOURCE="BOS"` 切换下载源。官方文档也支持用 `text_detection_model_dir`、`text_recognition_model_dir` 或管线 YAML 指定本地权重。完全使用本地模型时，PaddleX 官方 FAQ 推荐设置 `PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=1` 关闭启动时的模型托管平台联网检查。[PaddleOCR 模型下载源与本地目录](https://paddlepaddle.github.io/PaddleOCR/main/en/version3.x/module_usage/text_recognition.html)、[通用 OCR 本地模型路径](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/OCR.html#421-specify-the-local-model-path-through-parameters)、[PaddleX 模型源检查 FAQ](https://paddlepaddle.github.io/PaddleX/3.7/FAQ.html)

生产部署不应依赖“首次请求自动下载”，而应：

1. 在构建环境联网下载选定模型；固定 `paddleocr`、推理引擎及模型版本和 SHA-256。
2. 把检测、识别所需模型放入镜像只读目录，例如 `/opt/expense/models/`。
3. 初始化时显式指定模型名称与本地目录，并指定 `device="cpu"`。
4. 若样本均为方向正确的电子票据，先关闭文档方向分类、文档矫正和文本行方向分类，减少三个可选模块、下载项与 CPU 开销；只有真实样本证明需要时再打开。
5. CI/发布验收时用禁网容器执行启动和一份 OCR smoke test；任何运行期下载都视为失败。

PP-OCRv6 small 的官方推理模型约为检测 9.6 MB、识别 20.4 MB。项目只预置以下两个官方推理
模型，不使用训练模型：[PP-OCRv6 模型表](https://paddlepaddle.github.io/PaddleX/latest/en/pipeline_usage/tutorials/ocr_pipelines/OCR.html)

- [PP-OCRv6_small_det_infer.tar](https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/PP-OCRv6_small_det_infer.tar)
- [PP-OCRv6_small_rec_infer.tar](https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/PP-OCRv6_small_rec_infer.tar)

示意配置（确切模型名以实施时锁定版本的官方文档和样本基准为准）：

```python
ocr = PaddleOCR(
    lang="ch",
    device="cpu",
    text_detection_model_name="PP-OCRv6_small_det",
    text_detection_model_dir="/opt/expense/models/PP-OCRv6_small_det",
    text_recognition_model_name="PP-OCRv6_small_rec",
    text_recognition_model_dir="/opt/expense/models/PP-OCRv6_small_rec",
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    use_textline_orientation=False,
)
results = ocr.predict(local_file_path)
```

官方 3.x 文档会随小版本改变默认模型（当前文档已出现 PP-OCRv6），所以依赖不能只写宽泛的 `paddleocr>=3`；必须固定经过票据样本验证的精确版本。

### 3.3 单页 PDF 是否仍需 PyMuPDF

不需要在本应用中显式使用 PyMuPDF：

- PaddleOCR 通用 OCR 官方文档把本地 PDF 文件列为 `predict()` 支持的输入。[PaddleOCR 输入类型](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/OCR.html)
- PaddleX 官方仓库问题的运行栈显示当前 `PDFReaderBackend` 依赖 `pypdfium2`；维护者也说明底层使用 `pypdfium2` 解析 PDF。[PaddleX PDFReaderBackend 依赖记录](https://github.com/PaddlePaddle/PaddleX/issues/4088)、[PaddleX 维护者对 PDF 后端的说明](https://github.com/PaddlePaddle/PaddleX/discussions/3524)
- V1 已拒绝多页 PDF，不需要另外实现页选择、拆分或批量渲染。

实施时仍需检查锁定后的 `pip` 依赖树，确认 PDF 路径安装了 `pypdfium2`，并用真实单页 PDF 做禁网测试。这里的“移除 PyMuPDF”是指不直接依赖 `pymupdf`/`fitz`；`pypdfium2` 是名称相似但许可证和底层完全不同的组件。

## 4. RapidOCR 与 Tesseract：为什么不进 V1

### 4.1 RapidOCR

RapidOCR 是可离线运行的开源方案，工程代码为 Apache-2.0，默认 ONNX Runtime CPU 引擎；官方安装文档说明当前 wheel 约 27.2 MB，已包含检测、方向分类和识别三个 small 模型。ONNX Runtime 为 MIT 许可证。[RapidOCR 安装与内置模型](https://github.com/RapidAI/RapidOCRDocs/blob/main/docs/install_usage/rapidocr/install.md)、[RapidOCR 许可证说明](https://github.com/RapidAI/RapidOCR/blob/main/README.md#-license)、[ONNX Runtime LICENSE](https://github.com/microsoft/onnxruntime/blob/main/LICENSE)

它值得在 V1 稳定后，用同一批脱敏票据做一次速度、内存与字段准确率基准。但 V1 不应同时维护 PaddleOCR 和 RapidOCR 两套依赖、结果适配和故障路径。现阶段把 RapidOCR 记录为未来替代候选，不写入运行时依赖。

### 4.2 Tesseract

Tesseract 采用 Apache-2.0，官方提供简体中文 `chi_sim` 训练数据，可以完全本地运行。[Tesseract LICENSE](https://github.com/tesseract-ocr/tesseract/blob/main/LICENSE)、[官方语言数据列表](https://tesseract-ocr.github.io/tessdoc/Data-Files-in-different-versions.html)

但“支持中文”不等于已证明适合本项目票据。Tesseract 官方质量指南明确提示表格/结构化数据通常需要额外分割或版面分析；票据金额、日期和路线仍需本项目 Parser。[Tesseract 输出质量指南](https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html)

因此 V1 不引入 Tesseract。若未来 PaddleOCR 在某类清晰印刷票据上失败，可把它作为离线基准，而不是无证据地叠加第二套生产引擎。

## 5. PDF 库选择与许可证

| 组件 | 能力 | 许可证/成本判断 | V1 决策 |
|---|---|---|---|
| `pypdf` | 提取数字 PDF 文本、读取页数；不是 OCR，不能从纯图片中识字 | BSD-3-Clause，无按次 API 费。[官方许可证 FAQ](https://github.com/py-pdf/pypdf/blob/main/docs/meta/faq.md)、[OCR 与文本提取说明](https://pypdf.readthedocs.io/en/5.7.0/user/extract-text.html) | 保留，作为 PDF 第一层快速路径 |
| `pypdfium2` | PDFium Python 绑定，可提取文本与渲染页面；PaddleX 当前 PDF 后端使用它 | 项目为 Apache-2.0 OR BSD-3-Clause；PDFium 为 BSD 风格，二进制还包含多项第三方许可证，分发时需保留 wheel 附带 notices。[官方 Licensing](https://github.com/pypdfium2-team/pypdfium2#licensing)、[Python API](https://pypdfium2.readthedocs.io/en/stable/python_api.html) | 允许作为 PaddleOCR/PaddleX 间接依赖；应用不重复封装渲染器 |
| `PyMuPDF` | 高性能 PDF 提取与渲染 | 官方为 AGPL 与商业许可双轨；不能把“免费安装”直接等同于适合闭源内部应用。[官方许可说明](https://github.com/pymupdf/PyMuPDF/blob/main/docs/about.rst#license-and-copyright) | V1 移除，避免不必要的许可证审查或商业许可成本 |

上述不是法律意见。核心工程判断是：当前需求已有许可证更宽松且足够的路径，没有必要为单页 PDF 引入 PyMuPDF。

## 6. 其余技术栈是否需要付费 API

- FastAPI 为 MIT；它是本地 Web 框架，不调用收费 API。[FastAPI LICENSE](https://github.com/fastapi/fastapi/blob/master/LICENSE)
- SQLite 代码处于 public domain，可免费用于商业或内部用途；它是进程内数据库，不调用外部服务。[SQLite Copyright](https://www.sqlite.org/copyright.html)
- openpyxl 为 MIT/Expat，本地读写 `.xlsx`，不需要 Microsoft 365 或 Excel API。[openpyxl 官方文档](https://openpyxl.readthedocs.io/en/stable/index.html)
- Vue 3 为 MIT，本地构建和浏览器运行，不需要付费 API。[Vue 官方仓库](https://github.com/vuejs/core#license)

Vite、Pinia、Axios、Element Plus、Uvicorn、SQLAlchemy 等也都是本地开源库；实施时应通过锁文件与 SBOM 保存**实际版本**的许可证清单。开源许可证通常仍要求保留版权/许可证 notice，“免费”不等于没有合规义务。

一个容易忽略的成本点是 Docker Desktop：官方条款只对个人、教育、非商业开源项目和同时满足员工数/营收门槛的小企业免费；较大企业的专业使用需要订阅。Linux 服务器上的 Docker Engine/Moby 是开源方案。生产和 CI 优先用 Linux Docker Engine + Compose plugin；公司开发机是否能免费使用 Docker Desktop需由现有授权判断。[Docker Desktop 许可说明](https://docs.docker.com/subscription/desktop-license/)、[Docker Engine 安装与许可](https://docs.docker.com/engine/install/)

## 7. 钉钉 OpenAPI 成本边界

钉钉是 V1 唯一不可完全离线的业务依赖：免登需要钉钉客户端授权码和服务端 OpenAPI 换取身份、用户与部门信息。钉钉官方开发须知要求接入免登，并要求开发者了解调用频率限制。[钉钉开放平台调用频率限制](https://open.dingtalk.com/document/app/invocation-frequency-limit)、[钉钉服务协议中的开放平台服务定义](https://terms.alicdn.com/legal-agreement/terms/suit_bu1_dingtalk/suit_bu1_dingtalk202010200940_84493.html)

从公开官方资料不能稳妥推出“所有组织、所有调用量均永久免费”；钉钉服务协议也存在付费增值/专属能力。因此本调研不猜公司合同或当前套餐，实施前应由管理员在公司钉钉开发者后台确认：

- 当前组织每月 OpenAPI 调用量与 QPS 权益；
- 免登所需的 Token、用户和部门接口是否都在当前权益内；
- 超量后的限流或增购规则。

应用侧降低调用量的办法：

1. Access Token 缓存到接近过期，不为每次登录重复获取。
2. 成功免登后建立服务端 Session；`/api/me` 只读 Session，不重复调用钉钉。
3. 用户与部门数据按 Session 或短 TTL 缓存；仅在新登录或明确过期时刷新。
4. 不增加事件订阅、消息、审批等 V1 外调用。
5. 记录接口名、计数、429/限流错误和耗时，但不记录票据内容或 Token。

对一个小型内部 V1，这能把调用量压到“每次新登录少量调用”，但最终是否产生钉钉增量费用必须以公司后台权益为准。

## 8. 已同步到主实施方案的调整

以下约束已同步到 `V1_IMPLEMENTATION_PLAN.md`，实施时应继续保持：

1. 技术栈：把“`PyMuPDF` 仅用于 OCR 回退时渲染页面”改为“`pypdf` 提取文本；文本不足时将单页 PDF 直接交给本地 PaddleOCR 3.x `predict()`；PDF 底层使用 PaddleX 的 `pypdfium2`，不直接依赖 PyMuPDF”。
2. OCR 部署：新增“禁止托管 OCR/PaddleOCRClient；模型在构建期预下载，显式本地路径，禁网 smoke test”。
3. 依赖：要求精确固定 PaddleOCR、推理引擎、模型名称/哈希，而不是宽泛的 3.x 范围。
4. OCR 可选模块：电子票据默认关闭方向分类、文档矫正、文本行方向分类；真实样本证明需要时再打开。
5. 备选方案：RapidOCR 仅列入后续基准候选，不作为 V1 第二套引擎；Tesseract 不进入 V1。
6. 成本说明：加入“无 OCR API 费，但服务器/域名/运维、钉钉套餐配额、企业 Docker Desktop 授权不在零成本承诺内”。
7. 验收：增加“生产镜像断网后可启动并完成一份图片和一份单页 PDF 的 OCR；运行日志没有模型下载或第三方 OCR 请求”。

## 9. 置信度与缺口

### 高置信度

- PaddleOCR/PaddlePaddle 可在本地 CPU 推理，项目采用 Apache-2.0，不需要按次 OCR API。
- PaddleOCR 3.x `predict()` 支持本地 PDF；当前 PaddleX PDF 后端使用 `pypdfium2`。
- PyMuPDF 为 AGPL/商业双轨，当前需求可以不用它。
- `pypdf`、`pypdfium2`、FastAPI、SQLite、openpyxl、Vue 等无需付费 API。
- RapidOCR 和 Tesseract 均可本地运行，但 V1 没必要维护多套引擎。

### 中等置信度

- 关闭三个可选方向/矫正模块能满足全部真实票据：现有电子 PDF 很可能成立，但手机拍照的旋转、透视和折痕样本尚未验证。
- `PP-OCRv6_small` 是最佳 V1 模型组合：官方提供体积/性能信息，但必须以项目真实票据的字段准确率与 CPU 延迟决定，不能只看通用基准。

### 待实施前补齐

1. 用脱敏后的铁路电子客票、旅客运输电子发票、手机照片建立小型离线基准；评价字段正确率，不只评价 OCR 字符率。
2. 锁定一个可重复安装的 PaddleOCR/PaddlePaddle/pypdfium2 版本组合，并导出完整 SBOM、LICENSE 和 NOTICE。
3. 在目标 Linux CPU/内存上测冷启动、单票延迟、30 文件串行耗时和峰值内存。
4. 在公司钉钉开发者后台确认实际 OpenAPI 配额与当前套餐；公开资料不足以替代企业后台数据。
5. 确认目标 PDF 通过 PaddleOCR 直接输入时的 DPI、字体渲染和中文结果；若遇到 PDFium 兼容问题，再针对失败样本评估应用层显式使用 `pypdfium2` 渲染，而不是回退到 PyMuPDF。

## 10. Sources consulted

仅使用项目官方文档、官方仓库、许可证和钉钉官方条款：

- [PaddleOCR official repository](https://github.com/PaddlePaddle/PaddleOCR)
- [PaddleOCR 3.x General OCR pipeline](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/OCR.html)
- [PaddleOCR text recognition and model source](https://paddlepaddle.github.io/PaddleOCR/main/en/version3.x/module_usage/text_recognition.html)
- [PaddlePaddle LICENSE](https://github.com/PaddlePaddle/Paddle/blob/develop/LICENSE)
- [PaddleX PDF reader dependency issue](https://github.com/PaddlePaddle/PaddleX/issues/4088)
- [PaddleX PDF backend maintainer discussion](https://github.com/PaddlePaddle/PaddleX/discussions/3524)
- [RapidOCR official repository](https://github.com/RapidAI/RapidOCR)
- [RapidOCR official installation guide](https://github.com/RapidAI/RapidOCRDocs/blob/main/docs/install_usage/rapidocr/install.md)
- [ONNX Runtime LICENSE](https://github.com/microsoft/onnxruntime/blob/main/LICENSE)
- [Tesseract official repository and LICENSE](https://github.com/tesseract-ocr/tesseract)
- [Tesseract official quality guide](https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html)
- [pypdf official text extraction guide](https://pypdf.readthedocs.io/en/5.7.0/user/extract-text.html)
- [pypdf official license FAQ](https://github.com/py-pdf/pypdf/blob/main/docs/meta/faq.md)
- [pypdfium2 official repository and licensing](https://github.com/pypdfium2-team/pypdfium2#licensing)
- [PyMuPDF official license statement](https://github.com/pymupdf/PyMuPDF/blob/main/docs/about.rst#license-and-copyright)
- [FastAPI LICENSE](https://github.com/fastapi/fastapi/blob/master/LICENSE)
- [SQLite copyright/public-domain statement](https://www.sqlite.org/copyright.html)
- [openpyxl official documentation](https://openpyxl.readthedocs.io/en/stable/index.html)
- [Vue official repository](https://github.com/vuejs/core)
- [Docker Desktop license](https://docs.docker.com/subscription/desktop-license/)
- [Docker Engine install/license](https://docs.docker.com/engine/install/)
- [DingTalk Open Platform invocation frequency limit](https://open.dingtalk.com/document/app/invocation-frequency-limit)
- [DingTalk service agreement](https://terms.alicdn.com/legal-agreement/terms/suit_bu1_dingtalk/suit_bu1_dingtalk202010200940_84493.html)
