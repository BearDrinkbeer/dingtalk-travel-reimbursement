# 钉钉审批附件的存储归属与生命周期

> 调研日期：2026-09-03
> 范围：审批附件空间、服务端上传、提交文件、创建 OA、预览授权和删除文件。
> 资料限制：只采用钉钉开放平台、阿里官方 SDK 和本租户只读 PoC 结果；官方没有明确说明的地方标为“推断”或“未公开”。

## 结论

1. 实测文件上传到由审批空间接口返回的**审批钉盘空间**，不是服务器本地，也不是员工手工选择的普通“个人钉盘/我的文件”目录。文档不保留测试企业返回的具体 `spaceId`。
2. 更准确地说，这是钉钉按“企业内部应用 + 当前企业员工”提供的审批专用空间：官方接口要求 `agentId` 和 `userId`，作用是取得审批空间并给该员工授予上传权限。它位于测试企业的审批业务上下文中，但公开接口页没有展示底层 `ownerType`；在没有 `Storage.Space.Read` 权限时，不宜把它说成普通企业共享盘，也不宜说成个人“我的文件”。
3. OA 的附件控件保存的是 `spaceId + fileId + 文件元数据`，并非把文件复制一份进审批数据库。已经关联 OA 的文件不应主动删除；删除底层钉盘文件很可能让审批详情中的附件无法预览或下载。这一点是根据官方引用式接口契约得出的直接推断，官方没有提供一句“删除后必然失效”的文字保证。
4. 正常业务中无需人工清理已经进入 OA 的附件。只有“已经 commit、但最终没有创建/关联任何 OA”的孤儿文件适合做补偿清理。
5. 实测产生过两类文件：一类已 commit 但未关联 OA，可在确认无 OA 引用后清理；另一类已关联测试 OA，不建议单独删除。文档不保留测试文件名或审批标识。

## 一、空间属于谁

### 官方事实

