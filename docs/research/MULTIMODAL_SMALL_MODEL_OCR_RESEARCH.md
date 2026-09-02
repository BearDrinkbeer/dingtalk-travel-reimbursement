# 多模态小模型用于票据识别的可行性调研

状态：技术选型建议；通用 1.3B/2B 模型本机 PoC 已完成，不改变当前 V1 实现  
日期：2026-09-01  
范围：免费、本地运行；一份文件一张票据；只提取 Excel 所需的类别、发生日期、金额和路线/说明

实际测试数据、速度和错误样例见 [`LOCAL_VLM_RECEIPT_POC.md`](LOCAL_VLM_RECEIPT_POC.md) 和 [`PADDLEOCR_VL_16_LOCAL_POC.md`](PADDLEOCR_VL_16_LOCAL_POC.md)。

## 1. 结论

现阶段**不建议用多模态小模型直接替换现有的 `PDF 文本层 / PP-OCRv6 + 确定性 Parser`**。

更合适的方向是以后增加一个可选的“低置信度兜底”层：当前识别缺金额、缺发生日期、火车票缺路线或票据类型不确定时，才调用本地的文档多模态模型；模型结果仍要经过现有 Schema、日期、金额和业务规则校验，再交给用户修改。

原因很直接：

- 当前需求每张票据只取少数字段，规则可以明确区分“乘车/发生日期”和“开票日期”，财务数字也能做严格校验。
- 多模态模型更擅长理解版面和字段语义，但它是生成式模型，不能仅凭模型自报置信度保证日期、金额绝不臆造。
- 当前 PP-OCRv6 small 检测和识别模型合计约 30 MB；候选多模态模型约 0.9B–2B 参数，部署物、内存、冷启动和 CPU 延迟会显著增加。
- 现有生产设计是 Linux x86_64 CPU、单并发 OCR worker、2 GB OCR worker 内存限制。多模态模型不应直接塞进这个 worker；如果验证有效，应作为独立本地服务并单独设资源上限。

因此，本项目目前不需要安装新模型或修改用户流程。是否引入应由真实脱敏票据的离线对比结果决定，而不是由通用 OCR 榜单决定。

## 2. “更好”具体指什么

多模态小模型可能改善的是：

1. 识别“开票日期”“乘车日期”“行程日期”等相邻字段的语义，而不只是读出文字。
2. 在电子火车票、旅客运输发票等复杂布局中，把起点、终点、金额与正确标签对应起来。
3. 对拍照、倾斜、遮挡、弱光、印章干扰等情况进行更强的整体理解。
4. 新增报销类别时，可以先通过提示词做原型，减少初期规则数量。

但它不天然解决：

- 财务字段零错误要求；
- 公司对“发生日期”“可报类别”的业务定义；
- 输出 JSON 一定合法；
- CPU 推理速度、并发和进程内存限制；
- 用户最终核对责任。

新增类别的长期可维护方式仍应是：稳定类别保留独立 Parser 和测试样例，多模态模型帮助处理难例或提供候选字段，而不是把全部业务规则藏进一段 Prompt。

## 3. 候选模型对比

以下参数、能力和基准均来自模型发布方；其中准确率是发布方在通用文档基准上的结果，**不是本项目票据字段准确率**。

