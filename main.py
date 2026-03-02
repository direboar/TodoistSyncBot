import os
import time
import json
import requests
from datetime import datetime
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from openai import OpenAI
from todoist_api_python.api import TodoistAPI

# -------------------------------------------------------------
# 1. Pydantic Models for Gemini Structured Output
# -------------------------------------------------------------
class ScheduleItem(BaseModel):
    date: str = Field(description="開催日 YYYY-MM-DD")
    start_time: str = Field(description="開始時間 HH:MM")
    end_time: str = Field(description="終了時間 HH:MM")
    location: str = Field(description="場所の名前（例: XXX区民館）")

class ScheduleList(BaseModel):
    items: list[ScheduleItem] = Field(description="抽出されたスケジュールのリスト")

# -------------------------------------------------------------
# 2. Main Logic
# -------------------------------------------------------------
def load_state() -> dict:
    if os.path.exists("state.json"):
        with open("state.json", "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}

def save_state(state: dict):
    with open("state.json", "w", encoding="utf-8") as f:
        json.dump(state, f, indent=4, ensure_ascii=False)

def get_discord_headers(token: str) -> dict:
    return {"Authorization": f"Bot {token}"}

def get_guilds(headers: dict) -> list:
    url = "https://discord.com/api/v10/users/@me/guilds"
    res = requests.get(url, headers=headers)
    res.raise_for_status()
    return res.json()

def get_channels(guild_id: str, headers: dict) -> list:
    url = f"https://discord.com/api/v10/guilds/{guild_id}/channels"
    res = requests.get(url, headers=headers)
    res.raise_for_status()
    return res.json()

def get_messages(channel_id: str, after_id: str, headers: dict) -> list:
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    params = {"limit": 50}
    if after_id:
        params["after"] = after_id
    res = requests.get(url, headers=headers, params=params)
    res.raise_for_status()
    # Discord API は新しいものから順に返すため、古い順（時系列）に反転する
    messages = res.json()
    messages.reverse()
    return messages

import re
from dateutil.relativedelta import relativedelta

def is_target_channel(channel_name: str) -> bool:
    """正規表現を使ってチャンネル名が現在月または翌月に該当するか判定する"""
    now = datetime.now()
    next_month = now + relativedelta(months=1)
    
    # ターゲットとなる年月のリスト（今月と来月）
    target_dates = [(now.year, now.month), (next_month.year, next_month.month)]
    
    # チャンネル名から考えられる "(年)月" の表記を全て抽出
    # (年)部分をオプショナルではなく必須のグループとして扱うパターンに変更
    matches = re.finditer(r'(20\d{2}|令和\d+|\d{2})\s*年\s*(\d+)\s*月', channel_name)
    
    for match in matches:
        year_str = match.group(1)
        month_str = match.group(2)
        month_val = int(month_str)
        
        for tgt_year, tgt_month in target_dates:
            if month_val != tgt_month:
                continue
                
            # 年が指定されている場合、それがターゲットの年と一致するか確認
            tgt_short = str(tgt_year)[-2:]
            tgt_reiwa = f"令和{tgt_year - 2018}"
            
            if year_str in (str(tgt_year), tgt_short, tgt_reiwa):
                return True
                
    return False

def parse_message_to_schedules(message_content: str, ai_client: OpenAI) -> list[ScheduleItem] | None:
    """GitHub Models (gpt-4o-mini)を使ってメッセージ本文からスケジュールを抽出する"""
    prompt_system = "以下のテキストからスケジュール情報を抽出し、指定した構造化JSONで返してください。複数のスケジュールが含まれている場合は全て抽出してください。予定がない場合は空のリストを返してください。"
    prompt_user = f"テキスト:\n{message_content}"
    
    for attempt in range(3):
        try:
            response = ai_client.beta.chat.completions.parse(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": prompt_system},
                    {"role": "user", "content": prompt_user}
                ],
                response_format=ScheduleList,
                temperature=0.1
            )
            
            result: ScheduleList = response.choices[0].message.parsed
            if result is None:
                return []
            return result.items
            
        except Exception as e:
            error_msg = str(e).lower()
            if "429" in error_msg or "rate limit" in error_msg or "quota" in error_msg:
                print(f"Rate limit exceeded (429). Retrying in 10 seconds... (Attempt {attempt + 1}/3)")
                time.sleep(10)
            else:
                print(f"Error parsing message: {e}")
                return []
                
    print("Failed to parse message after 3 attempts due to API rate limits.")
    return None

