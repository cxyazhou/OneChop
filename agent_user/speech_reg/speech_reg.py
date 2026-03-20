import json
import requests
import os
import time
import threading
import math
import socket
import array
import queue
from datetime import datetime
import pyaudio
import array
import websocket
import json as json_module
import time
import sys
from time import sleep

speech_reg_client = None

# 将项目根目录添加到 Python 路径
root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from config import ConfigManager
from client import TCPClient, Message, MessageType
from client import check_username_exists

config = ConfigManager()

# 音频配置
SAMPLE_RATE = 16000  # 采样率
CHANNELS = 1  # 单声道
FORMAT = 2  # 16 位整数 (paInt16)
FRAMES_PER_BUFFER = 1600  # 每帧采样点数

# 语音识别配置
CMD_FRAME_NUM = SAMPLE_RATE // FRAMES_PER_BUFFER * 5  # 命令词帧数
NAME_FRAME_NUM = SAMPLE_RATE // FRAMES_PER_BUFFER * 2 # 注册名帧数
SPEAK_REG_SEN = 10  # 语音检测灵敏度

# FunASR 配置
FUNASR_SERVER_URL = config.get('speech_reg', 'funasr_url').strip()  # FunASR WebSocket 服务器地址
FUNASR_TIMEOUT = 30  # 识别超时时间（秒）

class AudioBuffer:
    """音频缓冲区"""
    def __init__(self, buffer_size=1024*100):
        self.buffer = bytearray(buffer_size)
        self.len = 0
        self.buffer_size = buffer_size
        self.last_buffer = bytearray(FRAMES_PER_BUFFER * 2)  # 最后一帧缓冲区
        self.frame_size = FRAMES_PER_BUFFER * 2  # 每帧字节数 (16 位)
        self.speak_start = 0
        self.init_cnt = 5  # 初始化计数器
        
