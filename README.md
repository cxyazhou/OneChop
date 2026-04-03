# OneChop

基于聊天方式的AI Agent。每个用户就是拥有独立功能的AI Agent，用户接收其他用户发送的消息，根据消息内容执行任务，最后将执行结果发送给其他用户。

## 功能特性

- 一个服务器管理所有用户，负责用户信息的转发
- 每个用户的用户名唯一，并且要体现自己实现的功能
- 用户发送消息给其他用户，其他用户根据消息内容执行任务
- 用户完成任务后，可以将执行结果当消息内容发送给其他用户
- 服务器及每个用户都可以部署到不同的设备上，来实现分布式部署
- 系统提供一些基础服务功能供用户调用，如语音转文字、文字转语音等
- 用户可能依赖第三方服务，如ollama服务、LocallAI服务等

## 运行
```bash
# 下载代码
git clone https://github.com/cxyazhou/OneChop
# 下载模型
cd OneChop
git lfs pull
# 安装python依赖包，python版本号：3.12
pip install -r requirements.txt
# 运行
python main.py
```

## 模块介绍

### 一、服务器
- 提供一个http服务，可以获取在线用户列表及给用户发送信息
- 提供一个tcp服务，提供功能的用户和服务器保持长链接
- 浏览器访问http网址即可获取在线用户列表及给用户发送消息
- 完成用户的信息转发

#### 启动
配置config.ini文件，将server.enable设置为True即可启动服务器

### 二、基础服务模块

1、ollama服务
- 提供现成的模型，用户可以直接调用
- 模型的下载和管理在ollama服务端进行

#### PC端启动
```bash
直接下载exe安装启动即可
```

2、funasr语音识别模块
- 接收一段语音，返回文字

#### 启动
```bash
配置config.ini文件，将funasr.enable设置为True即可启动模块
```

### 三、用户模块

1、llm
- 收到消息后，送到大语言模型处理，返回得到的文本结果

#### 启动
```bash
配置config.ini文件，将llm.enable设置为True即可启动模块
```

2、语音命令识别模块
- 监控麦克风，识别语音命令然后执行对应任务

#### 启动
```bash
配置config.ini文件，将speech_reg.enable设置为True即可启动模块
```