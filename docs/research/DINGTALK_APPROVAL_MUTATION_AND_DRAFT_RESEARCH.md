# 钉钉审批提交后修改、评论附件与草稿能力调研

调研日期：2026-09-03（Asia/Shanghai）
资料口径：只使用钉钉开放平台官方文档，以及 `com.aliyun:dingtalk` 官方生成 SDK 2.2.58 的类型和客户端源码。未获得官方明确说明的行为均列为待 PoC，不按可用能力设计。

## 结论

| 问题 | 结论 | 置信度 |
|---|---|---|
| 已提交或运行中的普通 OA 审批，能否用标准开放 API 修改原表单字段或原附件控件 | 不能按该能力设计。官方创建接口明确说明，发起审批实例后无法通过 API 修改审批实例信息。附件也是表单控件值，因此不能把“提交后补进原附件栏”视为标准能力 | 高 |
| 能否给已有审批追加一条带文件的评论 | 可以。评论请求有可选 `file`，其下支持多个 `attachments`；这会新增评论/操作记录，不会修改原表单附件控件 | 高 |
| 能否先由后端创建钉钉草稿，稍后让员工确认并正式提交 | 当前公开的普通 OA OpenAPI 和官方 SDK 中未发现“创建草稿 → 再提交”的接口组合。可靠方案是在本系统保存草稿，员工确认时才调用钉钉“发起审批实例” | 中高 |
| OA 高级版能否修改运行中实例变量 | 官方 SDK 和官方页面存在“更新流程表单审批实例”高级版接口，但其适用模板、实例状态和附件控件支持范围未在本次可见资料中充分确认；不得作为普通版方案承诺 | 中 |

对本项目的直接影响：

```text
如果走“先在智能报销确认，再创建 OA”：
  在本系统保存草稿 → 生成 Excel/准备附件 → 员工最终确认 → 一次性创建 OA

如果走“OA 已经提交，再补 Excel”：
  不改 OA 原附件字段 → 把 Excel 作为一条审批评论的附件追加
```

## 一、已提交审批能否修改字段或原附件

### 1. 普通 OA 标准接口：不能按可修改设计

