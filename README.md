# 债券底稿盖章页处理 Skill

一个遵循开放 Agent Skills 目录格式的本地文件处理技能。它帮助支持 `SKILL.md` 的智能体完成以下闭环：

1. 从一批 `.doc` / `.docx` 债券底稿中提取最后一页，并由用户选择是否按 Word 文件哈希排除重复文件。
2. 接收客户返回的乱序、缺页、可能为扫描件的回章页 PDF，完成标题匹配并生成已落日期的确认稿。
3. 等待用户检查或用 PDF 软件修改确认稿；用户明确确认后，再按已保存的页码关系回拼到底稿 PDF。

核心文件在 [`.agents/skills/processing-bond-seal-pages`](.agents/skills/processing-bond-seal-pages)。它不是 Codex 专用 Skill；Codex、WorkBuddy、ZCode、CodeBuddy 及其他兼容 Agent Skills / `SKILL.md` 的客户端都可以接入。

## 当前能力边界

支持：

- Windows 和 macOS。
- 每份底稿只有一张盖章页，且固定为最后一页。
- 生成合集前选择保留全部 Word，或按 SHA-256 排除完全相同的 Word。
- 回章页乱序、缺页、扫描页和重复标题。
- 本地 PP-OCRv6 small 标题识别。
- 日期定位双路交叉验证；两套结果不一致时采用更可靠结果并列入重点核对清单。
- 用户确认后直接用 PDF 页面替换 PDF 页面，不重新栅格化回章页。
- HTML 处理清单，不生成高耗资源的页面缩略图。

暂不支持：

- 一份文件包含多张签字页或盖章页的发行文件。
- Linux。
- 判断页面是否已经盖章、印章真伪或盖章主体。
- 把确认后的 PDF 内容写回 Word。

## 重复文件怎么处理

生成待盖章页合集前，Skill 会要求用户选择：

- **保留全部**：每份 Word 都参与转换、盖章、匹配和最终回拼。
- **排除重复**：先计算全部 Word 的 SHA-256。哈希相同的文件只保留路径排序最前的一份，其余文件不转换、不进入合集、不参与匹配，也不生成最终文件。

去重只看 Word 文件本身的哈希，不按文件名、标题或页面外观判断。标题相同但哈希不同的底稿不会被排除。被排除的路径会记录在 `manifest.json` 的 `excluded_duplicates` 中。

## 工作流程

```mermaid
flowchart LR
    A["底稿 Word 目录"] --> B["选择是否排除重复文件"]
    B --> C["prepare：处理批次 + seal-pages.pdf"]
    C --> D["客户签字或盖章"]
    D --> E0["date-review：匹配 + 落日期 + 确认稿"]
    E0 --> E["用户检查；可修改内容，但不改变页数和顺序"]
    E --> F["finalize：按保存的映射回拼"]
    F --> G["盖章版 PDF + HTML 清单"]
```

`date-review` 与 `finalize` 必须分两次执行。模型看到确认稿后必须暂停；不能把用户最初的处理请求当成事后确认。

## 推荐模型

这个 Skill 把文件遍历、OCR 路由、匹配、PDF 替换和日期定位交给确定性脚本，模型主要负责选择业务阶段、处理前置条件、停在确认关口以及解释清单。因此不要求模型亲自判断每一个 PDF 坐标，但需要较强的指令遵循和工具使用能力。

- 最低建议：GPT-5.4 `xhigh` 或同等水平的推理模型。
- 推荐：GPT-5.5 `high/xhigh` 或其他厂商同等级的前沿模型。
- 不建议：低推理档、mini/nano 级模型独立执行无人值守批次。它们可以运行脚本，但更容易跳过阶段边界、安装确认或最终人工确认。

模型所在客户端还需要具备：本地文件访问、运行 Python/终端命令、展示命令输出，以及在安装依赖或执行高风险操作前请求用户批准的能力。

