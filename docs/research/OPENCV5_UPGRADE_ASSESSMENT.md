# OpenCV 5 升级与隔离试验评估

> 调研基准日：2026-09-02  
> 范围：`opencv-contrib-python` 的发行状态、Python/NumPy/平台要求，以及本项目二维码与 PaddleOCR 链路的兼容风险。本文未修改代码、锁文件或运行环境。

## 结论

截至基准日，`opencv-contrib-python` 的 PyPI 默认最新稳定版是 **5.0.0.93**；OpenCV 上游稳定版是 **5.0.0**。虽然 4.x 线随后发布了 **4.14.0.94**，但它的版本号低于 5.0.0.93，因此不是 PyPI 的默认 `latest`。[PyPI 项目页](https://pypi.org/project/opencv-contrib-python/)；[OpenCV-Python 5.0.0.93 发布页](https://github.com/opencv/opencv-python/releases/tag/93)；[OpenCV 5.0.0 发布页](https://github.com/opencv/opencv/releases/tag/5.0.0)

**不建议直接升级本项目主 OCR 环境。** 项目锁定 `PaddleOCR==3.7.0`，后者依赖 `paddlex[ocr-core]>=3.7,<3.8`；当前锁文件解析到 PaddleX 3.7.2，而 PaddleX 3.7.2 的官方打包配置仍精确要求 `opencv-contrib-python==4.10.0.84`。将同一环境改为 5.0.0.93 会形成确定的 resolver 冲突，不是仅靠现有单元测试即可消除的“未知风险”。[PaddleOCR 3.7.0 `pyproject.toml`](https://github.com/PaddlePaddle/PaddleOCR/blob/v3.7.0/pyproject.toml)；[PaddleX 3.7.2 `setup.py`](https://github.com/PaddlePaddle/PaddleX/blob/v3.7.2/setup.py)；[PaddleX 跟踪 issue #5042](https://github.com/PaddlePaddle/PaddleX/issues/5042)

**值得做一次有边界的小型隔离 A/B 试验，但不值得现在推动生产升级。** 理由是 Python QR 公共调用面看起来保持兼容，且 4.10 之后官方确实持续修复二维码检测、解码、编码和崩溃问题；另一方面 PaddleX 尚未宣告支持 OpenCV 5，OpenCV 5 又改变了数组语义和部分图像处理结果。隔离试验的价值是获取本项目真实票据上的证据，不是绕过上游依赖约束直接上线。[OpenCV 4→5 迁移指南](https://github.com/opencv/opencv/wiki/OpenCV-4-to-5-migration)；[OpenCV 4.12/4.13/4.14 变更日志](https://github.com/opencv/opencv/wiki/OpenCV-Change-Logs)

如果试验目标只是“获得 4.10 之后的二维码修复”，应把 **4.14.0.94 作为低风险比较组**，与 4.10.0.84 和 5.0.0.93 三方 A/B；4.14 比 5.0 晚发布，包含额外 QR 错误纠正优化和边界检查，但它同样违反 PaddleX 3.7.2 的精确版本约束，因此也只能先隔离验证。[OpenCV 4.14 变更日志](https://github.com/opencv/opencv/wiki/OpenCV-Change-Logs#version4140)；[4.14.0.94 PyPI 元数据](https://pypi.org/pypi/opencv-contrib-python/4.14.0.94/json)

## 当前项目基线与实际调用面

- 后端声明 Python `>=3.11,<3.14`，OCR extra 精确锁定 `opencv-contrib-python==4.10.0.84`、`paddleocr==3.7.0`、`paddlepaddle==3.3.1`，目标平台仅 Linux x86-64 和 macOS arm64，见 [`backend/pyproject.toml`](../../backend/pyproject.toml)。
- 锁文件当前解析到 NumPy 2.3.5 和 PaddleX 3.7.2，见 [`backend/uv.lock`](../../backend/uv.lock)。
- 二维码路径直接调用 `cv2.imdecode`、`QRCodeDetector.detectAndDecodeMulti()` 和单码回退 `detectAndDecode()`；二维码失败被降级为空证据，不应让整张票据失败，见 [`backend/app/ocr/qr.py`](../../backend/app/ocr/qr.py)。
- OCR 路径会用 `cv2.resize(..., INTER_CUBIC)` 放大路线区域，再把 NumPy 图像交给 PaddleOCR `predict()`，见 [`backend/app/ocr/engine.py`](../../backend/app/ocr/engine.py)。
- 当前二维码集成测试用 `cv2.QRCodeEncoder_create()` 生成一张清晰的合成 QR，再用 `INTER_NEAREST` 放大；它能做冒烟测试，但不能代表拍照、扫描、压缩、旋转、污损、多码或 PDF 渲染后的真实发票，见 [`backend/tests/test_invoice_qr.py`](../../backend/tests/test_invoice_qr.py)。

## 发行、Python、NumPy 与平台要求

### 版本状态

OpenCV 5.0.0 已于 2026-06-06 作为正式版本发布；`opencv-contrib-python 5.0.0.93` 于 2026-07-02 上传到 PyPI，并被 PyPI 和 OpenCV-Python 发布页标为最新。它不是 2024 年的 5.0 alpha。[OpenCV 5.0.0 发布页](https://github.com/opencv/opencv/releases/tag/5.0.0)；[5.0.0.93 PyPI 元数据](https://pypi.org/pypi/opencv-contrib-python/5.0.0.93/json)

OpenCV 仍同时维护 4.x；`opencv-contrib-python 4.14.0.94` 于 2026-07-28/29 上传，时间晚于 5.0.0.93，但语义版本排序仍把 5.0 选作默认最新版。[OpenCV-Python 发布列表](https://github.com/opencv/opencv-python/releases)；[PyPI 发布历史](https://pypi.org/project/opencv-contrib-python/#history)

### Python 与 NumPy

5.0.0.93 的官方 PyPI 元数据声明：

- `Requires-Python >=3.6`；
- Python `<3.9` 时依赖 `numpy<2.0`；
- Python `>=3.9` 时依赖 **`numpy>=2`**。

其二进制 wheel 均使用 `cp37-abi3` 标签，因此保守地说，预编译二进制的实际入口是 CPython 3.7+；PyPI 分类器列到 Python 3.14。本项目的 3.11–3.13 均落在 wheel ABI 覆盖范围内，但 Python 3.14 不在本项目范围，且 PaddlePaddle 3.3.1 未提供 3.14 wheel。[5.0.0.93 PyPI JSON](https://pypi.org/pypi/opencv-contrib-python/5.0.0.93/json)；[PaddlePaddle 3.3.1 文件与元数据](https://pypi.org/project/paddlepaddle/3.3.1/)

就 NumPy 的声明交集而言，本项目当前 NumPy 2.3.5 可以满足 OpenCV 5：PaddleX 3.7.2 声明 `numpy>=1.24,<2.4`，PaddlePaddle 3.3.1 声明 `numpy>=1.21`，与 OpenCV 5 在 Python 3.9+ 的交集为 `numpy>=2,<2.4`。因此 **NumPy 不是当前首要阻断；PaddleX 对 OpenCV 4.10 的精确 pin 才是**。[PaddleX 3.7.2 `setup.py`](https://github.com/PaddlePaddle/PaddleX/blob/v3.7.2/setup.py)；[PaddlePaddle 3.3.1 PyPI JSON](https://pypi.org/pypi/paddlepaddle/3.3.1/json)；[OpenCV 5.0.0.93 PyPI JSON](https://pypi.org/pypi/opencv-contrib-python/5.0.0.93/json)

### 预编译平台

5.0.0.93 发布了 8 个 `cp37-abi3` wheel 和 1 个源码包：

| 平台 | 官方 wheel | 对本项目的含义 |
|---|---|---|
| Linux x86-64 | manylinux2014（glibc 2.17+）与 manylinux_2_28 | 覆盖正式部署的 Linux x86-64/glibc 路径 |
| Linux aarch64 | manylinux2014 与 manylinux_2_28 | 有 wheel，但不在项目当前 marker 范围 |
| macOS arm64 | macOS 13.0+ | 覆盖 Apple Silicon 开发机，但系统下限从项目当前 4.10 wheel 的 macOS 11.0+ 提高到 13.0+ |
| macOS x86-64 | macOS 14.0+ | 不在项目当前 marker 范围 |
| Windows | win32、win_amd64 | 不在项目当前 marker 范围 |
| musl/Alpine | 无 musllinux wheel | Alpine 会落入源码构建，不应作为这次试验平台 |

文件标签与最低平台均来自 [5.0.0.93 PyPI 文件清单](https://pypi.org/project/opencv-contrib-python/5.0.0.93/#files)；4.10 的 macOS arm64 wheel 基线来自 [4.10.0.84 PyPI JSON](https://pypi.org/pypi/opencv-contrib-python/4.10.0.84/json)。

## 二维码链路风险

### API 层：低到中风险

官方 4→5 迁移指南没有列出 `QRCodeDetector` 的破坏性 API 迁移。OpenCV 5.0 公共头文件仍保留 `QRCodeDetector`、`GraphicalCodeDetector`，以及单码/多码 `detect`、`decode`、`detectAndDecode` 方法；`QRCodeEncoder::create()` 也仍存在。[OpenCV 4→5 迁移指南](https://github.com/opencv/opencv/wiki/OpenCV-4-to-5-migration)；[OpenCV 5.0 `objdetect.hpp`](https://github.com/opencv/opencv/blob/5.0.0/modules/objdetect/include/opencv2/objdetect.hpp)；[OpenCV 5.0 `graphical_code_detector.hpp`](https://github.com/opencv/opencv/blob/5.0.0/modules/objdetect/include/opencv2/objdetect/graphical_code_detector.hpp)

但项目测试中的 `cv2.QRCodeEncoder_create()` 是绑定生成后的 Python 名称，不直接写在 C++ 头文件中；隔离试验必须实际检查该符号与返回 tuple 的形状，不能只凭头文件判定 Python wheel 完全等价。

### 行为层：中风险，也是试验的主要价值

4.10 之后 QR 实现有实质变化：4.12 修复了 QR 编解码越界、自动版本编码并加入 ECI；4.13 改进多码检测，修复退化源点抛异常和不一致检测导致的崩溃，并调整角度计算；4.14 又优化 QR 错误纠正并增加字母数字解码边界检查。[OpenCV 4.12 变更日志](https://github.com/opencv/opencv/wiki/OpenCV-Change-Logs#version4120)；[OpenCV 4.13 变更日志](https://github.com/opencv/opencv/wiki/OpenCV-Change-Logs#version4130)；[OpenCV 4.14 变更日志](https://github.com/opencv/opencv/wiki/OpenCV-Change-Logs#version4140)

这些记录说明新版可能提升鲁棒性，但也说明“函数名没变”不等于检测结果不变。5.0 的完整 changelog 仍标为 TBD，不能仅从发布说明证明每个 4.x QR 修复在 5.0.0 中的精确包含关系。[OpenCV 5.0 changelog 状态](https://github.com/opencv/opencv/wiki/OpenCV-Change-Logs#version50)

本项目会先尝试多码再回退单码，新版多码排序、点数组形状、空字符串、异常类型或解码文本的差异都可能改变最终证据。由于最外层把 QR 当补充证据并安全忽略多数失败，风险通常表现为少一条金额/日期交叉验证或提示变化，而不是整张票据处理失败；但这仍可能改变最终可靠性判断，必须用真实样本 A/B。

另一个明确行为变化是 OpenCV 5 的 `INTER_NEAREST` 与 `INTER_NEAREST_EXACT` 统一为 Pillow 风格，边界像素可能与 4.x 不同。当前合成 QR 测试正用 `INTER_NEAREST` 放大；整数倍清晰 QR 很可能仍能通过，但不能以此推断所有二维码图像一致。[OpenCV 4→5 插值迁移说明](https://github.com/opencv/opencv/wiki/OpenCV-4-to-5-migration#nearest-neighbor-resize)

## PaddleOCR / PaddlePaddle 链路风险

### 依赖解析：确定阻断

PaddleOCR 3.7.0 的发布配置不直接选 OpenCV 版本，而是要求 `paddlex[ocr-core]>=3.7.0,<3.8.0`。PaddleX 3.7.2 的 `ocr-core` extra 引入 `opencv-contrib-python`，基础规格把它精确写为 `==4.10.0.84`；相关“精确 pin 阻塞下游升级”issue 仍开放并已指派。[PaddleOCR 3.7.0 配置](https://github.com/PaddlePaddle/PaddleOCR/blob/v3.7.0/pyproject.toml)；[PaddleX 3.7.2 配置](https://github.com/PaddlePaddle/PaddleX/blob/v3.7.2/setup.py)；[PaddleX #5042](https://github.com/PaddlePaddle/PaddleX/issues/5042)

因此正常解析器无法同时满足 PaddleX 3.7.2 和 OpenCV 5。用 `--no-deps`、强制覆盖或手工改元数据只能用于一次性实验，不能被解读为上游支持声明。

### 运行行为：中到高风险

PaddleX 的 OCR 预处理和后处理广泛以 NumPy 数组调用 `cv2`，而 OpenCV 5 把传入 `InputArray` 的一维 NumPy 数组映射为真正的 1D `Mat`，不再沿用 4.x 的 `N×1` 语义；依赖代码若假定 `.rows/.cols` 或固定形状，可能发生结果或异常变化。[OpenCV 4→5 的 1D/0D 数组说明](https://github.com/opencv/opencv/wiki/OpenCV-4-to-5-migration#1d-and-0d-array-semantics)

OpenCV 5 的 DNN 默认引擎变化不是本项目首要风险：本项目模型推理由 PaddlePaddle 执行，项目代码也没有调用 `cv2.dnn`。真正需要验证的是 PaddleX 图像预处理、PaddleOCR 初始化/预测、结果坐标与置信度，以及项目二次裁剪识别。PaddlePaddle 3.3.1 自身不依赖 OpenCV，只声明 NumPy 等依赖。[PaddlePaddle 3.3.1 PyPI JSON](https://pypi.org/pypi/paddlepaddle/3.3.1/json)；[OpenCV 5 DNN 迁移说明](https://github.com/opencv/opencv/wiki/OpenCV-4-to-5-migration#5-dnn-module)

## 建议的隔离试验边界

不改本项目锁文件，不复用当前 `.venv`，不写入模型目录。用临时虚拟环境或一次性容器分别建立以下三组：

1. 基线：Python 3.11/3.13 + NumPy 2.3.5 + OpenCV 4.10.0.84；
2. 4.x 比较组：相同 Python/NumPy + OpenCV 4.14.0.94；
3. 5.x 试验组：相同 Python/NumPy + OpenCV 5.0.0.93。

PaddleOCR 组需要明确记录“强制替换了 PaddleX 的 OpenCV pin”，只验证，不产出可部署锁文件。先在 macOS arm64 快速筛查，再在正式目标 Linux x86-64/glibc 环境复验。

最低验收集：

- `cv2` 导入、版本与 build info；`QRCodeDetector`、`QRCodeEncoder_create`、`imdecode`、`resize` 符号检查；
- 现有 `test_invoice_qr.py` 冒烟测试；
- 真实旧版增值税发票二维码：原图、压缩图、旋转/透视、模糊/低对比、裁边、多个二维码；
- 同一批单页 PDF 在项目 DPI 渲染后的二维码检测；
- 比较解码 payload、码数量/顺序、点数组、空结果、异常与耗时；
- 用当前只读本地模型初始化 PaddleOCR，跑真实票据图片/PDF，比较文字、框、置信度、金额/日期/路线和业务 warning；
- 在 Linux x86-64 运行完整后端测试及冷启动/峰值内存测试。

通过标准不应只是“没有崩”：二维码和 OCR 的关键业务字段不得比 4.10 基线退化；已知难例若改善，应记录样本级证据；Linux 和 macOS 结果差异必须可解释。即使全部通过，也应等 PaddleX 发布解除/放宽 4.10 pin，或由项目明确承担维护一个受验证的依赖覆盖，才进入正式升级决策。

## 最终判断

| 决策 | 判断 |
|---|---|
| 现在把主环境升级到 5.0.0.93 | **否**：与 PaddleX 3.7.2 官方依赖精确冲突 |
| 只为“版本最新”而升级 | **否**：没有足够业务收益，且二维码只是补充证据 |
| 做一次隔离 A/B | **是**：成本可控，可验证 4.10 后 QR 修复是否改善真实发票，并提前发现 PaddleX/OpenCV 5 问题 |
| A/B 首选组合 | **4.10.0.84 / 4.14.0.94 / 5.0.0.93** 三方比较 |
| 生产采用的前置条件 | PaddleX 官方放宽版本约束，或项目正式拥有并持续维护经过 Linux/macOS、二维码与 OCR 全链路验证的覆盖方案 |

