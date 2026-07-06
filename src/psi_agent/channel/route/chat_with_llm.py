import requests
import json
import ast
import sys
sys.path.append('.')
SALANG_URL =  "http://192.168.0.2:8080/v1/chat/completions"
SALANG_MODEL = "Qwen3-8B"

class chat_with_qwen8b():
    def __init__(self, role, url=None):
        # 传入 prompt
        self.url = url 
        self.role = role

    def transfer_json_array(self):
        """尝试把返回的字符串解析为 JSON 数组，做一些容错处理。"""
        s = self.text.strip()
        self.json_list = []
        idx = s.rfind('<final>')
        if idx == -1:
            return
        text = s[idx + len('<final>'):-len('</final>')].lstrip()
        #print(text)
        try:
            obj = ast.literal_eval(text)
            if isinstance(obj, list):
                self.json_list = obj
            if isinstance(obj, dict):
                self.json_list = [obj]
        except Exception as e:
            print(f'transfer to json error: {e}')

    def chat(self):
        """
        text_list: 已经是 Python 对象（例如 [{'id':..., 'text':...}, ...]）
        返回：salang 返回的原始文本（str），供 transfer_json_array 解析
        """
        
        content = f"{self.input}"

        payload = {
            "model": SALANG_MODEL,
            # 常见字段名：prompt / input / text 等，按服务改写
            "messages": [
                {"role": "system", "content": self.role},
                {"role": "user", "content": content}
            ],
            # 可选参数，按需调整
            "max_tokens": 100000,
            "temperature": 0.1
        }

        headers = {"Content-Type": "application/json"}
        self.text = "" 
        try:
            if self.url:
                r = requests.post(self.url, json=payload, headers=headers)
            else:
                r = requests.post(SALANG_URL, json=payload, headers=headers)
            r.raise_for_status()
        except requests.RequestException as e:
            print("salang request failed:", e)
            try:
                print("resp text:", r.text[:2000])
            except Exception:
                return

        # 解析 JSON 并兼容常见返回格式
        try:
            j = r.json()
        except Exception:
            self.text = r.text
            return
        
        # chat/completions 风格
        if isinstance(j, dict) and "choices" in j and j["choices"]:
            ch0 = j["choices"][0]
            if isinstance(ch0, dict):
                # chat-style: {"choices":[{"message":{"content":"..."}}]}
                msg = ch0.get("message")
                if isinstance(msg, dict) and "content" in msg:
                    self.text = msg["content"]
                    return
                # fallback: {"choices":[{"text":"..."}]}
                if "text" in ch0 and isinstance(ch0["text"], str):
                    self.text = ch0["text"]
                    return
        # 其它常见格式
        if isinstance(j, dict):
            for k in ("output", "result", "text"):
                if k in j and isinstance(j[k], str):
                    self.text = j[k]
                    return
            if "results" in j and isinstance(j["results"], list) and j["results"]:
                first = j["results"][0]
                if isinstance(first, dict) and "text" in first:
                    self.text = first["text"]
                    return

        self.text = json.dumps(j, ensure_ascii=False)
    
    def run(self, input):
        self.input = input
        self.chat()
        self.transfer_json_array()

if __name__=="__main__":
    chat_agent = chat_with_qwen8b()
    chat_agent.run('')
    print(chat_agent.json_list)
