# 三国杀赛事战报生成工作流

把一场赛事直播的**转写稿**，自动变成一张能直接发的**战报长图**。

```
转写稿 txt ──► 文字校对 ──► 结构化提取 ──► HTML/CSS 渲染 ──► PNG 长图
              (独立上下文)   (DeepSeek)      (Playwright)
```

三步各自独立、可单独运行、中间产物可人工修改。纠错和写稿**彻底解耦**，
所以写稿模型不会被错字污染，改比分也不用重跑校对。

![成品示例](examples/example.png)

> 上图用的是仓库自带的**代码绘制默认素材**。作者日常用的官方衍生素材不在仓库里，
> 见 [NOTICE](NOTICE)。

---

## 30 秒上手

```bash
git clone https://github.com/chenlockyer/sgs-zhanbao.git
cd sgs-zhanbao

python -m venv .venv && .venv/Scripts/activate    # Windows
pip install -e ".[render]"
python -m playwright install chromium

copy .env.example .env      # 填入 DEEPSEEK_API_KEY

# 用仓库自带的示例数据跑一遍（不花钱、不联网，只验证渲染链路）
sgs-zhanbao render --data examples/sample_report.json --out output/demo.png
```

跑真实转写稿：

```bash
sgs-zhanbao run --transcript voice2text/你的逐字稿.txt \
                --score "第一场：开机大队 14 : 10 败者食尘" \
                --date 2026-11-05
```

产物：

| 文件 | 内容 |
|---|---|
| `voice2text/<名>.corrected.txt` | 校正后的逐字稿 |
| `voice2text/<名>.corrections.json` | 修正清单（含待确认项，**不进战报正文**） |
| `data/<日期>/report.json` | 结构化数据，可手改后重出图 |
| `output/SGL战报_<日期>.png` | 成品长图（1856px 宽） |
| `output/SGL战报_<日期>.html` | 渲染用的单文件 HTML，可浏览器打开调样式 |

出问题先跑自检：

```bash
sgs-zhanbao doctor
```

---

## 命令

```bash
sgs-zhanbao run        # 校对 → 提取 → 出图（一条命令跑完）
sgs-zhanbao proofread  # 只做文字校对
sgs-zhanbao extract    # 只做结构化提取
sgs-zhanbao render     # 只重出图（改了模板/JSON 后用，不花 token）
sgs-zhanbao doctor     # 自检依赖 / 素材 / 字体 / 密钥
```

常用参数：

```bash
--limit 2          # 校对只跑前 2 块（试跑，省 token）
--reuse            # 已有校正稿时跳过校对
--no-proofread     # 不校对直接提取（不推荐）
--root <目录>      # 指定工作目录（等价环境变量 SGS_HOME）
--glossary <yaml>  # 换自己的术语表
--prompts <目录>   # 换自己的提示词
--art <目录>       # 换自己的版式素材
--font <ttf>       # 换自己的正文字体
```

本地录音 / 离线转写那条链路（不依赖腾讯会议）：

```bash
sgs-zhanbao-local devices      # 列出可用的回环设备
sgs-zhanbao-local record --out match.wav
sgs-zhanbao-local transcribe --wav match.wav
sgs-zhanbao-local stream       # 边录边转
sgs-zhanbao-local auto         # 录音→转写→战报→出图
```

直播编排：

```bash
sgs-zhanbao-live prep          # 打印赛前准备清单
sgs-zhanbao-live live --wait   # 轮询等到开播提示
sgs-zhanbao-live watch         # 盯住 voice2text/ 发现新稿子就提示
```

---

## 目录结构

