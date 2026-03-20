import socket
import threading
import json
import time
import requests
from typing import Optional
from datetime import datetime

from config import ConfigManager

class MessageType:
    REGISTER = "register"
    GET_CLIENTS = "get_clients"
    SEND_MESSAGE = "send_message"
    BROADCAST = "broadcast"
    PRIVATE_MESSAGE = "private"
    HEARTBEAT = "heartbeat"
    RESPONSE = "response"

class Message:
    def __init__(self, msg_type: str, data: dict, sender: str = None, target: str = None, timestamp: str = None):
        self.type = msg_type
        self.data = data
        self.sender = sender
        self.target = target
        self.timestamp = timestamp or datetime.now().isoformat()
    
    def to_json(self) -> str:
        return json.dumps({
            'type': self.type,
            'data': self.data,
            'sender': self.sender,
            'target': self.target,
            'timestamp': self.timestamp
        })
    
    @staticmethod
    def from_json(json_str: str) -> 'Message':
        try:
            data = json.loads(json_str)
            return Message(
                msg_type=data.get('type'),
                data=data.get('data', {}),
                sender=data.get('sender'),
                target=data.get('target'),
                timestamp=data.get('timestamp')
            )
        except:
            return None

class TCPClient:
    def __init__(self, host: str = '127.0.0.1', port: int = 8080, heartbeat_interval: int = 60):
        self.host = host
        self.port = port
        self.socket = None
        self.name: Optional[str] = None
        self.running = False
        self.clients_list = []
        self.lock = threading.Lock()
        self.heartbeat_interval = heartbeat_interval
        self.heartbeat_thread = None
        self.message_queue = []
        self.buffer = ""

    def __init__(self):
        config = ConfigManager()

        # 获取服务器配置
        tcp_url = config.get('client', 'tcp_url')
        
        # 解析服务器地址
        if '://' in tcp_url:
            tcp_url = tcp_url.split('://', 1)[1]
        
        if ':' in tcp_url:
            tcp_host, tcp_port_str = tcp_url.rsplit(':', 1)
            try:
                tcp_port = int(tcp_port_str)
            except ValueError:
                tcp_host = tcp_url
                tcp_port = 8080
        else:
            tcp_host = tcp_url
            tcp_port = 8080

        self.host = tcp_host
        self.port = tcp_port
        self.socket = None
        self.name: Optional[str] = None
        self.running = False
        self.clients_list = []
        self.lock = threading.Lock()
        self.heartbeat_interval = int(config.get('client', 'heartbeat_interval'))
        self.heartbeat_thread = None
        self.message_queue = []
        self.buffer = ""
        
    def connect(self, name: str) -> bool:
        """连接到服务器（支持域名）"""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            
            # 设置连接超时
            self.socket.settimeout(10)
            
            print(f"{name} 正在连接 {self.host}:{self.port}...")
            
            # socket的connect方法会自动解析域名
            self.socket.connect((self.host, self.port))
            
            # 取消超时设置
            self.socket.settimeout(None)
            
            self.name = name
            self.running = True
            
            # 启动接收消息的线程
            receive_thread = threading.Thread(target=self.receive_messages)
            receive_thread.daemon = True
            receive_thread.start()

            # 启动处理消息的线程
            process_thread = threading.Thread(target=self.process_messages)
            process_thread.daemon = True
            process_thread.start()
            
            # 启动心跳线程
            self.heartbeat_thread = threading.Thread(target=self.send_heartbeat)
            self.heartbeat_thread.daemon = True
            self.heartbeat_thread.start()
            
            # 注册客户端
            return self.register()
            
        except socket.gaierror:
            print(f"{self.name} 域名解析失败: {self.host}")
            return False
        except socket.timeout:
            print(f"{self.name} 连接超时: {self.host}:{self.port}")
            return False
        except ConnectionRefusedError:
            print(f"{self.name} 连接被拒绝，请检查服务器是否运行在 {self.host}:{self.port}")
            return False
        except Exception as e:
            print(f"{self.name} 连接失败: {e}")
            return False
    
    def register(self) -> bool:
        """注册客户端"""
        message = Message(MessageType.REGISTER, {"name": self.name}, sender=self.name, target="server")
        self.send_message(message)
        return True
    
    def receive_messages(self):
        """接收消息的线程"""
        while self.running:
            try:
                # 设置1秒超时，让接收线程可以定期醒来检查 self.running 状态，实现优雅退出
                self.socket.settimeout(1.0)
                data = self.socket.recv(4096).decode('utf-8')

                if not data:
                    print(f"{self.name} 收到空数据，连接可能已关闭")
                    break
                
                # 将新数据添加到缓冲区
                self.buffer += data
                
                # 处理缓冲区中的所有完整消息
                while '\n' in self.buffer:
                    # 找到第一个换行符位置
                    newline_pos = self.buffer.index('\n')
                    # 提取消息
                    message_json = self.buffer[:newline_pos]
                    # 移除已处理的消息
                    self.buffer = self.buffer[newline_pos + 1:]
                    
                    # 跳过空消息
                    if not message_json.strip():
                        print(f"{self.name} 收到空消息")
                        continue
                    
                    message = Message.from_json(message_json)
                    if message:
                        print(f"{self.name} 解析消息: type={message.type}, data={message.data} from {message.sender} to {message.target}, timestamp={message.timestamp}")
                        self.add_message(message)
                    else:
                        print(f"{self.name} 收到无效消息: {message_json}")
                                    
            except (ConnectionResetError, BrokenPipeError):
                break
            except socket.timeout:
                continue
            except Exception as e:
                print(f"接收消息出错: {e}")
                break
        
        self.running = False
    
    def add_message(self, message: Message):
        """添加私聊消息到消息队列"""
        self.message_queue.append(message)

    def process_messages(self):
        """处理私聊消息，完成"""
        while self.running:
            if self.message_queue:
                message = self.message_queue.pop(0)
                self.process_message(message)
            else:
                time.sleep(0.1)
    
    def process_message(self, message: Message):
        """处理接收到的消息"""
        print(f"Base Client Process message, Please Override this method")
        if message.type == MessageType.GET_CLIENTS:
            # 直接处理客户端列表
            clients = message.data.get('clients', [])
            with self.lock:
                self.clients_list = clients
            print(f"\n当前在线用户 ({len(clients)}): {', '.join(clients) if clients else '暂无其他用户'}")
            
        elif message.type == MessageType.RESPONSE:
            # 处理响应
            data = message.data
            if data.get('success'):
                if 'message' in data:
                    print(f"\n[系统] {data['message']}")
                # 如果响应中包含客户端列表数据
                if 'data' in data and data['data'] and 'clients' in data['data']:
                    clients = data['data']['clients']
                    with self.lock:
                        self.clients_list = clients
                    print(f"当前在线用户: {', '.join(clients) if clients else '暂无其他用户'}")
            else:
                print(f"\n[错误] {data.get('message', '未知错误')}")
                
        elif message.type == MessageType.PRIVATE_MESSAGE:
            # 收到私聊消息
            print(f"\n[私聊] {message.sender}: {message.data.get('content')}")
            
        elif message.type == MessageType.BROADCAST:
            # 收到广播消息
            print(f"\n[广播] {message.sender}: {message.data.get('content')}")

    def send_heartbeat(self):
        """发送心跳包，保持长连接"""
        while self.running:
            try:
                time.sleep(self.heartbeat_interval)
                if not self.running:
                    break
                message = Message(MessageType.HEARTBEAT, {}, sender=self.name, target="server")
                self.send_message(message)
            except Exception as e:
                if self.running:
                    print(f"{self.name} 发送心跳包失败：{e}")
                break
    
    def send_message(self, message: Message):
        """发送消息"""
        try:
            json_str = message.to_json() + '\n'
            self.socket.send(json_str.encode('utf-8'))
        except Exception as e:
            print(f"发送消息失败: {e}")
    
    def get_clients(self):
        """获取所有客户端列表"""
        message = Message(MessageType.GET_CLIENTS, {}, sender=self.name, target="server")
        self.send_message(message)
    
    def send_private_message(self, target: str, content: str):
        """发送私聊消息"""
        message = Message(
            MessageType.SEND_MESSAGE,
            {"content": content},
            sender=self.name,
            target=target
        )
        self.send_message(message)
    
    def broadcast(self, content: str):
        """广播消息"""
        message = Message(MessageType.BROADCAST, {"content": content}, sender=self.name, target="all_users")
        self.send_message(message)
    
    def close(self):
        """关闭连接"""
        self.running = False
        
        # 等待心跳线程结束（最多等待 2 秒）
        if self.heartbeat_thread and self.heartbeat_thread.is_alive():
            self.heartbeat_thread.join(timeout=2)
        
        if self.socket:
            try:
                self.socket.shutdown(socket.SHUT_RDWR)
            except:
                pass
            try:
                self.socket.close()
            except:
                pass
            self.socket = None

