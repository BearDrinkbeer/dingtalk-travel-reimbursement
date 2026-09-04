# 钉钉差旅报销权限编码核对

更新时间：2026-09-03

## 可直接搜索的权限标识

| 权限标识 | 本项目中的调用范围 | 核对依据 |
|---|---|---|
| `Workflow.Form.Read` | 读取报销模板 Schema、控件和选项 | 调用表单 Schema 接口时，钉钉真实 403 响应明确返回该缺失权限 |
| `Workflow.Instance.Read` | 查询本人已通过的出差审批、读取审批详情、回读新建 OA | 出差关联和 OA 回读联调 |
| `Workflow.Instance.Write` | 创建差旅费报销 OA | 创建 OA 联调 |
| `Storage.UploadInfo.Read` | 获取审批附件空间的服务器上传信息 | 服务器直传附件联调 |
| `Storage.File.Write` | 提交上传文件，取得可写入审批附件控件的文件 ID | 服务器直传附件联调 |
| `qyapi_base` | 调用 `/topapi/v2/user/getuserinfo`，通过免登码取得 `userId` | [钉钉官方文档](https://open.dingtalk.com/document/development/obtain-the-userid-of-a-user-by-using-the-log-free)的权限详情显示该标识 |
| `qyapi_get_member` | 调用 `/topapi/v2/user/get` 读取员工姓名、部门列表和 `unionId` | [钉钉官方文档](https://open.dingtalk.com/document/development/query-user-details)的“成员信息读权限”详情显示该标识 |
| `qyapi_get_department_list` | 调用 `/topapi/v2/department/get` 读取部门名称 | [钉钉官方文档](https://open.dingtalk.com/document/development/query-department-details0-v2)的“通讯录部门信息读权限”详情显示该标识 |

钉钉的权限标识存在两套格式：新版 Workflow、Storage 接口使用 `Xxx.Yyy.Zzz`，现有 `/topapi/v2/*` 免登和通讯录接口仍显示 `qyapi_xxx`。后者不能自行改写成点号形式，否则在开发者后台无法搜索。

## 不属于权限编码的配置

H5 企业内免登、微应用首页地址、可信域名、应用可见范围和审批模板发起权限，都需要配置，但它们不是 `xxx.yyy.zzz` 格式的接口权限，因此不放进权限编码清单。

如后续把现有 `/topapi/v2/*` 调用迁移为新版通讯录接口，应重新按新版接口文档核对权限，不能继续沿用这三个 `qyapi_*` 标识。
