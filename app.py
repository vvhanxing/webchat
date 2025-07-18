import dashscope
import json
from dashscope.audio.asr import Recognition, RecognitionCallback, RecognitionResult

from dashscope.audio.tts_v2 import SpeechSynthesizer, ResultCallback
from datetime import datetime
import threading
import uuid


from flask import Flask, render_template, request, Response, jsonify
from flask_sock import Sock
import ssl
import httpx
from openai import OpenAI
import httpx
app = Flask(__name__)
app.config['SOCK_SERVER_OPTIONS'] = {'ping_interval': 25}
sock = Sock(app)

# 设置 DashScope API Key
dashscope.api_key = 'sk-b25cfb29c8e44b65b00fd8540625dddb'
# 模型参数
TTS_MODEL = "cosyvoice-v2"
TTS_VOICE = "longnan_v2"

# 存储每个用户的连接和合成器
user_sessions = {}
def get_timestamp():
    return datetime.now().strftime("[%Y-%m-%d %H:%M:%S.%f]")


proxy = "http://10.196.22.18:8080"



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






# 存储每个用户的识别器实例（根据用户唯一标识）
user_recognizers = {}

class WSCallback(RecognitionCallback):
    def __init__(self, ws, user_id):
        self.ws = ws
        self.user_id = user_id
        self.partial_text = ""

    def on_event(self, result: RecognitionResult):
        sentence = result.get_sentence()
        if 'text' in sentence:
            text = sentence['text']
            is_final = RecognitionResult.is_sentence_end(sentence)
            msg_type = "final" if is_final else "partial"
            try:
                self.ws.send(
                    json.dumps({
                        "type": msg_type,
                        "text": text
                    })
                )
            except Exception as e:
                print(f"[!] WebSocket send error for {self.user_id}: {e}")
            if is_final:
                self.partial_text = ""  # 句子结束，清空缓存
            else:
                self.partial_text = text

    def on_error(self, message):
        print(f"[ERROR] {self.user_id}: {message.message}")

    def on_complete(self):
        print(f"[INFO] {self.user_id}: Recognition completed.")


@sock.route('/ws')
def websocket_handler(ws):
    # 获取客户端 IP 和端口
    remote_addr = ws.environ.get('REMOTE_ADDR', 'unknown')
    remote_port = ws.environ.get('REMOTE_PORT', 'unknown')
    user_id = f"{remote_addr}:{remote_port}"
    print(f"[+] 用户连接: {user_id}")

    callback = WSCallback(ws, user_id)
    recognition = Recognition(
        model='paraformer-realtime-v2',
        format='pcm',
        sample_rate=16000,
        semantic_punctuation_enabled=False,
        callback=callback
    )

    recognition.start()

    try:
        while True:
            data = ws.receive()
            if not data:
                break
            # 将音频数据传入识别器
            recognition.send_audio_frame(data)
    except Exception as e:
        print(f"[!] 用户 {user_id} 异常断开：{e}")
    finally:
        recognition.stop()
        print(f"[-] 用户 {user_id} 已断开")


@app.route('/', methods=['GET', 'POST'])
def uploadpage():
    return render_template('index.html')


@app.route('/talk', methods=['GET', 'POST'])
def talk():
    return render_template('talk.html')

# 新增的 /chat 接口
@app.route('/chat', methods=['POST'])
def chat():
    try:
        data = request.get_json()
        user_input = data.get("text", "")
        print("user_input",user_input)
        if not user_input:
            return jsonify({"error": "未提供文本内容"}), 400

        client = OpenAI(
            api_key="sk-b25cfb29c8e44b65b00fd8540625dddb",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            http_client=httpx.Client(proxy=proxy)
        )

        def generate():
            stream = client.chat.completions.create(
                model="qwen-plus",
                messages=[
                    {"role": "system", "content": """
你是松下展示馆的介绍人员，以下是你准备的材料：
今年松下连续第7年出展进博会，出展面积1000平方米，消费品馆最大。我们以“Smart Life，Smart Society”为主题，带来涉及生活方面的家电和住宅设备的一体化解决方案，
以及贴合于绿色发展的车载产品和涉及智能制造的软硬件产品。住空间区域，用2大极具代入感的空间，为消费者提供美好生活的丰富产品和建议。
相较去年，生活相关展示区域扩大，以2个空间，分别展示适合于30代三口之家的年轻家庭和60代老人与宠物的2种家庭模式的空间设计，并在2个空间周边设置养老区、技术区和产品区，展示松下在空间设计、材质、收纳、清洁、安全呵护等方面的技术优势和设计特点。
放松空间，不妨来体验按摩椅带来的舒适。                     
通过以上材料为参展人员简要地解答问题。
                     
"""},
                    {"role": "user", "content": user_input}
                ],
                stream=True
            )
            for chunk in stream:
                content = chunk.choices[0].delta.content
                if content:
                    yield content

        return Response(generate(), mimetype='text/event-stream')

    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile='cert.pem', keyfile='key.pem')
    app.run(debug=True, host='0.0.0.0', port=5001, threaded=True, ssl_context=context)
