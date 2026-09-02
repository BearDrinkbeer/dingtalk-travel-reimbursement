# 文档索引

## 当前产品文档

- [`V1_IMPLEMENTATION_PLAN.md`](V1_IMPLEMENTATION_PLAN.md)：V1 范围、业务规则、接口、OCR、Excel、安全与验收契约。
- 仓库根目录 [`README.md`](../README.md)：开发、配置、运行、部署与日常维护入口。

## 调研与已完成实验

`research/` 只保存调研结论和复现实验说明，不属于当前默认运行链路：

- [`research/FREE_LOCAL_OCR_RESEARCH.md`](research/FREE_LOCAL_OCR_RESEARCH.md)：免费本地 OCR 方案。
- [`research/MULTIMODAL_SMALL_MODEL_OCR_RESEARCH.md`](research/MULTIMODAL_SMALL_MODEL_OCR_RESEARCH.md)：多模态小模型选型。
- [`research/PADDLEOCR_VL_RECEIPT_RESEARCH.md`](research/PADDLEOCR_VL_RECEIPT_RESEARCH.md)：PaddleOCR-VL Receipt/0.9B 调研。
- [`research/PADDLEOCR_VL_16_LOCAL_POC.md`](research/PADDLEOCR_VL_16_LOCAL_POC.md)：PaddleOCR-VL 1.6 本机 PoC 结果。
- [`research/LOCAL_VLM_RECEIPT_POC.md`](research/LOCAL_VLM_RECEIPT_POC.md)：通用 VLM 对照结果。
- [`research/PURE_TEXT_RECEIPT_CLASSIFIER_MODEL_RESEARCH.md`](research/PURE_TEXT_RECEIPT_CLASSIFIER_MODEL_RESEARCH.md)：纯文本分类模型调研。
- [`research/RECEIPT_TEST_DATA_SOURCES.md`](research/RECEIPT_TEST_DATA_SOURCES.md)：非生产测试票据来源与本地目录约定。

对应的一次性评测工具位于 `scripts/research/`。大模型、虚拟环境和下载缓存不属于仓库内容，需要时按调研文档重新准备。
