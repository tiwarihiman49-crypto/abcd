"""
chat_logger.py
Step 5 - Save every question and answer using Pandas.
"""

import os
import re
from datetime import datetime

import pandas as pd

import config

COLUMNS = [
    "chat_no", "timestamp", "question", "answer",
    "question_words", "answer_words", "response_time_sec",
]


def _storage_paths(workspace_id=None):
    if workspace_id is None:
        return config.DATA_DIR, config.LOG_FILE
    if not re.fullmatch(r"[0-9a-f]{32}", workspace_id):
        raise ValueError("Invalid workspace identifier.")
    data_dir = os.path.join(config.DATA_DIR, workspace_id)
    return data_dir, os.path.join(data_dir, "chat_history.csv")


def load_history(workspace_id=None):
    """Read one workspace's CSV file (or the CLI history when omitted)."""
    _, log_file = _storage_paths(workspace_id)
    if not os.path.exists(log_file):
        return pd.DataFrame(columns=COLUMNS)
    return pd.read_csv(log_file)


def save_chat(question, answer, response_time, workspace_id=None):
    """Append to one workspace's CSV (or the CLI history when omitted)."""
    data_dir, log_file = _storage_paths(workspace_id)
    os.makedirs(data_dir, exist_ok=True)

    chat_no = len(load_history(workspace_id)) + 1
    new_row = pd.DataFrame([{
        "chat_no": chat_no,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "question": question,
        "answer": answer,
        "question_words": len(question.split()),
        "answer_words": len(answer.split()),
        "response_time_sec": response_time,
    }])

    file_is_new = not os.path.exists(log_file)
    new_row.to_csv(log_file, mode="a", header=file_is_new, index=False)
