# PromptSSH-Fake

## 介绍

PromptSSH-Fake (Ollama 驱动的 终端模拟器)  有详细的日志系统，可以用于AI蜜罐实验

## 运行原理

```
┌────────────┐
│ SSH Client │   ← 攻击者 / 扫描器
└─────┬──────┘
      │ SSH
┌─────▼──────┐
│ Fake SSH   │  ← 假的 SSH 服务端
│ Server     │
└─────┬──────┘
      │ command string
┌─────▼──────┐
│ LLM Adapter│  ← 把命令送给 Ollama
└─────┬──────┘
      │ prompt
┌─────▼──────┐
│ Ollama LLM │  ← 模拟 Linux Shell
└────────────┘
```

## 关键组件

| 功能        | 技术                         |
| ----------- | ---------------------------- |
| SSH Server  | `paramiko.ServerInterface`   |
| PTY / Shell | paramiko channel             |
| LLM         | Ollama (`/api/generate`)     |
| 状态管理    | Python dict / session memory |
| 日志        | 原始命令 + LLM 输出          |

## 如何使用

程序就是run.py这个文件

**第一步** 安装依赖

```

```

**第二部** 配置端口和模型

```
修改代码第11，12行
	OLLAMA_URL = "http://127.0.0.1:11434/api/generate" 改成你的ollama api 默认是本机
	MODEL = "llama3.1:8b"	要用的模型
修改代码第299行
    sock.bind(("0.0.0.0", 2222)) 这是你模拟SSH的端口
```

**第三步** 运行程序 

每次被访问和访问者的操作都会在终端中输出，并记录都在 `logs/fake_ssh.log`

日志格式：

```
2026-02-04 11:20:01 | INFO | [a1b2c3d4] CONNECT from 192.168.1.10:54321
2026-02-04 11:20:01 | INFO | [a1b2c3d4] AUTH attempt from 192.168.1.10:54321 user='root' pass='123' -> SUCCESS
2026-02-04 11:20:01 | INFO | [a1b2c3d4] PTY requested term='xterm-256color' size=120x40 from 192.168.1.10:54321
2026-02-04 11:20:02 | INFO | [a1b2c3d4] CMD 'ls -la' (raw='ls -la')
2026-02-04 11:20:02 | INFO | [a1b2c3d4] LLM cost=1.832s
2026-02-04 11:20:02 | INFO | [a1b2c3d4] OUT 'total 12\n...'
2026-02-04 11:20:10 | INFO | [a1b2c3d4] LOGOUT by client command
2026-02-04 11:20:10 | INFO | [a1b2c3d4] DISCONNECT (shell ended)

```

