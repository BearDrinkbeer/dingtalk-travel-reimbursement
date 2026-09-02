# 研究脚本

这里的脚本用于复现本地 VLM、PaddleOCR-VL 和文本分类实验，不参与应用启动、测试、部署或默认 OCR 流程。

- `evaluate-vlm-receipts.py`：通用多模态模型对照。
- `evaluate-paddleocr-vl-receipts.py`：PaddleOCR-VL 结构化提取评测。
- `evaluate-receipt-text-classifier.py`：基于已保存 OCR 文本的分类评测。

默认从仓库根目录运行；测试数据位于被 `.gitignore` 排除的 `data/ocr-eval-private/`。
