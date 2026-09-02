# PaddleOCR-VL-Receipt 与 PaddleOCR-VL-0.9B 调研

日期：2026-09-01  
范围：只评估本地、免费模型在当前报销单 Excel 生成工具中的适用性；不改变现有实现。

## 结论

`PaddleOCR-VL-Receipt` 不是 PaddlePaddle 官方发布的一个新基座模型，也不是完整的 PaddleOCR-VL 管线。它是社区作者 `megemini` 基于官方 `PaddleOCR-VL-0.9B`、使用 WildReceipt 数据做监督微调（SFT）后发布的结构化收据信息抽取权重。配套的 `PaddleOCR-VL-REC` 是只保留视觉语言识别部分的简化推理脚本，明确没有文档预处理、版面检测、内容格式化、版面块合并、坐标和置信度等能力。[发布者模型卡](https://www.modelscope.cn/models/megemini/PaddleOCR-VL-Receipt/summary) [发布者推理工具](https://github.com/megemini/PaddleOCR-VL-REC)

对于当前项目，推荐顺序是：

1. 先测试**官方完整 PaddleOCR-VL 管线**能否比现有 PP-OCRv6 更稳定地读出文字、金额标签、发生日期和路线。
2. 金额、日期和路线仍交给确定性 Parser；这些财务字段不应由通用模型最终拍板。
3. 只有在规则无法分类时，才把 OCR 文本交给一个小型文本模型，从固定费用类别中选择一项，并要求返回原文证据。
4. `PaddleOCR-VL-Receipt` 可以进入对比实验，但不建议直接作为首选方案。它的训练域与公司中文发票、铁路电子客票、出租车电子发票并不一致。

因此，“专用文档 OCR → 规则/小文本模型分类”**很可能比 Qwen3-VL 直接看图并同时决定全部字段更稳定**，但这是工程推断，不是现有样本已证明的结果。需要用同一批人工标注票据做 A/B 测试后才能替换现有流程。

## 两个名称实际代表什么

### PaddleOCR-VL-0.9B

它是 PaddlePaddle 官方模型，是 PaddleOCR-VL v1 完整管线中的 VLM 识别组件，而不是完整管线本身。官方文档明确说明：v1 管线由 `PP-DocLayoutV2` 版面分析和 `PaddleOCR-VL-0.9B` 元素识别组成；单独通过 Transformers、vLLM 等运行 0.9B 组件，不等于执行完整 PaddleOCR-VL，并可能出现漏识别或过多幻觉。[官方管线说明](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PaddleOCR-VL.en.md#paddleocr-vl)

0.9B VLM 的官方任务是：

- 文本识别：`OCR:`
- 表格识别：`Table Recognition:`
- 公式识别：`Formula Recognition:`
- 图表识别：`Chart Recognition:`

它支持 109 种语言，核心由动态高分辨率视觉编码器和 ERNIE-4.5-0.3B 语言模型组成。官方模型卡把它定位为文档元素识别模型，而不是通用的任意 JSON 业务字段抽取器。[官方模型卡](https://huggingface.co/PaddlePaddle/PaddleOCR-VL)

### PaddleOCR-VL-Receipt

这是第三方发布者在 0.9B 基座上做的收据 KIE（Key Information Extraction，关键信息抽取）微调模型。它改变的主要是模型对自定义提示和 JSON 输出的响应能力：

- `OCR:{}`：尝试输出完整结构化信息。
- `OCR:{"field_name":""}`：提取字符串字段。
- `OCR:{"field_name":{}}`：提取对象字段。
- `OCR:{"field_name":[]}`：提取列表字段。

发布者没有给出一份固定且有准确率承诺的字段白名单；公开示例只展示了 `NAME`、`ITEMS`、`ITEM`、`AMOUNT` 等通用键。因此，可以让它尝试返回商户、日期、金额或明细，不等于它已经可靠支持“乘车日期而不是开票日期”“价税合计而不是税额”“铁路起终点”等本项目字段。[模型卡](https://www.modelscope.cn/models/megemini/PaddleOCR-VL-Receipt/summary) [推理代码](https://github.com/megemini/PaddleOCR-VL-REC/blob/master/paddleocr_vl_rec.py)

## 训练域与当前票据的差异

Receipt 模型卡明确说明它主要针对 WildReceipt 优化，微调数据量较小，其他文档类型的泛化能力需要验证；复杂表格、多栏布局、非标准字段和多页文档也属于已知风险。[Receipt 模型卡](https://www.modelscope.cn/models/megemini/PaddleOCR-VL-Receipt/summary)

PaddleOCR 官方资料将 WildReceipt 描述为一个**英文收据数据集**：1267 张训练图片、472 张测试图片、约 5 万个文本框和 26 个类别。官方文档举出的 KIE 类别示例包括订单号、发票号码和金额。[PaddleOCR WildReceipt 说明](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version2.x/algorithm/kie/algorithm_kie_sdmgr.en.md)

这与当前项目的主要输入存在明显域差异：

- 中国增值税电子发票；
- 铁路电子客票及乘车日期、起终点；
- 出租车/网约车发票中的服务发生日期和路线；
- 公司自定义的费用类别体系；
- PDF 文本层与单页 PDF。

所以 Receipt 更适合作为待验证候选，而不是因为名称中有 `Receipt` 就默认优于官方通用文档解析模型。

## 哪一种组合更合适

| 方案 | 优点 | 主要风险 | 当前建议 |
|---|---|---|---|
| Qwen3-VL 直接看图输出类别、日期、金额、说明 | 一个模型、接口简单、热态速度快 | 同一模型同时承担视觉读取和业务判断；已测到把税额或未税金额当总金额 | 不作为权威财务结果 |
| PaddleOCR-VL-Receipt 直接输出 JSON，再用小模型分类 | JSON 接口直观；可能擅长英文零售收据 | 英文 WildReceipt 域偏差；没有坐标/置信度；两个生成模型串联会累积错误 | 只进入对照测试 |
| 官方完整 PaddleOCR-VL 输出文本/结构，现有 Parser 提取，再由小文本模型兜底分类 | 感知和业务判断分层；结果可追溯；金额日期能走确定性校验；未来加类别方便 | 多一个本地运行时，整体比现有 PP-OCR 更重 | 最值得先做 PoC |
| 现有 PP-OCRv6 + Parser + 小文本模型兜底分类 | 改动最小，已有真实票据验证 | 复杂版面或文字关联仍受传统 OCR 限制 | 继续作为基线 |

推荐的数据流：

```text
票据图片 / 单页 PDF
  → PDF 文本层优先或完整 PaddleOCR-VL 文档解析
  → 原始文字与结构化块
  → 确定性 Parser：总金额、发生日期、开票日期、路线
  → 关键词规则分类
  → 仅当规则不确定：小文本模型从固定类别枚举中选择
  → 白名单、金额与日期一致性校验
  → 可编辑费用明细
```

分类模型只需要返回：

- `category_id`：必须属于系统固定枚举；
- `evidence`：必须是 OCR 原文中真实存在的短语；
- `confidence`：只用于决定是否提示人工检查，不能作为财务正确性的证明。

若模型不能给出原文证据，或者类别与规则冲突，就返回“其他/需检查”，不猜测。这样以后添加费用类别时，只需更新类别描述、关键词和分类提示，不必重新改视觉 OCR 模型。

## 为什么不让通用小模型继续提取金额和日期

分类属于语义判断，而金额和日期属于需要精确复现的财务字段。当前 Qwen3-VL PoC 已出现：

- 把未税金额当价税合计；
- 把税额当总金额。

即使先做 OCR，再让文本模型提取，仍可能选错候选数字。更稳妥的职责划分是：

- OCR/VL：尽量准确还原票面文字和布局；
- 确定性代码：按照“价税合计、票价、实际支付”等优先级选金额，区分发生日期与开票日期；
- 小文本模型：只处理规则难以表达的费用类别和简短说明；
- 用户：能修改任何识别结果。

## 本地 Mac 与 CPU 部署

### 官方 PaddleOCR-VL

官方 Apple Silicon 指南支持本地直接推理和“客户端 + MLX-VLM 服务”两种路径，但目前只在 Apple M4 上完成精度验证；其他 Apple Silicon 的兼容性没有完成官方验证。当前 M5 Pro 应视为“可试、需实测”，不能直接引用 M4 结果。[Apple Silicon 官方指南](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PaddleOCR-VL-Apple-Silicon.md)

官方直接推理安装要求是独立虚拟环境、`paddlepaddle>=3.2.1` 和 `paddleocr[doc-parser]`，Apple Silicon 示例仍使用 `device="cpu"`。Apple Silicon 不支持官方 Docker 路径；如果追求更好的本机推理性能，官方提供 `mlx-vlm>=0.3.11` 的 VLM 服务路径，但该服务只负责管线中的 VLM 环节，仍需完整 PaddleOCR-VL 客户端完成版面分析和结果组合。[Apple Silicon 安装与 MLX 服务](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PaddleOCR-VL-Apple-Silicon.md#1-本地运行环境准备)

当前项目已锁定 `paddleocr==3.7.0`、`paddlepaddle==3.3.1` 和 NumPy 2.3.5，框架版本满足官方最低版本，但目前安装的是现有 OCR 所需依赖，而不是完整 `doc-parser` 运行栈。PoC 应放到独立虚拟环境或独立本地进程，避免直接改变 Web 后端依赖。

### PaddleOCR-VL-Receipt

发布者的配套脚本参数接受 `device="cpu"`，但没有发布 Mac 或 Apple Silicon 验证记录。它的 `requirements.txt` 还包含一个 CUDA 12.6、Linux x86_64 专用的 nightly safetensors wheel，并固定 `numpy==1.26.4`；不能原样安装进当前 Mac 后端环境。[Receipt 工具依赖](https://github.com/megemini/PaddleOCR-VL-REC/blob/master/requirements.txt)

理论上可以删去 Linux/CUDA 专用 wheel，使用兼容的 Paddle/PaddleX CPU 运行时单独尝试，但这属于项目侧适配，不是发布者已经验证的安装路径。它也只接受单张图片；PDF 需要先单页渲染为图片。

### 速度预期

参数量 0.9B 不代表它会比现有 PP-OCRv6 CPU 管线更快。PaddleOCR-VL 是自回归生成模型，耗时还受图片分辨率、输出文字长度、是否使用完整布局管线和推理后端影响。官方资料没有提供当前 M5 Pro 的可直接套用数据，因此必须实际测量：

- 首次加载耗时；
- 热态单张耗时；
- 峰值内存；
- 10 张连续处理耗时；
- 两个模型同时常驻时的内存和并发退化。

## 建议 PoC，而不是立即接入

第一轮只做旁路评测，不改报销页面和正式 OCR：

1. 用已有 9 张非空分类样本，加上真实火车票、打车票、住宿票和普通发票。
2. 给每张票建立人工金标准：`category / event_date / invoice_date / total_amount / route / description`。
3. 同时比较：
   - 现有 PP-OCRv6 + Parser；
   - 官方完整 PaddleOCR-VL v1/0.9B + 现有 Parser；
   - PaddleOCR-VL-Receipt JSON；
   - OCR 文本 + 规则 + 小文本模型分类；
   - 已完成的 Qwen3-VL 2B 直出 JSON 基线。
4. 对金额和日期统计精确匹配，不能只看“看起来差不多”。
5. 统计高置信度错误、无证据分类、JSON 修复次数、冷/热耗时和峰值内存。

准入条件应是：混合方案在金额和发生日期上不增加静默错误，同时能明显降低“其他”类别和人工修改次数。未达到这一条件就保留现有 PP-OCRv6。

## 关于最新版本

官方模型卡已提示 PaddleOCR-VL 有更新的 1.6 版本，官方主文档当前默认管线版本也是 v1.6。若目标是寻找最好的官方 OCR/VL 基线，新的 PoC 应同时考虑完整 `PaddleOCR-VL-1.6`，而不是只测试最初的 v1/0.9B；但 `PaddleOCR-VL-Receipt` 本身仍是基于旧 0.9B 的社区微调，不能把 1.6 的能力直接推算到 Receipt 权重上。[官方 PaddleOCR-VL 模型卡](https://huggingface.co/PaddlePaddle/PaddleOCR-VL) [官方 PaddleOCR-VL 使用文档](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PaddleOCR-VL.en.md)

## 最终判断

- **是否值得试：**值得，优先测试官方完整 PaddleOCR-VL（并把 1.6 加入候选），而不是先押注 Receipt 社区微调。
- **Receipt 能否直接用：**可以做隔离 PoC，不能凭模型名认为它已经适配中国差旅票据。
- **提取后再分类是否更好：**架构上更合理，预计比一个通用 VLM 同时看图、抽取、分类更稳定；但应是“确定性规则优先，小文本模型兜底”，不是无条件串联两个生成模型。
- **是否替换现有 OCR：**现在不能。完成同一批金标准票据 A/B 测试后再决定。

