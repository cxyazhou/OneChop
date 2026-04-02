import os
import time
import threading
import math
import struct
import array
import queue
import pyaudio
import array
import websocket
import json as json_module
import time
import sys
from time import sleep
import keyboard

# 将项目根目录添加到 Python 路径
root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from config import ConfigManager
from client import TCPClient, Message, MessageType
from client import check_username_exists

config = ConfigManager()

speech_reg_client = None

# 音频配置
SAMPLE_RATE = 16000  # 采样率
CHANNELS = 1  # 单声道
FORMAT = 2  # 16 位整数 (paInt16)
FRAMES_PER_BUFFER = 1600  # 每帧采样点数
FRAMES_SIZE_PER_SECOND = SAMPLE_RATE * CHANNELS * FORMAT  # 每秒采样点数据大小

# FunASR 配置
FUNASR_SERVER_URL = config.get('speech_reg', 'funasr_url').strip()  # FunASR WebSocket 服务器地址
FUNASR_TIMEOUT = 30  # 识别超时时间（秒）
        
MODE_KEYBOARD_RECORD = "keyboard_record"  # 键盘录音模式
MODE_DIRECT_COMMAND = "direct_command"  # 直接命令模式
MODE_WAKE_WORD = "wake_word"  # 唤醒词检测模式

WAKE_WORD = config.get('speech_reg', 'wake_word').strip()  # 唤醒词

