# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [1.0.0] - 2026-11-22

第一个可以给别人用的版本。从「我自己电脑上的一堆脚本」整理成可安装的包。

### Added

- 打包成 PyPI 风格的包：`pip install -e ".[render]"`，三个命令入口
  `sgs-zhanbao` / `sgs-zhanbao-local` / `sgs-zhanbao-live`
- `doctor` 子命令：一次性自检依赖、素材、字体、密钥、浏览器
- 配置层：`.env` 支持 + 环境变量 + CLI 参数三级覆盖，
  可换 LLM 网关（任何兼容 OpenAI Chat Completions 的）
- 工作目录与包分离：包内只放提示词/术语表/素材，产物永远落在用户目录
- 素材可替换：`SGS_ART` / `--art`，不用重新打包
- **默认版式素材改为纯代码绘制**（`gen_default_art.py`），
  不含第三方美术，因此可以随 MIT 代码分发
- 输入格式扩展：腾讯会议逐字稿、`[HH:MM:SS]` 方括号稿、纯文本，自动识别；
  校正稿跟着输入格式写回
- 字体自动发现：Windows / macOS / Linux 常见楷体路径，找不到就退回浏览器默认
- 200+ 个测试（全部打桩 LLM，不联网不花钱）+ GitHub Actions CI
- 文档：架构说明、版式与素材、输入格式、自动化边界、每日操作手册、贡献指南
- 授权分层说明 `NOTICE`：代码 MIT，素材/字体/术语表各自另说

### Changed

- **校对替换改为单次扫描、长词优先**。逐条替换会互相打架：
  「丈八蛇矛→丈八长矛」之后再跑「丈八→丈八蛇矛」会得到「丈八蛇矛长矛」。
  同时修掉 `applied` 计数跨段累加的问题
- 终审的 `index` 改成全局下标（原来写的是批内下标，第二批之后会复核错条目）
- LLM 重试：4xx 里只重试 408/409/429；指数退避加抖动；
  `chat_json` 解析失败时带上下文再要一次（兼容不支持 `response_format` 的网关）
- JSON 兜底解析加强：markdown 围栏、前后缀说明、尾逗号、全角引号、行注释
- 渲染不再单列一行日期（标题里已经有 `11.5`，重复）
- 目标 PNG 被占用时：先原地重试，再带时间戳另存，最后仍失败则保留临时文件并说明原因
  （原来会静默返回一个不存在的路径）
- `models.from_dict` 对类型做就近转换并丢弃未知键，手改的 `report.json` 也能读
- 短稿（<6000 字）自动跳过分块摘要，省一半 token
- 提取结果为空时直接报错，而不是出一张空图

### Removed

- 仓库不再包含：字体文件、模型权重、真实逐字稿、衍生自官方美术的素材、历史产物
- `templates/report.css`（v1 版式的遗留，已不被引用）
- `render_v2.py`（合并进 `render.py`）

## [0.x] - 2026-11

内部迭代，未发布。

[1.0.0]: https://github.com/chenlockyer/sgs-zhanbao/releases/tag/v1.0.0
