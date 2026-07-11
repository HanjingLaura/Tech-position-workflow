# 技术候选人寻访 Skill

[English](README.md) | [简体中文](README.zh-CN.md)

这是一个本地 Codex Skill，可根据职位描述（JD）从公开网页中寻找算法、AI 研究及其他技术岗位候选人。

它会把 JD 转换成便于人工复核的 Markdown/CSV 候选人表，包含公开邮箱、来源链接、履历证据、匹配信号、风险点和建议动作。该 Skill 保持通用，不内置固定候选人名单、Agent 岗位预设、猜测邮箱或私有招聘数据。

## 功能

- 从 JD 中提取技术关键词。
- 检索公开的 GitHub、学术主页、个人主页、arXiv、DBLP、OpenAlex 和论文 PDF。
- 只提取网页直接公开或使用常见公开混淆格式展示的邮箱。
- 根据重叠的公开邮箱合并候选人记录。
- 输出 Markdown、CSV、JSONL、校验报告以及可选的邮件草稿。
- 默认禁用邮件发送；实际发送必须具备明确的审批信息和 SMTP 配置。

## 安装

在仓库根目录安装 Python 依赖：

```bash
python -m pip install -r tech-candidate-sourcing/requirements.txt
```

将 Skill 文件夹复制到 Codex skills 目录，即可让 Codex 发现并使用它：

```bash
cp -R tech-candidate-sourcing ~/.codex/skills/
```

Windows PowerShell：

```powershell
Copy-Item -Recurse -Force .\tech-candidate-sourcing $env:USERPROFILE\.codex\skills\
```

## 基础用法

运行仅生成候选人结果的广覆盖任务：

```bash
python tech-candidate-sourcing/scripts/run_sourcing_pipeline.py --jd path/to/jd.md --out-dir outputs --coverage-preset broad --no-outreach
```

直接传入 JD 文本：

```bash
python tech-candidate-sourcing/scripts/run_sourcing_pipeline.py --jd-text "算法研究员……" --out-dir outputs --coverage-preset custom --depth quick --no-outreach
```

优先检索与中国相关的公开来源，但不据此推断候选人的国籍或民族：

```bash
python tech-candidate-sourcing/scripts/run_sourcing_pipeline.py --jd path/to/jd.md --out-dir outputs --coverage-preset broad --focus china --require-china-signal --no-outreach
```

加入已知的公开页面作为种子来源：

```bash
python tech-candidate-sourcing/scripts/run_sourcing_pipeline.py --jd path/to/jd.md --out-dir outputs --seed-url https://example.edu/person --no-outreach
```

## 输出字段

候选人表使用 `tech-candidate-sourcing/scripts/candidate_schema.py` 中定义的标准字段，包括：

- 姓名或页面名
- 基本信息
- 电话
- 邮箱
- 渠道
- 线索类型
- 置信度
- 推荐级别
- 匹配分
- 来源
- 命中关键词
- 推荐点
- 风险点或待确认项
- 建议动作
- 研究方向
- 代表性论文或项目证据
- 与中国相关的公开信号

匹配分只用于初步排序。联系候选人之前，应人工核验身份归属、岗位匹配度、地点、薪资、可入职情况以及是否允许触达。

## 安全边界

- 只收集公开联系方式。
- 不猜测或合成邮箱。
- 不登录平台，不绕过反爬或访问限制。
- 不根据姓名、语言或外貌推断国籍或民族。
- 默认不发送邮件。
- 实际发送邮件必须通过队列审批、审批元数据、拒收名单检查和 SMTP 配置，并显式使用 `--send`。

## 校验

运行 Skill 结构校验：

```bash
python ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py tech-candidate-sourcing
```

运行回归测试：

```bash
python -m unittest discover -s tech-candidate-sourcing/tests -v
python -S -m unittest discover -s tech-candidate-sourcing/tests -v
```

运行语法检查：

```bash
python -m py_compile tech-candidate-sourcing/scripts/*.py
```

## 仓库结构

```text
tech-candidate-sourcing/
  SKILL.md
  agents/openai.yaml
  references/
  scripts/
  tests/
  requirements.txt
```

`.gitignore` 已排除生成结果、本地 JD 文件、缓存、Python 字节码、压缩包和密钥文件。