[发起审批实例](https://open.dingtalk.com/document/development/create-an-approval-instance) 的“使用场景”说明明确写明：

> 发起审批实例后，无法通过 API 修改审批实例信息。

创建时，文本、金额、关联审批单和附件等内容都通过 `formComponentValues` 一次性写入。因此，审批已经创建后：

- 不能依赖普通开放 API 修改项目、日期、金额、说明等原表单字段；
- 不能依赖普通开放 API 给原来的“附件”表单控件追加 Excel；
- 若原始内容有误，正常处理方式是申请人撤销后重新发起，或者按公司规则在评论中补充说明/文件；
- “添加评论”和“修改表单字段”是两种不同操作，不能混为一谈。

官方普通 SDK 中名为 `UpdateProcessInstanceRequest` 的类型也不能证明可修改表单。该类型只有：

```text
processInstanceId
status
result
notifiers
```

对应客户端方法的摘要是“更新实例状态”，没有 `formComponentValues`、`variables` 或附件字段。这类状态同步接口不能用于修改钉钉官方 OA 的原表单内容。
它对应的是 `/v1.0/workflow/processCentres/instances`，不能与 OA 高级版的实例变量更新接口混用。

### 2. OA 高级版存在一个例外候选，但不能直接承诺

钉钉官方文档目录中存在 [更新流程表单审批实例](https://open.dingtalk.com/document/development/api-premiumupdateprocessinstancevariables)。官方 Java SDK 2.2.58 中对应：

```text
Client.premiumUpdateProcessInstanceVariablesWithOptions(...)
PUT /v1.0/workflow/premium/processInstances
```

SDK 方法摘要为“更新审批实例（OA高级版专享）”。请求类型是：

```java
PremiumUpdateProcessInstanceVariablesRequest
  String opUserId                 // 必填
  String processInstanceId        // 必填
  String processCode
  String remark
  List<Variables> variables       // 必填

Variables
  String id                       // 控件/变量 ID，必填
  String value                    // 必填
  String bizAlias
  String extValue
```

这说明 OA 高级版存在“更新实例变量”的正式接口，但目前证据不足以推导出以下结论：

- 普通 OA 版本可以调用；
- 当前“差旅费报销申请”模板一定支持；
- 审批中的任意状态都可以改；
- 附件控件、关联审批控件都允许修改；
- 修改后是否重新触发条件分支、审批人计算或审计记录。

本项目本身不需要审批提交后改字段，因此不建议为这项高级版能力增加依赖。如果以后确有需求，应先确认公司 OA 版本和该接口权益，再用测试模板逐控件 PoC。

### 3. 证据位置

- 官方说明：[发起审批实例](https://open.dingtalk.com/document/development/create-an-approval-instance)，其中明确说明创建后无法通过 API 修改审批实例信息。
- 高级版页面：[更新流程表单审批实例](https://open.dingtalk.com/document/development/api-premiumupdateprocessinstancevariables)。
- 官方 Java SDK 2.2.58：
  - `com/aliyun/dingtalkworkflow_1_0/Client.java`，约第 4207–4255 行：`premiumUpdateProcessInstanceVariablesWithOptions`，HTTP `PUT /v1.0/workflow/premium/processInstances`；
  - `com/aliyun/dingtalkworkflow_1_0/models/PremiumUpdateProcessInstanceVariablesRequest.java`：`opUserId`、`processInstanceId`、`processCode`、`remark`、`variables`；
  - `com/aliyun/dingtalkworkflow_1_0/models/UpdateProcessInstanceRequest.java`：普通“更新实例状态”请求只有状态、结果和通知人相关字段。

## 二、审批评论能否带文件附件

### 1. 可以，且支持多个附件

调用 [添加审批评论](https://open.dingtalk.com/document/development/official-approval-adds-approval-comments)：

```http
POST https://api.dingtalk.com/v1.0/workflow/processInstances/comments
```

官方 SDK 2.2.58 的请求结构如下：

```json
{
  "commentUserId": "申请人的userId",
  "processInstanceId": "目标审批实例ID",
  "text": "费用报销单已生成，请查收附件。",
  "file": {
    "attachments": [
      {
        "spaceId": "审批钉盘空间ID",
        "fileId": "钉盘文件ID",
        "fileName": "差旅费报销单-测试员工.xlsx",
        "fileSize": "12186",
        "fileType": "xlsx"
      }
    ],
    "photos": []
  }
}
```

字段结论：

- `commentUserId`、`processInstanceId`、`text` 是必填字段；不能按“只有附件、没有文字”的请求设计。
- `file` 可选。
- `file.attachments` 是列表，支持一条评论附带多个文件。
- 每个附件需要 `spaceId`、`fileId`、`fileName`、`fileSize`、`fileType` 这些钉盘元数据。
- `file.photos` 是图片列表，与普通文件附件分开。
- 该接口需要 `Workflow.Instance.Write` 权限；官方页面当前标注企业内部应用支持、第三方企业应用不支持，实际应用类型和权限必须在 PoC 前确认。

评论成功后，文件属于这条评论/审批操作记录。它不会变成创建时 `formComponentValues` 中“附件”控件的新值，也不会回到审批详情顶部的原附件栏。

### 2. 文件必须先上传到正确的审批钉盘空间

[审批附件的操作流程](https://open.dingtalk.com/document/development/new-version-of-attachment-approval-process) 和 [获取审批钉盘空间信息](https://open.dingtalk.com/document/development/obtains-the-information-about-approval-nail-disk) 所描述的前置链路是：

1. 后端按用户取得审批钉盘空间。
2. 客户端把文件上传到该空间，取得文件元数据。
3. 后端把元数据传给“添加审批评论”接口。

官方 SDK 对应空间接口为：

```http
POST /v1.0/workflow/processInstances/spaces/infos/query
```

请求是 `userId`（必填）和 `agentId`（可选），响应中返回 `spaceId`。客户端 [uploadAttachmentToDingTalk](https://open.dingtalk.com/tools/explorer/jsapi?id=10318) 会返回：

```text
spaceId, fileId, fileName, fileSize, fileType
```

### 3. 对服务器生成 Excel 的实际缺口

“评论 API 能携带附件”已经明确，但它只接收已上传文件的元数据，不接收服务器文件路径、文件字节或任意下载 URL。

当前官方附件流程强调服务端与客户端 JSAPI 配合；公开的 `uploadAttachmentToDingTalk` 支持表中，H5 微应用明确支持 Android/iOS，Mac/Windows 标为不支持。因此，下面这一步仍需租户 PoC：

```text
服务器生成 Excel
    ↓
如何让 Excel 进入当前员工的审批钉盘空间
    ↓
取得 fileId/spaceId
    ↓
添加带附件评论
```

在没有证实服务端可直接上传生成文件到该审批空间前，不能承诺“服务器静默生成后自动追加评论附件”。保守可行的移动端流程是：让客户端下载或取得生成的 Excel，再由员工在 H5 中确认上传，随后调用评论接口。

### 4. 仍需实测

- `commentUserId` 必须是申请人、审批参与人，还是应用权限范围内任意在职成员。
- RUNNING、COMPLETED、TERMINATED 三种实例状态是否都允许追加评论附件。
- 单条评论的附件数量、大小、文件类型限制。
- Android、iOS 上 Excel 上传和审批内预览/下载是否一致。
- 服务端存储 API 是否能在公司租户中把生成的 Excel 直接写入审批专用空间；当前公开审批流程不能证明这一点。

### 5. 证据位置

- 官方接口：[添加审批评论](https://open.dingtalk.com/document/development/official-approval-adds-approval-comments)。
- 官方附件流程：[审批附件的操作流程](https://open.dingtalk.com/document/development/new-version-of-attachment-approval-process)。
- 官方 JSAPI：[uploadAttachmentToDingTalk](https://open.dingtalk.com/tools/explorer/jsapi?id=10318)。
- 官方 Java SDK 2.2.58：
  - `com/aliyun/dingtalkworkflow_1_0/Client.java`，约第 90–128 行：`addProcessInstanceCommentWithOptions`，HTTP `POST /v1.0/workflow/processInstances/comments`；
  - `com/aliyun/dingtalkworkflow_1_0/models/AddProcessInstanceCommentRequest.java`：顶层 `commentUserId`、`processInstanceId`、`text`、`file`；嵌套 `file.attachments[]` 和 `file.photos[]`；
  - `com/aliyun/dingtalkworkflow_1_0/models/GetAttachmentSpaceRequest.java` 和 `GetAttachmentSpaceResponseBody.java`：空间查询入参和 `spaceId` 响应。

## 三、是否存在“先写草稿，再由用户正式提交”的接口

### 1. 当前未发现普通 OA 草稿接口

普通 [发起审批实例](https://open.dingtalk.com/document/development/create-an-approval-instance) 是一次正式创建操作：请求提交 `originatorUserId`、`processCode`、`deptId`、`formComponentValues` 等数据，成功直接返回正式 `instanceId`。该请求没有 `draft`、`saveOnly`、`submitLater` 或同类参数。

对官方 Java SDK 2.2.58 的 `dingtalkworkflow_1_0` 包进行核对：

- `StartProcessInstanceRequest` 没有草稿标记；
- `Client` 中未发现名称或摘要包含 `draft`、`草稿`、`待提交`、`暂存` 的 OA 方法；
- `SaveProcess` 是创建/更新审批模板，不是保存员工审批草稿；
- `SaveIntegratedInstance` / `PremiumSaveIntegratedProcessInstance` 是自有 OA/流程中心外部集成实例，不是钉钉官方 OA 表单草稿；
- `PremiumSaveFormInstance` 是 OA 高级版“数据表单实例”，也不能等同于官方 OA 审批草稿。

因此，当前公开证据不支持下面这种设计：

```text
后端在钉钉生成一个待提交审批
→ 用户打开钉钉原生表单检查
→ 再把同一实例转为正式审批
```

这里的“未发现”不是断言钉钉客户端内部绝对没有草稿机制，而是说明公开 OpenAPI 没有可依赖的正式接口。若钉钉技术支持提供专享接口，必须获得正式文档和权限说明后另行评估。

### 2. 推荐实现：草稿保存在本系统

本项目应把草稿状态放在自己的数据库：

```text
DRAFT       本系统草稿，可反复修改
REVIEWING   OCR完成，员工正在核对
READY       已确认，正在准备Excel和附件
SUBMITTING  已开始调用钉钉创建接口，禁止重复点击
SUBMITTED   已取得钉钉processInstanceId
FAILED      创建失败，可基于原草稿重试
```

只有员工点下最终的“提交差旅费报销申请”后，后端才调用一次钉钉创建接口。这样既不需要修改已提交 OA，也不需要钉钉草稿接口。

还应使用本系统任务 ID、提交内容摘要和 `processInstanceId` 做幂等记录，避免钉钉已创建成功但客户端超时时，员工再次点击产生重复审批。

### 3. 置信度和实测缺口

“公开普通 OA OpenAPI 没有草稿/二阶段提交接口”的置信度为中高，依据是官方创建文档的请求模型和当前官方 SDK 接口面均没有该状态。它属于基于完整公开接口面的否定性结论，仍保留以下核实项：

- 向公司钉钉技术支持确认是否有租户灰度或 OA 高级版专享的“预填/草稿”能力。
- 确认是否存在只能从钉钉原生客户端页面使用、但不开放给 H5/服务端的内部草稿能力。
- 即使有“打开原生发起页”的跳转能力，也要单独确认它是否支持预填全部表单字段和附件；仅能打开模板不等于可创建后端草稿。

## 四、推荐的集成决策

### 目标方式：智能报销确认后创建 OA

本项目最合理的主流程是：

```text
员工在智能报销 H5 填写并上传
→ 本系统保存草稿、OCR、生成 Excel
→ 员工检查金额、关联出差申请和附件
→ 员工点击最终提交
→ 后端一次性调用发起审批实例
→ 成功后只读展示/跳转钉钉审批
```

这条路线不需要提交后修改 OA，也不需要钉钉草稿接口。

### 备用方式：已有 OA 后补 Excel

如果目标审批模板不能通过 API 发起，则备用流程是：

```text
员工先在钉钉发起 OA
→ 本系统生成 Excel
→ 员工确认上传到审批钉盘
→ 后端调用添加审批评论
→ Excel 出现在评论中
```

应明确向业务说明：Excel 在评论中，不是在原表单附件栏中。

## 五、来源清单与复现信息

### 钉钉官方文档

- [发起审批实例](https://open.dingtalk.com/document/development/create-an-approval-instance)
- [添加审批评论](https://open.dingtalk.com/document/development/official-approval-adds-approval-comments)
- [审批附件的操作流程](https://open.dingtalk.com/document/development/new-version-of-attachment-approval-process)
- [获取审批钉盘空间信息](https://open.dingtalk.com/document/development/obtains-the-information-about-approval-nail-disk)
- [上传附件到钉盘/从钉盘选择文件](https://open.dingtalk.com/tools/explorer/jsapi?id=10318)
- [更新流程表单审批实例（OA 高级版）](https://open.dingtalk.com/document/development/api-premiumupdateprocessinstancevariables)

### 钉钉官方 SDK

- Maven 坐标：`com.aliyun:dingtalk:2.2.58`
- [SDK JavaDoc](https://javadoc.io/doc/com.aliyun/dingtalk/2.2.58)
- [AddProcessInstanceCommentRequest 类型](https://javadoc.io/static/com.aliyun/dingtalk/2.2.58/com/aliyun/dingtalkworkflow_1_0/models/AddProcessInstanceCommentRequest.html)
- [workflow Client 类型](https://javadoc.io/static/com.aliyun/dingtalk/2.2.58/com/aliyun/dingtalkworkflow_1_0/Client.html)
- [Maven Central 官方 SDK 源码包](https://repo1.maven.org/maven2/com/aliyun/dingtalk/2.2.58/dingtalk-2.2.58-sources.jar)
- 本次核对的源码包 SHA-256：`7138837b0c681cb6db9debdaabd0b9e66da0771672d9dda1db18684cc787c15a`

## 六、最终判断

1. 普通 OA 审批一旦创建，不应设计成随后再改原字段或原附件。
2. 已有审批可以追加带文件的评论，但必须先取得审批钉盘文件元数据。
3. 当前没有可依赖的公开 OA 草稿 API；草稿应由本系统保存，最终确认后一次创建钉钉审批。
4. OA 高级版更新实例变量是一个例外候选，但不属于当前方案的必要条件，也不能在未做权益和控件 PoC 前用于承诺“可修改附件”。
