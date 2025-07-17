# app.py
from flask import Flask, render_template
from flask_sock import Sock
import dashscope
from dashscope.audio.tts_v2 import SpeechSynthesizer, ResultCallback
from datetime import datetime
import threading
import uuid

app = Flask(__name__)
app.config['SOCK_SERVER_OPTIONS'] = {'ping_interval': 25}
sock = Sock(app)

# 设置 DashScope API Key
dashscope.api_key = "sk-2b368fe2160f4223a82098770f28df0"

# 模型参数
TTS_MODEL = "cosyvoice-v2"
TTS_VOICE = "longxiaochun_v2"

# 存储每个用户的连接和合成器
user_sessions = {}

def get_timestamp():
    return datetime.now().strftime("[%Y-%m-%d %H:%M:%S.%f]")

class TTSStreamCallback(ResultCallback):
    def __init__(self, user_id, ws):
        self.user_id = user_id
        self.ws = ws
        self.file = None  # 用于调试保存音频文件
        self.connected = True

    def on_open(self):
        print(f"{get_timestamp()} [用户 {self.user_id}] 连接建立")
        self.file = open(f"output_{self.user_id}.mp3", "wb")

    def on_complete(self):
        print(f"{get_timestamp()} [用户 {self.user_id}] 语音合成完成")
        self.file.close()

    def on_error(self, message: str):
        print(f"{get_timestamp()} [用户 {self.user_id}] 合成错误：{message}")
        self.ws.send(f"ERROR:{message}")

    def on_close(self):
        print(f"{get_timestamp()} [用户 {self.user_id}] 连接关闭")
        self.connected = False
        if self.file:
            self.file.close()
        if self.user_id in user_sessions:
            del user_sessions[self.user_id]

    def on_data(self, data: bytes):
        if self.connected:
            self.ws.send(data)  # 直接发送 bytes，flask-sock 会自动识别为二进制帧
            self.file.write(data)

@sock.route('/tts')
def tts_websocket(ws):
    user_id = str(uuid.uuid4())
    print(f"{get_timestamp()} 新用户连接: {user_id}")

    callback = TTSStreamCallback(user_id, ws)
    synthesizer = SpeechSynthesizer(model=TTS_MODEL, voice=TTS_VOICE, callback=callback)
    user_sessions[user_id] = {
        "callback": callback,
        "synthesizer": synthesizer
    }

    try:
        while True:
            text = ws.receive()
            if text == 'close':
                break
            if isinstance(text, str) and text.startswith("TEXT:"):
                real_text = text[5:]
                print(f"{get_timestamp()} [用户 {user_id}] 收到文本：{real_text}")
                synthesizer.call(real_text)
    except Exception as e:
        print(f"WebSocket 错误：{e}")
    finally:
        if callback:
            callback.on_close()

@app.route('/')
def index():
    return render_template('index-2.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