[获取审批钉盘空间信息](https://open.dingtalk.com/document/development/obtains-the-information-about-approval-nail-disk) 的官方说明是：调用接口“获取审批钉盘空间的 ID 并授予当前用户上传附件的权限”。接口为：

```http
POST /v1.0/workflow/processInstances/spaces/infos/query
```

请求包含当前企业员工的 `userId` 和应用的 `agentId`，且官方页面列出的支持类型是“企业内部应用”。响应给出审批用的 `spaceId`。

[uploadAttachmentToDingTalk](https://open.dingtalk.com/tools/explorer/jsapi?id=10318) 则以指定 `spaceId` 上传，并返回 `spaceId/fileId/fileName/fileSize/fileType`。另一个独立 JSAPI [chooseDingTalkDir](https://open.dingtalk.com/tools/explorer/jsapi?id=10319) 才是让用户从“企业空间或个人空间”主动选择目录。我们的服务端 PoC 没有调用目录选择器，而是直接使用审批接口返回的空间。

### 因而可以确定

- 它是云端钉盘文件，不在员工电脑本地。
- 它是审批业务专用空间，不是员工手工选择的个人“我的文件”目录。
- `userId` 的作用至少包含上传授权；不能仅因请求里有员工 `userId` 就断言空间是员工私人所有。
- `agentId` 属于当前企业内部应用，因此该空间处于当前企业/应用的审批上下文中。

### 尚不能从公开资料确定

存储通用 API 支持读取空间详情，包括 `ownerType/ownerId/corpId/scene/sceneId`。本租户只读查询该详情时，钉钉要求额外的 `Storage.Space.Read` 权限；当前未申请，因此没有读取这些底层字段。这个权限对正式上传和创建 OA 不是必需的，不建议只为给空间贴“个人/公司”标签而扩大生产权限。

## 二、OA 是否保存一份附件副本

官方 [授权预览审批附件](https://open.dingtalk.com/document/development/official-authorized-preview-approval-attachment) 明确要求：

- 传入审批实例 `processInstanceId` 和审批附件 `fileId`；
- `fileId` 必须与发起审批时附件控件里的 `fileId` 一致，否则会返回无权限；
- 每次预览前，都要给用户授权后再调用钉盘预览 JSAPI。

这说明审批记录与钉盘文件之间使用同一个文件标识建立关联。发起审批时提交的附件值也是 `spaceId/fileId/fileName/fileSize/fileType`，并不携带文件字节。

因此：

- **官方事实**：审批预览依赖发起审批时记录的同一个 `fileId`。
- **高可信推断**：OA 保存的是对审批钉盘文件的引用，不是另存一份与原文件完全独立的副本。
- **高可信推断**：删除这个底层文件后，原 OA 虽仍可能显示附件名称，但预览/下载很可能失败。

未经一个可丢弃审批的破坏性 PoC，不应宣称具体 UI 会显示什么错误。当前没有执行删除测试。

## 三、三种上传状态的处理

| 状态 | 官方接口层面的含义 | 建议处理 |
|---|---|---|
| 取得上传地址，尚未 commit | 上传信息接口返回临时签名、`expirationSeconds` 和 `uploadKey`；尚未返回正式 dentry/fileId | 失败后直接放弃并重新申请上传。单文件流程此时没有可供业务调用的 dentry 删除目标。官方未公开临时对象的清理期限，不要自定义一个 TTL 当成官方保证 |
| 已 commit，尚未关联 OA | `files/commit` 已创建正式 dentry，并返回文件 ID、空间 ID、名称、大小等 | 若能确定 OA 创建失败，可把它当孤儿文件做补偿删除；若创建请求超时、结果不明，应先按幂等记录或查询结果确认没有 OA，再删除 |
| 已 commit，且已关联 OA | OA 表单记录了同一个 `spaceId/fileId`，预览授权也校验该审批实例与 fileId 的关联 | 正常情况下保留，不做自动清理。审批完成、拒绝或撤销也不要擅自删附件，除非业务明确允许破坏历史附件访问 |

官方 Go SDK 的 [Storage 1.0 客户端源码](https://raw.githubusercontent.com/alibabacloud-go/dingtalk/master/storage_1_0/client.go) 可验证：

- `CommitFile` 调用 `POST /v1.0/storage/spaces/{spaceId}/files/commit`，commit 后返回 dentry；
- `DeleteDentry` 调用 `DELETE /v1.0/storage/spaces/{spaceId}/dentries/{dentryId}`；
- 删除参数有 `toRecycleBin`；
- SDK 同时提供回收站查询、恢复和清空接口。

这也表明 commit 是“把上传内容登记成正式文件”的边界；删除是之后单独发生的存储操作，并不会自动修改已经提交的 OA 表单值。

## 四、是否需要手动清理本次测试

### 已关联测试 OA 的文件

不需要，也不建议手动删文件。它已经被测试 OA 引用。若测试审批仍在运行，首先应撤销/终止审批，避免继续占用审批人的待办；附件是否保留，应按是否需要测试证据和历史留痕决定。撤销审批不等于可以安全删除附件，官方没有说明撤销后会复制或自动清理文件。

### 已 commit 但未关联 OA 的文件

纯上传 PoC 文件未关联 OA，可在确认没有审批引用后清理，以免长期留下无业务归属的测试文件。当前调研没有执行删除。

### 生产建议

不要设计“每天清理所有审批空间旧文件”的任务。建议维护本系统自己的上传记录：

```text
UPLOAD_PENDING -> COMMITTED -> OA_LINKED
                       \-> ORPHAN_CONFIRMED -> CLEANED
```

只有 `ORPHAN_CONFIRMED` 才允许删除。`OA_LINKED` 永不由通用清理任务删除。对于创建 OA 超时，先用请求幂等键、业务流水号或回查实例确认结果，再决定是否补偿删除，避免 OA 已创建但附件被误删。

## 五、公开资料未给出的保证

截至调研日期，钉钉公开审批文档和官方 SDK 没有明确给出：

- 未 commit 的 PUT 对象在服务端保留多久；
- 审批完成后，附件是否按固定天数自动删除；
- 删除已关联文件后，审批页面的确切错误样式；
- 审批专用空间底层 `ownerType` 的固定枚举值。

因此上线设计不应依赖“钉钉会自动清理孤儿文件”，也不应依赖“OA 已经复制附件，所以源文件可删”。

## 参考资料

- [获取审批钉盘空间信息](https://open.dingtalk.com/document/development/obtains-the-information-about-approval-nail-disk)
- [审批附件的操作流程](https://open.dingtalk.com/document/development/new-version-of-attachment-approval-process)
- [授权预览审批附件](https://open.dingtalk.com/document/development/official-authorized-preview-approval-attachment)
- [上传附件到钉盘/从钉盘选择文件](https://open.dingtalk.com/tools/explorer/jsapi?id=10318)
- [选择钉盘目录](https://open.dingtalk.com/tools/explorer/jsapi?id=10319)
- [阿里官方 DingTalk Go SDK：Storage 1.0](https://github.com/alibabacloud-go/dingtalk/tree/master/storage_1_0)
- [阿里官方 DingTalk Go SDK：Storage 1.0 客户端源码](https://raw.githubusercontent.com/alibabacloud-go/dingtalk/master/storage_1_0/client.go)
