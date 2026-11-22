# 安全政策

## 这个项目的威胁面

它不联网服务、不监听端口、不碰数据库。风险集中在三处：

1. **API 密钥**：`DEEPSEEK_API_KEY` 只从环境变量 / `.env` 读取，
   `.env` 已在 `.gitignore` 里。代码不会把密钥写进任何产物文件，
   也不会打印全文（`doctor` 只打印前 6 后 4）。
2. **转写稿内容**：逐字稿、校正稿、`report.json`、成品图都落在你指定的
   `SGS_HOME` 下，**不会上传到除 LLM 网关以外的任何地方**。
   跑 `run` 时稿件内容会发给 LLM 网关 —— 这是这个工具的工作原理，
   介意请把 `SGS_LLM_BASE_URL` 指向本地部署的模型。
3. **模型输出**：LLM 返回的 JSON 会被渲染成 HTML。渲染层所有动态文本都过
   `html.escape()`，且成品 HTML 只从 `file://` 加载、素材全部内联，
   不请求任何外部 URL。

## 请报告的

* 密钥被写进日志 / 产物 / 报错信息
* 渲染层出现未转义的注入点（能构造出让 HTML 执行脚本的 `report.json`）
* 依赖里的已知漏洞
* 任何会让用户数据发到第三方的行为变化

## 请不要报告的

* 你自己把 `.env` 提交到公开仓库
* 你用 `--no-proofread` 跳过校对导致战报里有错字
* 腾讯会议 / B站 接口变更（那是外部服务，见 `docs/自动化边界.md`）

## 方式

开一个 **私密 issue**（GitHub 的 security advisory 也可以）：
<https://github.com/chenlockyer/sgs-zhanbao/security/advisories/new>

我会尽量在 7 天内回复。修复后会在 [CHANGELOG](CHANGELOG.md) 里说明，
不会在公开 issue 里贴复现细节。
