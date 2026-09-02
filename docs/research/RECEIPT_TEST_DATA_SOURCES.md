# 票据 OCR / VLM 测试数据来源

核查日期：2026-09-01

## 结论先行

公开、第一方可核验且适合本项目的中文真实票据数据并不多。当前 V1 只保留两层测试素材：

1. **中国官方电子票样**：覆盖通用数电发票、航空电子行程单、铁路电子客票、旧版增值税发票和通行费票据的标准版式；
2. **国内类别代表样本**：覆盖住宿、办公、通讯、广告、劳务、招待、租赁和水费等票面语义。

国外收据、出租车纸质小票和大规模相机拍摄训练集不属于当前 V1 验收范围，不在项目目录长期保留；只有需求范围变化时才按下文来源重新获取。

需要特别区分：税务总局发布的票样是**官方示例，不是真实已开具交易票据**；财政部的实例包是结构化 XML 测试数据，也不是 OCR 图片真票。酒店、通讯、咨询/广告/劳务/会议、租赁/物业/水电等在中国通常仍共用通用增值税或数电发票版式，类别主要由“货物或应税劳务、服务名称”一栏决定。公开第一方来源中未找到能逐类覆盖这些中文真实交易语义的完整数据集，最终仍应补少量经授权或脱敏的内部样本。

本文同时记录来源、下载方式和当前本地落地状态。授权条件不作为本项目本地测试素材的筛选条件；仍保留来源、样本性质和校验信息，便于复现测试并区分真实票据、官方票样与合成数据。

## 当前本地测试集

本地目录为 `data/ocr-eval-private/`，已由仓库根目录 `.gitignore` 中的 `data/` 规则排除，不会随项目代码提交。

| 目录 | 当前内容 | 数量 / 规模 | 用途 |
|---|---|---:|---|
| `official/` | 数电发票、铁路电子客票、旧版电子专票、通行费官方票样 | 4 个 PDF/PNG；约 3.3 MiB | 标准版式和字段位置冒烟测试 |
| `examples/` | 针对项目类别单独挑选的代表图片 | 9 张；约 2.1 MiB | 类别识别与金额、日期、说明字段测试 |

2026-09-02 已删除本地 `InvoiceDatasets`、`WildReceipt` 和重复的 DOC 源文件，共约 530 MiB。下文仍保留数据来源说明，便于将来范围变化时复现，不代表这些数据当前存在于本地。

`examples/` 当前覆盖：航空、住宿、餐饮/招待、办公用品、通讯、广告、劳务、租赁和水费。咨询、物业管理和会议费尚缺文字明确、清晰可用的独立样本；员工福利是报销用途而不是稳定的票面类型，应使用餐饮、零售或商品票据测试后由用户确认用途；“其他”本身没有固定票面。

桌面目录中已通过的火车票和市内交通票据继续作为项目私有回归样本，不在这里重复收集。出差补助不依赖票据 OCR，也不属于本测试集。

### 2026-09-01 本地 OCR 冒烟结果

使用项目锁定的 PP-OCRv6 small 本地模型，对当时 `examples/` 的代表图片执行了一次测试。测试只记录结构化结果，不保存或输出 OCR 原文。原目录中一张名为咨询服务、票面实际为劳务费的错误标签样本已于 2026-09-02 删除，所以下表不再列入该结果。

| 预期类别 | 当前分类 | 日期 | 金额 | 结论 |
|---|---|---|---:|---|
| 广告费 | 其他 | 2023-06-07 | 5000.00 | 返回非空字段，缺类别规则 |
| 飞机票（官方空白票样） | 飞机票 | 缺失 | 缺失 | 类型正确；空白票样本身没有交易字段 |
| 通讯费 | 其他 | 2020-12-09 | 139.00 | 返回非空字段，缺类别规则 |
| 招待费 / 餐饮 | 其他 | 2024-09-11 | 2139.00 | 返回非空字段，缺类别规则 |
| 劳务费 | 其他 | 2019-11-09 | 400.00 | 返回非空字段，缺类别规则 |
| 租赁费 | 其他 | 2021-12-27 | 264000.00 | 返回非空字段，缺类别规则 |
| 住宿费 | 住宿费 | 2023-05-27 | 2749.99 | 类别正确，日期和金额均非空 |
| 办公费 | 其他 | 2023-06-02 | 56.43 | 返回非空字段，缺类别规则 |
| 水电费 | 其他 | 2021-03-29 | 51.70 | 返回非空字段，缺类别规则 |

