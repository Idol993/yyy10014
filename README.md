# CI/CD Fix Bot

基于大语言模型的自动化CI/CD流水线修复机器人。当构建失败时，它会自动解析构建日志，利用AST对比技术定位问题代码，通过RAG检索项目上下文和依赖文档，自动生成修复补丁并提交Pull Request。

## 核心特性

- 🔍 **智能日志解析** - 支持多种构建工具和编程语言的错误解析
- 🌳 **AST代码对比** - 通过抽象语法树精确定位最近提交中的代码变更
- 📚 **RAG文档检索** - 基于向量搜索的项目文档和代码上下文检索
- 🤖 **LLM修复生成** - 利用大语言模型生成高质量修复补丁
- 🔒 **安全沙箱验证** - 多层安全防护，防止引入安全漏洞
- 🔄 **兼容性检查** - 自动检测API向后兼容性问题
- ✅ **自动化测试** - 运行测试套件验证修复的正确性
- 📦 **自动PR提交** - 一键生成带有详细说明的Pull Request

## 系统架构

```
┌─────────────────────────────────────────────────────────┐
│                    GitHub Actions                        │
└─────────────────────┬───────────────────────────────────┘
                      │ 触发（构建失败时）
                      ▼
┌─────────────────────────────────────────────────────────┐
│                   Build Log Parser                       │
│               （构建日志解析模块）                        │
└─────────────────────┬───────────────────────────────────┘
                      │ 错误信息
                      ▼
┌─────────────────────────────────────────────────────────┐
│                   AST Comparator                         │
│               （AST代码变更对比模块）                     │
└─────────────────────┬───────────────────────────────────┘
                      │ 变更位置
          ┌───────────┴───────────┐
          ▼                       ▼
┌───────────────────┐   ┌─────────────────────┐
│  RAG Retriever    │   │  LLM Fix Generator  │
│（文档检索增强模块）│   │ （修复补丁生成模块） │
└─────────┬─────────┘   └───────────┬─────────┘
          │ 上下文信息               │
          └───────────┬─────────────┘
                      ▼
┌─────────────────────────────────────────────────────────┐
│                   Sandbox Validator                      │
│            （安全沙箱验证模块）                           │
│  ┌──────────┐  ┌──────────────┐  ┌───────────────────┐  │
│  │ Security │  │ Compatibility│  │  Test Runner      │  │
│  │  Scan    │  │   Check      │  │                   │  │
│  └──────────┘  └──────────────┘  └───────────────────┘  │
└─────────────────────┬───────────────────────────────────┘
                      │ 验证通过
                      ▼
┌─────────────────────────────────────────────────────────┐
│                   PR Submitter                           │
│               （Pull Request提交模块）                   │
└─────────────────────────────────────────────────────────┘
```

## 安全沙箱验证策略

安全沙箱是本系统的核心难点，采用三层防护策略：

### 第一层：静态安全扫描
- 使用Semgrep和Bandit进行静态代码分析
- 检测SQL注入、XSS、命令注入等常见安全漏洞
- 检测硬编码的密钥、密码等敏感信息
- 支持Python、JavaScript、Java等多种语言

### 第二层：API兼容性检查
- 检测公共API的删除或签名变更
- 验证向后兼容性
- 防止破坏现有消费者的代码

### 第三层：自动化测试验证
- 运行相关测试套件
- 支持pytest、Jest、JUnit、Go test等
- 在隔离环境中运行测试

## 快速开始

### 环境要求

- Python 3.9+
- OpenAI API Key
- GitHub Token

### 安装

```bash
pip install -r requirements.txt
```

### 配置环境变量

```bash
export OPENAI_API_KEY="your-openai-api-key"
export GITHUB_TOKEN="your-github-token"
export GITHUB_REPOSITORY="owner/repo"
export BUILD_LOG_PATH="./build.log"
```

### 本地运行

```bash
python -m src.main
```

## GitHub Actions 集成

在项目的 `.github/workflows/` 目录下添加工作流文件：

```yaml
name: CI/CD Fix Bot

on:
  workflow_run:
    workflows: ["*"]
    types:
      - completed

permissions:
  contents: write
  pull-requests: write

jobs:
  fix-bot:
    runs-on: ubuntu-latest
    if: ${{ github.event.workflow_run.conclusion == 'failure' }}
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      
      - name: Run CI/CD Fix Bot
        uses: your-org/ci-cd-fix-bot@v1
        with:
          openai-api-key: ${{ secrets.OPENAI_API_KEY }}
          github-token: ${{ secrets.GITHUB_TOKEN }}
```

## 配置选项

| 环境变量 | 默认值 | 说明 |
|---------|-------|------|
| `OPENAI_API_KEY` | - | OpenAI API密钥（必需） |
| `OPENAI_MODEL` | gpt-4-turbo-preview | 使用的GPT模型 |
| `OPENAI_TEMPERATURE` | 0.1 | 生成温度（越低越确定） |
| `GITHUB_TOKEN` | - | GitHub访问令牌（必需） |
| `GITHUB_REPOSITORY` | - | 仓库路径 owner/repo（必需） |
| `BUILD_LOG_PATH` | ./build.log | 构建日志文件路径 |
| `ENABLE_SANDBOX` | true | 是否启用沙箱验证 |
| `ENABLE_SECURITY_SCAN` | true | 是否启用安全扫描 |
| `ENABLE_COMPATIBILITY_CHECK` | true | 是否启用兼容性检查 |
| `ENABLE_TEST_RUN` | true | 是否运行测试 |
| `MAX_FIX_ATTEMPTS` | 3 | 最大修复尝试次数 |
| `SECURITY_SEVERITY_THRESHOLD` | medium | 安全问题严重程度阈值 |
| `SANDBOX_TIMEOUT` | 300 | 沙箱超时时间（秒） |

## 支持的语言和工具

### 编程语言
- ✅ Python
- ✅ JavaScript / TypeScript
- ✅ Java
- ✅ Go
- ✅ Rust
- ✅ C / C++

### 构建工具
- ✅ pytest
- ✅ npm / yarn
- ✅ Maven / Gradle
- ✅ Go build
- ✅ Cargo
- ✅ GCC / Clang

## 开发

### 运行测试

```bash
pytest tests/ -v
```

### 代码结构

```
src/
├── config.py              # 配置管理
├── main.py                # 主入口
├── log_parser/            # 构建日志解析
├── ast_diff/              # AST代码对比
├── rag/                   # RAG文档检索
├── fix_generator/         # LLM修复生成
├── sandbox/               # 安全沙箱验证
│   ├── security_scanner.py      # 安全扫描
│   ├── compatibility_checker.py # 兼容性检查
│   └── test_runner.py           # 测试运行
└── pr_submitter/          # PR提交
```

## 安全说明

⚠️ **重要安全提示：**

1. 本工具生成的代码修复需要人工审查后才能合并
2. 所有自动生成的PR都会标记 `automated-fix` 标签
3. 安全扫描发现的高危问题会阻止PR自动合并
4. 建议在测试环境中充分验证后再用于生产环境

## License

MIT