def check_username_exists(http_host: str, http_port: int, username: str) -> bool:
    """通过 HTTP 检查用户名是否已存在"""
    try:
        url = f"http://{http_host}:{http_port}/check_username?username={username}"
        response = requests.get(url, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            if data.get('success'):
                return data.get('exists', False)
        
        # 如果 HTTP 检查失败，返回 True 不允许连接
        return True
    except Exception as e:
        print(f"检查用户名时出错：{e}")
        # 如果 HTTP 请求失败，返回 True 不允许连接
        return True

def check_username_exists(username: str) -> bool:
    """通过 HTTP 检查用户名是否已存在"""
    config = ConfigManager()
    
    # 获取 HTTP 服务器配置
    http_url = config.get('client', 'http_url')
    if '://' in http_url:
        http_url = http_url.split('://', 1)[1]
    
    if ':' in http_url:
        http_host, http_port_str = http_url.rsplit(':', 1)
        try:
            http_port = int(http_port_str)
        except ValueError:
            print(f"HTTP 端口格式错误：{http_port_str}")
            return True
    else:
        return True

    try:
        url = f"http://{http_host}:{http_port}/check_username?username={username}"
        response = requests.get(url, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            if data.get('success'):
                return data.get('exists', False)
        
        # 如果 HTTP 检查失败，返回 True 不允许连接
        return True
    except Exception as e:
        print(f"检查用户名时出错：{e}")
        # 如果 HTTP 请求失败，返回 True 不允许连接
        return True

def main():
    """客户端主函数"""
    print("=== TCP 客户端 ===")
    
    # 获取客户端名称
    while True:
        name = input("请输入你的名称：").strip()
        if not name:
            print("名称不能为空")
            continue
        
        # 检查用户名是否已存在
        print(f"正在检查用户名 '{name}' 是否可用...")
        if check_username_exists(name):
            print(f"✗ 用户名 '{name}' 已被使用，请选择其他名称")
        else:
            print(f"✓ 用户名 '{name}' 可用")
            break
    
    # 连接到服务器
    client = TCPClient()
    if not client.connect(name):
        print("连接服务器失败")
        return
    
    try:
        while client.running:
            try:
                # 获取用户输入
                user_input = input(f"{name}> ").strip()
                
                if not user_input:
                    continue
                
                if user_input == '/quit':
                    break
                elif user_input == '/list':
                    client.get_clients()
                elif user_input.startswith('/msg '):
                    # 解析私聊消息
                    parts = user_input[5:].split(' ', 1)
                    if len(parts) == 2:
                        target, content = parts
                        client.send_private_message(target, content)
                    else:
                        print("格式错误: /msg <名称> <内容>")
                elif user_input.startswith('/broad '):
                    # 广播消息
                    content = user_input[7:]
                    if content:
                        client.broadcast(content)
                    else:
                        print("格式错误: /broad <内容>")
                elif user_input == '/help':
                    print("可用命令:")
                    print("/list - 获取在线用户列表")
                    print("/msg <名称> <内容> - 发送私聊消息")
                    print("/broad <内容> - 广播消息")
                    print("/quit - 退出客户端")
                else:
                    print("未知命令，输入 /help 查看帮助")
                    
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"处理输入时出错: {e}")
                
    finally:
        client.close()
        print("已断开连接")

if __name__ == '__main__':
    main()