结果表明当前主要缺口是分类 Parser，而不是“没有识别到文字”：保留的 8 张非空白样本都返回了非空日期和金额，非空白样本类别命中为 1/8。日期和金额是否等于票面目标字段仍需建立人工真值后再统计准确率。后续分类规则应只依赖票面“货物或应税劳务、服务名称”等业务文字；无法可靠判断时继续返回“其他”并让用户修改，不能为了提高命中率进行无证据猜测。

## 本项目类别覆盖

| 项目类别 | 首选公开测试材料 | 能验证什么 | 仍缺什么 |
|---|---|---|---|
| 航空 | SCID 航空行程单；税务总局航空运输电子客票行程单票样；财政部 XML 实例 | 真实扫描噪声、中文字段、纸质/电子标准版式、结构化字段 | 不同航司的真实新式电子行程单 |
| 住宿 / 酒店 | 通用数电票样；DocILE 通用商业发票；FATURA 合成发票 | 发票布局、抬头、金额、税额、明细表、多页文档 | 可公开下载的中文真实酒店账单/住宿发票专集 |
| 餐饮 / 招待 | CORD、WildReceipt、SROIE；通用数电票样 | 店名、日期、菜品/商品行、数量、单价、小计、税额、总额及随手拍噪声 | 中文餐饮小票或餐饮数电发票的第一方公开真实集 |
| 办公用品 | WildReceipt、CORD；DocILE；FATURA | 商品明细、多行项目、数量、单价和总额 | 中文办公用品真实发票及具体商品分类语义 |
| 通讯 | 通用数电票样；DocILE 通用商业文档 | 通用发票字段及长明细布局 | 中文真实话费/通信服务账单专集 |
| 咨询 / 广告 / 劳务 / 会议 | 通用数电票样；DocILE；FATURA | 通用服务类发票、项目明细、英文商业/广告相关文档版式 | 各服务类别的中文真实票据；类别需靠明细文字单独验证 |
| 租赁 / 物业 / 水电 | 通用数电票样；DocILE 的 utility bill 等文档 | 通用发票和水电类英文商业文档布局 | 中文真实租赁、物业及水电账单专集 |
| 通用数电发票 | 税务总局数电发票票样；财政部 XML/XSD 实例；PaddleOCR 38 张增值税发票小集 | 标准展示样式、结构化字段、KIE 流程冒烟测试 | 大规模真实数电发票扫描/截图数据集 |
| 出租车 / 铁路 / 通行费 | SCID；FuxiJia TID；税务总局铁路及通行费票样 | 中文真实票据、旧式纸票与电子标准版式 | 新式电子票据的多来源真实样本 |

## 中文真实或脱敏票据数据集

### 1. SCID：Scanned Chinese Invoice Dataset