class SpeechRegClient(TCPClient):
    """语音识别客户端"""
    def __init__(self):
        # 调用父类构造函数
        super().__init__()
                
        # PortAudio 相关
        self.pa = None
        self.stream = None
        self.record_stream = None
        
        # 识别任务队列和线程
        self.recognition_queue = queue.Queue()
        self.recognition_thread = None
        self.is_recognizing = False
        self.recognition_lock = threading.Lock()

        self.recognition_mode = MODE_WAKE_WORD
       
        self.is_recording_keyboard = False  # 是否正在录音
        self.record_buffer_keyboard = bytearray()  # 录音缓冲区
        self.record_lock_keyboard = threading.Lock()  # 录音锁

        self.is_recording_command = False  # 是否正在录音
        self.record_buffer_command = bytearray()  # 录音缓冲区
        self.record_lock_command = threading.Lock()  # 录音锁

        self.is_recording_wake = False  # 是否正在录音
        self.record_buffer_wake = bytearray()  # 录音缓冲区
        self.record_lock_wake = threading.Lock()  # 录音锁
        self.wake_is_recognizing = False  # 唤醒词检测是否正在识别
        self.wake_words_reg_end = False  # 唤醒词检测是否结束
        self.is_wake = False  # 是否唤醒

        self.audio_value = [0] * 5  # 最近 5 帧的 RMS 值
        self.audio_init_cnt = 5
        self.speak_reg_sen = 10
        self.is_speaking = False  # 是否正在说话

        self.speak_start_time = 0  # 开始说话时间
        self.target_duration_command = 5  # 目标命令录音时长
        self.target_duration_wake = 2  # 目标唤醒词检测录音时长

        self.keyboard_listener = None  # 键盘监听线程
            
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
            
            # 打开音频流（用于录音）
            self.record_stream = self.pa.open(
                format=self.pa.get_format_from_width(FORMAT),
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                input=True,
                frames_per_buffer=FRAMES_PER_BUFFER,
                stream_callback=self.record_callback
            )
            
            self.record_stream.start_stream()
            print("音频录制已启动")
            
            # 启动识别处理线程
            self.recognition_thread = threading.Thread(target=self.process_recognition_queue)
            self.recognition_thread.daemon = True
            self.recognition_thread.start()
            print("识别处理线程已启动")
            
            # 启动键盘监听
            self.start_keyboard_listener()
                    
        except Exception as e:
            print(f"启动音频捕获失败：{e}")
    
    def stop_audio_capture(self):
        # 发送退出信号到识别队列
        self.recognition_queue.put(None)  # 退出信号
        
        # 停止键盘监听
        try:
            if hasattr(self, 'keyboard_hook') and self.keyboard_hook:
                keyboard.unhook(self.keyboard_hook)
            self.keyboard_hooks_registered = False
            print("键盘监听已停止")
        except Exception as e:
            print(f"停止键盘监听时出错：{e}")
        
        # 停止音频流
        if self.record_stream:
            self.record_stream.stop_stream()
            self.record_stream.close()
        
        if self.pa:
            self.pa.terminate()

    def record_callback(self, in_data, frame_count, time_info, status):
        # 这里需要加锁，确保线程安全写入缓冲区，读取的地方也需要加锁并且进行拷贝后将缓冲区数据清空及重置录音状态
        with self.record_lock_keyboard:
            if self.is_recording_keyboard:
                self.record_buffer_keyboard.extend(in_data)
        
        with self.record_lock_command:
            if self.is_recording_command:
                self.record_buffer_command.extend(in_data)
        
        with self.record_lock_wake:
            if self.is_recording_wake:
                self.record_buffer_wake.extend(in_data)
        
        if self.recognition_mode == MODE_DIRECT_COMMAND:
            self.detect_speech_command(in_data)

        if self.recognition_mode == MODE_WAKE_WORD:
            if not self.wake_is_recognizing:
                self.detect_speech_wake(in_data)
            else:
                self.wake_process(in_data)
        
        return (None, pyaudio.paContinue)
    
    def detect_speech_command(self, in_data):
        """自动检测语音并开始录音"""
        # 计算音频 RMS 值        
        frame_size = len(in_data)
        sum_square = 0.0
        interval = 10
        
        for cnt in range(0, frame_size, 2):  # 16 位音频，每 2 个字节一个采样点
            if (cnt // 2) % interval == 0:
                sample = struct.unpack('<h', in_data[cnt:cnt+2])[0]
                sum_square += sample * sample
        
        rms = math.sqrt(sum_square / (frame_size // 2 // interval)) / 32768.0
        
        cur = 0
        if rms > 0:
            cur = min(100, max(0, int(20.0 * math.log10(rms) + 70)))
        
        # 初始化前 5 帧
        if self.audio_init_cnt > 0:
            self.audio_value[5 - self.audio_init_cnt] = cur
            self.audio_init_cnt -= 1
            return
        
        # 计算最近 5 帧的平均 RMS 值
        all_sum = sum(self.audio_value)
        ave = all_sum // 5
        
        # 更新音频值缓冲区
        for i in range(4):
            self.audio_value[i] = self.audio_value[i + 1]
        self.audio_value[4] = cur
        
        # 检测是否开始说话（音量陡升）
        if cur > (ave + self.speak_reg_sen) and not self.is_speaking:
            # 开始说话，启动录音
            self.is_speaking = True
            self.speak_start_time = time.time()

            with self.record_lock_command:
                if not self.is_recording_command:
                    self.is_recording_command = True
                    self.record_buffer_command.extend(in_data)
                    print("开始命令录音...")
        
        # 如果正在说话，检查是否达到目标时长
        if self.is_speaking:
            elapsed = time.time() - self.speak_start_time
            if elapsed >= self.target_duration_command:
                # 达到目标时长，停止录音
                self.is_speaking = False

                audio_data = b''
                with self.record_lock_command:
                    if len(self.record_buffer_command) > 0:
                        audio_data = bytes(self.record_buffer_command)
                    self.is_recording_command = False
                    print(f"命令录音结束，录制了 {len(self.record_buffer_command)} 字节")
                    self.record_buffer_command.clear()
                
                # 将录音数据放入识别队列
                if len(audio_data) >= FRAMES_SIZE_PER_SECOND:
                    if self.recognition_queue.qsize() > 0:
                        print("识别队列已满，已忽略")
                    else:
                        with self.recognition_lock:
                            if not self.is_recognizing:
                                self.recognition_queue.put(audio_data)
                            else:
                                print("上一次识别未完成，已忽略")
                else:
                    print("录音数据过短，已忽略")

    def detect_speech_wake(self, in_data):
        """自动检测语音并开始录音"""
        # 计算音频 RMS 值        
        frame_size = len(in_data)
        sum_square = 0.0
        interval = 10
        
        for cnt in range(0, frame_size, 2):  # 16 位音频，每 2 个字节一个采样点
            if (cnt // 2) % interval == 0:
                sample = struct.unpack('<h', in_data[cnt:cnt+2])[0]
                sum_square += sample * sample
        
        rms = math.sqrt(sum_square / (frame_size // 2 // interval)) / 32768.0
        
        cur = 0
        if rms > 0:
            cur = min(100, max(0, int(20.0 * math.log10(rms) + 70)))
        
        # 初始化前 5 帧
        if self.audio_init_cnt > 0:
            self.audio_value[5 - self.audio_init_cnt] = cur
            self.audio_init_cnt -= 1
            return
        
        # 计算最近 5 帧的平均 RMS 值
        all_sum = sum(self.audio_value)
        ave = all_sum // 5
        
        # 更新音频值缓冲区
        for i in range(4):
            self.audio_value[i] = self.audio_value[i + 1]
        self.audio_value[4] = cur
        
        # 检测是否开始说话（音量陡升）
        if cur > (ave + self.speak_reg_sen) and not self.is_speaking:
            # 开始说话，启动录音
            self.is_speaking = True
            self.speak_start_time = time.time()

            with self.record_lock_wake:
                if not self.is_recording_wake:
                    self.is_recording_wake = True
                    self.record_buffer_wake.extend(in_data)
                    print("开始唤醒词检测录音...")
        
        # 如果正在说话，检查是否达到目标时长
        if self.is_speaking:
            elapsed = time.time() - self.speak_start_time
            if elapsed >= self.target_duration_wake:
                # 达到目标时长，停止录音
                self.is_speaking = False

                audio_data = b''
                with self.record_lock_wake:
                    if len(self.record_buffer_wake) > 0:
                        audio_data = bytes(self.record_buffer_wake)
                    self.is_recording_wake = False
                    print(f"唤醒词检测录音结束，录制了 {len(self.record_buffer_wake)} 字节")
                    self.record_buffer_wake.clear()
                
                # 将录音数据放入识别队列
                if len(audio_data) >= FRAMES_SIZE_PER_SECOND:
                    if self.recognition_queue.qsize() > 0:
                        print("识别队列已满，已忽略")
                    else:
                        with self.recognition_lock:
                            if not self.is_recognizing:
                                self.recognition_queue.put(audio_data)
                                self.wake_is_recognizing = True
                                self.wake_words_reg_end = False
                            else:
                                print("上一次识别未完成，已忽略")
                else:
                    print("录音数据过短，已忽略")

    def wake_process(self, in_data):
        """唤醒词检测处理"""
        if not self.wake_words_reg_end:
            return

        if not self.is_wake:
            self.wake_is_recognizing = False
            return
        
        if not self.is_speaking:
            # 开始说话，启动录音
            self.is_speaking = True
            self.speak_start_time = time.time()

            with self.record_lock_wake:
                if not self.is_recording_wake:
                    self.is_recording_wake = True
                    self.record_buffer_wake.extend(in_data)
                    print("开始唤醒后录音...")
        
        # 如果正在说话，检查是否达到目标时长
        if self.is_speaking:
            elapsed = time.time() - self.speak_start_time
            if elapsed >= self.target_duration_command:
                # 达到目标时长，停止录音
                self.is_speaking = False

                audio_data = b''
                with self.record_lock_wake:
                    if len(self.record_buffer_wake) > 0:
                        audio_data = bytes(self.record_buffer_wake)
                    self.is_recording_wake = False
                    print(f"唤醒后录音结束，录制了 {len(self.record_buffer_wake)} 字节")
                    self.record_buffer_wake.clear()
                
                # 将录音数据放入识别队列
                if len(audio_data) >= FRAMES_SIZE_PER_SECOND:
                    if self.recognition_queue.qsize() > 0:
                        print("识别队列已满，已忽略")
                    else:
                        with self.recognition_lock:
                            if not self.is_recognizing:
                                self.recognition_queue.put(audio_data)
                            else:
                                print("上一次识别未完成，已忽略")
                else:
                    print("录音数据过短，已忽略")
                    
                self.wake_is_recognizing = False

    def start_keyboard_listener(self):
        """启动键盘监听线程"""
        
        # 防止重复注册
        if hasattr(self, 'keyboard_hooks_registered') and self.keyboard_hooks_registered:
            print("键盘监听已启动，跳过重复注册")
            return
        
        # 按键状态
        self.ctrl_pressed = False
        
        def keyboard_handler(event):
            try:
                key_name = getattr(event, 'name', str(event))
                event_type = getattr(event, 'event_type', 'unknown')
                
                # print(f"键盘事件：{event_type} - {key_name}")
                
                if event_type == 'down':
                    # 功能键处理（只响应按下事件）
                    # Ctrl 键按下
                    if key_name in ['ctrl', 'left ctrl', 'right ctrl']:
                        self.ctrl_pressed = True
                    # 检查是否在按 Ctrl 的同时按下空格
                    elif key_name == 'space' and self.ctrl_pressed:
                        if self.recognition_mode == MODE_KEYBOARD_RECORD:
                            with self.record_lock_keyboard:
                                if not self.is_recording_keyboard:
                                    self.is_recording_keyboard = True
                                    print("开始录音...")
                
                elif event_type == 'up':
                    # 按键释放
                    if key_name in ['ctrl', 'left ctrl', 'right ctrl']:
                        self.ctrl_pressed = False
                    # 检查空格键是否释放
                    elif key_name == 'space':
                        audio_data = b''
                        with self.record_lock_keyboard:
                            if len(self.record_buffer_keyboard) > 0:
                                audio_data = bytes(self.record_buffer_keyboard)
                            self.is_recording_keyboard = False
                            print(f"键盘录音结束，录制了 {len(self.record_buffer_keyboard)} 字节")
                            self.record_buffer_keyboard.clear()
                        
                        # 将录音数据放入识别队列
                        if len(audio_data) >= FRAMES_SIZE_PER_SECOND:
                            if self.recognition_queue.qsize() > 0:
                                print("识别队列已满，已忽略")
                            else:
                                with self.recognition_lock:
                                    if not self.is_recognizing:
                                        self.recognition_queue.put(audio_data)
                                    else:
                                        print("上一次识别未完成，已忽略")
                        else:
                            print("录音数据过短，已忽略")
            except Exception as e:
                print(f"键盘处理函数出错：{e}")
        
        # 注册全局键盘事件钩子
        self.keyboard_hook = keyboard.hook(keyboard_handler)
        self.keyboard_hooks_registered = True
        
        print("键盘监听已启动（按住 Ctrl+ 空格键录音）")
    
    def process_recognition_queue(self):
        """处理识别队列的后台线程"""
        while self.running:
            try:
                if self.recognition_queue.empty():
                    sleep(0.1)
                    continue

                with self.recognition_lock:
                    self.is_recognizing = True
                
                # 从队列中获取音频数据进行识别
                audio_data = self.recognition_queue.get(timeout=1.0)
                
                if audio_data is None:
                    # 退出信号
                    print("audio_data 为空，识别处理线程退出")
                    with self.recognition_lock:
                        self.is_recognizing = False
                    break
                
                # 执行识别
                result = self.recognize_audio(audio_data)
                
                # 如果有识别结果，处理
                #if result and len(result) > 0:
                #    print(f"识别结果：{result}")

                command = None

                if self.recognition_mode == MODE_DIRECT_COMMAND or self.recognition_mode == MODE_KEYBOARD_RECORD:
                    command = result

                if self.recognition_mode == MODE_WAKE_WORD:
                    if self.is_wake:
                        command = result
                    if WAKE_WORD in result:
                        self.is_wake = True
                    else:
                        self.is_wake = False

                self.wake_words_reg_end = True

                with self.recognition_lock:
                    self.is_recognizing = False

                if command is not None:
                    print(f"命令：{command}")
                    self.broadcast(f"[命令] {self.name}: {command}")
                
                if(command == "键盘录制"):
                    self.recognition_mode = MODE_KEYBOARD_RECORD
                elif(command == "识别命令"):
                    self.recognition_mode = MODE_DIRECT_COMMAND
                elif(command == "唤醒模式"):
                    self.recognition_mode = MODE_WAKE_WORD

            except queue.Empty:
                # 队列为空，继续等待
                continue
            except Exception as e:
                print(f"处理识别队列时出错：{e}")
                with self.recognition_lock:
                    self.is_recognizing = False
        
        print("识别处理线程已退出")

def speech_reg_start():
    global speech_reg_client
    global config
    
    """启动语音识别客户端"""    
    
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
    
    """关闭语音识别客户端"""
    if speech_reg_client:
        try:
            speech_reg_client.stop_audio_capture()
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