模型名称会随平台更新；选择原则是“强工具调用 + 强指令遵循”，而不是绑定某一个供应商。OpenAI 当前模型信息可查看[官方模型目录](https://developers.openai.com/api/docs/models)。

## 系统前置条件

- 64 位 Python 3.10 或更高版本。
- Windows `prepare`：Microsoft Word；Python 侧还需要 `pywin32`。
- macOS `prepare`：LibreOffice。
- `date-review` / `finalize` 不需要 Word 或 LibreOffice。

运行时依赖包括：

```text
rapidocr>=3.9.0
onnxruntime
numpy
opencv-python-headless
pypdf
pdfplumber
pypdfium2
reportlab
pymupdf
```

不建议先盲目安装。Skill 会先检测当前激活的 Conda/Anaconda、当前 Python 和系统 Python，列出缺失项与目标解释器；只有用户明确同意后，才给出并执行针对唯一目标 Python 的安装计划。它不会私自创建回退环境，也不会同时污染多个 Python。

## 安装

### 方法一：通用 skills CLI

把仓库发布到 GitHub 后，支持该安装器的客户端可以运行：

```bash
npx skills add Vvvvvhhhhh-21321/processing-bond-seal-pages --skill processing-bond-seal-pages
```

也可以先只查看仓库中可安装的 Skill：

```bash
npx skills add Vvvvvhhhhh-21321/processing-bond-seal-pages --list
```

`npx` 会下载并执行安装器。仅对可信仓库使用，并先审查 Skill 中的脚本。安装器的当前参数以 [skills CLI 文档](https://www.skills.sh/docs/cli)为准。

### 方法二：手动复制，兼容性最好

复制整个目录，而不是只复制 `SKILL.md`：

```text
.agents/skills/processing-bond-seal-pages/
```

目录中的 `scripts/` 和 `references/` 都是运行所必需的。

常见目标位置：

| 客户端 | 用户级目录或安装方式 |
|---|---|
| Codex | `~/.agents/skills/processing-bond-seal-pages/`，或项目内 `.agents/skills/processing-bond-seal-pages/` |
| ZCode | `~/.zcode/skills/processing-bond-seal-pages/`；也可在 Settings → Skills 中从其他 Agent 导入，选择软链接或复制 |
| CodeBuddy Code | `~/.codebuddy/skills/processing-bond-seal-pages/`，或项目内 `.codebuddy/skills/processing-bond-seal-pages/` |
| WorkBuddy | 技能页面 → 添加技能 → 上传从 Release 下载的本地技能包 |
| 其他客户端 | 查找该客户端的 skills 根目录，把整个技能目录放到它的直接子目录中 |

安装后若客户端没有立即显示 Skill，刷新技能列表或重启客户端。

相关官方说明：[Codex Skills](https://developers.openai.com/codex/skills)、[WorkBuddy 技能](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market)、[ZCode Skills](https://zcode.z.ai/en/docs/skill)、[CodeBuddy Code Skills](https://www.codebuddy.cn/docs/cli/skills)。

## 使用

优先在对话中显式调用 Skill。客户端支持 `$` 调用时，可以使用下面三段提示词。

### 1. 生成待盖章页合集

```text
$processing-bond-seal-pages
请处理“<底稿文件目录>”中的债券底稿，在“<处理批次目录>”生成处理批次和待盖章页合集。开始转换前按 Word 文件哈希排除重复文件，重复文件不参与任何后续流程。先检查环境；如需安装任何东西，先告诉我影响并等待同意。
```

如果要让所有 Word 都参与，把“按 Word 文件哈希排除重复文件”改成“不要排除重复文件”。如果没有说明，Skill 会先询问，不会自行决定。

生成后，把处理批次中的 `seal-pages.pdf` 发给客户，完整保留处理批次目录。

### 2. 生成已落日期确认稿并暂停

```text
$processing-bond-seal-pages
客户回章页合集是“<回章页合集.pdf>”，原处理批次在“<处理批次目录>”。落款日期为 2026-07-15。请生成完整日期确认稿到“<日期确认目录>”，列出需要重点核对的页码，然后暂停，不要回拼。
```

打开 `dated-returned-pages.pdf` 检查。可以用金山 PDF 等软件修改页面内容，但不要新增、删除页面，也不要改变页面顺序。

### 3. 确认后回拼

```text
$processing-bond-seal-pages
我已经检查并确认“<日期确认目录>”中的日期确认稿，页面数量和顺序没有改变。请按已保存的匹配关系回拼到“<输出目录>”，并生成处理清单。
```

## 直接运行脚本

通常应由模型按照 Skill 指令运行。排查环境时，也可以直接使用统一入口：

```text
<skill-root>/scripts/run_bond_seal_pages.py
```

先检查前置条件：

```bash
python <skill-root>/scripts/run_bond_seal_pages.py preflight --stage prepare
python <skill-root>/scripts/run_bond_seal_pages.py preflight --stage date-review
python <skill-root>/scripts/run_bond_seal_pages.py preflight --stage finalize
```

生成合集时必须明确选择重复文件策略；`keep` 保留全部，`deduplicate` 按 Word 文件哈希排除重复文件：

```bash
python <skill-root>/scripts/run_bond_seal_pages.py prepare <底稿目录> <处理批次目录> --duplicate-policy keep
python <skill-root>/scripts/run_bond_seal_pages.py prepare <底稿目录> <处理批次目录> --duplicate-policy deduplicate
python <skill-root>/scripts/run_bond_seal_pages.py date-review <处理批次目录> <回章页合集.pdf> <日期确认目录> --signing-date YYYY-MM-DD
python <skill-root>/scripts/run_bond_seal_pages.py finalize <处理批次目录> <日期确认目录> <输出目录> --confirmed
```

入口输出 UTF-8 JSON。详细完成条件见 [`SKILL.md`](.agents/skills/processing-bond-seal-pages/SKILL.md) 及按阶段加载的参考文档。

## 安全说明

- Skill 脚本在本地处理 Word 与 PDF，不主动上传业务文件。
- 选择的智能体客户端和模型服务是否上传提示词或文件，取决于该平台自身的隐私设置与部署方式。
- OCR 模型和 Python 依赖可能需要联网下载；前置检查会先报告计划并等待同意。
- 安装第三方 Skill 前应审查 `SKILL.md` 与 `scripts/`。发布者应为每个 Release 提供可核对的源码标签。

## 许可证

本项目使用 [MIT License](LICENSE)。
