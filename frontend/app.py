"""Streamlit 前端骨架。启动：streamlit run frontend/app.py"""
import json

import httpx
import streamlit as st

API = "http://localhost:8000"

st.set_page_config(page_title="JobPilot", page_icon="🍮", layout="wide")
st.title("JobPilot · 智能求职助手")

# 侧边栏：知识库管理
with st.sidebar:
    st.header("📚 知识库")
    category = st.selectbox("类别", ["八股文", "项目笔记", "行业认知", "简历素材", "通用"])
    pasted = st.text_area("粘贴知识文本", height=150, placeholder="把八股文笔记、项目复盘贴进来…")
    if st.button("添加到知识库", type="primary", use_container_width=True) and pasted.strip():
        resp = httpx.post(f"{API}/kb/documents", json={
            "text": pasted, "source": "streamlit", "category": category,
        }, timeout=60)
        if resp.status_code == 200:
            data = resp.json()
            st.success(f"入库 {data['added']} 块，去重跳过 {data['skipped']} 块")
        else:
            st.error(resp.text)
    uploaded = st.file_uploader("或上传文件（md/txt/pdf/docx）")
    if uploaded is not None and st.button("上传入库", use_container_width=True):
        resp = httpx.post(f"{API}/kb/upload",
                          files={"file": (uploaded.name, uploaded.getvalue())},
                          data={"category": category}, timeout=120)
        if resp.status_code == 200:
            st.success(f"入库 {resp.json()['added']} 块")
        else:
            st.error(resp.text)

# 聊天区
if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if prompt := st.chat_input("输入 JD、问简历问题，或说「开始模拟面试」…"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        box = st.empty()
        full_reply = ""
        with httpx.stream("POST", f"{API}/chat/stream",
                          json={"session_id": "streamlit", "message": prompt},
                          timeout=300) as resp:
            event_name = None
            for line in resp.iter_lines():
                if line.startswith("event: "):
                    event_name = line[7:].strip()
                elif line.startswith("data: ") and event_name == "delta":
                    full_reply += json.loads(line[6:])["text"]
                    box.markdown(full_reply + "▌")
                elif line.startswith("data: ") and event_name == "error":
                    full_reply += f"\n\n**出错：**{json.loads(line[6:])['message']}"
        box.markdown(full_reply or "（后端无响应，请确认 uvicorn 已启动）")
    st.session_state.messages.append({"role": "assistant", "content": full_reply})