| 候选 | 定位与规模 | 本地/许可证 | 对本项目的判断 |
|---|---|---|---|
| **PaddleOCR-VL 1.6** | 文档解析专用，核心 VLM 约 0.9B；完整管线还包含布局分析。官方称支持 109 种语言以及文字、表格、公式、图表等元素 | Apache-2.0；官方矩阵支持 x64 CPU 和 Apple Silicon，Apple 还支持 MLX-VLM | **第一 PoC 候选**。与现有 Paddle 技术栈最接近、中文文档能力针对性强，但完整管线和依赖远重于当前 OCR |
| **GLM-OCR 0.9B** | OCR 专用 VLM，面向文档解析/信息抽取 | Apache-2.0；官方提供 Apple Silicon MLX 部署，称 8 GB 统一内存可运行；当前 Python 包标记为 Alpha | Mac 验证很方便，但 MLX 服务与 SDK 需要分环境，工程成熟度暂不如 Paddle 路线，列为第二 OCR 专用候选 |
| **MiniCPM-V 4.6 1.3B** | 通用多模态小模型，强调边缘部署 | 官方仓库提供 vLLM、SGLang、llama.cpp、Ollama 等部署说明 | 适合测试“图片直接生成字段 JSON”，但不是票据专用模型，稳定财务字段仍需约束和校验 |
| **Qwen3-VL-2B-Instruct** | 2B 通用视觉语言指令模型 | Apache-2.0；支持 Transformers、vLLM、SGLang，并有量化生态 | 指令/结构化提取能力值得对照，但规模更大，CPU 部署代价更高，不应作为当前默认 OCR |
| **InternVL3.5-1B** | 1.1B 通用多模态模型（0.3B 视觉 + 0.8B 语言） | Apache-2.0，支持 Transformers/LMDeploy 等 | 能作为通用基线，但相较 OCR 专用候选没有明显的集成优势 |
| **HunyuanOCR 1.5** | 1B OCR 专用 VLM，官方明确支持文档解析、文字定位和信息抽取 | Tencent Hunyuan Community License | 能力方向匹配，但许可证不是 Apache-2.0；在已有宽松许可证候选时，不建议 V1 优先引入 |

PaddleOCR-VL 官方特别说明：完整效果来自“布局分析 + VLM 识别”，直接只运行 0.9B VLM 并不等同于完整管线，且可能出现过多幻觉。官方也把直接推理定位为快速验证，提示其速度、内存和稳定性未必满足生产，并建议生产使用独立 VLM 推理服务。[PaddleOCR-VL 官方使用文档](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PaddleOCR-VL.en.md)

相关官方资料：

