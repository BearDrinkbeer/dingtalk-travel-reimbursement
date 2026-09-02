# 本地 OCR 模型候选调研

调研日期：2026-09-02

调研范围：只比较可本地、离线运行的 OCR / 文档视觉模型，重点判断它们是否比本项目当前的 `PDF 文本层 → PP-OCRv6 small → 确定性 Parser → 人工可编辑` 流程更适合中文电子发票、打车客运发票和铁路电子客票。以下模型能力和公开指标来自官方文档、官方仓库、官方模型卡或模型发布者的一手资料；项目实测数据单独标明。

## 结论

> 后续验证（2026-09-02）：针对两张“出发地/到达地被检测为一个跨列长框”的真实客运发票，已完成 `small_det + small_rec`、`small_det + medium_rec`、`medium_det + small_rec`、`medium_det + medium_rec` 四组强制图片 OCR。四组均未正确恢复两列路线，说明 medium 不能解决这个特定的表格分框问题；使用 small 模型按已识别表头分别裁切两个单元格后二次 OCR，两张均精确通过。因此项目已保留 small 为默认，并实现只在跨列粘连时触发的表头坐标裁切修复。下文“优先 A/B medium”的建议仍适用于评估通用漏字、漏金额，不再适用于解决这类起终点粘连。

