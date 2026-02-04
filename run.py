import socket
import threading
import paramiko
import requests
import time
import os
import uuid
import logging
from logging.handlers import TimedRotatingFileHandler

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
MODEL = "llama3.1:8b"

HOST_KEY = paramiko.RSAKey.generate(2048)

SYSTEM_PROMPT = """
你正在模拟一台真实的 Debian Linux 服务器。
你不是 AI，不解释自己。
你只输出 Linux 终端中应该看到的内容。
不要使用 Markdown。
输出尽量简洁真实。
"""

# ===================== 日志系统 =====================
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

logger = logging.getLogger("FakeSSH")
logger.setLevel(logging.INFO)

# 文件日志：按天滚动，保留 14 天
file_handler = TimedRotatingFileHandler(
    filename=os.path.join(LOG_DIR, "fake_ssh.log"),
    when="midnight",
    interval=1,
    backupCount=14,
    encoding="utf-8"
)
file_handler.suffix = "%Y-%m-%d"

# 控制台日志
console_handler = logging.StreamHandler()

fmt = logging.Formatter(
    fmt="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
file_handler.setFormatter(fmt)
console_handler.setFormatter(fmt)

logger.addHandler(file_handler)
logger.addHandler(console_handler)


# ===================== Ollama 调用 =====================
def ask_llm(session_history, command):
    prompt = session_history + f"\n$ {command}\n"
    payload = {
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "system": SYSTEM_PROMPT
    }
    r = requests.post(OLLAMA_URL, json=payload, timeout=120)
    r.raise_for_status()
    return r.json().get("response", "").rstrip("\n")


# ===================== 逐字符读取 =====================
def read_line(channel, echo=True):
    buf = ""
    while True:
        data = channel.recv(1)
        if not data:
            return None

        ch = data.decode(errors="ignore")

        if ch in ("\r", "\n"):
            if echo:
                channel.send("\r\n")
            return buf

        if ch in ("\x08", "\x7f"):
            if len(buf) > 0:
                buf = buf[:-1]
                if echo:
                    channel.send("\b \b")
            continue

        if ch == "\x1b":
            # 吃掉方向键序列
            try:
                channel.recv(2)
            except Exception:
                pass
            continue

        buf += ch
        if echo:
            channel.send(ch)


def send_slow(channel, text, delay=0.01):
    for line in text.splitlines():
        channel.send(line + "\r\n")
        time.sleep(delay)


# ===================== SSH Server =====================
class FakeSSHServer(paramiko.ServerInterface):
    """
    这个对象是 Paramiko 用来回调认证/PTY/exec 的。
    我们把会话信息塞进去，用于日志记录。
    """
    def __init__(self, session_id, client_addr):
        self.event = threading.Event()
        self.exec_command = None
        self.session_id = session_id
        self.client_addr = client_addr
        self.username = None
        self.password = None

    def check_auth_password(self, username, password):
        self.username = username
        self.password = password

        # 记录登录尝试
        logger.info(
            f"[{self.session_id}] AUTH attempt from {self.client_addr[0]}:{self.client_addr[1]} "
            f"user='{username}' pass='{password}' -> SUCCESS"
        )
        return paramiko.AUTH_SUCCESSFUL

    def get_allowed_auths(self, username):
        return "password"

    def check_channel_request(self, kind, chanid):
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_pty_request(self, channel, term, width, height, pixelwidth, pixelheight, modes):
        logger.info(
            f"[{self.session_id}] PTY requested term='{term}' size={width}x{height} "
            f"from {self.client_addr[0]}:{self.client_addr[1]}"
        )
        return True

    def check_channel_shell_request(self, channel):
        logger.info(f"[{self.session_id}] SHELL requested")
        self.event.set()
        return True

    def check_channel_exec_request(self, channel, command):
        self.exec_command = command.decode(errors="ignore")
        logger.info(f"[{self.session_id}] EXEC requested cmd='{self.exec_command}'")
        self.event.set()
        return True


# ===================== 客户端处理 =====================
def handle_client(client, addr):
    session_id = str(uuid.uuid4())[:8]

    logger.info(f"[{session_id}] CONNECT from {addr[0]}:{addr[1]}")

    transport = None
    channel = None

    try:
        transport = paramiko.Transport(client)
        transport.add_server_key(HOST_KEY)

        server = FakeSSHServer(session_id=session_id, client_addr=addr)
        transport.start_server(server=server)

        channel = transport.accept(20)
        if channel is None:
            logger.warning(f"[{session_id}] No channel opened (timeout)")
            transport.close()
            return

        server.event.wait(10)

        session_history = ""

        # ====== exec 模式 ======
        if server.exec_command:
            cmd = server.exec_command.strip()

            t0 = time.time()
            response = ask_llm(session_history, cmd)
            cost = time.time() - t0

            logger.info(f"[{session_id}] CMD(exec) '{cmd}' cost={cost:.3f}s")
            if response:
                logger.info(f"[{session_id}] OUT(exec) {response!r}")

            if response:
                channel.send(response + "\r\n")
            channel.send_exit_status(0)

            logger.info(f"[{session_id}] DISCONNECT (exec finished)")
            channel.close()
            transport.close()
            return

        # ====== shell 模式 ======
        channel.settimeout(30.0)

        banner = """Linux debian 4.9.0-0-amd64 #1 SMP Debian 4.9.65-3+deb9u1 (2017-12-23)

The programs included with the Debian GNU/Linux system are free software;
the exact distribution terms for each program are described in the
individual files in /usr/share/doc/*/copyright.

Debian GNU/Linux comes with ABSOLUTELY NO WARRANTY, to the extent
permitted by applicable law.
"""
        send_slow(channel, banner, delay=0.01)

        username = "root"
        hostname = "debian"
        cwd = "~"

        def prompt():
            return f"{username}@{hostname}:{cwd}# "

        logger.info(f"[{session_id}] INTERACTIVE shell started")

        while True:
            channel.send(prompt())
            cmd = read_line(channel, echo=True)
            if cmd is None:
                logger.info(f"[{session_id}] Client closed connection")
                break

            raw_cmd = cmd
            cmd = cmd.strip()

            if not cmd:
                continue

            if cmd in ("exit", "logout"):
                logger.info(f"[{session_id}] LOGOUT by client command")
                break

            # 记录命令
            logger.info(f"[{session_id}] CMD '{cmd}' (raw={raw_cmd!r})")

            # 调用 LLM
            t0 = time.time()
            try:
                response = ask_llm(session_history, cmd)
            except Exception as e:
                response = f"bash: {cmd}: command failed"
                logger.exception(f"[{session_id}] LLM ERROR: {e}")
            cost = time.time() - t0

            logger.info(f"[{session_id}] LLM cost={cost:.3f}s")
            if response:
                logger.info(f"[{session_id}] OUT {response!r}")

            # 更新 history
            session_history += f"\n$ {cmd}\n{response}\n"

            if response:
                channel.send(response + "\r\n")
            else:
                channel.send("\r\n")

        logger.info(f"[{session_id}] DISCONNECT (shell ended)")

    except Exception as e:
        logger.exception(f"[{session_id}] SESSION ERROR: {e}")

    finally:
        try:
            if channel:
                channel.close()
        except Exception:
            pass
        try:
            if transport:
                transport.close()
        except Exception:
            pass
        try:
            client.close()
        except Exception:
            pass


# ===================== 主程序 =====================
def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", 2222))
    sock.listen(100)

    logger.info("Fake SSH listening on port 2222")

    while True:
        client, addr = sock.accept()
        t = threading.Thread(target=handle_client, args=(client, addr), daemon=True)
        t.start()


if __name__ == "__main__":
    main()
