import socket
import threading
import json
import logging
import sys
from typing import Dict, Optional
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from time import sleep

from config import ConfigManager

# 全局服务器实例
server = None

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

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
        except json.JSONDecodeError as e:
            logging.error(f"JSON解析错误: {e}")
            return None

class ClientHandler:
    def __init__(self, client_socket: socket.socket, address: tuple, server):
        self.socket = client_socket
        self.address = address
        self.server = server
        self.name: Optional[str] = None
        self.running = True
        self.buffer = ""
        
    def start(self):
        thread = threading.Thread(target=self.handle_client)
        thread.daemon = True
        thread.start()
    
    def handle_client(self):
        try:            
            while self.running:
                try:
                    self.socket.settimeout(300.0)
                    data = self.socket.recv(4096).decode('utf-8')
                    
                    if not data:
                        logging.info(f"server 客户端 {self.name or self.address} 连接关闭（收到空数据）")
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
                            logging.warning(f"server 收到空消息: {message_json}")
                            continue
                                                
                        message = Message.from_json(message_json)
                        if not message:
                            logging.warning(f"server 无法解析的消息: {message_json[:100]}")
                            continue
                        
                        logging.info(f"server 收到来自 {self.name or self.address} 的消息: type={message.type}, data={message.data} from {message.sender} to {message.target}")

                        self.process_message(message)
                    
                except socket.timeout:
                    logging.info(f"server 客户端 {self.name or self.address} 超时")
                    continue
                except (ConnectionResetError, BrokenPipeError) as e:
                    logging.info(f"server 客户端 {self.name or self.address} 连接断开: {e}")
                    break
                except Exception as e:
                    logging.error(f"server 客户端 {self.name or self.address} 接收消息时出错: {e}")
                    break
                    
        except Exception as e:
            logging.error(f"server 处理客户端消息时出错: {e}", exc_info=True)
        finally:
            self.close()
    
    def process_message(self, message: Message):
        if message.type == MessageType.REGISTER:
            self.name = message.data.get('name')
            
            if self.name:
                if self.server.register_client(self):
                    self.send_response(True, f"欢迎 {self.name}！")
                else:
                    self.send_response(False, f"注册失败：名称 {self.name} 已存在")
            else:
                self.send_response(False, "注册失败：未提供名称")
                logging.warning(f"客户端 {self.address} 注册失败：未提供名称")
                
        elif message.type == MessageType.GET_CLIENTS:
            clients = self.server.get_clients_list()
            self.send_message(Message(
                MessageType.GET_CLIENTS,
                {"clients": clients},
                sender="server",
                target=self.name
            ))
            
        elif message.type == MessageType.SEND_MESSAGE:
            target = message.target
            content = message.data.get('content')
            if target and content:
                success = self.server.send_message_to_client(
                    target, 
                    Message(
                        MessageType.PRIVATE_MESSAGE,
                        {"content": content},
                        sender=self.name,
                        target=target
                    )
                )
                if not success:
                    self.send_response(False, f"消息发送失败：客户端 {target} 不存在")
            else:
                self.send_response(False, "消息发送失败：缺少目标或内容")
                
        elif message.type == MessageType.BROADCAST:
            content = message.data.get('content')
            if content:
                self.server.broadcast_message(
                    Message(
                        MessageType.BROADCAST,
                        {"content": content},
                        sender=self.name,
                        target="all_users"
                    ),
                    exclude=self.name
                )
            else:
                self.send_response(False, "广播发送失败：缺少内容")
                
        elif message.type == MessageType.HEARTBEAT:
            pass
    
    def send_response(self, success: bool, message: str, data: dict = None):
        response = Message(
            MessageType.RESPONSE,
            {
                "success": success,
                "message": message,
                "data": data or {}
            },
            sender="server",
            target=self.name
        )
        self.send_message(response)
    
    def send_message(self, message: Message):
        try:
            json_str = message.to_json() + '\n'
            self.socket.send(json_str.encode('utf-8'))
        except Exception as e:
            logging.error(f"客户端 {self.name or self.address} 发送消息失败: {e}")
    
    def close(self):
        self.running = False
        if self.name:
            self.server.unregister_client(self)
        try:
            self.socket.close()
        except:
            pass

class HTTPMessageHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        logging.info(f"server 收到http请求: {args[0]}")
    
    def do_POST(self):
        parsed_path = urlparse(self.path)
        
        if parsed_path.path == '/send_message':
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length).decode('utf-8')
                        
            try:
                data = json.loads(post_data)
                username = data.get('username')
                message_text = data.get('message')
                
                if not username or not message_text:
                    self.send_json_response(400, {
                        'success': False,
                        'error': '缺少username或message参数'
                    })
                    return
                
                tcp_server = self.server.server
                success = tcp_server.send_message_to_client(username, Message(
                    MessageType.PRIVATE_MESSAGE,
                    {"content": message_text},
                    sender="http_client",
                    target=username
                ))
                
                if success:
                    self.send_json_response(200, {
                        'success': True,
                        'message': f'消息已发送给 {username}'
                    })
                else:
                    self.send_json_response(404, {
                        'success': False,
                        'error': f'用户 {username} 不存在或未连接'
                    })
                    
            except json.JSONDecodeError as e:
                logging.error(f"JSON解析错误: {e}")
                self.send_json_response(400, {
                    'success': False,
                    'error': f'JSON解析错误: {str(e)}'
                })
            except Exception as e:
                logging.error(f"处理请求时出错: {e}", exc_info=True)
                self.send_json_response(500, {
                    'success': False,
                    'error': f'服务器错误: {str(e)}'
                })
        else:
            self.send_json_response(404, {
                'success': False,
                'error': '路径不存在'
            })
    
    def do_GET(self):
        parsed_path = urlparse(self.path)
        
        if parsed_path.path == '/':
            # 返回管理页面
            self.send_page()
        elif parsed_path.path == '/health':
            tcp_server = self.server.server
            self.send_json_response(200, {
                'success': True,
                'status': 'running',
                'clients': len(tcp_server.get_clients_list())
            })
        elif parsed_path.path == '/clients':
            tcp_server = self.server.server
            clients = tcp_server.get_clients_list()
            self.send_json_response(200, {
                'success': True,
                'clients': clients,
                'count': len(clients)
            })
        elif parsed_path.path == '/check_username':
            # 检查用户名是否已存在
            query_params = parse_qs(parsed_path.query)
            username = query_params.get('username', [None])[0]
            
            if not username:
                self.send_json_response(400, {
                    'success': False,
                    'error': '缺少 username 参数'
                })
                return
            
            tcp_server = self.server.server
            exists = username in tcp_server.get_clients_list()
            
            self.send_json_response(200, {
                'success': True,
                'username': username,
                'exists': exists
            })
        else:
            self.send_json_response(404, {
                'success': False,
                'error': '路径不存在'
            })
    
    def send_page(self):
        """发送管理页面"""
        html = '''<!DOCTYPE html>
        <html lang="zh-CN">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>服务器管理面板</title>
            <style>
                * { margin: 0; padding: 0; box-sizing: border-box; }
                body {
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    min-height: 100vh;
                    padding: 20px;
                }
                .container {
                    max-width: 800px;
                    margin: 0 auto;
                    background: white;
                    border-radius: 10px;
                    box-shadow: 0 10px 40px rgba(0,0,0,0.2);
                    overflow: hidden;
                }
                .header {
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                    padding: 20px 30px;
                    text-align: center;
                }
                .header h1 { font-size: 24px; margin-bottom: 5px; }
                .header p { opacity: 0.9; font-size: 14px; }
                .content { padding: 30px; }
                .section { margin-bottom: 30px; }
                .section-title {
                    font-size: 18px;
                    color: #333;
                    margin-bottom: 15px;
                    padding-bottom: 10px;
                    border-bottom: 2px solid #667eea;
                }
                .client-list {
                    list-style: none;
                    max-height: 300px;
                    overflow-y: auto;
                    border: 1px solid #e0e0e0;
                    border-radius: 5px;
                }
                .client-item {
                    padding: 12px 20px;
                    border-bottom: 1px solid #f0f0f0;
                    cursor: pointer;
                    transition: all 0.3s;
                    display: flex;
                    align-items: center;
                }
                .client-item:hover {
                    background: #f5f5f5;
                }
                .client-item.selected {
                    background: #e8f0fe;
                    border-left: 3px solid #667eea;
                }
                .client-item:last-child { border-bottom: none; }
                .client-icon {
                    width: 30px;
                    height: 30px;
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    border-radius: 50%;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    color: white;
                    margin-right: 15px;
                    font-weight: bold;
                }
                .client-name { flex: 1; font-weight: 500; }
                .client-status {
                    width: 10px;
                    height: 10px;
                    background: #4caf50;
                    border-radius: 50%;
                }
                .form-group { margin-bottom: 20px; }
                .form-group label {
                    display: block;
                    margin-bottom: 8px;
                    color: #555;
                    font-weight: 500;
                }
                .form-control {
                    width: 100%;
                    padding: 12px 15px;
                    border: 1px solid #ddd;
                    border-radius: 5px;
                    font-size: 14px;
                    transition: border-color 0.3s;
                }
                .form-control:focus {
                    outline: none;
                    border-color: #667eea;
                }
                textarea.form-control {
                    min-height: 120px;
                    resize: vertical;
                }
                .btn {
                    padding: 12px 30px;
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                    border: none;
                    border-radius: 5px;
                    font-size: 16px;
                    cursor: pointer;
                    transition: transform 0.2s, box-shadow 0.2s;
                }
                .btn:hover {
                    transform: translateY(-2px);
                    box-shadow: 0 5px 20px rgba(102, 126, 234, 0.4);
                }
                .btn:active { transform: translateY(0); }
                .btn:disabled {
                    background: #ccc;
                    cursor: not-allowed;
                    transform: none;
                    box-shadow: none;
                }
                .message-box {
                    padding: 15px;
                    border-radius: 5px;
                    margin-top: 15px;
                    display: none;
                }
                .message-box.success {
                    background: #d4edda;
                    border: 1px solid #c3e6cb;
                    color: #155724;
                }
                .message-box.error {
                    background: #f8d7da;
                    border: 1px solid #f5c6cb;
                    color: #721c24;
                }
                .empty-state {
                    text-align: center;
                    padding: 40px 20px;
                    color: #999;
                }
                .empty-state-icon {
                    font-size: 48px;
                    margin-bottom: 15px;
                }
                .refresh-btn {
                    background: none;
                    border: 1px solid #667eea;
                    color: #667eea;
                    padding: 8px 15px;
                    border-radius: 5px;
                    cursor: pointer;
                    font-size: 14px;
                    margin-left: 10px;
                }
                .refresh-btn:hover {
                    background: #667eea;
                    color: white;
                }
                .header-actions {
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    margin-top: 10px;
                }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>🛠️ 服务器管理面板</h1>
                    <div class="header-actions">
                        <p>在线用户管理系统</p>
                        <button class="refresh-btn" onclick="loadClients()">🔄 刷新</button>
                    </div>
                </div>
                <div class="content">
                    <div class="section">
                        <h2 class="section-title">📋 在线用户列表</h2>
                        <ul class="client-list" id="clientList">
                            <li class="empty-state">
                                <div class="empty-state-icon">👥</div>
                                <div>加载中...</div>
                            </li>
                        </ul>
                    </div>
                    <div class="section">
                        <h2 class="section-title">✉️ 发送消息</h2>
                        <div class="form-group">
                            <label for="selectedUser">接收用户：</label>
                            <input type="text" id="selectedUser" class="form-control" readonly placeholder="请先从列表选择用户">
                        </div>
                        <div class="form-group">
                            <label for="messageContent">消息内容：</label>
                            <textarea id="messageContent" class="form-control" placeholder="输入要发送的消息..."></textarea>
                        </div>
                        <button class="btn" id="sendBtn" onclick="sendMessage()" disabled>📨 发送消息</button>
                        <div id="messageBox" class="message-box"></div>
                    </div>
                </div>
            </div>
            <script>
                let selectedClient = null;
                let clients = [];
                
                // 加载用户列表
                async function loadClients() {
                    try {
                        const response = await fetch('/clients');
                        const data = await response.json();
                        
                        if (data.success) {
                            clients = data.clients || [];
                            renderClientList(clients);
                        } else {
                            showError('加载用户列表失败');
                        }
                    } catch (error) {
                        showError('连接服务器失败：' + error.message);
                    }
                }
                
                // 渲染用户列表
                function renderClientList(clientList) {
                    const ul = document.getElementById('clientList');
                    
                    if (clientList.length === 0) {
                        ul.innerHTML = `
                            <li class="empty-state">
                                <div class="empty-state-icon">👥</div>
                                <div>暂无在线用户</div>
                            </li>
                        `;
                        return;
                    }
                    
                    ul.innerHTML = clientList.map(name => `
                        <li class="client-item ${selectedClient === name ? 'selected' : ''}" onclick="selectClient('${name}')">
                            <div class="client-icon">${name.charAt(0).toUpperCase()}</div>
                            <span class="client-name">${name}</span>
                            <div class="client-status"></div>
                        </li>
                    `).join('');
                }
                
                // 选择用户
                function selectClient(name) {
                    selectedClient = name;
                    document.getElementById('selectedUser').value = name;
                    document.getElementById('sendBtn').disabled = false;
                    
                    // 更新选中状态
                    const items = document.querySelectorAll('.client-item');
                    items.forEach(item => {
                        item.classList.remove('selected');
                        if (item.querySelector('.client-name').textContent === name) {
                            item.classList.add('selected');
                        }
                    });
                    
                    // 隐藏消息框
                    document.getElementById('messageBox').style.display = 'none';
                }
                
                // 发送消息
                async function sendMessage() {
                    const username = document.getElementById('selectedUser').value;
                    const content = document.getElementById('messageContent').value.trim();
                    
                    if (!username) {
                        showMessage('请先选择用户', 'error');
                        return;
                    }
                    
                    if (!content) {
                        showMessage('消息内容不能为空', 'error');
                        return;
                    }
                    
                    const sendBtn = document.getElementById('sendBtn');
                    sendBtn.disabled = true;
                    sendBtn.textContent = '发送中...';
                    
                    try {
                        const response = await fetch('/send_message', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json'
                            },
                            body: JSON.stringify({
                                username: username,
                                message: content
                            })
                        });
                        
                        const data = await response.json();
                        
                        if (data.success) {
                            showMessage('✓ 消息已成功发送给 ' + username, 'success');
                            document.getElementById('messageContent').value = '';
                        } else {
                            showMessage('✗ 发送失败：' + (data.error || '未知错误'), 'error');
                        }
                    } catch (error) {
                        showMessage('✗ 发送失败：' + error.message, 'error');
                    } finally {
                        sendBtn.disabled = false;
                        sendBtn.textContent = '📨 发送消息';
                    }
                }
                
                // 显示消息
                function showMessage(text, type) {
                    const messageBox = document.getElementById('messageBox');
                    messageBox.textContent = text;
                    messageBox.className = 'message-box ' + type;
                    messageBox.style.display = 'block';
                    
                    // 3 秒后自动隐藏
                    setTimeout(() => {
                        messageBox.style.display = 'none';
                    }, 3000);
                }
                
                // 显示错误
                function showError(text) {
                    const ul = document.getElementById('clientList');
                    ul.innerHTML = `
                        <li class="empty-state">
                            <div class="empty-state-icon">⚠️</div>
                            <div>${text}</div>
                        </li>
                    `;
                }
                
                // 页面加载时获取用户列表
                loadClients();
                
                // 每 60 秒自动刷新
                setInterval(loadClients, 60000);
            </script>
        </body>
        </html>'''
        
        try:
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(html.encode('utf-8'))
        except Exception as e:
            logging.error(f"发送页面时出错：{e}")
    
    def send_json_response(self, status_code: int, data: dict):
        try:
            self.send_response(status_code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))
        except Exception as e:
            logging.error(f"发送响应时出错: {e}")

