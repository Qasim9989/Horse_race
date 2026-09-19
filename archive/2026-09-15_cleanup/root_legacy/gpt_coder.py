"""
GPT / LUNA CUSTOM MODEL & CODE GENERATOR FOR ANTIGRAVITY
Supports custom OpenAI models, GPT-4o, and Custom GPT Assistant IDs (e.g. Luna).
"""

import os
import sys
import argparse

try:
    from openai import OpenAI
except ImportError:
    print("Installing openai Python package...")
    os.system("pip install openai")
    from openai import OpenAI

def run_gpt_luna(prompt, model="gpt-4o", assistant_id=None, file_path=None):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY environment variable is missing!")
        print("Please set your API key in PowerShell:")
        print('  $env:OPENAI_API_KEY="sk-proj-your-key-here"')
        return
    
    client = OpenAI(api_key=api_key)
    
    context = ""
    if file_path and os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            code_content = f.read()
        context = f"\n\n--- TARGET FILE: {file_path} ---\n{code_content}\n--- END TARGET FILE ---"
    
    full_prompt = prompt + context
    
    # If Assistant ID is provided (e.g. Luna Assistant ID)
    if assistant_id:
        print(f"Connecting to Custom GPT Assistant (ID: {assistant_id})...")
        try:
            thread = client.beta.threads.create()
            client.beta.threads.messages.create(
                thread_id=thread.id,
                role="user",
                content=full_prompt
            )
            run = client.beta.threads.runs.create_and_poll(
                thread_id=thread.id,
                assistant_id=assistant_id
            )
            messages = client.beta.threads.messages.list(thread_id=thread.id)
            answer = messages.data[0].content[0].text.value
            print("\n" + "="*80)
            print(f"GPT LUNA ASSISTANT OUTPUT ({assistant_id}):")
            print("="*80)
            print(answer)
            print("="*80)
            return answer
        except Exception as e:
            print(f"OpenAI Assistant API Error: {e}")
            return
            
    print(f"Sending prompt to OpenAI model: {model}...")
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are Luna, an elite custom AI engineer and racing quantitative strategist writing production code."},
                {"role": "user", "content": full_prompt}
            ],
            temperature=0.2
        )
        answer = response.choices[0].message.content
        print("\n" + "="*80)
        print(f"GPT LUNA OUTPUT ({model}):")
        print("="*80)
        print(answer)
        print("="*80)
        return answer
    except Exception as e:
        print(f"OpenAI API Error: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run GPT / Luna Custom Model in Antigravity")
    parser.add_argument("prompt", type=str, help="Prompt or task instructions")
    parser.add_argument("--model", type=str, default="gpt-4o", help="Model name (e.g. gpt-4o, luna, ft:gpt-4o...)")
    parser.add_argument("--assistant", type=str, default=None, help="Custom GPT Assistant ID (e.g. asst_abc123)")
    parser.add_argument("--file", type=str, default=None, help="Path to target file to analyze or edit")
    
    args = parser.parse_args()
    run_gpt_luna(args.prompt, model=args.model, assistant_id=args.assistant, file_path=args.file)