class SpeechRegClient(TCPClient):
    """语音注册客户端"""
    def __init__(self):
        # 调用父类构造函数
        super().__init__()
        
        # 音频相关
        self.audio_buffer = AudioBuffer()
        self.audio_value = [0] * 5  # 最近 5 帧的 RMS 值
        self.audio_init_cnt = 5
        self.speak_sen = 0
        self.speak_reg_sen = SPEAK_REG_SEN
        
        # PortAudio 相关
        self.pa = None
        self.stream = None
        
        # 识别任务队列和线程
        self.recognition_queue = queue.Queue()
        self.recognition_thread = None
        self.is_recognizing = False
    
    def process_message(self, message: Message):
        """
        重写父类方法，处理收到的消息
        :param message: Message 对象，包含发送方、内容等信息
        """
        pass
    
    def send_private_message(self, target: str, content: str):
        """发送私聊消息"""
        message = Message(
            MessageType.SEND_MESSAGE,
            {"content": content},
            sender=self.username,
            target=target
        )
        self.send_message(message)
    
    def broadcast(self, content: str):
        """广播消息"""
        message = Message(MessageType.BROADCAST, {"content": content}, sender=self.name, target="all_users")
        self.send_message(message)
    
    def audio_shot(self, audio_data):
        """
        音频检测算法 - 计算 RMS 并检测突变
        :param audio_data: 音频数据 (16 位整数数组)
        :return: 0 表示检测到突变，-1 表示正常
        """
        # 转换为 short 数组
        pdata = array.array('h', audio_data)
        frame_count = len(pdata)
        
        # 计算 RMS（均方根）
        sum_square = 0.0
        interval = 10  # 计算采样点的间隔
        
        for cnt in range(frame_count):
            if cnt % interval == 0:
                sample = float(pdata[cnt])
                sum_square += sample * sample
        
        # 计算 RMS 值
        rms = math.sqrt(sum_square / (frame_count / interval)) / 32768.0
        
        cur = 0
        if rms > 0:
            cur = min(100, max(0, int(20.0 * math.log10(rms) + 70)))
        
        # 初始化前 5 帧
        if self.audio_init_cnt > 0:
            self.audio_value[5 - self.audio_init_cnt] = cur
            self.audio_init_cnt -= 1
            return -1
        
        # 计算最近 5 帧的平均 RMS 值
        all_sum = sum(self.audio_value)
        ave = all_sum // 5
        
        # 更新音频值缓冲区
        for i in range(4):
            self.audio_value[i] = self.audio_value[i + 1]
        self.audio_value[4] = cur
        
        # 检测突变
        if cur > ave and (cur - ave) > 5:
            self.speak_sen = cur - ave
        
        if cur > (ave + self.speak_reg_sen):
            return 0  # 检测到突变
        
        return -1  # 正常
    
    def reg_name(self, audio_data):
        """
        注册名字 - 收集音频
        :param audio_data: 音频数据
        :return: 需要识别的音频数据（bytes），如果没有收集完成则返回 None
        """
        # 初始化阶段
        if self.audio_buffer.init_cnt > 0:
            self.audio_buffer.init_cnt -= 1
            # 保存最后一帧
            frame_size = self.audio_buffer.frame_size
            if len(audio_data) >= frame_size:
                self.audio_buffer.last_buffer = audio_data[:frame_size]
            return None
        
        # 检测是否开始说话
        if self.audio_buffer.speak_start == 0:
            sr_name_flag = self.audio_shot(audio_data)
            if sr_name_flag != 0:
                # 保存最后一帧
                frame_size = self.audio_buffer.frame_size
                if len(audio_data) >= frame_size:
                    self.audio_buffer.last_buffer = audio_data[:frame_size]
                return None
            
            print("检测到开始说话")
            self.audio_buffer.speak_start = NAME_FRAME_NUM
            
            # 清空缓冲区
            self.audio_buffer.buffer = bytearray(self.audio_buffer.buffer_size)
            self.audio_buffer.len = 0
            
            # 保存第一帧
            self.audio_buffer.buffer[:len(audio_data)] = audio_data
            self.audio_buffer.len += len(audio_data)
            
            self.audio_buffer.speak_start -= 1
            return None
        
        # 继续收集音频
        self.audio_buffer.speak_start -= 1
        
        # 添加到缓冲区
        if self.audio_buffer.len + len(audio_data) <= self.audio_buffer.buffer_size:
            self.audio_buffer.buffer[self.audio_buffer.len:self.audio_buffer.len + len(audio_data)] = audio_data
            self.audio_buffer.len += len(audio_data)
        
        # 收集完成，返回音频数据供识别
        if self.audio_buffer.speak_start == 0:
            print(f"音频收集完成，长度：{self.audio_buffer.len} 字节")
            
            # 返回需要识别的音频数据
            audio_to_recognize = bytes(self.audio_buffer.buffer[:self.audio_buffer.len])
            
            # 重置状态
            self.audio_buffer.speak_start = 0
            self.audio_buffer.init_cnt = 5
            
            return audio_to_recognize
        
        return None
    
    def recognize_audio(self, audio_data):
        """
        识别音频
        :param audio_data: 音频数据 (PCM 格式)
        :return: 识别结果字符串
        """
        try:            
            # 创建 WebSocket 连接
            ws = websocket.create_connection(
                f"{FUNASR_SERVER_URL}",
                timeout=FUNASR_TIMEOUT
            )
            
            try:
                # 发送配置消息
                config = {
                    "mode": "offline",
                    "audio_fs": SAMPLE_RATE,
                    "wav_name": "demo",
                    "wav_format": "pcm",
                    "is_speaking": True,
                    "hotwords": "{\"hello world\": 20}"
                }
                
                ws.send(json_module.dumps(config))
                
                # 计算分块参数
                chunk_size = 10  # 毫秒
                chunk_interval = 10  # 毫秒
                stride = int(60 * chunk_size / chunk_interval / 1000.0 * SAMPLE_RATE * 2)
                
                # 分块发送音频数据
                size = len(audio_data)
                chunk_num = (size - 1) // stride + 1
                
                for i in range(chunk_num):
                    start = i * stride
                    if i != chunk_num - 1:
                        # 发送音频块
                        ws.send(audio_data[start:start + stride], opcode=websocket.ABNF.OPCODE_BINARY)
                    else:
                        # 发送最后一块音频数据
                        remaining = size % stride
                        ws.send(audio_data[start:start + remaining], opcode=websocket.ABNF.OPCODE_BINARY)
                        
                        # 发送结束消息
                        end_msg = {"is_speaking": False}
                        ws.send(json_module.dumps(end_msg))
                    
                    # 短暂延迟
                    time.sleep(0.001)
                
                # 等待识别结果
                result_text = ""
                result_ready = False
                
                while not result_ready:
                    try:
                        ws.settimeout(0.1)
                        result = ws.recv()
                        
                        # 解析结果
                        result_json = json_module.loads(result)
                        
                        if "text" in result_json:
                            result_text = result_json["text"]
                            result_ready = True
                            
                    except websocket.WebSocketTimeoutException:
                        continue
                    except Exception as e:
                        print(f"接收结果时出错：{e}")
                        break
                
                return result_text
                
            finally:
                ws.close()
                
        except Exception as e:
            print(f"音频识别过程中出错：{e}")
            return ""
    
    def start_audio_capture(self):
        """开始音频捕获"""
        try:
            self.pa = pyaudio.PyAudio()
            
            # 打开音频流
            self.stream = self.pa.open(
                format=self.pa.get_format_from_width(2),  # 16 位
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                input=True,
                frames_per_buffer=FRAMES_PER_BUFFER,
                stream_callback=self.audio_callback
            )
            
            self.stream.start_stream()
            print("音频捕获已启动")
            
            # 启动识别处理线程
            self.recognition_thread = threading.Thread(target=self.process_recognition_queue)
            self.recognition_thread.daemon = True
            self.recognition_thread.start()
            print("识别处理线程已启动")
            
        except Exception as e:
            print(f"启动音频捕获失败：{e}")
    
    def process_recognition_queue(self):
        """处理识别队列的后台线程"""
        print("识别处理线程开始运行...")
        while self.running:
            try:
                # 从队列中获取音频数据进行识别
                audio_data = self.recognition_queue.get(timeout=1.0)
                
                if audio_data is None:
                    # 退出信号
                    break
                
                # 执行识别
                self.is_recognizing = True
                result = self.recognize_audio(audio_data)
                self.is_recognizing = False
                
                # 如果有识别结果，发送出去
                if result and len(result) > 0:
                    print(f"识别结果：{result}")
                    self.broadcast(f"[语音识别] {self.name}: {result}")
                
            except queue.Empty:
                # 队列为空，继续等待
                continue
            except Exception as e:
                print(f"处理识别队列时出错：{e}")
                self.is_recognizing = False
        
        print("识别处理线程已退出")
    
    def audio_callback(self, in_data, frame_count, time_info, status):
        """音频回调函数 - 只负责收集音频，不阻塞识别"""
        if self.running:
            # 将音频数据交给 reg_name 处理（检测是否开始说话）
            result = self.reg_name(in_data)
            
            # 如果有识别结果，加入队列异步处理
            # 注意：reg_name 返回的是需要识别的音频数据
            if result is not None and isinstance(result, bytes):
                # 将音频数据放入队列，由后台线程处理
                if self.recognition_queue.qsize() < 1:
                    self.recognition_queue.put(result)
        
        return (None, pyaudio.paContinue)
    
    def close(self):
        """关闭连接"""
        self.running = False
        
        # 发送退出信号到识别队列
        if hasattr(self, 'recognition_queue'):
            self.recognition_queue.put(None)  # 退出信号
        
        # 停止音频流
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
        
        if self.pa:
            self.pa.terminate()
        
        # 关闭 socket
        if self.socket:
            try:
                self.socket.shutdown(socket.SHUT_RDWR)
                self.socket.close()
            except:
                pass

def speech_reg_start():
    global speech_reg_client

    """启动语音注册客户端"""    
    config = ConfigManager()
    
    # 获取用户名
    name = config.get('speech_reg', 'username')
    if not name:
        name = "speech_reg"
    
    # 检查用户名是否已存在
    if check_username_exists(name):
        print(f"用户名 '{name}' 已被使用")
        return
    
    # 连接到服务器
    speech_reg_client = SpeechRegClient()
    if not speech_reg_client.connect(name):
        print("speech_reg 连接服务器失败")
        return
    
    # 启动音频捕获
    speech_reg_client.start_audio_capture()
    
    print(f"speech_reg 已连接，等待语音输入...")

def speech_reg_stop():
    global speech_reg_client
    
    """关闭语音注册客户端"""
    if speech_reg_client:
        try:
            speech_reg_client.close()
        except Exception as e:
            print(f"speech_reg 关闭连接时出错：{e}")

def main():
    speech_reg_start()
    while True:
        try:
            sleep(1)
        except KeyboardInterrupt:
            break

    speech_reg_stop()

if __name__ == "__main__":
    main()