- [PaddleOCR-VL 1.6 模型卡](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6)
- [GLM-OCR Apple Silicon / MLX 部署](https://github.com/zai-org/GLM-OCR/blob/main/examples/mlx-deploy/README.md)
- [GLM-OCR 包信息与 Alpha 状态](https://github.com/zai-org/GLM-OCR/blob/main/pyproject.toml)
- [MiniCPM-V 官方仓库](https://github.com/OpenBMB/MiniCPM-V)
- [Qwen3-VL-2B-Instruct 模型卡](https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct)
- [InternVL3.5-1B 模型卡](https://huggingface.co/OpenGVLab/InternVL3_5-1B)
- [HunyuanOCR 模型卡](https://huggingface.co/tencent/HunyuanOCR)

## 4. 对当前开发机和生产环境的影响

### 当前 Apple Silicon 开发机

48 GB 统一内存足够做 0.9B–2B 模型的单票据 PoC。优先顺序建议：

1. PaddleOCR-VL 1.6 的 Apple Silicon 官方路径；
2. GLM-OCR 的 MLX 服务；
3. 如需对照通用指令模型，再测试 Qwen3-VL-2B 或 MiniCPM-V 4.6 的量化版本。

这只证明“能运行”，不能证明生产 CPU 的速度和内存可接受。

### Linux x86_64 CPU 生产环境

PaddleOCR-VL 官方文档列出了 x64 CPU 的 PaddlePaddle 和 llama.cpp 路径，但同时建议生产部署使用专用推理服务。当前 Compose 的 2 GB OCR worker 限制不适合作为多模态模型的默认预算。若 PoC 通过，应新增独立进程/容器，例如：

```text
FastAPI
  -> 现有 PP-OCRv6 worker（默认、快速）
  -> 现有 Parser 与字段校验
  -> 仅低置信度时调用本机 VLM service（慢、单并发）
  -> 同一 ParsedExpense 校验与人工编辑界面
```

这不是微服务化业务系统；只是把重量级模型运行时与 Web/OCR 进程隔离，避免一次模型 OOM 拖垮报销接口。

## 5. 推荐的混合判定规则

只有出现以下任一情况才进入多模态兜底：

- `amount` 或 `date` 为空；
- 火车票缺少起终点；
- 同时存在开票日期和乘车/发生日期，Parser 无法可靠选择；
- 分类为 `other`，但文本存在已知票据特征；
- OCR 平均置信度过低或关键字段来自低置信度文本；
- 用户主动点击“增强识别”（只有真实测试证明有价值时才需要这个入口）。

模型建议返回严格结构，但返回值仍视为不可信输入：

```json
{
  "receipt_type": "train",
  "event_date": "2026-07-07",
  "invoice_date": "2026-07-08",
  "amount": "473.50",
  "from": "合肥南站",
  "to": "北京南站",
  "evidence": {
    "event_date": "乘车日期附近的原文",
    "amount": "金额附近的原文"
  }
}
```

服务端必须继续执行：Pydantic Schema 校验、`Decimal` 金额转换、合法日期校验、出差日期范围提示、类别白名单和关键字段证据检查。模型不得直接生成 Excel，也不得覆盖 Session 中的姓名和部门。

## 6. 是否值得引入的验证方案

先建立离线 shadow benchmark，不接用户主流程，也不上传第三方。建议收集 50–100 份脱敏、独立单票据样本，覆盖：

- 铁路电子客票和旅客运输电子发票；
- 出租车/网约车发票；
- 普通增值税发票；
- 清晰 PDF、手机拍照、倾斜/弱光/印章干扰；
- 同时包含开票日期和实际发生日期的难例。

人工建立 `category / event_date / amount / from / to / description` 金标准，然后比较：

1. 当前 PP-OCRv6 + Parser；
2. PaddleOCR-VL 1.6；
3. 当前方案失败时才调用 PaddleOCR-VL 的混合方案；
4. 可选：一个通用小模型直接输出受约束 JSON。

必须记录：

- 各字段精确匹配率和整条记录全对率；
- **高置信度但错误**的日期/金额次数；
- 需要人工修改的票据比例；
- JSON/Schema 合法率；
- 冷启动、P50/P95 单票延迟、峰值 RSS、模型磁盘占用；
- Mac 与目标 Linux CPU 分别测试的结果。

建议的项目准入线（属于工程建议，不是模型官方指标）：

- 在当前低置信度/失败子集中，人工修改率至少下降 30%；
- 不增加“看起来成功但金额或发生日期错误”的次数；
- 解析失败必须安全回退到当前结果或人工填写；
- 目标生产机器单票 P95 不超过 30 秒，否则需要 GPU 或放弃在线兜底；
- 使用独立资源预算，不能突破现有 Web 服务稳定性。

若达不到以上条件，继续优化 Parser 和样本测试通常更简单、更稳。

## 7. 对当前代码结构的建议

现有结构已经为后续扩展提供了合适边界：

- `PaddleLocalOcrEngine` 只负责把文件转成带置信度的文本行；
- `ReceiptParserRegistry` 负责类别匹配和字段解析；
- `OcrService` 负责 PDF 文本优先、OCR 回退和统一错误处理；
- `ParsedExpense` 是前端和 Excel 生成前的稳定契约。

以后不要让 VLM 侵入每个 API 或 Parser。可新增一个窄接口，例如 `DocumentUnderstandingEngine.extract(path, context) -> ParsedExpenseCandidate`，由 `OcrService` 根据兜底条件调用；最终仍转换为 `ParsedExpense`。这样新增报销类别时，可独立增加 Parser、测试和可选 VLM 提示，不影响上传、临时文件、Excel 或钉钉鉴权。

## 8. 最终建议

1. **V1 保持现状**：继续使用免费本地 PP-OCRv6 + Parser + 人工可编辑。
2. **不把已下载的 PoC 模型打包进默认部署**：PaddleOCR-VL 1.6 虽然在 9 张有效样本中取得 9/9 总金额，但平均约 45 秒/张、峰值约 11.4 GiB；Qwen3-VL 2B 图片直出的类别和金额均为 7/9，也不足以独立承担财务字段。
3. **积累带金标准的失败样本**：扩大到至少 50 张后再决定是否把 PaddleOCR-VL 1.6 做成独立的低置信度兜底；GLM-OCR 只在仍需速度/资源对照时再测。
4. **通过后也只做兜底**：独立本地模型服务、单并发、字段证据与确定性校验，不调用付费或云端 API。
5. **未来新增类别仍以 Parser 为正式扩展点**：多模态模型可以加速发现规则和处理长尾，但不替代公司报销规则本身。
