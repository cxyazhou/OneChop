import configparser
import os

class ConfigManager:
    def __init__(self, config_file='config.ini'):
        self.config_file = config_file
        self.config = configparser.ConfigParser()
        self.load_config()
    
    def load_config(self):
        """加载配置文件"""
        if os.path.exists(self.config_file):
            self.config.read(self.config_file, encoding='utf-8')
        else:
            # 创建默认配置
            self.config['server'] = {
                'enable': 'True',
                'host': '127.0.0.1',
                'tcp_port': '8080',
                'http_port': '8081',
                'passwd': '123456'
            }
            self.config['client'] = {
                'tcp_url': '127.0.0.1:8080',
                'http_url': '127.0.0.1:8081',
                'heartbeat_interval': '60'
            }
            self.config['funasr'] = {
                'enable': 'True',
                'host': 'localhost',
                'port': '10095'
            }
            self.config['llm'] = {
                'enable': 'True',
                'username': 'llm',
                'gen_url': 'http://localhost:11434/api/generate',
                'chat_url': 'http://localhost:11434/api/chat',
                'model_name': 'qwen3-vl:8b'
            }
            self.config['speech_reg'] = {
                'enable': 'True',
                'username': 'speech_reg',
                'funasr_url': 'ws://localhost:10095',
                'wake_word': '小兔子'
            }
            
            self.save_config()
    
    def save_config(self):
        """保存配置"""
        with open(self.config_file, 'w', encoding='utf-8') as f:
            self.config.write(f)
    
    def get(self, section, key):
        """获取配置值"""
        return self.config.get(section, key)
    
    def set(self, section, key, value):
        """设置配置值"""
        if not self.config.has_section(section):
            self.config.add_section(section)
        self.config.set(section, key, str(value))
        self.save_config()