# 参与贡献

## 环境

```bash
git clone https://github.com/chenlockyer/sgs-zhanbao.git
cd sgs-zhanbao
python -m venv .venv && .venv/Scripts/activate
pip install -e ".[dev,render]"
python -m playwright install chromium
cp .env.example .env      # 填 DEEPSEEK_API_KEY（只有真跑流水线才需要）
```

Python 3.9+。开发用 3.12/3.13 都行。

## 跑测试

```bash
pytest                        # 约 40 秒
ruff check src tests scripts
```

**测试不联网、不花钱**：所有 LLM 调用都打桩（见 `tests/conftest.py` 的 `FakeLLM`），
渲染测试在 playwright 不可用时自动 skip 而不是 fail。

加新代码请一并加测试。这个项目最容易出问题的就是「边界情况把图搞坏」，
所以下面几类测试是必须有的：

* 解析：畸形行、断行、BOM、空文件、时间戳格式变体
* 校对：`wrong` 不在原文里、`wrong == right`、长短词互相打架、终审否决
* 结构：模型多给/少给/给错类型的字段
* 渲染：接缝、底部信息区高度、目标文件被占用

## 代码风格

* `ruff` 是唯一的 lint，配置在 `pyproject.toml`；提交前先跑一遍
* 行宽 100，中文注释优先（这个项目的所有设计约束都写在注释里，别删）
* 模块级 docstring 要说清楚**为什么这么设计**，不只是「这个模块干什么」
* 注释里写踩过的坑（例：「早先版本是拿头部末行拼的，边框会偏深」）
  —— 这类信息比代码本身更有价值

## 提交

```bash
git checkout -b feat/你的分支名
git commit -m "校对：长词优先改为单次扫描，避免替换结果被二次改写"
```

commit message 用中文，一句话说清**改了什么 + 为什么**。
一个 commit 只做一件事。

提 PR 前：

1. `pytest` 全绿
2. `ruff check src tests scripts` 无输出
3. 真跑一次流水线（有 API key 的话），贴一张成品图到 PR 里
4. 改了版式的话，说明接缝校验的实际数值

## 别做的事

* **不要提交任何模型权重、字体文件、逐字稿、成品图**
  （`.gitignore` 已经挡了，但 PR 前自己再确认一遍 `git status`）
* **不要提交衍生自《三国杀》官方美术的素材**。仓库自带的默认素材是纯代码绘制的，
  这是它能随 MIT 分发的前提。要换素材请走 `SGS_ART`，见 [NOTICE](NOTICE)
* 不要把 LLM 调用塞进测试
* 不要在 `extract` 的提示词里加纠错指令 —— 那是被专门拆出去的东西，
  理由见 [docs/架构说明.md](docs/架构说明.md)

## 报 issue

带上这些能省很多来回：

```bash
sgs-zhanbao doctor > doctor.txt      # 环境自检
```

* 你的 Python 版本、操作系统
* `doctor` 的输出
* 报错的完整 traceback
* 涉及版式问题：贴一张出问题的图（注意打码掉不该公开的内容）
* 涉及校对问题：贴那一段原文 + 修正清单里对应的那条
