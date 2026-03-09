
import requests
import json
import time

URL = "http://localhost:8000/generate/stream"

def test_sliding_window():
    # Mocking some history
    history = [
        {"role": "user", "content": f"Message {i}"} for i in range(12)
    ]
    history.append({"role": "assistant", "content": "Hello! How can I help you today?"})
    
    payload = {
        "prompt": "What was my first message?",
        "history": history,
        "conversationId": 12345,
        "currentUrl": "https://example.com"
    }
    
    print(f"🚀 Sending request with {len(history)} history messages...")
    
    try:
        response = requests.post(URL, json=payload, stream=True)
        
        full_text = ""
        for line in response.iter_lines():
            if line:
                decoded_line = line.decode('utf-8')
                if decoded_line.startswith("data: "):
                    content = json.loads(decoded_line[6:])
                    if content["type"] == "text":
                        print(content["data"], end="", flush=True)
                        full_text += content["data"]
                    elif content["type"] == "done":
                        print("\n✅ Stream finished")
        
        print("\n--- Summary ---")
        print(f"Total history sent: {len(history)}")
        print(f"Prompt: {payload['prompt']}")
        print(f"Full Response: {full_text[:100]}...")
        
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    # Note: This assumes the server is running locally on port 8000
    print("⚠️  Ensure the FastAPI server is running on http://localhost:8000 before running this script.")
    test_sliding_window()