- 官方数据页：[DAVAR-Lab SCID](https://davar-lab.github.io/dataset/scid.html)
- 官方论文页：[《大规模真实场景扫描中文票据数据集》](https://www.cjig.cn/zh/article/doi/10.11834/jig.220911/)
- 官方存档页：[Science Data Bank](https://www.scidb.cn/en/detail?dataSetId=8a4bf3ef7ec84d2f962897a0e1e86d5b&version=V1)
- 下载：从 [SCID 官方页](https://davar-lab.github.io/dataset/scid.html) 打开网盘下载入口，提取码 `az49`；也可在 ScienceDB 页面申请/下载。官方给出的网盘入口是 [drive.ticklink.com](https://drive.ticklink.com/disk/fileDownload?link=mVD3EthW)。
- 规模：40,716 张；19,999 张训练、10,358 张验证、10,359 张测试。
- 类型：航空行程单、出租车发票、定额发票、客运发票、火车票、通行费发票，共 6 类。
- 格式：JPG 图片；`ocr.json` 含文字内容与四边形坐标；`gt.json` 含结构化字段标签。
- 性质：论文明确称为真实财务扫描票据；全部图片经过人工脱敏。
- 适用：当前最优先的中文真实 OCR/KIE 回归集，尤其适合航空、铁路、出租车、客运和通行费。
- 来源事实：官方页面声明公开标注归海康威视研究院所有，采用 CC BY-NC-SA 4.0，并要求从官方渠道获取。

### 2. TID / VATID：相机拍摄的中国出租车票与增值税发票

- 官方仓库：[FuxiJia/InvoiceDatasets](https://github.com/FuxiJia/InvoiceDatasets)
- 下载：

  ```bash
  git clone --depth 1 https://github.com/FuxiJia/InvoiceDatasets.git
  ```

  或下载 [master 分支 ZIP](https://github.com/FuxiJia/InvoiceDatasets/archive/refs/heads/master.zip)。
- 规模：当前检出的仓库内容为出租车票 228 张 JPG（train/test 各 114 张）、增值税发票 100 张 JPG（train/test 各 50 张），合计 328 张唯一图片；仓库根目录另有 2 张便捷取样副本。出租车票来自中国 25 个省份。
- 格式：JPG；`dataset/labels/charGT` 和 `dataset/labels/textGT` 提供字符/文本级标注，`lexicon/` 提供词表。
- 性质：官方 README 将其描述为采集的相机拍摄发票；未说明是否脱敏。
- 适用：小而直接，适合补充真实手机拍照倾斜、反光、模糊等噪声，以及经典增值税票版式。
- 限制：标注目标偏文字检索/识别，不是完整的结构化发票字段标注；仓库未声明单独的数据集许可证。

## 中国政府官方票样与结构化实例

以下文件均可直接从政府网站下载。它们适合做版式、渲染、字段定位和解析测试，但不是实际交易数据。

### 3. 国家税务总局票样

| 票样 | 官方发布页 | 直接附件 | 格式 / 大小 | 性质 |
|---|---|---|---|---|
| 通用数电发票 | [公告 2024 年第 11 号](https://fgk.chinatax.gov.cn/zcfgk/c100012/c5236067/content.html) | [数电发票样式.doc](https://fgk.chinatax.gov.cn/zcfgk/c100012/c5236067/5236067/files/%E6%95%B0%E7%94%B5%E5%8F%91%E7%A5%A8%E6%A0%B7%E5%BC%8F.doc) | DOC，9,513,472 字节 | 官方票样；非真实交易，未说明示例数据的生成方式 |
| 航空运输电子客票行程单 | [公告 2024 年第 9 号](https://fgk.chinatax.gov.cn/zcfgk/c100012/c5235729/content.html) | [电子行程单样式.doc](https://fgk.chinatax.gov.cn/zcfgk/c100012/c5235729/5235729/files/%E7%94%B5%E5%AD%90%E5%8F%91%E7%A5%A8%EF%BC%88%E8%88%AA%E7%A9%BA%E8%BF%90%E8%BE%93%E7%94%B5%E5%AD%90%E5%AE%A2%E7%A5%A8%E8%A1%8C%E7%A8%8B%E5%8D%95%EF%BC%89%E6%A0%B7%E5%BC%8F.doc) | DOC，870,912 字节 | 官方票样；非真实交易 |
| 铁路电子客票 | [公告 2024 年第 8 号](https://fgk.chinatax.gov.cn/zcfgk/c100012/c5235333/content.html) | [铁路电子客票样式.doc](https://fgk.chinatax.gov.cn/zcfgk/c100012/c5235333/5235333/files/%E7%94%B5%E5%AD%90%E5%8F%91%E7%A5%A8%EF%BC%88%E9%93%81%E8%B7%AF%E7%94%B5%E5%AD%90%E5%AE%A2%E7%A5%A8%EF%BC%89%E6%A0%B7%E5%BC%8F.doc) | DOC，179,200 字节 | 官方票样；非真实交易 |
| 增值税电子专用发票（旧税控版式） | [公告 2020 年第 22 号](https://fgk.chinatax.gov.cn/zcfgk/c100012/c5194955/content.html) | [电子专票票样.pdf](https://fgk.chinatax.gov.cn/zcfgk/c100012/c5194955/5194955/files/%E5%A2%9E%E5%80%BC%E7%A8%8E%E7%94%B5%E5%AD%90%E4%B8%93%E7%94%A8%E5%8F%91%E7%A5%A8%EF%BC%88%E7%A5%A8%E6%A0%B7%EF%BC%89.pdf) | PDF，1 页，135,362 字节 | 官方票样，最方便直接转图做 OCR 测试 |
| 增值税电子普通发票（旧税控版式） | [公告 2020 年第 1 号](https://fgk.chinatax.gov.cn/zcfgk/c100012/c5194907/content.html) | [电子普票票样.doc](https://fgk.chinatax.gov.cn/zcfgk/c100012/c5194907/5194907/files/%E9%80%9A%E8%BF%87%E5%A2%9E%E5%80%BC%E7%A8%8E%E7%94%B5%E5%AD%90%E5%8F%91%E7%A5%A8%E5%85%AC%E5%85%B1%E6%9C%8D%E5%8A%A1%E5%B9%B3%E5%8F%B0%E5%BC%80%E5%85%B7%E7%9A%84%E5%A2%9E%E5%80%BC%E7%A8%8E%E7%94%B5%E5%AD%90%E6%99%AE%E9%80%9A%E5%8F%91%E7%A5%A8%E7%A5%A8%E6%A0%B7.doc) | DOC，184,320 字节 | 官方票样；公告说明实际版式文件为 OFD |
| 纸质增值税专用发票 | [公告 2014 年第 43 号](https://fgk.chinatax.gov.cn/zcfgk/c100012/c5194478/content.html) | [专票票样.docx](https://fgk.chinatax.gov.cn/zcfgk/c100012/c5194478/5194478/files/%E5%A2%9E%E5%80%BC%E7%A8%8E%E4%B8%93%E7%94%A8%E5%8F%91%E7%A5%A8%E7%A5%A8%E6%A0%B7.docx) | DOCX，614,400 字节 | 官方空白票样 |
| 纸质增值税普通发票 | 同上 | [普票票样.docx](https://fgk.chinatax.gov.cn/zcfgk/c100012/c5194478/5194478/files/%E5%A2%9E%E5%80%BC%E7%A8%8E%E6%99%AE%E9%80%9A%E5%8F%91%E7%A5%A8%E7%A5%A8%E6%A0%B7.docx) | DOCX，633,344 字节 | 官方空白票样 |

2014 年第 43 号公告页面标为“已修改”，页面注释明确第一条及附件 3 失效，但没有将附件 1、附件 2 的两种票样列为失效。

税务总局的[官方解读](https://fgk.chinatax.gov.cn/zcfgk/c100015/c5236070/content.html)说明，数电发票是单一联次的数字化发票，取消固定版式，增加 XML 文件，同时保留 PDF、OFD 等格式。因此 DOC 票样主要用于验证展示布局，不应被当作数电发票唯一可能版式。

### 4. 收费公路通行费电子票据票样

- 官方页面：[交通运输部、财政部、国家税务总局等关于收费公路通行费电子票据的公告](https://www.chinatax.gov.cn/chinatax/n810341/n810765/c101653/202005/c5155102/content.html)
- 可直接下载：
  - [通行费增值税电子普通发票票样 PNG](https://www.chinatax.gov.cn/chinatax/n810341/n810765/c101653/202005/c5155102/5155102/images/1%20%E5%89%AF%E6%9C%AC-20200628172045763.png)
  - [通行费财政电子票据票样 JPG](https://www.chinatax.gov.cn/chinatax/n810341/n810765/c101653/202005/c5155102/5155102/images/2%20%E5%89%AF%E6%9C%AC-20200628172147994.jpg)
  - [通行费电子票据汇总单示例 JPG](https://www.chinatax.gov.cn/chinatax/n810341/n810765/c101653/202005/c5155102/5155102/images/3%20%E5%89%AF%E6%9C%AC-20200628172201540.jpg)
- 性质：官方票样/示例，不是真实交易；优点是已经是 PNG/JPG，可直接进入图像测试流程。

### 5. 财政部电子凭证会计数据标准实例包

- 官方页面：[财政部电子凭证会计数据标准深化试点数据标准](https://kjs.mof.gov.cn/zt/kuaijixinxihuajianshe/dzpzkjsjbzshsd/sjbz/202505/t20250519_3964020.htm)
- 直接下载：
  - [通用数电发票包](https://kjs.mof.gov.cn/zt/kuaijixinxihuajianshe/dzpzkjsjbzshsd/sjbz/202505/P020250617561583232009.zip)，196,786 字节
  - [铁路电子客票包](https://kjs.mof.gov.cn/zt/kuaijixinxihuajianshe/dzpzkjsjbzshsd/sjbz/202505/P020250519372945377513.zip)，211,637 字节
  - [航空运输电子客票行程单包](https://kjs.mof.gov.cn/zt/kuaijixinxihuajianshe/dzpzkjsjbzshsd/sjbz/202505/P020250519372945554296.zip)，216,883 字节
  - [增值税电子普票/专票包](https://kjs.mof.gov.cn/zt/kuaijixinxihuajianshe/dzpzkjsjbzshsd/sjbz/202505/P020250519372946417875.zip)，235,172 字节
- 格式：每个 ZIP 内含 DOCX 指南、XLSX 元素清单、XSD/XML 标准文件，以及标为“示例”的 XML 实例。
- 性质：官方结构化测试实例，不是 OCR 图片或真实交易票据。
- 适用：验证 XML 字段映射、结构化解析和 OCR 结果与电子凭证字段的对齐。

上述政府页面未单独声明“数据集许可证”；这里记录的是政府正式公开附件，而不是把它们归类为开放数据集。

## 小型中文 KIE 冒烟数据

### 6. PaddleOCR 增值税发票 KIE 示例集

- 官方文档：[PaddleOCR 发票关键信息抽取](https://www.paddleocr.ai/v2.10.0/ko/applications/%E5%8F%91%E7%A5%A8%E5%85%B3%E9%94%AE%E4%BF%A1%E6%81%AF%E6%8A%BD%E5%8F%96.html)
- 官方数据页：[Paddle AI Studio 数据集 165561](https://aistudio.baidu.com/aistudio/datasetdetail/165561)
- 下载：从 AI Studio 数据集页登录后下载。
- 规模：38 张，训练集 30 张、验证集 8 张。
- 格式：`imgs/` 图片、`train.json`、`val.json`、`class_list.txt`；JSON 行包含文字、`other/question/answer` 标签、四边形坐标，关系抽取版本还包含 `id` 与 `linking`。
- 性质：官方文档没有说明图片是真实、脱敏还是合成，因此应标记为“来源形态未披露”，不能宣称是真实票据。
- 适用：快速验证中文增值税发票检测、识别及 KIE 流程，不适合作为真实性或覆盖率基准。
- 来源事实：文档页未给出独立的数据集许可证说明，下载需遵循 AI Studio 页面条件。

## 海外真实收据/发票数据集

这些来源不能替代中文票据，但能显著补足餐饮、零售、多行商品、复杂拍摄和通用商业发票版式。

### 7. WildReceipt

- 官方仓库：[OpenMMLab MMOCR](https://github.com/open-mmlab/mmocr)
- 官方元数据：[WildReceipt metafile](https://github.com/open-mmlab/mmocr/blob/main/dataset_zoo/wildreceipt/metafile.yml)
- 直接下载：[wildreceipt.tar](https://download.openmmlab.com/mmocr/data/wildreceipt.tar)
- 下载命令：

  ```bash
  curl -L -o wildreceipt.tar https://download.openmmlab.com/mmocr/data/wildreceipt.tar
  ```

- 规模：论文正文报告 1,740 张收据、68,975 个文本框，约每张 39 个文本实例；同一论文的表格曾列 1,768 张，存在版本数字差异。官方服务器当前归档为 185,323,520 字节，官方数据准备脚本给出的 MD5 为 `2a2c4a1b4777fb4fe185011e17ad46ae`。
- 类型：商店名称、地址、电话、日期、时间、商品、数量、价格、小计、税、服务费/小费、总额等 25 类。
- 格式：图片和 `train.txt`、`test.txt`、`class_list.txt`、`dict.txt` 标注文件。
- 性质：论文说明图片从搜索引擎收集并筛除非英语样本，属于自然场景收据；语言为英语，包含非正面拍摄、褶皱等噪声。
- 适用：餐饮、购物/办公用品小票和复杂手机拍摄 KIE。
- 来源事实：MMOCR 官方元数据的数据集许可证字段为 `N/A`。

### 8. CORD v2

- 官方仓库：[ClovaAI CORD](https://github.com/clovaai/cord)
- 官方数据页：[NAVER CLOVA CORD v2](https://huggingface.co/datasets/naver-clova-ix/cord-v2)
- 下载：完整下载可用 Hugging Face `datasets`；为了避免一次下载约 2.31 GB，建议先流式取验证或测试样本：

  ```python
  from datasets import load_dataset

  stream = load_dataset("naver-clova-ix/cord-v2", split="test", streaming=True)
  sample = next(iter(stream))
  sample["image"].save("cord_test_sample.jpg")
  ```

- 规模：公开版 1,000 张印度尼西亚收据，800 训练、100 验证、100 测试；官方仓库说明原始采集超过 11,000 张，但公开的是 1,000 张样本。
- 大小：官方 `dataset_infos.json` 记录下载量 2,307,284,272 字节；训练/验证/测试分别为 800/100/100 条。
- 类型：商店和餐厅收据，含菜单/商品行、数量、单价、小计、税额、付款和总额等多级语义标签。
- 格式：Hugging Face Parquet/Image；样本内含图片和序列化 `ground_truth` 标注，原始标注包含文字及框。
- 性质：真实采集收据；公开版按印度尼西亚法规移除了部分字段。语言不是中文。
- 来源事实：CC BY 4.0。

### 9. SROIE

- 官方挑战页：[ICDAR 2019 SROIE / Robust Reading Competition Challenge 13](https://rrc.cvc.uab.es/?ch=13&com=downloads)
- 官方论文：[ICDAR 2019 Competition on Scanned Receipt OCR and Information Extraction](https://arxiv.org/abs/2103.10213)
- 下载：在官方 RRC Challenge 13 的 Downloads 页面注册/登录后下载各任务数据包。
- 规模：1,000 张英文扫描收据；官方竞赛覆盖文本定位、OCR 和关键信息抽取三项任务。
- 格式：JPG 图片；文本定位/识别标注使用含四边形和转录文本的 TXT，关键信息包括公司、地址、日期、总额等字段。
- 性质：真实扫描收据；官方资料未声明已脱敏。
- 适用：餐饮/零售扫描件、传统 OCR 与基础 KIE 基准。
- 来源事实：官方挑战页未列出独立开放许可证，下载受 RRC 注册页面条件约束。

### 10. DocILE

- 官方网站：[DocILE Benchmark](https://docile.rossum.ai/)
- 官方仓库：[rossumai/docile](https://github.com/rossumai/docile)
- 官方论文：[DocILE: Document Information Localization and Extraction](https://arxiv.org/html/2302.05658)
- 下载：先在官网取得 secret token，然后使用官方脚本；只需要真实已标注训练/验证集时可执行：

  ```bash
  git clone --depth 1 https://github.com/rossumai/docile.git
  cd docile
  ./download_dataset.sh SECRET_TOKEN labeled-trainval data/docile --unzip
  ```

  合成集支持 `synthetic-chunk-0` 至 `synthetic-chunk-9` 分块下载，每块 10,000 份文档；不必一次拉取全部。
- 规模：6,680 份已标注真实商业文档（8,715 页，5,180 train / 500 val / 1,000 test）；100,000 份一页合成文档；另有 932,467 份未标注真实文档、约 340 万页。
- 类型：税务发票、订单、采购订单、收据、销售订单、形式发票、贷项/借项通知、utility bill 等；提供行项目和 55 个字段类别。真实来源还包含与政治电视/广播广告有关的公开发票、订单和合同。
- 格式：英语 PDF/页面图像、JSON 标注、预计算 OCR 文字框；真实文档经过旋转/去倾斜等预处理并以 150 DPI 提供。
- 性质：真实集和合成集分开提供；真实文档来自 UCSF Industry Documents Library 和 FCC Public Inspection Files 等公开来源。
- 适用：咨询/服务/广告类通用商业发票、多页表格、明细行，以及英文 utility bill；不保证本项目每个费用类别都有独立标签。
- 来源事实：仓库代码使用 MIT，不应把代码许可证等同于数据集授权；数据下载需要官网 token 并按官网访问条件使用。

## 合成发票补充集

### 11. FATURA

- 官方数据页：[Zenodo FATURA](https://zenodo.org/records/8261508)
- 官方论文：[FATURA: A Multi-Layout Invoice Image Dataset for Document Analysis](https://arxiv.org/html/2311.11856)
- 直接下载：[FATURA.zip](https://zenodo.org/api/records/8261508/files/FATURA.zip/content)
- 下载命令：

  ```bash
  curl -L -o FATURA.zip https://zenodo.org/api/records/8261508/files/FATURA.zip/content
  ```

- 规模：10,000 张合成英文发票图片，50 个版式、每版式 200 张；另含 30,000 个 JSON 标注文件。
- 格式：JPG；原始、COCO 和 Hugging Face/LayoutLM 三种 JSON 标注，含 24 类字段、文字、边界框和类别。
- 大小：363,504,027 字节，MD5 `a25c4f9292630f94774e0ca29d121e82`。
- 性质：完全合成；模板受真实发票启发，发送方、接收方、商品等内容为随机生成。
- 适用：大量通用发票布局、明细表及字段定位回归；不能验证真实拍摄噪声或中国票据语义。
- 来源事实：CC BY 4.0。

## 建议的最小下载顺序

如果目标是补足桌面目录之外、且仍属于当前 V1 的测试类型，可以按以下顺序取样：

1. 优先保留税务总局 PDF/PNG 电子票样和现有国内类别代表图片。
2. 每个实际使用类别补 1～3 张脱敏的国内电子发票，并人工标注类别、发生日期、价税合计和说明。
3. 只有新增纸质出租车票、国外收据或复杂拍照票据需求时，才重新下载 FuxiJia、WildReceipt、CORD、SCID 等数据集。

建议将真实票据测试集放在不提交 Git 的本地目录，并为每张样本维护 `source`、`dataset_version`、`document_type`、`real_or_sample`、`desensitized`、`expected_fields` 和 `checksum`。即使本地测试不受当前授权决策影响，这些元数据也能防止将官方票样、真实票据和合成数据混在同一准确率统计中。

## 明确的空白与排除项

- 未找到符合“第一方公开来源 + 可下载真实样本”条件的中国酒店账单、通讯账单、咨询/广告/劳务/会议发票、租赁/物业/水电票据专集。
- 这些类别大多共用通用增值税/数电发票版式；要测分类效果，必须让样本的明细文字真实覆盖相应服务，而不是只换发票底图。
- EPHOIE 是中文考试卷抬头数据，不是票据；不建议为了本任务下载。
- PaddleOCR 文档中的 38 张小集未披露真实/合成状态，所以没有计入“真实票据”覆盖率。
- CUTIE 论文使用过酒店及餐饮/娱乐收据，但 WildReceipt 官方论文指出 CUTIE 数据并未公开，因此不列作可下载来源。
