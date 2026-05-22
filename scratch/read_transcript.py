import json

log_path = r"C:\Users\HP\.gemini\antigravity\brain\13632467-02e9-4005-8a66-5523b4324f16\.system_generated\logs\transcript.jsonl"

user_inputs = []
with open(log_path, 'r', encoding='utf-8') as f:
    for line in f:
        try:
            data = json.loads(line)
            if data.get("type") == "USER_INPUT":
                user_inputs.append(data)
        except Exception:
            pass

print(f"Total user inputs: {len(user_inputs)}")
for ui in user_inputs[-10:]:
    print("---")
    print(f"Step: {ui.get('step_index')}")
    print(f"Content: {ui.get('content')}")
