# 中文 OCR 票据纯文本费用分类小模型调研

日期：2026-09-02  
范围：输入已经由 OCR 得到的中文票据文本，从约 16 个固定费用类别中选择一类；免费、本地运行；Apple M5 Pro 做 PoC，后续迁移到 Linux x86_64 CPU；不讨论图片识别，也不修改当前应用。

## 结论

**首选 `openbmb/MiniCPM5-1B`，备用 `Qwen/Qwen3-1.7B`。**

- **首选：MiniCPM5-1B。**它是 2026-05 发布的纯文本、中文/英文 1.08B 模型，原生 131K 上下文；发布方同时提供 Transformers、官方 GGUF 和 Apple Silicon 4-bit MLX 权重。官方 Q4_K_M GGUF 为 657 MB，MLX 仓库约 618 MB。Mac 可先用 MLX 获得更好的本机吞吐，也可直接用 GGUF/llama.cpp 保持与 Linux CPU 相同的部署形态。[官方模型卡](https://huggingface.co/openbmb/MiniCPM5-1B) [官方 GGUF](https://huggingface.co/openbmb/MiniCPM5-1B-GGUF) [官方 MLX 部署说明](https://github.com/OpenBMB/MiniCPM/blob/main/docs/deployment/mlx.md)
- **备用：Qwen3-1.7B。**它的参数和磁盘占用更大，但 Qwen 官方明确列出简体中文、繁体中文和粤语，指令跟随和本地运行生态成熟；Ollama 的 Q4_K_M 包约 1.4 GB。若 MiniCPM5 在相近类别（例如市内交通、网约车、差旅交通）之间区分不足，应优先用它做准确率对照。[官方模型卡](https://huggingface.co/Qwen/Qwen3-1.7B) [Qwen 官方多语言说明](https://qwenlm.github.io/blog/qwen3/) [Ollama 模型页](https://ollama.com/library/qwen3)
- **超轻量对照：Qwen3-0.6B。**Ollama Q4_K_M 只有 523 MB，适合作为速度/资源下限，但其非嵌入参数仅 0.44B；不能在没有本项目测试的情况下假定它能稳定处理含 OCR 噪声、商户别名和相近费用定义的 16 分类。[官方 GGUF 模型卡](https://huggingface.co/Qwen/Qwen3-0.6B-GGUF) [Ollama 文件信息](https://ollama.com/library/qwen3%3A0.6b/blobs/7f4030143c1c)

以上是工程选型判断，不是官方票据分类榜单结论。所有候选的发布方都没有公布“公司中文票据 OCR 文本 → 本项目 16 类”的准确率，最终选择必须由同一批人工标注样本决定。

## 候选对比

| 模型 | 中文与指令能力 | 上下文 | 本地量化与下载大小 | JSON/结构化输出 | 许可证（仅事实） | 判断 |
|---|---|---:|---|---|---|---|
| **MiniCPM5-1B** | 官方标注中文、英文；1,080,632,832 总参数；支持 Think / No Think；面向本地和资源受限场景 | 131,072 | 官方 MLX 4-bit 仓库约 **618 MB**；官方 GGUF Q4_K_M **657 MB**、Q8_0 1.1 GB、F16 2.1 GB；Transformers、llama.cpp、Ollama、MLX | 模型有指令/工具调用能力；严格 JSON 应由运行时 JSON Schema 约束 | Apache-2.0 | **首选**。中文、本机 MLX、跨平台 GGUF 三项同时满足；但发布时间较新，需验证 M5 和目标 CPU |
| **Qwen3-1.7B** | 官方支持 119 种语言/方言，明确含简中、繁中、粤语；1.7B 总参数、1.4B 非嵌入参数；Think / No Think | 32,768（官方模型卡；Ollama 包显示 40K 运行时窗口） | Ollama Q4 约 **1.4 GB**；官方支持 Transformers，本地生态列出 llama.cpp、Ollama、MLX-LM 等 | 同上；不要把聊天指令跟随等同于 schema 保证 | Apache-2.0 | **准确率备用**。更重，但可能比 0.6B 更能处理相近类别与噪声文本 |
| **Qwen3-0.6B** | 与 Qwen3 家族相同的中文覆盖和双模式；0.6B 总参数、0.44B 非嵌入参数 | 32,768（Ollama 包显示 40K） | Ollama Q4_K_M **523 MB**、Q8_0 832 MB；Qwen 官方还发布 Q8_0 GGUF，并给出 llama.cpp/Ollama 命令 | 同上 | Apache-2.0 | **资源下限对照**。最小、迁移简单；语义余量也最小 |
| **Gemma 3 1B IT** | 1B 版本是纯文本指令模型；官方称家族训练覆盖 140+ 语言并支持结构化输出/函数调用，但未给出本任务中文指标 | 32K | Ollama Q4_K_M **815 MB**、Q8_0 1.1 GB、FP16 2.0 GB；Transformers、llama.cpp/Ollama 量化生态 | Google 官方把 structured outputs 列为 Gemma 3 能力；本地仍建议用运行时 schema 强制 | Gemma Terms；HF 下载需接受条款 | **第三候选**。体积合适、指令跟随强，但中文针对性证据不如前两个中国团队模型 |
| **BERT-Base Chinese + 16 类分类头** | 简体/繁体中文专用，110M 参数；不是聊天模型，必须用本项目标注数据微调 | 512 | Transformers safetensors **412 MB**；CPU 直接输出 16 类 logits，无需 Ollama/llama.cpp | 不生成 JSON；应用将 logits 映射成类别，天然没有 JSON 语法错误 | Apache-2.0 | **有标注数据后的生产路线**。可能更快、更稳定，但不是零/少样本 PoC 的替代品 |

参数、上下文和格式来源：[MiniCPM5 模型卡](https://huggingface.co/openbmb/MiniCPM5-1B)、[MiniCPM5 GGUF 文件表](https://huggingface.co/openbmb/MiniCPM5-1B-GGUF)、[MiniCPM5 MLX 文件页](https://huggingface.co/openbmb/MiniCPM5-1B-MLX/tree/main)、[Qwen3-0.6B GGUF 模型卡](https://huggingface.co/Qwen/Qwen3-0.6B-GGUF)、[Qwen3-1.7B 模型卡](https://huggingface.co/Qwen/Qwen3-1.7B)、[Ollama Qwen3 tags](https://ollama.com/library/qwen3/tags)、[Gemma 3 官方模型卡](https://ai.google.dev/gemma/docs/core/model_card_3)、[Gemma 3 官方型号表](https://ai.google.dev/gemma/docs/get_started)、[Ollama Gemma 3 tags](https://ollama.com/library/gemma3/tags)、[Google BERT 官方仓库](https://github.com/google-research/bert#pre-trained-models)、[BERT-Base Chinese 文件页](https://huggingface.co/google-bert/bert-base-chinese/tree/main)。

## 为什么首选 MiniCPM5-1B

1. **任务和语言匹配。**它是纯文本中英双语模型，不为视觉编码器付出额外内存；1.08B 又比 0.6B 留出更多语义容量。发布方的中文模型卡和快速上手直接使用中文提示。[官方中文说明](https://github.com/OpenBMB/MiniCPM/blob/main/README-cn.md)
2. **Mac 与 Linux 有明确的两条官方路径。**发布方把 MLX 4-bit 定位为 Apple Silicon 本地路径，把 GGUF/llama.cpp 定位为 CPU/GPU 跨平台路径；官方 GGUF 无需补丁即可用于 vanilla llama.cpp、Ollama 和 LM Studio。[MLX 指南](https://github.com/OpenBMB/MiniCPM/blob/main/docs/deployment/mlx.md) [GGUF 模型卡](https://huggingface.co/openbmb/MiniCPM5-1B-GGUF)
3. **部署物足够小。**618–657 MB 的 4-bit 权重对 M5 Pro PoC 很轻，迁移到 Linux CPU 时也无需携带 Python/Transformers 全精度模型。
4. **长上下文不是关键，但有余量。**单张票据 OCR 通常远少于 8K token；生产时应把实际上下文限制为 4K–8K，避免为官方 131K 上限分配不必要的 KV cache。

注意：MiniCPM 官方 MLX 文档目前写的是 Apple Silicon M1–M4，没有 M5 Pro 专项基准。M5 仍属于 Apple Silicon，运行路径合理，但“能运行、速度多快、输出是否一致”都必须在当前机器实测，不能直接套用 M1–M4 结果。

## 结构化输出应由运行时保证

“模型能按指令回答 JSON”和“输出一定满足固定 schema”是两件事。对 16 分类，应由运行时限制可生成 token：

- Ollama 的 `format` 可以传 JSON Schema，并明确支持用 schema 强制结构化响应。[Ollama Structured Outputs](https://docs.ollama.com/capabilities/structured-outputs)
- llama.cpp 可以把 JSON Schema 转成 GBNF grammar；`llama-server` 的 `response_format` / `json_schema` 和 CLI 的 `--json` 都能做约束。官方也提醒 schema 不会自动注入提示，因此提示中仍要解释字段和类别定义。[llama.cpp grammar 文档](https://github.com/ggml-org/llama.cpp/blob/master/grammars/README.md)

建议模型只返回一个很窄的对象：

```json
{
  "category_id": "固定枚举中的一个值",
  "evidence": ["OCR 原文中的短语"],
  "needs_review": false
}
```

`category_id` 用 JSON Schema `enum` 限死；`evidence` 必须在服务端检查确实是 OCR 原文子串。模型自报的浮点 `confidence` 未经校准，不应作为自动通过依据。规则已经高置信命中时不调用模型；模型与规则冲突、无原文证据或输入信息不足时进入“其他/需人工确认”。

## Apple M5 Pro PoC 与 Linux CPU 路线

### 第一阶段：M5 Pro

- 快速验证首选官方 `MiniCPM5-1B-MLX` 4-bit；若需要从第一天就与生产保持同一推理栈，则直接用 `MiniCPM5-1B-Q4_K_M.gguf` + llama.cpp。
- 对分类关闭思考模式，限制短输出；不要让 `<think>` 内容增加延迟或污染 JSON。
- 同时跑 `Qwen3-1.7B` Q4 和 `Qwen3-0.6B` Q4，得到“更大模型准确率上限”和“最小模型资源下限”。
- llama.cpp 官方把 Apple Silicon 列为一等支持平台，使用 ARM NEON、Accelerate 和 Metal；Ollama 也明确通过 Metal 加速 Apple GPU。[llama.cpp README](https://github.com/ggml-org/llama.cpp) [Ollama 硬件支持](https://docs.ollama.com/gpu)

### 第二阶段：Linux x86_64 CPU

- 使用同一 MiniCPM5 checkpoint 的 Q4_K_M GGUF 和 llama.cpp；它支持 x86 的 AVX/AVX2/AVX512/AMX，并提供纯 CPU 构建。[llama.cpp README](https://github.com/ggml-org/llama.cpp) [CPU 构建文档](https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md)
- 只为票据长度配置 4K–8K context，单并发起步，记录冷启动、热态 P50/P95、峰值 RSS 和每秒 token。
- Ollama 可用于 PoC，但若生产需要可审计的 schema 参数、固定版本和更少运行层，直接使用 `llama-server` 更透明。

## PoC 准入标准

至少准备覆盖全部 16 类的脱敏金标准，既包含清晰文本，也包含常见 OCR 错字、断行、商户简称和多个金额/日期。固定同一 prompt、schema 和样本切分，比较：

- `category_id` exact match、macro-F1 和每类召回率；
- “其他/需确认”的覆盖率与覆盖内准确率；
- schema 合法率、证据原文命中率、同一输入重复运行的一致率；
- Mac 和目标 Linux CPU 的冷/热延迟、吞吐、模型磁盘和峰值内存。

若累计出数百条稳定标注样本，应增加 `BERT-Base Chinese + 16 类分类头` 对照。Google 官方模型是简繁中文 110M、512 token，并明确提供分类微调路径；Transformers 提供 `BertForSequenceClassification`。它不需要生成文字，长期可能比任何 1B 生成模型更快、更可复现。[Google BERT 中文模型说明](https://github.com/google-research/bert/blob/master/multilingual.md) [Transformers BERT 分类接口](https://huggingface.co/docs/transformers/model_doc/bert#transformers.BertForSequenceClassification)

## 最终建议

先以 **MiniCPM5-1B 4-bit** 完成旁路 PoC：Mac 上优先 MLX，若重视部署一致性则直接 GGUF/llama.cpp；用运行时 JSON Schema 限制 16 类枚举。用 **Qwen3-1.7B Q4** 作为准确率备用，用 **Qwen3-0.6B Q4** 测资源下限。不要依据通用聊天榜单直接接入正式分类；只有真实票据金标准证明它优于关键词规则和现有兜底后，才进入 Linux CPU 服务。
