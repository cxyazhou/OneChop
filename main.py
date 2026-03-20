import sys
import os
from time import sleep

# 将项目根目录添加到 Python 路径
root_dir = os.path.dirname(os.path.abspath(__file__))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from config import ConfigManager
from server import server_start, server_stop
from base_service.funasr.funasr_wss_server import funasr_server_start, funasr_server_stop
from agent_user.llm.llm import llm_start, llm_stop
from agent_user.speech_reg.speech_reg import speech_reg_start, speech_reg_stop

def main():
    # 启动服务器
    config = ConfigManager()
    if config.get('server', 'enable') == 'True':
        server_start()
    
    # 启动 funasr 服务端
    if config.get('funasr', 'enable') == 'True':
        funasr_server_start()
    
    # 启动 llm 客户端
    if config.get('llm', 'enable') == 'True':
        llm_start()

    # 启动语音识别客户端
    if config.get('speech_reg', 'enable') == 'True':
        speech_reg_start()

    while True:
        try:
           sleep(1)
        except KeyboardInterrupt:
            break

    # 关闭语音识别客户端
    if config.get('speech_reg', 'enable') == 'True':
        speech_reg_stop()

    # 关闭 llm 客户端
    if config.get('llm', 'enable') == 'True':
        llm_stop()
    
    # 关闭 funasr 服务端
    if config.get('funasr', 'enable') == 'True':
        funasr_server_stop()
    
    # 关闭服务器
    if config.get('server', 'enable') == 'True':
        server_stop()

if __name__ == "__main__":
    main()