1. **当前模型并不落后。** 项目已锁定 PaddleOCR 3.7.0 的 `PP-OCRv6_small_det + PP-OCRv6_small_rec`。PP-OCRv6 是 PaddleOCR 当前最新通用 OCR 系列，small 是兼顾精度和部署成本的中档方案，并不是需要先淘汰的旧版 OCR。[PaddleOCR 通用 OCR 文档](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/OCR.en.md)
2. **最值得先做的升级实验是 PP-OCRv6 medium。** 官方把 medium 定位为最高精度档：检测 Hmean 为 86.2、识别平均准确率为 83.2；small 对应 84.1 和 81.3。模型文件也由 small 的约 `9.6 + 20.4 MB` 增至 medium 的约 `59.4 + 73.3 MB`。官方端到端测试（含图片 I/O、前后处理和推理）中，Apple M4 使用 PaddlePaddle 时 medium/small 为 `8.82/3.07 秒/图`，使用 ONNX Runtime 为 `5.55/1.29 秒/图`；Intel Xeon 8350C 使用 PaddlePaddle 时为 `2.05/0.79 秒/图`。这些速度和准确率仍不是本项目票据字段指标，必须在真实票据上 A/B，不能直接断言一定更好。[PP-OCRv6 官方算法与端到端速度](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/algorithm/PP-OCRv6/PP-OCRv6.en.md)
3. **PaddleOCR-VL 1.6 是更强的文档解析器，但不适合默认逐票调用。** 官方 0.9B 模型在 OmniDocBench v1.6 达到 96.33%，支持文本、表格、公式、图表、印章和复杂版面解析；项目本机 PoC 也将 9 张非空样本的总金额全部读对。但本机平均 45.14 秒/张、峰值约 11.4 GiB，远超当前 OCR worker 的资源预算。因此它适合作为未来“现有组合缺关键字段或置信度低”时的独立、单并发兜底，不适合作为默认 OCR。[官方 1.6 技术说明](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/algorithm/PaddleOCR-VL/PaddleOCR-VL-1.6.md)；[项目本机 PoC](PADDLEOCR_VL_16_LOCAL_POC.md)
4. **不存在一个官方的 `PaddleOCR-VL-Receipt`。** 这个名称对应社区作者基于旧版官方 `PaddleOCR-VL-0.9B` 做的收据字段微调，不在 PaddleOCR 官方支持模型枚举和 v1/v1.5/v1.6 管线中。其配套脚本还明确删掉了预处理、版面检测、坐标和置信度输出，不能当作 Paddle 官方票据解决方案。[PaddleOCR 官方模型枚举](https://github.com/PaddlePaddle/PaddleOCR/blob/main/paddleocr/_api_client/models.py)；[发布者推理仓库](https://github.com/megemini/PaddleOCR-VL-REC)
5. **RapidOCR 是部署引擎替代，不是新的更准模型；MinerU、Surya、Qwen3-VL、GLM-OCR 和 HunyuanOCR-1.5 是更重的文档/生成式 OCR-VLM；docTR 没有官方中文识别词表。** 其中 GLM-OCR 和 HunyuanOCR-1.5 是值得关注的较新专用 OCR-VLM，但它们同样没有官方中文客运票据字段基准证明优于现有组合，不能仅凭通用文档榜单直接加入生产依赖。

因此推荐顺序是：

```text
现有 PDF 文本层 + PP-OCRv6 small + Parser（继续作为默认）
  ↓ 同一批真实样本 A/B
PP-OCRv6 medium（唯一优先测试的无架构变化候选）
  ↓ 只有失败样本继续积累且收益足够时
PaddleOCR-VL 1.6 独立低置信度兜底
```

## 为什么“更强 OCR”仍不能保证图片票据全正确

通用 OCR 和文档 VLM的公开指标主要衡量文字检测、文字识别、阅读顺序、表格或整页 Markdown 还原。本项目真正的财务指标是：

- 乘车/消费发生日期，而不是开票日期；
- 价税合计，而不是未税金额或税额；
- 起点与终点的正确拼接；
- 公司报销类别的正确映射。

这些包含业务语义和同一票面多候选消歧。PP-OCRv6 medium 可能减少漏字，PaddleOCR-VL 1.6 可能改善跨行和复杂版面，但都不等价于可靠选择上述业务字段。因此，模型升级不能替代当前 Parser、字段交叉校验、低置信度告警和前端人工编辑。

本轮项目 15 张真实票据回归也说明了这个边界：OCR 单路分类和日期均为 15/15，金额为 12/15、说明为 13/15、整条全对为 10/15；PDF 文本层与 OCR 互补后整条为 15/15。因此下一轮模型 A/B 的重点不是推翻组合流程，而是看 medium 能否补上“只有图片时”的 3 个金额和 2 个说明错误。

## 候选对比

| 候选 | 文字/版面/表格能力 | 规模与本地运行 | 官方速度或公开实测 | 接入成本 | 对本项目判断 |
|---|---|---|---|---|---|
| **PP-OCRv6 small（当前）** | 文本检测和识别；不直接输出业务字段 | det 9.6 MB + rec 20.4 MB；当前 Mac CPU 和 Linux CPU 路径已跑通 | 项目已有真实样本基线 | 已接入 | **保留默认** |
| **PP-OCRv6 medium** | 与 small 同接口，官方 v6 最高精度档 | det 59.4 MB + rec 73.3 MB；支持 Paddle、ONNX Runtime/OpenVINO 等本地后端 | Apple M4：Paddle 8.82 秒、ONNX 5.55 秒；Xeon 8350C：Paddle 2.05 秒、OpenVINO 1.40 秒（官方端到端测试） | 很低：替换模型目录/名称并重跑基准 | **优先 A/B** |
| **PP-OCRv5 server/mobile** | 通用文字检测/识别 | server_rec 81 MB；mobile_rec 16 MB | 官方识别模型推理时间分别列为 CPU 31.21 ms、21.20/5.32 ms，但不含检测和前后处理 | 低 | 已被 v6 系列覆盖，无回退价值 |
| **PaddleOCR-VL 1.6** | 整页文档解析；文本、版面、表格、公式、图表、印章；Markdown/JSON | 0.9B，官方权重约 1.93 GB；支持 x64 CPU、Apple Silicon、MLX-VLM、GGUF/llama.cpp 等路径 | 项目 M5 Pro：平均 45.14 秒/张，峰值约 11.4 GiB | 高：完整管线、独立进程和结果适配 | **只做低置信度兜底** |
| **PaddleOCR-VL v1 / 0.9B** | 旧一代完整管线的 VLM 组件；支持文本、表格、公式、图表 | 0.9B，权重约 1.92 GB | 没有本项目必要的比较优势 | 中高 | 同规模优先测 1.6，不再投入旧版 |
| **PaddleOCR-VL-Receipt** | 社区字段微调可尝试 JSON；简化脚本没有版面、坐标和置信度 | 基于旧 0.9B；发布者脚本接受 CPU 参数，但依赖示例含 Linux/CUDA 专用 wheel | 发布者没有中文发票/铁路票字段级官方基准 | 高且域不匹配 | **不采用** |
| **RapidOCR + ONNX/OpenVINO** | 可运行 PP-OCRv4/v5/v6；结果仍由所选 Paddle OCR 权重决定 | wheel 约 27.2 MB；ONNX Runtime CPU 可离线；v6 支持 tiny/small/medium | 未发布独立于底层 Paddle 权重的中文票据增益 | 中：需重做结果适配和回归 | 只在部署/性能有问题时对照，不是精度升级 |
| **Surya OCR 2** | 单一 650M VLM 做整页 OCR、版面、阅读顺序和表格 | NVIDIA 用 vLLM；CPU/Apple Silicon 用 llama.cpp | 官方称 RTX 5090 约 5 页/秒、olmOCR-bench 83.3、91 语言内部集平均 87.2%；未给 Mac 中文票据数字 | 中高，新运行时和输出 Schema | 可做研究对照，暂无替换依据 |
| **MinerU** | PDF/图片/Office 全文档解析，包含 OCR、版面、公式、表格 | macOS 14+；pipeline 可纯 CPU；完整本地方案官方要求至少 16 GB RAM、20 GB 磁盘，推荐 32 GB | 官方 OmniDocBench v1.6：pipeline 86.47，VLM 约 95.26–95.39 | 很高，远超单张票据需求 | **过度设计** |
| **Qwen3-VL-2B-Instruct** | 32 种语言 OCR、长文档结构理解、视觉问答，可提示输出字段 | 2B BF16；Transformers/vLLM/SGLang/GGUF 可本地运行 | 官方没有适用于当前 Mac 的中文客运票据字段速度/准确率 | 高，生成式输出还需 Schema 校验 | 不作为主 OCR；项目已有 PoC 也出现财务字段误选 |
| **GLM-OCR** | 专用文档 OCR-VLM；完整管线含 PP-DocLayout-V3，可输出 Markdown/JSON，官方宣称覆盖信息抽取 | 0.9B BF16；支持 vLLM/SGLang/Ollama，官方另有 Apple Silicon MLX 指南 | 官方 OmniDocBench v1.5 为 94.62；没有当前票据字段级或本机端到端数据 | 高：独立模型服务、版面管线和生成结果校验 | 可列入第二阶段研究 A/B，不能直接替换 |
| **HunyuanOCR-1.5** | 专用端到端 OCR-VLM；覆盖文档解析、文本定位、信息抽取和翻译 | 官方提供 Transformers/vLLM、DFlash，以及 GGUF + llama.cpp 的 CPU/笔记本路径 | 官方提供通用 OCR/文档 benchmark 和 GPU 加速数据；没有本项目 Mac 中文票据字段数据 | 高：新模型服务、提示/Schema 和生成结果校验 | 可列入第二阶段研究 A/B，优先级低于 medium |
| **docTR** | 文本检测/识别、行/块层级和 JSON；支持 PDF 图片 | PyTorch，本地 CPU/GPU | 官方识别 benchmark 主要是 Latin/FUNSD/CORD；官方 vocab 列表没有中文字符集 | 中 | **不适合中文票据** |

## 逐项证据与取舍

### PP-OCRv6 medium：最小改动的第一候选

PaddleOCR 3.7 的默认通用 OCR 已升级为 PP-OCRv6 medium，并提供 tiny、small、medium 三档。官方称 medium 相比 PP-OCRv5_server 检测提升 4.6%、识别提升 5.1%，单模型覆盖中文、英文、日文和 46 种拉丁文字；同时官方特别注明 v6 与 v5 的绝对指标来自不同评测集，不能直接横向比较全部表格数字。[PaddleOCR README](https://github.com/PaddlePaddle/PaddleOCR)；[通用 OCR 文档](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/OCR.en.md)

对本项目的价值不在于新增分类能力，而是可能减少纯图片中的小金额、紧邻符号和路线末尾漏字。它与当前 small 使用同一 PaddleOCR 3.7 通用 OCR 接口，最适合先做隔离 A/B。是否升为默认，必须以本项目字段级指标和目标 Linux CPU 的延迟、RSS 为准。

### PaddleOCR-VL 1.6：文档恢复能力更强，但非常重

官方将 1.6 定位为 0.9B 文档解析 VLM，OmniDocBench v1.6 总体 96.33%，并报告在扫描、弯曲、倾斜、屏摄和光照变化场景达到 SOTA。完整 PaddleOCR-VL 产线还包含版面检测和页面结果组合；只运行 0.9B 权重，不等于运行完整文档解析管线。[1.6 算法说明](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/algorithm/PaddleOCR-VL/PaddleOCR-VL-1.6.md)；[完整管线文档](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PaddleOCR-VL.md)

官方支持本地 x64 CPU，也为 Apple Silicon 提供 PaddlePaddle 直推和 MLX-VLM 服务路径；Apple 指南说明目前主要在 M4 验证，Apple Silicon 不支持官方 Docker Compose 路径。[硬件支持矩阵](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PaddleOCR-VL.en.md)；[Apple Silicon 指南](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PaddleOCR-VL-Apple-Silicon.md)

本项目已经做过比通用文档 benchmark 更相关的真实样本实验：9 张非空样本总金额 9/9，但平均 45.14 秒、峰值约 11.4 GiB。这个结果支持“困难样本兜底”，不支持“替换默认 OCR”。详见[项目本机 PoC](PADDLEOCR_VL_16_LOCAL_POC.md)。

### PaddleOCR-VL v1 / 0.9B 与 Receipt 社区微调

官方 v1 管线的核心是 `PaddleOCR-VL-0.9B`；模型卡权重约 1.92 GB，支持 109 种语言以及文本、表格、公式和图表解析。[官方模型卡](https://huggingface.co/PaddlePaddle/PaddleOCR-VL) 目前官方管线默认版本已是 v1.6；在同为 0.9B 的前提下，没有理由再把旧 v1 作为新的主要试验对象。

`PaddleOCR-VL-Receipt` 则是社区名称。发布者配套仓库说明其脚本只保留 VL recognition，明确不包含文档预处理、版面检测、内容格式化、版面块合并，也不输出 score 和 coordinate；默认官方模型不能仅靠 query 正确输出 JSON，只有配套微调权重才支持这种用法。[发布者仓库](https://github.com/megemini/PaddleOCR-VL-REC) 这意味着它无法直接继承官方完整 1.6 的能力，也没有公开证据证明它能可靠区分本项目的乘车日期/开票日期、价税合计/税额和铁路路线。

### RapidOCR：可能更易部署，不会天然更准

RapidOCR 的核心定位是把 PaddleOCR 模型转换并运行在 ONNX Runtime、OpenVINO、MNN、Paddle、TensorRT 或 PyTorch 等后端。当前文档支持 PP-OCRv6 tiny/small/medium；安装文档说明默认 ONNX Runtime CPU 可离线使用，wheel 约 27.2 MB 并包含检测、方向分类和识别模型。[RapidOCR 仓库](https://github.com/RapidAI/RapidOCR)；[模型矩阵](https://github.com/RapidAI/RapidOCRDocs/blob/main/docs/model_list.md)；[安装文档](https://github.com/RapidAI/RapidOCRDocs/blob/main/docs/install_usage/rapidocr/install.md)

因此，RapidOCR 的潜在收益是 CPU 后端、包大小或跨平台体验，不是一个独立的新识别模型。若它和当前 PaddleOCR 都使用 PP-OCRv6 small，不应预设准确率会明显提高。

### Surya OCR 2：较轻的文档 VLM，但没有中文票据优势证据

Surya 2 用约 650M 参数的单一 VLM 处理 OCR、版面和表格，NVIDIA GPU 用 vLLM，CPU/Apple Silicon 用 llama.cpp。发布说明给出 olmOCR-bench 83.3、91 语言内部 benchmark 平均 87.2、RTX 5090 吞吐约 5 页/秒。[Surya 2 发布说明](https://github.com/datalab-to/surya/releases)

这些指标证明它值得作为通用文档解析研究候选，但没有中文发票、出租车客运发票或铁路电子客票的字段级结果，也没有 Apple Silicon 中文票据速度。接入新的服务、Schema 和模型缓存前，尚无证据说明它比已在本项目验证过的 Paddle 组合更好。

### MinerU：能力全面，但不是单张票据 OCR 的轻量替代

MinerU 支持 PDF、图片、DOCX、PPTX 和 XLSX，提供 pipeline、hybrid 和 VLM 等后端。官方列出的 OmniDocBench v1.6 总体分数为 pipeline 86.47，VLM/混合路径约 95.26–95.39；pipeline 可纯 CPU，macOS 需 14.0+。完整本地方案需要至少 16 GB RAM、20 GB 磁盘并推荐 32 GB RAM。[MinerU Quick Start](https://github.com/opendatalab/MinerU/blob/master/docs/en/quick_start/index.md)

它适合复杂长文档转换，不针对一张一票的字段提取。为本项目引入整套 MinerU 会增加远超收益的依赖、磁盘和部署复杂度。

### Qwen3-VL 2B：可以理解票面，但不应承担财务确定性

官方模型卡将 Qwen3-VL-2B-Instruct 定位为通用图文模型，支持 32 种语言 OCR、低光/模糊/倾斜文字和长文档结构理解，并提供 Transformers、vLLM、SGLang 和 GGUF 本地运行方式。[Qwen3-VL-2B-Instruct 模型卡](https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct)

它可以通过提示生成 JSON 并同时做类别判断，但本质是通用生成式 VLM，不提供发票关键字段置信度，也没有官方当前硬件的中文客运票据字段 benchmark。项目此前图片直出 PoC 已出现把税额或未税金额选为总金额的情况，见[本地 VLM 票据 PoC](LOCAL_VLM_RECEIPT_POC.md)。因此它不应替代确定性金额、日期和路线规则。

### GLM-OCR 与 HunyuanOCR-1.5：较新的专用 OCR-VLM，保留为第二阶段候选

GLM-OCR 是 0.9B 专用文档 OCR-VLM，官方完整自托管管线使用 PP-DocLayout-V3 做版面分析并行识别，支持 Markdown/JSON 输出；官方提供 vLLM、SGLang、Ollama，以及 Apple Silicon 的 MLX 部署指南。官方报告 OmniDocBench v1.5 为 94.62，并列出信息抽取能力，但没有公司当前三类票据的字段级基准。[GLM-OCR 官方仓库](https://github.com/zai-org/GLM-OCR)

HunyuanOCR-1.5 同样是专用端到端 OCR-VLM，覆盖文档解析、文本定位、信息抽取和翻译。官方除了 GPU 推理，还提供 GGUF + llama.cpp 的 CPU、消费级 GPU 和笔记本部署路线；其主推加速路径仍是生成式长输出和独立服务，接入复杂度明显高于 PP-OCRv6 medium。[HunyuanOCR-1.5 官方仓库](https://github.com/Tencent-Hunyuan/HunyuanOCR)

这两者比 Qwen3-VL 更贴近 OCR 任务，但“专用 OCR-VLM”仍不等于“已学会本公司的发生日期、价税合计和类别规则”。若 medium 仍不能覆盖后续积累的图片失败集，可以只选其中一个做隔离 A/B；在此之前同时接入多个生成式 OCR 服务属于过度设计。

### docTR：官方预训练词表不覆盖中文

docTR 可以从 PDF/图片输出词、行、块层级和可序列化 JSON，支持多种检测和识别网络。[docTR Quickstart](https://mindee.github.io/doctr/latest/getting_started/quickstart.html) 但官方支持 vocab 列表包含 Latin、欧洲语言、越南语、俄语、希腊语、希伯来语等，没有中文字符集；其官方识别模型表主要在 FUNSD、CORD 等数据上报告结果。[官方 vocab 列表](https://mindee.github.io/doctr/latest/modules/datasets.html#supported-vocabs)；[模型选择与 benchmark](https://mindee.github.io/doctr/using_doctr/using_models.html) 在没有自行训练中文识别器的情况下，它不适合作为当前中文票据 OCR 替代。

## 建议的 PP-OCRv6 medium A/B 方法

不要只比较 OCR 原文或通用 CER，应沿用当前票面人工真值，分别统计：

1. 分类正确率；
2. 发生日期正确率；
3. 价税合计正确率；
4. 起终点/说明正确率；
5. 四项全对率；
6. 静默错误数，即没有告警但字段错误；
7. 单张冷启动、热态 P50/P95 耗时和 worker 峰值 RSS。

对比组应保持 PDF 文本提取、Parser 和图片渲染参数完全一致，只替换：

```text
基线：PP-OCRv6_small_det + PP-OCRv6_small_rec
实验：PP-OCRv6_medium_det + PP-OCRv6_medium_rec
```

为了定位收益来自检测还是识别，可额外加一组 `small_det + medium_rec`；若问题主要是小数、站名末尾或相邻符号被识错，这一混合组可能用较小成本得到大部分收益。所有组合仍须使用项目固定的离线权重路径，避免运行时联网下载。

准入建议：medium 至少要减少纯图片的金额/路线错误，不能增加任何金额或发生日期的静默错误；同时在目标 Linux CPU 上满足用户可接受的上传等待时间和并发内存。若字段全对率没有实质提升，就继续使用 small，不为了模型榜单增加部署成本。

## 最终建议

- **现在不改生产架构。** 当前“PDF 文本 + PP-OCRv6 small + Parser”已经是最新传统 OCR 技术路线，且真实票据组合测试优于任一单独识别路径。
- **下一次模型实验只做 PP-OCRv6 medium。** 它是接口不变、风险最低、最可能改善纯图片漏字的候选。
- **PaddleOCR-VL 1.6 保留为可选兜底设计，不默认启用。** 等未来累计足够多真实失败样本后，再判断它是否能显著减少人工修改，且应放进独立、单并发的大内存 worker。
- **暂不引入 Receipt 社区微调、Qwen3-VL、GLM-OCR、HunyuanOCR-1.5、Surya、MinerU、docTR 或第二套 RapidOCR 运行时。** 在没有同一批票据的字段级优势证据前，它们只会增加依赖、部署和故障路径；若未来要研究生成式 OCR，只选择一个独立候选做离线 A/B。
