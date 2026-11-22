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
- **默认版式素材由代码绘制**（`gen_default_art.py`），属本项目原创，随 MIT 分发
- 输入格式扩展：腾讯会议逐字稿、`[HH:MM:SS]` 方括号稿、纯文本，自动识别；
  校正稿跟着输入格式写回
- 字体自动发现：Windows / macOS / Linux 常见楷体路径，找不到就退回浏览器默认
- 200+ 个测试（全部打桩 LLM，不联网不花钱）+ GitHub Actions CI
- 文档：架构说明、版式与素材、输入格式、自动化边界、每日操作手册、贡献指南
- 授权说明 `NOTICE`：代码与素材均 MIT；字体不随仓库分发，用你本机已装的楷体

### Changed

- **配色收进 `config.Palette`，成为唯一入口**。原先 CSS 里的色值写死在
  `render.py` 的字符串里，同时 `config.Theme` 声称「改这里就能换皮」却从没被
  渲染层读过 —— 改它静默无效，表现就是「换了素材配色，文字还是玫红」。
  现在删掉 `Theme`，`Palette` 的每个字段都进 CSS，并可用
  `SGS_COLOR_<字段大写>` 单独覆盖。**默认值与旧 CSS 逐字节一致**，
  不改配置时出图完全不变（已用示例图字节比对验证）
- 删除死 CSS：`table.tb th`（渲染层早就不输出 `<th>`）、`.date`、`.note`
  及对应的 `FS_DATE` / `FS_NOTE` / `Palette.note`
- 接缝测试改为「整行页底色占比」判据，不再猜具体列坐标，因此与素材形状无关；
  且「判据能识别缝隙」是「测试通过」的必要条件，不会空过
- `doctor` 检查 chromium 时真的启动一次浏览器（原先只查 `executable_path`
  存在，而 playwright 实际启动的是 headless shell，另一个二进制）
- 校对替换改为单次扫描、长词优先。逐条替换会互相打架：
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

- 仓库不再包含：字体文件、模型权重、真实逐字稿、历史产物与旧版素材
- `templates/report.css`（v1 版式的遗留，已不被引用）
- `render_v2.py`（合并进 `render.py`）

## [0.x] - 2026-11

内部迭代，未发布。

[1.0.0]: https://github.com/chenlockyer/sgs-zhanbao/releases/tag/v1.0.0
