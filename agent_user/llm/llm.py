import json
import requests
import os
from datetime import datetime
import sys
from time import sleep

# 将项目根目录添加到 Python 路径
root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from config import ConfigManager
from client import TCPClient, Message, MessageType
from client import check_username_exists

config = ConfigManager()

llm_client = None

# 本地大模型地址
LLM_GEN_API_URL = config.get('llm', 'gen_url')
LLM_CHAT_API_URL = config.get('llm','chat_url')
MODEL_NAME = config.get('llm','model_name')
OUTPUT_DIR = "./scripts"
#os.makedirs(OUTPUT_DIR, exist_ok=True)

def generate_short_drama_script(topic, total_episodes=5):
    global LLM_GEN_API_URL, MODEL_NAME, OUTPUT_DIR
    """
    根据主题生成分集短剧剧本
    :param topic: 用户输入的提示词/创意
    :param total_episodes: 生成的集数
    """
    print(f"🎬 正在构思短剧：《{topic}》... 预计生成 {total_episodes} 集")
    
    prompt = f"""
    你是一位爆款短剧编剧。请根据主题 "{topic}" 创作一部短剧。
    要求：
    1. 总共 {total_episodes} 集，每集时长约 40-60 秒。
    2. 剧情要紧凑，每集结尾要有悬念 (Hook)。
    3. 风格适合抖音/TikTok，节奏快，反转多。
    
    请严格按照以下 JSON 格式返回数据，不要包含 Markdown 标记，直接返回纯 JSON：
    {{
        "title": "短剧标题",
        "genre": "类型",
        "episodes": [
            {{
                "episode_number": 1,
                "plot_summary": "本集剧情简述",
                "narration_script": "完整的口播解说词 (约 150-200 字，口语化，有情绪)",
                "visual_prompts": [
                    "镜头1的详细画面提示词 (英文，包含风格、光影、动作)",
                    "镜头2的详细画面提示词",
                    "镜头3的详细画面提示词"
                ]
            }},
            ... (后续集数)
        ]
    }}
    """

    try:
        response = requests.post(LLM_GEN_API_URL, json={
            "model": MODEL_NAME,
            "prompt": prompt,
            "stream": False,
            "format": "json"
        })
        
        if response.status_code == 200:
            response_data = response.json()
            print(f"🔍 API 响应：{response_data}")
            
            # 尝试从 response 或 thinking 字段获取内容（某些模型会将内容放在 thinking 字段）
            raw_text = response_data.get('response', '') or response_data.get('thinking', '')
            
            if not raw_text:
                print(f"❌ API 返回空响应：response 和 thinking 字段都为空")
                return None
                
            print(f"📝 原始响应文本：{raw_text[:200]}...")
            
            # 清洗可能存在的 Markdown 标记
            clean_json = raw_text.replace("```json", "").replace("```", "").strip()
            
            if not clean_json:
                print("❌ 清洗后的 JSON 为空")
                return None
                
            try:
                script_data = json.loads(clean_json)
            except json.JSONDecodeError as je:
                print(f"❌ JSON 解析失败：{je}")
                print(f"   尝试解析的内容：{clean_json[:500]}")
                return None
            
            # 保存文件
            filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{script_data['title']}.json"
            # 文件名不能有特殊字符，简单处理
            safe_filename = "".join([c for c in filename if c.isalnum() or c in ('_', '.', '-')])
            filepath = os.path.join(OUTPUT_DIR, safe_filename)
            
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(script_data, f, ensure_ascii=False, indent=2)
            
            print(f"✅ 剧本生成成功！已保存至: {filepath}")
            print(f"📖 剧名：{script_data['title']}")
            for ep in script_data['episodes']:
                print(f"   - 第{ep['episode_number']}集: {ep['plot_summary'][:20]}...")
                
            return filepath
        else:
            print(f"❌ 模型调用失败: {response.text}")
            return None
            
    except Exception as e:
        print(f"❌ 发生错误: {e}")
        return None

def generate_chat(topic):
    global LLM_CHAT_API_URL, MODEL_NAME
    
    """
    返回聊天回复
    :param topic: 用户输入的提示词/创意
    """

    try:
        response = requests.post(LLM_CHAT_API_URL, json={
            "model": MODEL_NAME,
            'messages': [
                {'role': 'system', 'content': '你是一个乐于助人的助手，直接回答用户问题，不需要输出思考过程。'},
                {'role': 'user', 'content': topic}
            ],
            'stream': False,
            'options': {
                'temperature': 0.1,
                'num_predict': -1
            },
            "format": "json"
        })
        
        if response.status_code == 200:
            response_data = response.json()
            print(f"🔍 API 响应：{response_data}")
            
            # 尝试从 response 或 thinking 或 message 字段获取内容
            raw_text = response_data.get('response', '') or response_data.get('thinking', '') or response_data.get('message', '').get('content', '')
            
            if not raw_text:
                print(f"❌ API 返回空响应：response、thinking、 message.content 字段都为空")
                return None
            
            print(raw_text)

            return raw_text
        else:
            print(f"❌ 模型调用失败: {response.text}")
            return None
            
    except Exception as e:
        print(f"❌ 发生错误: {e}")
        return None
        
class LLMClient(TCPClient): 
    def process_message(self, message: Message):
        """
        重写父类方法，处理收到的消息
        :param message: Message 对象，包含发送方、内容等信息
        """
        if message.type == MessageType.PRIVATE_MESSAGE:
            # 收到私聊消息
            content = message.data.get('content', '')
            print(f"llm get private message: {content}")
            # 调用 LLM 生成回复
            #if content:
            #    reply = generate_chat(content)
            #    if reply:
            #        self.send_private_message(message.sender, reply)
        elif message.type == MessageType.BROADCAST:
            content = message.data.get('content', '')
            # 收到广播消息
            if message.sender == config.get('speech_reg', 'username'):
                if content:
                    reply = generate_chat(content)
                    if reply:
                        self.send_private_message(message.sender, reply)

def llm_start():
    global llm_client

    """启动 LLM 客户端"""    
    config = ConfigManager()
        
    # 获取用户名
    name = config.get('llm', 'username')
    if not name:
        name = "llm"
    
    # 检查用户名是否已存在
    if check_username_exists(name):
        print(f"用户名 '{name}' 已被使用")
        return

    # 连接到服务器
    llm_client = LLMClient()
    if not llm_client.connect(name):
        print("llm 连接服务器失败")
        return
    
def llm_stop():
    global llm_client

    """关闭 LLM 客户端"""
    if llm_client:
        try:
            llm_client.close()
        except Exception as e:
            print(f"llm 关闭连接时出错：{e}")

def main():
    llm_start()
    while True:
        try:
            sleep(1)
        except KeyboardInterrupt:
            break

    llm_stop()

if __name__ == "__main__":
    main()