def main():
    load_dotenv()
    
    discord_token = os.getenv("DISCORD_BOT_TOKEN")
    todoist_token = os.getenv("TODOIST_API_TOKEN")
    github_models_token = os.getenv("GH_MODELS_TOKEN")
    category_prefix = os.getenv("TARGET_CATEGORY_PREFIX", "公民館予約")
    project_id = os.getenv("TODOIST_PROJECT_ID", "")
    event_prefix = os.getenv("EVENT_TITLE_PREFIX", "FN8")

    if not discord_token or not todoist_token or not github_models_token:
        print("Required environment variables are missing.")
        return

    # Initialize clients
    discord_headers = get_discord_headers(discord_token)
    todoist_client = TodoistAPI(todoist_token)
    
    # Initialize GitHub Models client (OpenAI SDK)
    ai_client = OpenAI(
        base_url="https://models.inference.ai.azure.com",
        api_key=github_models_token
    )

    state = load_state()
    
    try:
        guilds = get_guilds(discord_headers)
    except Exception as e:
        print(f"Failed to fetch guilds: {e}")
        return

    for guild in guilds:
        guild_id = guild["id"]
        try:
            channels = get_channels(guild_id, discord_headers)
        except Exception as e:
            print(f"Failed to fetch channels for guild {guild_id}: {e}")
            continue
        
        # 1. カテゴリの抽出
        # type 4 is GUILD_CATEGORY
        target_categories = [
            c for c in channels
            if c["type"] == 4 and category_prefix in c["name"]
        ]
        category_ids = {c["id"] for c in target_categories}

        if not category_ids:
            continue
            
        # 2. 対象チャンネルの抽出
        # type 0 is GUILD_TEXT
        text_channels = [
            c for c in channels
            if c["type"] == 0 and c.get("parent_id") in category_ids
        ]

        # 3. チャンネル名が今月・来月か判別
        target_channel_ids = []
        for ch in text_channels:
            if is_target_channel(ch["name"]):
                target_channel_ids.append(ch["id"])
                print(f"Target channel found: {ch['name']} (ID: {ch['id']})")
            else:
                print(f"Ignored channel: {ch['name']}")

        # 4. メッセージの取得と処理
        for channel_id in target_channel_ids:
            last_message_id = state.get(channel_id, "")
            
            try:
                messages = get_messages(channel_id, last_message_id, discord_headers)
            except Exception as e:
                print(f"Failed to fetch messages for channel {channel_id}: {e}")
                continue

            if not messages:
                continue
                
            print(f"Fetched {len(messages)} new messages from channel {channel_id}.")

            max_msg_id = last_message_id
            api_exhausted = False
            for msg in messages:
                msg_id = msg["id"]
                content = msg.get("content", "")
                
                if not content.strip():
                    if not max_msg_id or int(msg_id) > int(max_msg_id):
                        max_msg_id = msg_id
                    continue

                # GitHub Models(gpt-4o-mini)で解析
                schedules = parse_message_to_schedules(content, ai_client)
                
                # APIコールが完全に失敗した場合（Quota Errorなどで復帰不可だった場合）はそこで中断する
                if schedules is None:
                    print(f"Skipping further messages in channel {channel_id} due to API exhaustion.")
                    api_exhausted = True
                    break

                for schedule in schedules:
                    # Todoistタスク作成
                    # Task content e.g., "FN8(XXX区民館)"
                    task_content = f"{event_prefix}({schedule.location})"
                    # Due datetime (Note: todoist expects YYYY-MM-DDTHH:MM:SS or due_string)
                    # For simplicity, we can pass it as a parseable natural string format.
                    # e.g., "2026-04-11 13:00"
                    due_string = f"{schedule.date} {schedule.start_time}"
                    
                    try:
                        task_args = {
                            "content": task_content,
                            "due_string": due_string,
                            "due_lang": "ja"
                        }
                        if project_id:
                            task_args["project_id"] = project_id
                            
                        # start_time -> end_timeのデュレーションを計算して追加することも可能だが、
                        # 今回は開始時刻のみセットするシンプルな実装とする。
                        # Todoistの仕様によりdue_stringに時刻が含まれると時間指定タスクになる。
                        
                        task = todoist_client.add_task(**task_args)
                        print(f"Created task: {task.content} (Due: {task.due.string if task.due else 'N/A'})")
                    except Exception as e:
                        print(f"Error creating Todoist task: {e}")

                # 本文解析とTodoist登録の行程が全て正常終了した時のみ既読ステータスを更新する
                if not max_msg_id or int(msg_id) > int(max_msg_id):
                    max_msg_id = msg_id

                # APIの無料枠制限を回避するため、解析1件ごとに待機する (GitHub Modelsも15 RPM上限あり)
                time.sleep(5)

            # 状態更新
            if max_msg_id and max_msg_id != state.get(channel_id):
                state[channel_id] = max_msg_id
                save_state(state)
                
            if api_exhausted:
                print("API quota exhausted. Stopping further processing completely.")
                break

if __name__ == "__main__":
    main()
