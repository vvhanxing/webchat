import dashscope
import json
from dashscope.audio.asr import Recognition, RecognitionCallback, RecognitionResult

from dashscope.audio.tts_v2 import SpeechSynthesizer, ResultCallback
from datetime import datetime
import threading
import uuid


from flask import Flask, render_template, request, Response, jsonify,stream_with_context

from flask_cors import CORS  # 新增
from flask_sock import Sock
import ssl
import httpx
from openai import OpenAI
import httpx
app = Flask(__name__)
CORS(app)
app.config['SOCK_SERVER_OPTIONS'] = {'ping_interval': 25}
sock = Sock(app)

# 设置 DashScope API Key
dashscope.api_key = 'sk-b25cfb29c8e48540625dd'
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
        if self.file:
            self.file.close()
        try:
            # 发送一个空的 blob 作为结束标记（类型为 audio/mpeg）
            self.ws.send(b"")  # 空字节流
        except Exception as e:
            print(f"[WARN] 发送空 blob 结束标记失败: {e}")

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
            #self.file.write(data)

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


@sock.route('/record')
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


# 用于保存每个用户的 conversation_id
conversation_ids = {}  # {user_id: conversation_id}

# API 配置
API_KEY = "app-GAutSi2mcN9kNSNPOUtYn4hS"
BASE_URL = "https://test-ookoo.is.panasonic.cn/v1/chat-messages"

# SSL 证书验证路径（如果你有自签名证书，可指定路径）
# 如果使用 verify=False，仅限测试环境
VERIFY_SSL = False  # 或指定证书路径，如 '/path/to/cert.pem'

import requests
@app.route('/chat', methods=['POST'])
def chat():
    try:
        data = request.get_json()
        user_input = data.get("text", "")
        user_id = data.get("user_id", "default_user")
        conversation_id = data.get("conversation_id", conversation_ids.get(user_id, ""))
        print("talking conversation id: ",conversation_id)

        if not user_input:
            return jsonify({"error": "未提供文本内容"}), 400

        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        }

        payload = {
            "query": user_input,
            "inputs": {},
            "response_mode": "streaming",
            "user": user_id,
            "conversation_id": conversation_id,
            "auto_generate_name": True,
            "files": []
        }

        def generate():
            try:
                with requests.post(
                    BASE_URL,
                    headers=headers,
                    json=payload,
                    stream=True,
                    verify=VERIFY_SSL  # 控制是否验证 SSL 证书
                ) as resp:
                    if resp.status_code != 200:
                        yield f"【错误】: {resp.text}"
                        return

                    answer = ""
                    for line in resp.iter_lines():
                        if line:
                            decoded_line = line.decode('utf-8')
                            if decoded_line.startswith("data: "):
                                event_data = decoded_line[len("data: "):]
                                try:
                                    event = json.loads(event_data)
                                    event_type = event.get("event")

                                    if event_type == "message":
                                        chunk = event.get("answer", "")
                                        answer += chunk
                                        yield chunk
                                    elif event_type == "message_end":
                                        new_conversation_id = event.get("conversation_id")
                                        if new_conversation_id:
                                            conversation_ids[user_id] = new_conversation_id
                                            yield f"\n【对话结束】\nConversation ID: {new_conversation_id}"
                                    elif event_type == "error":
                                        yield f"\n【错误】: {event.get('message')}"
                                    elif event_type == "ping":
                                        continue
                                    else:
                                        yield f"\n【其他事件】: {event}"
                                except json.JSONDecodeError:
                                    yield f"\n【JSON解析失败】: {decoded_line}"
            except requests.exceptions.RequestException as e:
                yield f"\n【网络错误】: {str(e)}"

        return Response(stream_with_context(generate()), mimetype='text/event-stream')

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/scenes', methods=['POST','GET'])
def get_scenes():
    scenes = [
    {"scene_name":"销售训练场景",
"role":["小王","小张"],
"character_description":["男性，35","女性，32"],
"scene_introduction":"扮演团队中的经理",
"conversation_goals":"改进计划，并获取承诺"}
]
    
    response = jsonify(scenes)
    response.headers['Content-Type'] = 'application/json; charset=utf-8'
    return response

if __name__ == '__main__':
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile='cert.pem', keyfile='key.pem')
    app.run(debug=True, host='0.0.0.0', port=5001, threaded=True, ssl_context=context)