class TCP_HTTP_Server:
    def __init__(self, host: str = '0.0.0.0', tcp_port: int = 8080, http_port: int = 8081):
        self.host = host
        self.tcp_port = tcp_port
        self.http_port = http_port
        self.clients: Dict[str, ClientHandler] = {}
        self.lock = threading.Lock()
        self.running = False
        
    def start(self):
        try:
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.bind((self.host, self.tcp_port))
            self.server_socket.listen(5)
            self.running = True
            
            logging.info(f"server tcp 服务器已启动，监听 {self.host}:{self.tcp_port}")
            
            http_server = HTTPServer((self.host, self.http_port), HTTPMessageHandler)
            http_server.server = self
            
            logging.info(f"server http 服务器已启动，监听 {self.host}:{self.http_port}")
            
            tcp_thread = threading.Thread(target=self.accept_tcp_connections)
            tcp_thread.daemon = True
            tcp_thread.start()
            
            http_thread = threading.Thread(target=http_server.serve_forever)
            http_thread.daemon = True
            http_thread.start()
                            
        except Exception as e:
            logging.error(f"启动服务器失败：{e}", exc_info=True)
            self.stop()
            raise
    
    def accept_tcp_connections(self):
        while self.running:
            try:
                client_socket, address = self.server_socket.accept()
                logging.info(f"server 新连接来自 {address}")
                
                handler = ClientHandler(client_socket, address, self)
                handler.start()
                
            except Exception as e:
                if self.running:
                    logging.error(f"接受连接时出错: {e}")
    
    def register_client(self, handler: ClientHandler) -> bool:
        should_broadcast = False
        broadcast_message = None
        
        with self.lock:
            if handler.name in self.clients:
                logging.warning(f"客户端名称 {handler.name} 已存在，拒绝注册")
                return False
            
            self.clients[handler.name] = handler
            
            should_broadcast = True
            broadcast_message = Message(
                MessageType.BROADCAST,
                {"content": f"用户 {handler.name} 加入了聊天室"},
                sender="server",
                target="all_users"
            )
        
        if should_broadcast:
            self.broadcast_message(broadcast_message)
        
        return True
    
    def unregister_client(self, handler: ClientHandler):
        should_broadcast = False
        broadcast_message = None
        
        with self.lock:
            if handler.name and handler.name in self.clients:
                del self.clients[handler.name]
                should_broadcast = True
                broadcast_message = Message(
                    MessageType.BROADCAST,
                    {"content": f"用户 {handler.name} 离开了聊天室"},
                    sender="server",
                    target="all_users"
                )
        
        if should_broadcast:
            self.broadcast_message(broadcast_message)
    
    def get_clients_list(self) -> list:
        with self.lock:
            return list(self.clients.keys())
    
    def send_message_to_client(self, target_name: str, message: Message) -> bool:
        with self.lock:
            handler = self.clients.get(target_name)
            if handler:
                handler.send_message(message)
                return True
            return False
    
    def broadcast_message(self, message: Message, exclude: str = None):
        with self.lock:
            for name, handler in self.clients.items():
                if name != exclude:
                    handler.send_message(message)
    
    def stop(self):
        self.running = False
        with self.lock:
            for handler in list(self.clients.values()):
                handler.close()
            self.clients.clear()
        
        if hasattr(self, 'server_socket'):
            self.server_socket.close()
        logging.info("服务器已停止")

def server_start():
    global server
    config = ConfigManager()
    host = config.get('server', 'host')
    tcp_port = int(config.get('server', 'tcp_port'))
    http_port = int(config.get('server', 'http_port'))
    server = TCP_HTTP_Server(host=host, tcp_port=tcp_port, http_port=http_port)
    server.start()

def server_stop():
    global server
    if server:
        server.stop()

def main():
    server_start()
    while True:
        try:
            sleep(1)
        except KeyboardInterrupt:
            break
    
    server_stop()

if __name__ == '__main__':
    main()