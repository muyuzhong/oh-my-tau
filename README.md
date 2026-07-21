# Lion Code

Lion Code 是一个用 Python 构建的轻量级编码 Agent。它可以读取和修改项目文件、执行 Shell 命令、搜索代码，并通过 Anthropic 或 OpenAI 兼容接口与模型交互。

## 功能

- Anthropic API 和 OpenAI 兼容 API
- 流式输出、重试和费用/轮次限制
- 文件读写、编辑、目录搜索、正则搜索和网页读取
- 默认、Plan、Accept Edits、Dont Ask、Yolo、Auto 等权限模式
- 会话保存与恢复
- Memory、Skill、Sub-agent 和 MCP
- /goal 和 /loop 自主运行命令

## 安装

需要 Python 3.11 或更高版本。

~~~powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .
~~~

安装后可使用 lion-code 命令，也可以直接使用 Python 模块入口：

~~~powershell
lion-code --help
python -m lion_code --help
~~~

## 配置模型

使用 Anthropic：

~~~powershell
$env:ANTHROPIC_API_KEY = "你的 API Key"
lion-code "读取当前项目并总结结构"
~~~

使用 OpenAI 兼容接口：

~~~powershell
$env:OPENAI_API_KEY = "你的 API Key"
lion-code --api-base "https://api.openai.com/v1" --model "gpt-4o" "检查这个项目"
~~~

也可以通过环境变量设置默认模型：

~~~powershell
$env:LION_CODE_MODEL = "claude-opus-4-6"
~~~

## 常用用法

~~~powershell
lion-code "修复这个项目中的测试错误"
lion-code --plan "设计一个重构方案"
lion-code --yolo "运行测试并修复失败项"
lion-code --resume
~~~

交互式 REPL 中支持：

| 命令 | 作用 |
|---|---|
| /clear | 清空当前对话 |
| /plan | 切换 Plan 模式 |
| /cost | 查看 Token 使用量和费用 |
| /compact | 压缩当前对话 |
| /memory | 查看已保存的记忆 |
| /skills | 查看可用 Skill |
| /goal <条件> | 持续工作直到目标满足 |
| /loop <任务> | 重复执行任务 |
| exit / quit | 退出程序 |

## 项目结构

~~~text
Lion/
├── lion_code/        # Agent 核心包
│   ├── __main__.py   # CLI 和 REPL 入口
│   ├── agent.py      # Agent 主循环
│   ├── tools.py      # 工具和权限控制
│   ├── prompt.py     # 系统提示词
│   ├── memory.py     # 记忆管理
│   ├── session.py    # 会话管理
│   ├── skills.py     # Skill 系统
│   ├── subagent.py   # 子 Agent
│   ├── mcp_client.py # MCP 客户端
│   ├── autonomy.py   # Goal、Loop、Auto Mode
│   ├── frontmatter.py
│   └── ui.py         # Rich 终端界面
├── tests/            # Python 测试
├── pyproject.toml    # Python 打包和 CLI 配置
├── LICENSE           # MIT 许可证
└── README.md
~~~

运行时数据默认保存在用户目录下的 .lion-code 中：

- sessions/：会话记录
- projects/：项目记忆
- tool-results/：较大的工具结果

## 测试

~~~powershell
python -m unittest discover -s tests -p "test_*.py"
python -m compileall -q lion_code tests
~~~

当前快照没有保留 assets/auto-mode-rules.json，因此 Auto Mode 流程测试会跳过；普通对话和其他工具不依赖该文件。

## 开发约定

- Python 包名：lion_code
- CLI 命令：lion-code
- 默认模型环境变量：LION_CODE_MODEL
- SDK 重试调试变量：LION_CODE_SDK_MAX_RETRIES