```
src/sgs_zhanbao/
  cli.py            命令入口（run / proofread / extract / render / doctor）
  config.py         配置、路径解析、.env、密钥、字体发现
  ingest.py         转写稿解析、切块、写回校正稿
  proofread.py      转写稿 → 校正稿 + 修正清单（唯一的纠错入口）
  extract.py        校正稿 → 结构化数据（分块摘要 + 最终提取）
  llm.py            Chat 封装（重试 / JSON 兜底解析 / 用量统计）
  models.py         战报数据结构
  render.py         ReportData → HTML → PNG（固定头 / 拉伸正文 / 固定尾）
  build_art.py      从三张源图重建版式切片
  gen_default_art.py 生成仓库自带的代码绘制默认素材
  record.py         WASAPI 环回录音
  asr.py            SenseVoice 离线转写
  stream_asr.py     流式实时转写
  bilibili.py       直播间状态检测
  live.py           直播工作流编排
  wemeet*.py        腾讯会议窗口操作（半自动，见「自动化边界」）
  data/
    prompts/        三个提示词（校对 / 终审 / 提取）
    glossary.yaml   领域术语表（校对依据，可持续补充）
    art/v2/         head.png / mid.png / tail.png

scripts/build_art.py    薄封装：不 pip install 也能重建素材
examples/               示例转写稿 + 示例 report.json + 成品图
tests/                  200+ 个测试（不联网、不花钱）
docs/                   架构说明、版式原理、输入格式、常见问题
```

---

## 为什么这么设计

三句话：

1. **纠错和写稿是两种判断尺度**，放一个上下文里必然互相拖累。
   校对要保守（没把握不改、玩梗必须保留、术语按表还原），
   写稿要放开（把关键回合写透）。拆开后各对各的标准负责。
   → [docs/架构说明.md](docs/架构说明.md)

2. **文字必须由代码渲染**，不能用生图模型。
   生图模型出中文会大量变形；HTML/CSS 渲染文字 100% 准确、版式精确可控、
   内容变了只换数据不动版式。
   → [docs/版式与素材.md](docs/版式与素材.md)

3. **转写稿是唯一绕不开的人工环节**，所以整条链路设计成「半自动最稳」。
   每天只需要动 2 下（开始共享、导出稿子），比全自动方案更可靠，
   也不会半夜悄无声息地失败。
   → [docs/自动化边界.md](docs/自动化边界.md)

---

## 配置

优先级：**命令行参数 / 环境变量 > `.env` > 内置默认值**。

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | — | 必填 |
| `SGS_HOME` | 当前目录 | 工作目录（读写产物都落这里） |
| `SGS_LLM_BASE_URL` | `https://api.deepseek.com` | 任何兼容 OpenAI 协议的网关 |
| `SGS_LLM_MODEL` | `deepseek-chat` | 模型名 |
| `SGS_LLM_TEMPERATURE` | `0.35` | 战报要稳定，不要飘 |
| `SGS_LLM_JSON_MODE` | `1` | 是否用 `response_format=json_object` |
| `SGS_GLOSSARY` | 包内 | 术语表路径 |
| `SGS_PROMPTS` | 包内 | 提示词目录 |
| `SGS_ART` | 包内 | 版式素材目录 |
| `SGS_FONT` | 系统楷体 | 正文字体路径 |
| `SGS_REPORTER` | 战报记者：王战答疑团-江南 | 记者署名 |
| `SGS_SCALE` | `2.0` | 出图倍率 |

---

## 开发

```bash
pip install -e ".[dev]"
pytest                    # 200+ 个测试，不联网、不花钱
ruff check src tests scripts
```

测试全部打桩 LLM，跑起来很快（约 40 秒，含真实浏览器渲染测试）。
改代码前请先读 [CONTRIBUTING.md](CONTRIBUTING.md)。

---

## 授权与素材说明（重要）

* **代码**：MIT，见 [LICENSE](LICENSE)
* **仓库自带的默认版式素材**：由 `gen_default_art.py` 纯代码绘制，不含第三方美术
* **作者日常用的官方衍生素材**：**没有提交进仓库**，版权不在本项目手上
* **字体**：仓库不含任何字体文件；渲染时用你本机已安装的楷体

完整分层说明见 [NOTICE](NOTICE)。

本项目与《三国杀》官方没有任何关联。
